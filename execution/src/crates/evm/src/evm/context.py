from __future__ import annotations

from dataclasses import dataclass, field

from primitives import Address

from .logs import LogEntry
from .utils import ZERO_ADDRESS


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    address: Address
    caller: Address = ZERO_ADDRESS
    origin: Address = ZERO_ADDRESS
    value: int = 0
    calldata: bytes = b""
    code: bytes = b""
    gas: int = 0
    static: bool = False
    depth: int = 0
    gas_price: int = 0
    chain_id: int = 1
    code_address: Address | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", int(self.value))
        object.__setattr__(self, "calldata", bytes(self.calldata))
        object.__setattr__(self, "code", bytes(self.code))
        object.__setattr__(self, "gas", int(self.gas))
        object.__setattr__(self, "gas_price", int(self.gas_price))
        object.__setattr__(self, "code_address", self.address if self.code_address is None else self.code_address)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    success: bool
    gas_remaining: int
    gas_refund: int = 0
    output: bytes = b""
    logs: tuple[LogEntry, ...] = field(default_factory=tuple)
    error: Exception | None = None
    reverted: bool = False
    created_address: Address | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "gas_refund", int(self.gas_refund))
        object.__setattr__(self, "output", bytes(self.output))
        object.__setattr__(self, "logs", tuple(self.logs))
