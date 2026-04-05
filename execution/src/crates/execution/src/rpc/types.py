from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from execution import AccessListEntry, Block, BlockEnvironment, ChainConfig, EIP1559Transaction, LegacyTransaction, ZKTransaction
from execution.hashing import address_from_hex, hash_from_hex, hex_to_bytes
from execution.transaction import Transaction
from primitives import Address, Hash, U256


LATEST = "latest"
EARLIEST = "earliest"
PENDING = "pending"


def to_quantity(value: int) -> str:
    normalized = int(value)
    if normalized < 0:
        raise ValueError("quantities must be non-negative")
    return hex(normalized)


def to_data(value: bytes | bytearray | memoryview, *, expected_length: int | None = None) -> str:
    raw = bytes(value)
    if expected_length is not None and len(raw) != expected_length:
        raise ValueError(f"value must be {expected_length} bytes")
    return "0x" + raw.hex()


def to_word(value: int) -> str:
    if value < 0:
        raise ValueError("word values must be non-negative")
    return "0x" + value.to_bytes(32, byteorder="big", signed=False).hex()


def parse_quantity(value: object, *, label: str) -> int:
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{label} must be non-negative")
        return value
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a hex quantity string")
    if not value.startswith(("0x", "0X")):
        raise ValueError(f"{label} must be a 0x-prefixed hex quantity")
    if value in {"0x", "0X"}:
        raise ValueError(f"{label} cannot be empty")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be valid hexadecimal") from exc


def parse_data(value: object, *, label: str, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a hex string")
    return hex_to_bytes(value, label=label, expected_length=expected_length)


def parse_bool(value: object, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be a boolean")
    return value


def parse_address(value: object, *, label: str = "address") -> Address:
    if isinstance(value, Address):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a hex address")
    return address_from_hex(value, label=label)


def parse_hash(value: object, *, label: str = "hash") -> Hash:
    if isinstance(value, Hash):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a hex hash")
    return hash_from_hex(value, label=label)


@dataclass(frozen=True, slots=True)
class BlockSelector:
    number: int | None = None
    tag: str | None = None

    def __post_init__(self) -> None:
        if (self.number is None) == (self.tag is None):
            raise ValueError("block selectors must contain exactly one of number or tag")

    @property
    def is_pending(self) -> bool:
        return self.tag == PENDING


def parse_block_selector(value: object | None, *, default: str = LATEST) -> BlockSelector:
    if value is None:
        return BlockSelector(tag=default)
    if isinstance(value, BlockSelector):
        return value
    if isinstance(value, int):
        return BlockSelector(number=value)
    if not isinstance(value, str):
        raise TypeError("block selector must be a tag or hex quantity")
    lowered = value.lower()
    if lowered in {LATEST, EARLIEST, PENDING}:
        return BlockSelector(tag=lowered)
    if lowered in {"safe", "finalized"}:
        raise ValueError(f"unsupported block tag: {value}")
    return BlockSelector(number=parse_quantity(lowered, label="block number"))


def serialize_access_list(access_list: Sequence[AccessListEntry]) -> list[dict[str, object]]:
    return [
        {
            "address": entry.address.to_hex(),
            "storageKeys": [to_word(int(key)) for key in entry.storage_keys],
        }
        for entry in access_list
    ]


@dataclass(frozen=True, slots=True)
class CallRequest:
    from_address: Address
    to: Address | None
    gas: int | None
    gas_price: int | None
    max_fee_per_gas: int | None
    max_priority_fee_per_gas: int | None
    value: int
    data: bytes
    access_list: tuple[AccessListEntry, ...] = ()

    def effective_gas_price(self, block_env: BlockEnvironment, compat_default_gas_price: int, compat_priority_fee: int) -> int:
        if self.max_fee_per_gas is not None or self.max_priority_fee_per_gas is not None:
            if block_env.base_fee is None:
                raise ValueError("maxFeePerGas requires an EIP-1559 block context")
            max_priority = compat_priority_fee if self.max_priority_fee_per_gas is None else self.max_priority_fee_per_gas
            max_fee = block_env.base_fee + max_priority if self.max_fee_per_gas is None else self.max_fee_per_gas
            if max_fee < max_priority:
                raise ValueError("maxFeePerGas must be at least maxPriorityFeePerGas")
            if max_fee < block_env.base_fee:
                raise ValueError("maxFeePerGas is below the current base fee")
            return block_env.base_fee + min(max_priority, max_fee - block_env.base_fee)
        if self.gas_price is not None:
            if block_env.base_fee is not None and self.gas_price < block_env.base_fee:
                raise ValueError("gasPrice is below the current base fee")
            return self.gas_price
        if block_env.base_fee is None:
            return compat_default_gas_price
        return max(compat_default_gas_price, block_env.base_fee + compat_priority_fee)

    def synthetic_transaction(self, chain_config: ChainConfig, gas_limit: int) -> Transaction:
        if self.max_fee_per_gas is not None or self.max_priority_fee_per_gas is not None:
            return EIP1559Transaction(
                chain_id=chain_config.chain_id,
                nonce=0,
                max_priority_fee_per_gas=0 if self.max_priority_fee_per_gas is None else self.max_priority_fee_per_gas,
                max_fee_per_gas=0 if self.max_fee_per_gas is None else self.max_fee_per_gas,
                gas_limit=gas_limit,
                to=self.to,
                value=self.value,
                data=self.data,
                access_list=self.access_list,
            )
        return LegacyTransaction(
            nonce=0,
            gas_price=0 if self.gas_price is None else self.gas_price,
            gas_limit=gas_limit,
            to=self.to,
            value=self.value,
            data=self.data,
            chain_id=chain_config.chain_id,
        )


def parse_access_list(value: object) -> tuple[AccessListEntry, ...]:
    if not isinstance(value, list):
        raise TypeError("accessList must be a list")
    entries: list[AccessListEntry] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(f"accessList[{index}] must be an object")
        storage_keys_raw = item.get("storageKeys", [])
        if not isinstance(storage_keys_raw, list):
            raise TypeError(f"accessList[{index}].storageKeys must be a list")
        entries.append(
            AccessListEntry(
                address=parse_address(item.get("address"), label=f"accessList[{index}].address"),
                storage_keys=tuple(U256(parse_quantity(key, label=f"accessList[{index}].storageKeys")) for key in storage_keys_raw),
            )
        )
    return tuple(entries)


def parse_call_request(value: object) -> CallRequest:
    if not isinstance(value, dict):
        raise TypeError("call object must be a JSON object")
    data_value = value.get("data")
    input_value = value.get("input")
    if data_value is not None and input_value is not None and data_value != input_value:
        raise ValueError("call object data and input fields disagree")
    payload = "0x" if data_value is None and input_value is None else data_value if data_value is not None else input_value
    to_value = value.get("to")
    return CallRequest(
        from_address=Address.zero() if value.get("from") is None else parse_address(value.get("from"), label="from"),
        to=None if to_value is None else parse_address(to_value, label="to"),
        gas=None if value.get("gas") is None else parse_quantity(value.get("gas"), label="gas"),
        gas_price=None if value.get("gasPrice") is None else parse_quantity(value.get("gasPrice"), label="gasPrice"),
        max_fee_per_gas=None
        if value.get("maxFeePerGas") is None
        else parse_quantity(value.get("maxFeePerGas"), label="maxFeePerGas"),
        max_priority_fee_per_gas=None
        if value.get("maxPriorityFeePerGas") is None
        else parse_quantity(value.get("maxPriorityFeePerGas"), label="maxPriorityFeePerGas"),
        value=0 if value.get("value") is None else parse_quantity(value.get("value"), label="value"),
        data=parse_data(payload, label="data"),
        access_list=() if value.get("accessList") is None else parse_access_list(value.get("accessList")),
    )


def transaction_type_hex(transaction: Transaction) -> str:
    if isinstance(transaction, LegacyTransaction):
        return "0x0"
    return to_quantity(int(transaction.type_byte))


def serialize_transaction(
    transaction: Transaction,
    *,
    sender: Address,
    block_hash: Hash | None,
    block_number: int | None,
    transaction_index: int | None,
    effective_gas_price: int | None = None,
) -> dict[str, object]:
    gas_price: int
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None
    access_list: Sequence[AccessListEntry] = ()
    chain_id: int | None
    if isinstance(transaction, LegacyTransaction):
        gas_price = int(transaction.gas_price)
        chain_id = transaction.chain_id
        v = 0 if transaction.v is None else transaction.v
        r = 0 if transaction.r is None else int(transaction.r)
        s = 0 if transaction.s is None else int(transaction.s)
    elif isinstance(transaction, ZKTransaction):
        gas_price = int(transaction.max_fee_per_gas) if effective_gas_price is None else effective_gas_price
        max_fee_per_gas = int(transaction.max_fee_per_gas)
        max_priority_fee_per_gas = int(transaction.max_priority_fee_per_gas)
        access_list = transaction.access_list
        chain_id = transaction.chain_id
        v = 0 if transaction.v is None else transaction.v
        r = 0 if transaction.r is None else int(transaction.r)
        s = 0 if transaction.s is None else int(transaction.s)
    else:
        gas_price = int(transaction.max_fee_per_gas) if effective_gas_price is None else effective_gas_price
        max_fee_per_gas = int(transaction.max_fee_per_gas)
        max_priority_fee_per_gas = int(transaction.max_priority_fee_per_gas)
        access_list = transaction.access_list
        chain_id = transaction.chain_id
        v = 0 if transaction.v is None else transaction.v
        r = 0 if transaction.r is None else int(transaction.r)
        s = 0 if transaction.s is None else int(transaction.s)
    result: dict[str, object] = {
        "hash": transaction.tx_hash().to_hex(),
        "nonce": to_quantity(int(transaction.nonce)),
        "blockHash": None if block_hash is None else block_hash.to_hex(),
        "blockNumber": None if block_number is None else to_quantity(block_number),
        "transactionIndex": None if transaction_index is None else to_quantity(transaction_index),
        "from": sender.to_hex(),
        "to": None if transaction.to is None else transaction.to.to_hex(),
        "value": to_quantity(int(transaction.value)),
        "gas": to_quantity(int(transaction.gas_limit)),
        "gasPrice": to_quantity(gas_price),
        "input": to_data(transaction.data),
        "type": transaction_type_hex(transaction),
        "chainId": None if chain_id is None else to_quantity(chain_id),
        "v": to_quantity(v),
        "r": to_quantity(r),
        "s": to_quantity(s),
    }
    if max_fee_per_gas is not None:
        result["maxFeePerGas"] = to_quantity(max_fee_per_gas)
    if max_priority_fee_per_gas is not None:
        result["maxPriorityFeePerGas"] = to_quantity(max_priority_fee_per_gas)
    if access_list:
        result["accessList"] = serialize_access_list(access_list)
    return result


def serialize_log(
    *,
    address: Address,
    topics: Sequence[U256],
    data: bytes,
    block_hash: Hash,
    block_number: int,
    transaction_hash: Hash,
    transaction_index: int,
    log_index: int,
) -> dict[str, object]:
    return {
        "address": address.to_hex(),
        "topics": [to_word(int(topic)) for topic in topics],
        "data": to_data(data),
        "blockNumber": to_quantity(block_number),
        "transactionHash": transaction_hash.to_hex(),
        "transactionIndex": to_quantity(transaction_index),
        "blockHash": block_hash.to_hex(),
        "logIndex": to_quantity(log_index),
        "removed": False,
    }


def serialize_receipt(
    *,
    transaction: Transaction,
    sender: Address,
    receipt: Any,
    block_hash: Hash,
    block_number: int,
    transaction_index: int,
    log_index_start: int,
) -> dict[str, object]:
    logs = [
        serialize_log(
            address=log.address,
            topics=log.topics,
            data=log.data,
            block_hash=block_hash,
            block_number=block_number,
            transaction_hash=transaction.tx_hash(),
            transaction_index=transaction_index,
            log_index=log_index_start + offset,
        )
        for offset, log in enumerate(receipt.logs)
    ]
    return {
        "transactionHash": transaction.tx_hash().to_hex(),
        "transactionIndex": to_quantity(transaction_index),
        "blockHash": block_hash.to_hex(),
        "blockNumber": to_quantity(block_number),
        "from": sender.to_hex(),
        "to": None if transaction.to is None else transaction.to.to_hex(),
        "contractAddress": None if receipt.contract_address is None else receipt.contract_address.to_hex(),
        "cumulativeGasUsed": to_quantity(receipt.cumulative_gas_used),
        "gasUsed": to_quantity(receipt.gas_used),
        "logs": logs,
        "logsBloom": to_data(receipt.logs_bloom, expected_length=256),
        "status": None if receipt.status is None else to_quantity(receipt.status),
        "effectiveGasPrice": None
        if receipt.effective_gas_price is None
        else to_quantity(receipt.effective_gas_price),
        "type": transaction_type_hex(transaction),
    }


def serialize_block(
    block: Block,
    *,
    total_difficulty: int,
    size: int,
    transactions: Sequence[dict[str, object] | str],
    pending: bool = False,
) -> dict[str, object]:
    header = block.header
    result: dict[str, object] = {
        "number": None if pending else to_quantity(header.number),
        "hash": None if pending else block.hash().to_hex(),
        "parentHash": header.parent_hash.to_hex(),
        "nonce": None if pending else to_data(header.nonce, expected_length=8),
        "sha3Uncles": header.ommers_hash.to_hex(),
        "logsBloom": None if pending else to_data(header.logs_bloom, expected_length=256),
        "transactionsRoot": header.transactions_root.to_hex(),
        "stateRoot": header.state_root.to_hex(),
        "receiptsRoot": header.receipts_root.to_hex(),
        "miner": header.coinbase.to_hex(),
        "difficulty": to_quantity(header.difficulty),
        "totalDifficulty": None if pending else to_quantity(total_difficulty),
        "extraData": to_data(header.extra_data),
        "size": None if pending else to_quantity(size),
        "gasLimit": to_quantity(header.gas_limit),
        "gasUsed": to_quantity(header.gas_used),
        "timestamp": to_quantity(header.timestamp),
        "transactions": list(transactions),
        "uncles": [ommer.hash().to_hex() for ommer in block.ommers],
        "mixHash": header.mix_hash.to_hex(),
    }
    if header.base_fee is not None:
        result["baseFeePerGas"] = to_quantity(header.base_fee)
    return result
