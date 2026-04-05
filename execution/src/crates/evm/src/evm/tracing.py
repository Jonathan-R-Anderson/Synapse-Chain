from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from primitives import Address

from .context import ExecutionContext, ExecutionResult


@dataclass(frozen=True, slots=True)
class ExecutionTraceRow:
    depth: int
    pc: int
    opcode: int
    opcode_name: str
    gas_before: int
    gas_after: int
    stack_before: tuple[int, ...]
    stack_after: tuple[int, ...]
    memory_before: bytes | None = None
    memory_after: bytes | None = None
    storage_reads: tuple[tuple[int, int], ...] = ()
    storage_writes: tuple[tuple[int, int], ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth,
            "pc": self.pc,
            "opcode": hex(self.opcode),
            "opcode_name": self.opcode_name,
            "gas_before": self.gas_before,
            "gas_after": self.gas_after,
            "stack_before": [hex(value) for value in self.stack_before],
            "stack_after": [hex(value) for value in self.stack_after],
            "memory_before": None if self.memory_before is None else "0x" + self.memory_before.hex(),
            "memory_after": None if self.memory_after is None else "0x" + self.memory_after.hex(),
            "storage_reads": [[hex(key), hex(value)] for key, value in self.storage_reads],
            "storage_writes": [[hex(key), hex(value)] for key, value in self.storage_writes],
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class FrameTraceEvent:
    event: str
    depth: int
    address: Address
    caller: Address
    code_address: Address
    gas: int
    success: bool | None = None
    gas_remaining: int | None = None
    output: bytes | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event,
            "depth": self.depth,
            "address": self.address.to_hex(),
            "caller": self.caller.to_hex(),
            "code_address": self.code_address.to_hex(),
            "gas": self.gas,
            "success": self.success,
            "gas_remaining": self.gas_remaining,
            "output": None if self.output is None else "0x" + self.output.hex(),
        }


class TraceSink(Protocol):
    capture_memory_snapshots: bool

    def on_step(self, row: ExecutionTraceRow) -> None:
        ...

    def on_enter_frame(self, event: FrameTraceEvent) -> None:
        ...

    def on_exit_frame(self, event: FrameTraceEvent) -> None:
        ...


@dataclass(slots=True)
class TraceCaptureSink:
    capture_memory_snapshots: bool = False
    rows: list[ExecutionTraceRow] = field(default_factory=list)
    frame_events: list[FrameTraceEvent] = field(default_factory=list)

    def on_step(self, row: ExecutionTraceRow) -> None:
        self.rows.append(row)

    def on_enter_frame(self, event: FrameTraceEvent) -> None:
        self.frame_events.append(event)

    def on_exit_frame(self, event: FrameTraceEvent) -> None:
        self.frame_events.append(event)

    def to_dict(self) -> dict[str, object]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "frame_events": [event.to_dict() for event in self.frame_events],
        }
