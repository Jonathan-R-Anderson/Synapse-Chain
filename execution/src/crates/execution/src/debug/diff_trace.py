from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from evm import ExecutionTraceRow


@dataclass(frozen=True, slots=True)
class TraceDiffMismatch:
    index: int
    field: str
    expected: object
    actual: object
    detail: str


def diff_trace_rows(expected: Sequence[ExecutionTraceRow], actual: Sequence[ExecutionTraceRow]) -> list[TraceDiffMismatch]:
    mismatches: list[TraceDiffMismatch] = []
    if len(expected) != len(actual):
        mismatches.append(
            TraceDiffMismatch(
                index=min(len(expected), len(actual)),
                field="length",
                expected=len(expected),
                actual=len(actual),
                detail="trace length mismatch",
            )
        )
    for index, (left, right) in enumerate(zip(expected, actual)):
        for field in ("depth", "pc", "opcode", "gas_before", "gas_after", "stack_after", "storage_writes", "error"):
            left_value = getattr(left, field)
            right_value = getattr(right, field)
            if left_value != right_value:
                mismatches.append(
                    TraceDiffMismatch(
                        index=index,
                        field=field,
                        expected=left_value,
                        actual=right_value,
                        detail=f"trace field {field} diverged",
                    )
                )
                break
    return mismatches
