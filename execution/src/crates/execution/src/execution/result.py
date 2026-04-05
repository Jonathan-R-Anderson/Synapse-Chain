from __future__ import annotations

from dataclasses import dataclass, field

from evm import LogEntry, StateDB
from primitives import Address, Hash

from .block import Block
from .receipt import Receipt
from .transaction import Transaction


@dataclass(frozen=True, slots=True)
class TransactionExecutionResult:
    transaction: Transaction
    sender: Address
    success: bool
    gas_limit: int
    gas_used: int
    gas_refunded: int
    gas_remaining: int
    effective_gas_price: int
    total_fee_paid: int
    coinbase_fee: int
    base_fee_burned: int
    logs: tuple[LogEntry, ...] = field(default_factory=tuple)
    output: bytes = b""
    revert_data: bytes = b""
    contract_address: Address | None = None
    receipt: Receipt | None = None
    error: Exception | None = None
    zk_verified: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "gas_limit", int(self.gas_limit))
        object.__setattr__(self, "gas_used", int(self.gas_used))
        object.__setattr__(self, "gas_refunded", int(self.gas_refunded))
        object.__setattr__(self, "gas_remaining", int(self.gas_remaining))
        object.__setattr__(self, "effective_gas_price", int(self.effective_gas_price))
        object.__setattr__(self, "total_fee_paid", int(self.total_fee_paid))
        object.__setattr__(self, "coinbase_fee", int(self.coinbase_fee))
        object.__setattr__(self, "base_fee_burned", int(self.base_fee_burned))
        object.__setattr__(self, "logs", tuple(self.logs))
        object.__setattr__(self, "output", bytes(self.output))
        object.__setattr__(self, "revert_data", bytes(self.revert_data))


@dataclass(frozen=True, slots=True)
class ExecutionPayload:
    receipts: tuple[Receipt, ...]
    gas_used: int
    state: StateDB | None = None
    state_root: Hash | None = None
    logs_bloom: bytes = b""
    transaction_results: tuple[TransactionExecutionResult, ...] = field(default_factory=tuple)
    pre_state_root: Hash | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts))
        object.__setattr__(self, "gas_used", int(self.gas_used))
        object.__setattr__(self, "transaction_results", tuple(self.transaction_results))
        if self.state_root is not None and not isinstance(self.state_root, Hash):
            object.__setattr__(self, "state_root", Hash(bytes(self.state_root)))
        if self.pre_state_root is not None and not isinstance(self.pre_state_root, Hash):
            object.__setattr__(self, "pre_state_root", Hash(bytes(self.pre_state_root)))
        bloom = bytes(self.logs_bloom) if self.logs_bloom else b""
        if bloom and len(bloom) != 256:
            raise ValueError("execution payload logs_bloom must be 256 bytes")
        object.__setattr__(self, "logs_bloom", bloom)

    def resolved_state_root(self) -> Hash:
        if self.state_root is not None:
            return self.state_root
        if self.state is None:
            raise ValueError("execution payload requires either a state object or a precomputed state_root")
        from .trie import compute_state_root

        return compute_state_root(self.state)

    def resolved_logs_bloom(self) -> bytes:
        if self.logs_bloom:
            return self.logs_bloom
        from .trie import compute_logs_bloom

        return compute_logs_bloom(self.receipts)


@dataclass(frozen=True, slots=True)
class BlockExecutionResult:
    block: Block
    state: StateDB
    gas_used: int
    receipts: tuple[Receipt, ...]
    transaction_results: tuple[TransactionExecutionResult, ...]
    logs: tuple[LogEntry, ...] = field(default_factory=tuple)
    coinbase_fees: int = 0
    base_fee_burned: int = 0
    state_root: Hash | None = None
    transactions_root: Hash | None = None
    receipts_root: Hash | None = None
    logs_bloom: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "gas_used", int(self.gas_used))
        object.__setattr__(self, "receipts", tuple(self.receipts))
        object.__setattr__(self, "transaction_results", tuple(self.transaction_results))
        object.__setattr__(self, "logs", tuple(self.logs))
        object.__setattr__(self, "coinbase_fees", int(self.coinbase_fees))
        object.__setattr__(self, "base_fee_burned", int(self.base_fee_burned))
        if self.state_root is not None and not isinstance(self.state_root, Hash):
            object.__setattr__(self, "state_root", Hash(bytes(self.state_root)))
        if self.transactions_root is not None and not isinstance(self.transactions_root, Hash):
            object.__setattr__(self, "transactions_root", Hash(bytes(self.transactions_root)))
        if self.receipts_root is not None and not isinstance(self.receipts_root, Hash):
            object.__setattr__(self, "receipts_root", Hash(bytes(self.receipts_root)))
        bloom = bytes(self.logs_bloom) if self.logs_bloom else b""
        if bloom and len(bloom) != 256:
            raise ValueError("block execution result logs_bloom must be 256 bytes")
        object.__setattr__(self, "logs_bloom", bloom)

    def to_execution_payload(self, *, pre_state_root: Hash | None = None) -> ExecutionPayload:
        return ExecutionPayload(
            receipts=self.receipts,
            gas_used=self.gas_used,
            state=self.state,
            state_root=self.state_root,
            logs_bloom=self.logs_bloom,
            transaction_results=self.transaction_results,
            pre_state_root=pre_state_root,
        )
