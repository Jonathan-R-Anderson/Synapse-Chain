from __future__ import annotations

from dataclasses import dataclass, field

from .context import ExecutionContext
from .gas import GasMeter
from .logs import LogEntry
from .memory import Memory
from .stack import Stack
from .utils import collect_jumpdest_offsets


@dataclass(slots=True)
class CallFrame:
    context: ExecutionContext
    stack: Stack = field(default_factory=Stack)
    memory: Memory = field(default_factory=Memory)
    pc: int = 0
    last_return_data: bytes = b""
    logs: list[LogEntry] = field(default_factory=list)
    gas_meter: GasMeter = field(init=False)
    jumpdests: frozenset[int] = field(init=False)

    def __post_init__(self) -> None:
        self.gas_meter = GasMeter(self.context.gas)
        self.jumpdests = collect_jumpdest_offsets(self.context.code)
