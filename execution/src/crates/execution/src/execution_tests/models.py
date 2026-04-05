from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from evm import LogEntry, StateDB
from execution import Receipt, decode_transaction_payload
from primitives import Address, Hash, U256
from transactions import AccessListEntry, EIP1559Transaction, LegacyTransaction, Transaction, ZKTransaction
from zk import ProofType, ZKProof


def _coerce_address(value: Address | bytes | bytearray | memoryview) -> Address:
    if isinstance(value, Address):
        return value
    raw = bytes(value)
    if len(raw) != Address.SIZE:
        raise ValueError("addresses must be 20 bytes")
    return Address(raw)


def _coerce_hash(value: Hash | bytes | bytearray | memoryview) -> Hash:
    if isinstance(value, Hash):
        return value
    raw = bytes(value)
    if len(raw) != Hash.SIZE:
        raise ValueError("hash values must be 32 bytes")
    return Hash(raw)


def _coerce_u256(value: U256 | int) -> U256:
    return value if isinstance(value, U256) else U256(int(value))


def _coerce_storage(
    storage: Mapping[U256 | int, U256 | int] | Sequence[tuple[U256 | int, U256 | int]] | None,
) -> tuple[tuple[U256, U256], ...]:
    if storage is None:
        return ()
    items = storage.items() if isinstance(storage, Mapping) else storage
    normalized = [(_coerce_u256(key), _coerce_u256(value)) for key, value in items]
    return tuple(sorted(normalized, key=lambda item: int(item[0])))


def _coerce_access_list(
    access_list: Sequence[AccessListEntry | tuple[Address | bytes, Sequence[U256 | int]]] | None,
) -> tuple[AccessListEntry, ...]:
    if access_list is None:
        return ()
    entries: list[AccessListEntry] = []
    for entry in access_list:
        if isinstance(entry, AccessListEntry):
            entries.append(entry)
            continue
        address, storage_keys = entry
        entries.append(
            AccessListEntry(
                address=_coerce_address(address),
                storage_keys=tuple(_coerce_u256(key) for key in storage_keys),
            )
        )
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class AccountFixture:
    address: Address
    nonce: int = 0
    balance: int = 0
    code: bytes = b""
    storage: tuple[tuple[U256, U256], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", _coerce_address(self.address))
        object.__setattr__(self, "nonce", int(self.nonce))
        object.__setattr__(self, "balance", int(self.balance))
        object.__setattr__(self, "code", bytes(self.code))
        object.__setattr__(self, "storage", _coerce_storage(self.storage))
        if self.nonce < 0:
            raise ValueError("account nonce must be non-negative")
        if self.balance < 0:
            raise ValueError("account balance must be non-negative")

    def storage_dict(self) -> dict[int, int]:
        return {int(key): int(value) for key, value in self.storage}

    def to_dict(self) -> dict[str, object]:
        return {
            "address": self.address.to_hex(),
            "nonce": hex(self.nonce),
            "balance": hex(self.balance),
            "code": "0x" + self.code.hex(),
            "storage": {key.to_hex(): value.to_hex() for key, value in self.storage},
        }


@dataclass(frozen=True, slots=True)
class TestEnvironment:
    coinbase: Address
    gas_limit: int
    number: int
    timestamp: int
    difficulty: int | None = None
    prev_randao: Hash | None = None
    base_fee: int | None = None
    chain_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "coinbase", _coerce_address(self.coinbase))
        object.__setattr__(self, "gas_limit", int(self.gas_limit))
        object.__setattr__(self, "number", int(self.number))
        object.__setattr__(self, "timestamp", int(self.timestamp))
        if self.difficulty is not None:
            object.__setattr__(self, "difficulty", int(self.difficulty))
        if self.prev_randao is not None:
            object.__setattr__(self, "prev_randao", _coerce_hash(self.prev_randao))
        if self.base_fee is not None:
            object.__setattr__(self, "base_fee", int(self.base_fee))
        if self.chain_id is not None:
            object.__setattr__(self, "chain_id", int(self.chain_id))
        if self.gas_limit < 0 or self.number < 0 or self.timestamp < 0:
            raise ValueError("environment numeric fields must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "coinbase": self.coinbase.to_hex(),
            "gas_limit": hex(self.gas_limit),
            "number": hex(self.number),
            "timestamp": hex(self.timestamp),
            "difficulty": None if self.difficulty is None else hex(self.difficulty),
            "prev_randao": None if self.prev_randao is None else self.prev_randao.to_hex(),
            "base_fee": None if self.base_fee is None else hex(self.base_fee),
            "chain_id": self.chain_id,
        }


@dataclass(frozen=True, slots=True)
class MessageCall:
    caller: Address
    to: Address
    value: int = 0
    gas: int = 1_000_000
    data: bytes = b""
    static: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "caller", _coerce_address(self.caller))
        object.__setattr__(self, "to", _coerce_address(self.to))
        object.__setattr__(self, "value", int(self.value))
        object.__setattr__(self, "gas", int(self.gas))
        object.__setattr__(self, "data", bytes(self.data))
        if self.value < 0 or self.gas < 0:
            raise ValueError("message call gas and value must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "caller": self.caller.to_hex(),
            "to": self.to.to_hex(),
            "value": hex(self.value),
            "gas": hex(self.gas),
            "data": "0x" + self.data.hex(),
            "static": self.static,
        }


@dataclass(frozen=True, slots=True)
class TestTransaction:
    sender: Address | None = None
    to: Address | None = None
    gas_limit: int = 0
    gas_price: int | None = None
    max_fee_per_gas: int | None = None
    max_priority_fee_per_gas: int | None = None
    value: int = 0
    data: bytes = b""
    nonce: int = 0
    access_list: tuple[AccessListEntry, ...] = ()
    v: int | None = None
    r: int | None = None
    s: int | None = None
    chain_id: int | None = None
    tx_type: str | int | None = None
    raw: bytes | None = None
    secret_key: int | bytes | None = None
    proof_type: ProofType | int | None = None
    proof_data: bytes = b""
    public_inputs: tuple[U256, ...] = ()

    def __post_init__(self) -> None:
        if self.sender is not None:
            object.__setattr__(self, "sender", _coerce_address(self.sender))
        if self.to is not None:
            object.__setattr__(self, "to", _coerce_address(self.to))
        object.__setattr__(self, "gas_limit", int(self.gas_limit))
        object.__setattr__(self, "value", int(self.value))
        object.__setattr__(self, "data", bytes(self.data))
        object.__setattr__(self, "nonce", int(self.nonce))
        object.__setattr__(self, "access_list", _coerce_access_list(self.access_list))
        if self.gas_price is not None:
            object.__setattr__(self, "gas_price", int(self.gas_price))
        if self.max_fee_per_gas is not None:
            object.__setattr__(self, "max_fee_per_gas", int(self.max_fee_per_gas))
        if self.max_priority_fee_per_gas is not None:
            object.__setattr__(self, "max_priority_fee_per_gas", int(self.max_priority_fee_per_gas))
        if self.v is not None:
            object.__setattr__(self, "v", int(self.v))
        if self.r is not None:
            object.__setattr__(self, "r", int(self.r))
        if self.s is not None:
            object.__setattr__(self, "s", int(self.s))
        if self.chain_id is not None:
            object.__setattr__(self, "chain_id", int(self.chain_id))
        if self.raw is not None:
            object.__setattr__(self, "raw", bytes(self.raw))
        if self.proof_type is not None and not isinstance(self.proof_type, ProofType):
            object.__setattr__(self, "proof_type", ProofType(int(self.proof_type)))
        object.__setattr__(self, "proof_data", bytes(self.proof_data))
        object.__setattr__(self, "public_inputs", tuple(_coerce_u256(value) for value in self.public_inputs))
        if self.gas_limit < 0 or self.value < 0 or self.nonce < 0:
            raise ValueError("transaction numeric fields must be non-negative")

    def inferred_type(self) -> str:
        if self.tx_type is None:
            if self.proof_type is not None:
                return "zk"
            if self.max_fee_per_gas is not None or self.max_priority_fee_per_gas is not None:
                return "eip1559"
            return "legacy"
        if isinstance(self.tx_type, int):
            if self.tx_type in {2}:
                return "eip1559"
            return "zk" if self.tx_type not in {0, 1, 2} else "legacy"
        lowered = self.tx_type.lower()
        if lowered in {"0x2", "2", "eip1559", "dynamic_fee", "typed"}:
            return "eip1559"
        if lowered in {"zk", "0x7e"}:
            return "zk"
        return "legacy"

    def to_transaction(self, *, default_chain_id: int) -> Transaction:
        if self.raw is not None:
            return decode_transaction_payload(self.raw)

        tx_kind = self.inferred_type()
        chain_id = default_chain_id if self.chain_id is None else self.chain_id
        if tx_kind == "legacy":
            transaction: Transaction = LegacyTransaction(
                nonce=self.nonce,
                gas_price=0 if self.gas_price is None else self.gas_price,
                gas_limit=self.gas_limit,
                to=self.to,
                value=self.value,
                data=self.data,
                chain_id=chain_id,
            )
        else:
            base_tx = EIP1559Transaction(
                chain_id=chain_id,
                nonce=self.nonce,
                max_priority_fee_per_gas=0 if self.max_priority_fee_per_gas is None else self.max_priority_fee_per_gas,
                max_fee_per_gas=0 if self.max_fee_per_gas is None else self.max_fee_per_gas,
                gas_limit=self.gas_limit,
                to=self.to,
                value=self.value,
                data=self.data,
                access_list=self.access_list,
            )
            if tx_kind == "zk":
                proof = ZKProof(
                    proof_type=ProofType.GROTH16 if self.proof_type is None else self.proof_type,
                    data=self.proof_data,
                )
                transaction = ZKTransaction(base_tx=base_tx, proof=proof, public_inputs=self.public_inputs)
            else:
                transaction = base_tx

        if self.v is not None and self.r is not None and self.s is not None:
            return transaction.with_signature(self.v, self.r, self.s)  # type: ignore[return-value]
        if self.secret_key is not None:
            return transaction.sign(self.secret_key)  # type: ignore[return-value]
        raise ValueError("fixture transaction must include either raw bytes, signature values, or a secret_key")

    def to_dict(self) -> dict[str, object]:
        return {
            "sender": None if self.sender is None else self.sender.to_hex(),
            "to": None if self.to is None else self.to.to_hex(),
            "gas_limit": hex(self.gas_limit),
            "gas_price": None if self.gas_price is None else hex(self.gas_price),
            "max_fee_per_gas": None if self.max_fee_per_gas is None else hex(self.max_fee_per_gas),
            "max_priority_fee_per_gas": None
            if self.max_priority_fee_per_gas is None
            else hex(self.max_priority_fee_per_gas),
            "value": hex(self.value),
            "data": "0x" + self.data.hex(),
            "nonce": hex(self.nonce),
            "access_list": [
                {"address": entry.address.to_hex(), "storage_keys": [key.to_hex() for key in entry.storage_keys]}
                for entry in self.access_list
            ],
            "v": self.v,
            "r": None if self.r is None else hex(self.r),
            "s": None if self.s is None else hex(self.s),
            "chain_id": self.chain_id,
            "tx_type": self.tx_type,
            "raw": None if self.raw is None else "0x" + self.raw.hex(),
            "proof_type": None if self.proof_type is None else int(self.proof_type),
            "proof_data": "0x" + self.proof_data.hex(),
            "public_inputs": [value.to_hex() for value in self.public_inputs],
        }


@dataclass(frozen=True, slots=True)
class ExpectedResult:
    success: bool | None = None
    gas_used: int | None = None
    output: bytes | None = None
    logs: tuple[LogEntry, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    state_root: Hash | None = None
    receipts_root: Hash | None = None
    transactions_root: Hash | None = None
    logs_bloom: bytes | None = None
    post_state: tuple[AccountFixture, ...] = ()
    error_substring: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "logs", tuple(self.logs))
        object.__setattr__(self, "receipts", tuple(self.receipts))
        object.__setattr__(self, "post_state", tuple(self.post_state))
        if self.success is not None:
            object.__setattr__(self, "success", bool(self.success))
        if self.gas_used is not None:
            object.__setattr__(self, "gas_used", int(self.gas_used))
        if self.output is not None:
            object.__setattr__(self, "output", bytes(self.output))
        if self.state_root is not None:
            object.__setattr__(self, "state_root", _coerce_hash(self.state_root))
        if self.receipts_root is not None:
            object.__setattr__(self, "receipts_root", _coerce_hash(self.receipts_root))
        if self.transactions_root is not None:
            object.__setattr__(self, "transactions_root", _coerce_hash(self.transactions_root))
        if self.logs_bloom is not None:
            bloom = bytes(self.logs_bloom)
            if len(bloom) != 256:
                raise ValueError("expected logs_bloom must be 256 bytes")
            object.__setattr__(self, "logs_bloom", bloom)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "gas_used": self.gas_used,
            "output": None if self.output is None else "0x" + self.output.hex(),
            "logs": [
                {
                    "address": log.address.to_hex(),
                    "topics": [topic.to_hex() for topic in log.topics],
                    "data": "0x" + log.data.hex(),
                }
                for log in self.logs
            ],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "state_root": None if self.state_root is None else self.state_root.to_hex(),
            "receipts_root": None if self.receipts_root is None else self.receipts_root.to_hex(),
            "transactions_root": None if self.transactions_root is None else self.transactions_root.to_hex(),
            "logs_bloom": None if self.logs_bloom is None else "0x" + self.logs_bloom.hex(),
            "post_state": {item.address.to_hex(): item.to_dict() for item in self.post_state},
            "error_substring": self.error_substring,
        }


@dataclass(frozen=True, slots=True)
class ExecutionFixtureCase:
    name: str
    environment: TestEnvironment
    pre_state: tuple[AccountFixture, ...]
    expected: ExpectedResult
    transactions: tuple[TestTransaction, ...] = ()
    message_call: MessageCall | None = None
    fork_name: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "pre_state", tuple(self.pre_state))
        object.__setattr__(self, "transactions", tuple(self.transactions))
        if self.message_call is not None and self.transactions:
            raise ValueError("fixtures cannot declare both message_call and transactions")
        if self.message_call is None and not self.transactions:
            raise ValueError("fixtures must declare either transactions or message_call")


@dataclass(frozen=True, slots=True)
class ValidationMismatch:
    category: str
    path: str
    expected: object
    actual: object
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    passed: bool
    mismatches: tuple[ValidationMismatch, ...] = ()
    block_number: int | None = None
    tx_index: int | None = None
    test_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mismatches", tuple(self.mismatches))
        if self.block_number is not None:
            object.__setattr__(self, "block_number", int(self.block_number))
        if self.tx_index is not None:
            object.__setattr__(self, "tx_index", int(self.tx_index))

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "block_number": self.block_number,
            "tx_index": self.tx_index,
            "test_name": self.test_name,
            "mismatches": [mismatch.to_dict() for mismatch in self.mismatches],
        }

    def render_text(self) -> str:
        if self.passed:
            target = self.test_name or "validation"
            return f"PASS: {target}"
        lines = [f"FAIL: {self.test_name or 'validation'}"]
        for mismatch in self.mismatches:
            lines.append(f"- [{mismatch.category}] {mismatch.path}: {mismatch.detail}")
            lines.append(f"  expected: {mismatch.expected}")
            lines.append(f"  actual:   {mismatch.actual}")
        return "\n".join(lines)


@dataclass(slots=True)
class ActualExecutionArtifacts:
    success: bool
    gas_used: int
    output: bytes
    logs: tuple[LogEntry, ...]
    receipts: tuple[Receipt, ...]
    state: StateDB
    state_root: Hash
    logs_bloom: bytes
    receipts_root: Hash | None = None
    transactions_root: Hash | None = None
    error: Exception | None = None
    block_number: int | None = None
    traces: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        self.gas_used = int(self.gas_used)
        self.output = bytes(self.output)
        self.logs = tuple(self.logs)
        self.receipts = tuple(self.receipts)
        self.state_root = _coerce_hash(self.state_root)
        self.logs_bloom = bytes(self.logs_bloom)
        self.traces = tuple(self.traces)
        if len(self.logs_bloom) != 256:
            raise ValueError("actual logs_bloom must be 256 bytes")
        if self.receipts_root is not None:
            self.receipts_root = _coerce_hash(self.receipts_root)
        if self.transactions_root is not None:
            self.transactions_root = _coerce_hash(self.transactions_root)

