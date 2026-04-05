from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from evm import LogEntry
from execution import Receipt
from primitives import Address, Hash, U256
from zk import ProofType

from .models import AccountFixture, ExecutionFixtureCase, ExpectedResult, MessageCall, TestEnvironment, TestTransaction


class FixtureLoadingError(ValueError):
    pass


def hex_to_bytes(value: str, *, label: str = "value", expected_length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise FixtureLoadingError(f"{label} must be a hex string")
    if not value.startswith(("0x", "0X")):
        raise FixtureLoadingError(f"{label} must use a 0x-prefixed hex string")
    normalized = value[2:]
    if len(normalized) % 2 != 0:
        raise FixtureLoadingError(f"{label} must contain an even number of hex digits")
    try:
        raw = bytes.fromhex(normalized)
    except ValueError as exc:
        raise FixtureLoadingError(f"{label} must be valid hexadecimal") from exc
    if expected_length is not None and len(raw) != expected_length:
        raise FixtureLoadingError(f"{label} must be exactly {expected_length} bytes")
    return raw


def hex_to_int(value: str | int, *, label: str = "value") -> int:
    if isinstance(value, int):
        if value < 0:
            raise FixtureLoadingError(f"{label} must be non-negative")
        return value
    if not isinstance(value, str):
        raise FixtureLoadingError(f"{label} must be an int or 0x-prefixed string")
    if not value.startswith(("0x", "0X")):
        raise FixtureLoadingError(f"{label} must use a 0x-prefixed hex integer")
    normalized = value[2:].lower()
    if normalized == "":
        raise FixtureLoadingError(f"{label} must not be empty")
    if normalized != "0" and normalized.startswith("0"):
        raise FixtureLoadingError(f"{label} must not contain ambiguous leading zeroes")
    if any(char not in "0123456789abcdef" for char in normalized):
        raise FixtureLoadingError(f"{label} must contain only hexadecimal digits")
    return int(normalized, 16)


def normalize_address(value: str, *, label: str = "address") -> Address:
    return Address(hex_to_bytes(value, label=label, expected_length=Address.SIZE))


def normalize_hash(value: str, *, label: str = "hash") -> Hash:
    return Hash(hex_to_bytes(value, label=label, expected_length=Hash.SIZE))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FixtureLoadingError(f"unable to read fixture file {path}") from exc
    except json.JSONDecodeError as exc:
        raise FixtureLoadingError(f"fixture file {path} is not valid JSON") from exc


def _parse_storage_map(storage_data: Mapping[str, object] | None) -> tuple[tuple[U256, U256], ...]:
    if storage_data is None:
        return ()
    normalized = []
    for key, value in storage_data.items():
        normalized.append((U256(hex_to_int(key, label="storage key")), U256(hex_to_int(value, label="storage value"))))
    return tuple(sorted(normalized, key=lambda item: int(item[0])))


def _parse_account_fixture(address: str, data: Mapping[str, object]) -> AccountFixture:
    return AccountFixture(
        address=normalize_address(address, label="account address"),
        nonce=hex_to_int(data.get("nonce", "0x0"), label=f"{address}.nonce"),
        balance=hex_to_int(data.get("balance", "0x0"), label=f"{address}.balance"),
        code=hex_to_bytes(str(data.get("code", "0x")), label=f"{address}.code"),
        storage=_parse_storage_map(data.get("storage") if isinstance(data.get("storage"), Mapping) else None),
    )


def _parse_accounts(data: Mapping[str, object] | Sequence[Mapping[str, object]] | None) -> tuple[AccountFixture, ...]:
    if data is None:
        return ()
    if isinstance(data, Mapping):
        return tuple(
            _parse_account_fixture(address, account_data if isinstance(account_data, Mapping) else {})
            for address, account_data in sorted(data.items())
        )
    accounts: list[AccountFixture] = []
    for index, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise FixtureLoadingError(f"account fixture at index {index} must be an object")
        address = item.get("address")
        if not isinstance(address, str):
            raise FixtureLoadingError(f"account fixture at index {index} requires an address")
        accounts.append(_parse_account_fixture(address, item))
    return tuple(accounts)


def _parse_log(data: Mapping[str, object], *, label: str) -> LogEntry:
    address = data.get("address")
    if not isinstance(address, str):
        raise FixtureLoadingError(f"{label}.address must be a hex string")
    topics = data.get("topics", [])
    if not isinstance(topics, Sequence):
        raise FixtureLoadingError(f"{label}.topics must be a list")
    return LogEntry(
        address=normalize_address(address, label=f"{label}.address"),
        topics=tuple(U256(hex_to_int(topic, label=f"{label}.topics")) for topic in topics),
        data=hex_to_bytes(str(data.get("data", "0x")), label=f"{label}.data"),
    )


def _parse_receipts(data: Sequence[Mapping[str, object]] | None) -> tuple[Receipt, ...]:
    if data is None:
        return ()
    receipts: list[Receipt] = []
    for index, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise FixtureLoadingError(f"receipt at index {index} must be an object")
        receipts.append(Receipt.from_dict(dict(item)))
    return tuple(receipts)


def _parse_environment(data: Mapping[str, object]) -> TestEnvironment:
    coinbase = data.get("coinbase", data.get("beneficiary", Address.zero().to_hex()))
    if not isinstance(coinbase, str):
        raise FixtureLoadingError("environment.coinbase must be a hex string")
    prev_randao = data.get("prev_randao", data.get("prevRandao"))
    return TestEnvironment(
        coinbase=normalize_address(coinbase, label="environment.coinbase"),
        gas_limit=hex_to_int(data.get("gas_limit", data.get("gasLimit", "0x0")), label="environment.gas_limit"),
        number=hex_to_int(data.get("number", "0x0"), label="environment.number"),
        timestamp=hex_to_int(data.get("timestamp", "0x0"), label="environment.timestamp"),
        difficulty=None if data.get("difficulty") is None else hex_to_int(data["difficulty"], label="environment.difficulty"),
        prev_randao=None if prev_randao is None else normalize_hash(str(prev_randao), label="environment.prev_randao"),
        base_fee=None if data.get("base_fee", data.get("baseFee")) is None else hex_to_int(
            data.get("base_fee", data.get("baseFee")),
            label="environment.base_fee",
        ),
        chain_id=None if data.get("chain_id", data.get("chainId")) is None else int(data.get("chain_id", data.get("chainId"))),
    )


def _parse_message_call(data: Mapping[str, object]) -> MessageCall:
    return MessageCall(
        caller=normalize_address(str(data["caller"]), label="message_call.caller"),
        to=normalize_address(str(data["to"]), label="message_call.to"),
        value=hex_to_int(data.get("value", "0x0"), label="message_call.value"),
        gas=hex_to_int(data.get("gas", "0xf4240"), label="message_call.gas"),
        data=hex_to_bytes(str(data.get("data", "0x")), label="message_call.data"),
        static=bool(data.get("static", False)),
    )


def _parse_access_list(data: Sequence[Mapping[str, object]] | None) -> tuple[tuple[Address, tuple[U256, ...]], ...]:
    if data is None:
        return ()
    entries: list[tuple[Address, tuple[U256, ...]]] = []
    for index, item in enumerate(data):
        if not isinstance(item, Mapping):
            raise FixtureLoadingError(f"access list entry {index} must be an object")
        address = item.get("address")
        if not isinstance(address, str):
            raise FixtureLoadingError(f"access list entry {index} requires an address")
        keys = item.get("storage_keys", item.get("storageKeys", []))
        if not isinstance(keys, Sequence):
            raise FixtureLoadingError(f"access list entry {index}.storage_keys must be a list")
        entries.append(
            (
                normalize_address(address, label=f"access_list[{index}].address"),
                tuple(U256(hex_to_int(key, label=f"access_list[{index}].storage_keys")) for key in keys),
            )
        )
    return tuple(entries)


def _parse_proof_type(value: str | int | None) -> ProofType | int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return ProofType(value)
    lowered = value.strip().lower()
    mapping = {
        "groth16": ProofType.GROTH16,
        "plonk": ProofType.PLONK,
        "stark": ProofType.STARK,
    }
    if lowered.startswith("0x"):
        return ProofType(hex_to_int(lowered, label="proof_type"))
    try:
        return mapping[lowered]
    except KeyError as exc:
        raise FixtureLoadingError(f"unsupported proof_type: {value}") from exc


def _parse_transaction(data: Mapping[str, object]) -> TestTransaction:
    sender = data.get("sender")
    destination = data.get("to")
    raw = data.get("raw")
    return TestTransaction(
        sender=None if sender is None else normalize_address(str(sender), label="transaction.sender"),
        to=None if destination is None else normalize_address(str(destination), label="transaction.to"),
        gas_limit=hex_to_int(data.get("gas_limit", data.get("gas", "0x0")), label="transaction.gas_limit"),
        gas_price=None if data.get("gas_price", data.get("gasPrice")) is None else hex_to_int(
            data.get("gas_price", data.get("gasPrice")),
            label="transaction.gas_price",
        ),
        max_fee_per_gas=None
        if data.get("max_fee_per_gas", data.get("maxFeePerGas")) is None
        else hex_to_int(data.get("max_fee_per_gas", data.get("maxFeePerGas")), label="transaction.max_fee_per_gas"),
        max_priority_fee_per_gas=None
        if data.get("max_priority_fee_per_gas", data.get("maxPriorityFeePerGas")) is None
        else hex_to_int(
            data.get("max_priority_fee_per_gas", data.get("maxPriorityFeePerGas")),
            label="transaction.max_priority_fee_per_gas",
        ),
        value=hex_to_int(data.get("value", "0x0"), label="transaction.value"),
        data=hex_to_bytes(str(data.get("data", "0x")), label="transaction.data"),
        nonce=hex_to_int(data.get("nonce", "0x0"), label="transaction.nonce"),
        access_list=_parse_access_list(data.get("access_list", data.get("accessList"))),
        v=None if data.get("v") is None else int(data["v"]),
        r=None if data.get("r") is None else hex_to_int(data["r"], label="transaction.r"),
        s=None if data.get("s") is None else hex_to_int(data["s"], label="transaction.s"),
        chain_id=None if data.get("chain_id", data.get("chainId")) is None else int(data.get("chain_id", data.get("chainId"))),
        tx_type=data.get("tx_type", data.get("type")),
        raw=None if raw is None else hex_to_bytes(str(raw), label="transaction.raw"),
        secret_key=None
        if data.get("secret_key", data.get("private_key")) is None
        else hex_to_int(data.get("secret_key", data.get("private_key")), label="transaction.secret_key"),
        proof_type=_parse_proof_type(data.get("proof_type", data.get("proofType"))),
        proof_data=hex_to_bytes(str(data.get("proof_data", data.get("proofData", "0x"))), label="transaction.proof_data"),
        public_inputs=tuple(
            U256(hex_to_int(item, label="transaction.public_inputs"))
            for item in data.get("public_inputs", data.get("publicInputs", []))
        ),
    )


def _parse_expected_result(data: Mapping[str, object] | None) -> ExpectedResult:
    if data is None:
        return ExpectedResult()
    logs_raw = data.get("logs", [])
    if not isinstance(logs_raw, Sequence):
        raise FixtureLoadingError("expected.logs must be a list")
    return ExpectedResult(
        success=data.get("success"),
        gas_used=None if data.get("gas_used", data.get("gasUsed")) is None else hex_to_int(
            data.get("gas_used", data.get("gasUsed")),
            label="expected.gas_used",
        ),
        output=None if data.get("output") is None else hex_to_bytes(str(data["output"]), label="expected.output"),
        logs=tuple(_parse_log(item, label=f"expected.logs[{index}]") for index, item in enumerate(logs_raw)),
        receipts=_parse_receipts(data.get("receipts") if isinstance(data.get("receipts"), Sequence) else None),
        state_root=None if data.get("state_root", data.get("stateRoot")) is None else normalize_hash(
            str(data.get("state_root", data.get("stateRoot"))),
            label="expected.state_root",
        ),
        receipts_root=None if data.get("receipts_root", data.get("receiptsRoot")) is None else normalize_hash(
            str(data.get("receipts_root", data.get("receiptsRoot"))),
            label="expected.receipts_root",
        ),
        transactions_root=None
        if data.get("transactions_root", data.get("transactionsRoot")) is None
        else normalize_hash(
            str(data.get("transactions_root", data.get("transactionsRoot"))),
            label="expected.transactions_root",
        ),
        logs_bloom=None
        if data.get("logs_bloom", data.get("logsBloom")) is None
        else hex_to_bytes(
            str(data.get("logs_bloom", data.get("logsBloom"))),
            label="expected.logs_bloom",
            expected_length=256,
        ),
        post_state=_parse_accounts(
            data.get("post_state", data.get("postState", data.get("accounts")))
            if isinstance(data.get("post_state", data.get("postState", data.get("accounts"))), (Mapping, Sequence))
            else None
        ),
        error_substring=None if data.get("error_substring", data.get("errorSubstring")) is None else str(
            data.get("error_substring", data.get("errorSubstring"))
        ),
    )


def _parse_case(name: str, data: Mapping[str, object]) -> ExecutionFixtureCase:
    environment_raw = data.get("environment", data.get("env"))
    if not isinstance(environment_raw, Mapping):
        raise FixtureLoadingError(f"fixture {name} requires an environment object")
    pre_state_raw = data.get("pre_state", data.get("pre"))
    transactions_raw = data.get("transactions", data.get("transaction"))
    message_call_raw = data.get("message_call", data.get("messageCall"))
    expected_raw = data.get("expected", data.get("post"))
    if expected_raw is not None and not isinstance(expected_raw, Mapping):
        raise FixtureLoadingError(f"fixture {name}.expected must be an object")

    transactions: tuple[TestTransaction, ...] = ()
    if transactions_raw is not None:
        if isinstance(transactions_raw, Mapping):
            transactions = (_parse_transaction(transactions_raw),)
        elif isinstance(transactions_raw, Sequence):
            transactions = tuple(_parse_transaction(item) for item in transactions_raw if isinstance(item, Mapping))
        else:
            raise FixtureLoadingError(f"fixture {name}.transactions must be an object or a list")

    message_call = None
    if message_call_raw is not None:
        if not isinstance(message_call_raw, Mapping):
            raise FixtureLoadingError(f"fixture {name}.message_call must be an object")
        message_call = _parse_message_call(message_call_raw)

    return ExecutionFixtureCase(
        name=name,
        environment=_parse_environment(environment_raw),
        pre_state=_parse_accounts(pre_state_raw if isinstance(pre_state_raw, (Mapping, Sequence)) else None),
        expected=_parse_expected_result(expected_raw),
        transactions=transactions,
        message_call=message_call,
        fork_name=None if data.get("fork") is None else str(data["fork"]),
        description=None if data.get("description") is None else str(data["description"]),
    )


def load_fixture_file(path: str | Path) -> dict[str, ExecutionFixtureCase]:
    fixture_path = Path(path)
    raw = _load_json(fixture_path)
    if isinstance(raw, Mapping) and "tests" in raw:
        tests = raw["tests"]
        if not isinstance(tests, Mapping):
            raise FixtureLoadingError("fixture.tests must be an object")
        return {str(name): _parse_case(str(name), dict(case)) for name, case in tests.items() if isinstance(case, Mapping)}

    if isinstance(raw, Mapping):
        name = str(raw.get("name", fixture_path.stem))
        return {name: _parse_case(name, raw)}

    raise FixtureLoadingError("fixture file must contain either a single object or a {\"tests\": ...} mapping")
