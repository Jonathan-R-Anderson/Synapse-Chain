from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from evm import LogEntry
from execution import Block, BlockHeader, Receipt
from execution_tests.loader import (
    FixtureLoadingError,
    _parse_accounts,
    _parse_expected_result,
    _parse_log,
    _parse_receipts,
    _parse_transaction,
    hex_to_bytes,
    hex_to_int,
    normalize_address,
    normalize_hash,
)
from execution_tests.models import ExpectedResult, TestTransaction

from .reference import ReferenceBlockBundle


class ReplayLoadingError(ValueError):
    pass


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReplayLoadingError(f"unable to read block bundle {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReplayLoadingError(f"block bundle {path} is not valid JSON") from exc


def _receipt_from_rpc(data: Mapping[str, object]) -> Receipt:
    logs = []
    for index, item in enumerate(data.get("logs", [])):
        if not isinstance(item, Mapping):
            raise ReplayLoadingError(f"receipt log {index} must be an object")
        logs.append(_parse_log(item, label=f"receipt.logs[{index}]"))
    return Receipt(
        status=None if data.get("status") is None else hex_to_int(data["status"], label="receipt.status"),
        post_state=None if data.get("root") is None else normalize_hash(str(data["root"]), label="receipt.root"),
        cumulative_gas_used=hex_to_int(data.get("cumulativeGasUsed", data.get("cumulative_gas_used", "0x0")), label="receipt.cumulative_gas_used"),
        gas_used=hex_to_int(data.get("gasUsed", data.get("gas_used", "0x0")), label="receipt.gas_used"),
        logs=tuple(logs),
        transaction_type=None if data.get("type") is None else hex_to_int(data["type"], label="receipt.type"),
        contract_address=None if data.get("contractAddress") is None else normalize_address(
            str(data["contractAddress"]),
            label="receipt.contractAddress",
        ),
        transaction_hash=None if data.get("transactionHash") is None else normalize_hash(
            str(data["transactionHash"]),
            label="receipt.transactionHash",
        ),
        effective_gas_price=None if data.get("effectiveGasPrice") is None else hex_to_int(
            data["effectiveGasPrice"],
            label="receipt.effectiveGasPrice",
        ),
        bloom=hex_to_bytes(
            str(data.get("logsBloom", "0x" + ("00" * 256))),
            label="receipt.logsBloom",
            expected_length=256,
        ),
    )


def _header_from_rpc(data: Mapping[str, object]) -> BlockHeader:
    return BlockHeader(
        parent_hash=normalize_hash(str(data["parentHash"]), label="block.parentHash"),
        ommers_hash=normalize_hash(str(data["sha3Uncles"]), label="block.sha3Uncles"),
        coinbase=normalize_address(str(data.get("miner", data.get("beneficiary"))), label="block.miner"),
        state_root=normalize_hash(str(data["stateRoot"]), label="block.stateRoot"),
        transactions_root=normalize_hash(str(data["transactionsRoot"]), label="block.transactionsRoot"),
        receipts_root=normalize_hash(str(data["receiptsRoot"]), label="block.receiptsRoot"),
        logs_bloom=hex_to_bytes(str(data["logsBloom"]), label="block.logsBloom", expected_length=256),
        difficulty=hex_to_int(data.get("difficulty", "0x0"), label="block.difficulty"),
        number=hex_to_int(data["number"], label="block.number"),
        gas_limit=hex_to_int(data["gasLimit"], label="block.gasLimit"),
        gas_used=hex_to_int(data["gasUsed"], label="block.gasUsed"),
        timestamp=hex_to_int(data["timestamp"], label="block.timestamp"),
        extra_data=hex_to_bytes(str(data.get("extraData", "0x")), label="block.extraData"),
        mix_hash=normalize_hash(str(data.get("mixHash", data.get("prevRandao", "0x" + ("00" * 32)))), label="block.mixHash"),
        nonce=hex_to_bytes(str(data.get("nonce", "0x0000000000000000")), label="block.nonce", expected_length=8),
        base_fee=None
        if data.get("baseFeePerGas", data.get("base_fee_per_gas")) is None
        else hex_to_int(data.get("baseFeePerGas", data.get("base_fee_per_gas")), label="block.baseFeePerGas"),
    )


def _parse_transactions_from_rpc(data: Sequence[object], *, chain_id: int) -> tuple[tuple[object, ...], bool]:
    transactions = []
    complete = True
    for index, item in enumerate(data):
        if isinstance(item, str):
            complete = False
            continue
        if not isinstance(item, Mapping):
            raise ReplayLoadingError(f"transaction {index} must be an object or hash string")
        tx_input = dict(item)
        if "data" not in tx_input and "input" in tx_input:
            tx_input["data"] = tx_input["input"]
        if "gas_limit" not in tx_input and "gas" in tx_input:
            tx_input["gas_limit"] = tx_input["gas"]
        if "tx_type" not in tx_input and "type" in tx_input:
            tx_input["tx_type"] = tx_input["type"]
        if "chain_id" not in tx_input and "chainId" not in tx_input:
            tx_input["chain_id"] = chain_id
        fixture_tx = _parse_transaction(tx_input)
        transactions.append(fixture_tx.to_transaction(default_chain_id=chain_id))
    return tuple(transactions), complete


def _block_from_rpc(data: Mapping[str, object], *, chain_id: int, receipts: tuple[Receipt, ...]) -> tuple[Block, bool]:
    transactions_raw = data.get("transactions", [])
    if not isinstance(transactions_raw, Sequence):
        raise ReplayLoadingError("rpc block.transactions must be a list")
    transactions, complete = _parse_transactions_from_rpc(transactions_raw, chain_id=chain_id)
    return Block(header=_header_from_rpc(data), transactions=transactions, receipts=receipts), complete


def load_block_bundle(path: str | Path, *, state_path: str | Path | None = None) -> ReferenceBlockBundle:
    bundle_path = Path(path)
    raw = _read_json(bundle_path)
    if not isinstance(raw, Mapping):
        raise ReplayLoadingError("block bundle must decode to a JSON object")

    block_payload = raw.get("block", raw.get("result", raw))
    receipts_payload = raw.get("receipts")
    receipts: tuple[Receipt, ...]
    if isinstance(receipts_payload, Sequence):
        if receipts_payload and isinstance(receipts_payload[0], Mapping) and "blockHash" in receipts_payload[0]:
            receipts = tuple(_receipt_from_rpc(item) for item in receipts_payload if isinstance(item, Mapping))
        else:
            receipts = _parse_receipts(receipts_payload)  # type: ignore[arg-type]
    else:
        receipts = ()

    chain_id = int(raw.get("chain_id", raw.get("chainId", 1)))
    transaction_bodies_complete = True
    if isinstance(block_payload, Mapping) and "header" in block_payload:
        block = Block.from_dict(dict(block_payload))
        transaction_count = len(block.transactions)
    elif isinstance(block_payload, Mapping) and "parentHash" in block_payload:
        block, transaction_bodies_complete = _block_from_rpc(block_payload, chain_id=chain_id, receipts=receipts)
        raw_transactions = block_payload.get("transactions", [])
        transaction_count = len(raw_transactions) if isinstance(raw_transactions, Sequence) else len(block.transactions)
    else:
        raise ReplayLoadingError("unsupported block payload shape")

    parent_header = None
    if raw.get("parent_header") is not None:
        if not isinstance(raw["parent_header"], Mapping):
            raise ReplayLoadingError("parent_header must be an object")
        parent_header = BlockHeader.from_dict(dict(raw["parent_header"]))

    pre_state_payload = raw.get("pre_state", raw.get("state"))
    if pre_state_payload is None and state_path is not None:
        separate_state = _read_json(Path(state_path))
        pre_state_payload = separate_state
    pre_state = _parse_accounts(pre_state_payload) if isinstance(pre_state_payload, (Mapping, Sequence)) else ()

    expected_payload = raw.get("expected")
    if isinstance(expected_payload, Mapping):
        expected = _parse_expected_result(expected_payload)
    else:
        expected = ExpectedResult(
            gas_used=block.header.gas_used,
            state_root=block.header.state_root,
            receipts_root=block.header.receipts_root,
            transactions_root=block.header.transactions_root,
            logs_bloom=block.header.logs_bloom,
            receipts=receipts,
            logs=tuple(log for receipt in receipts for log in receipt.logs),
        )

    return ReferenceBlockBundle(
        name=str(raw.get("name", bundle_path.stem)),
        block=block,
        pre_state=pre_state,
        expected=expected,
        parent_header=parent_header,
        chain_id=chain_id,
        fork_name=None if raw.get("fork") is None else str(raw["fork"]),
        transaction_bodies_complete=transaction_bodies_complete,
        transaction_count=transaction_count,
        description=None if raw.get("description") is None else str(raw["description"]),
    )
