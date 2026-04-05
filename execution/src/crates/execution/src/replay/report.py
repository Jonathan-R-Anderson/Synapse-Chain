from __future__ import annotations

from dataclasses import dataclass

from execution import BlockExecutionResult
from execution_tests.models import ValidationReport


@dataclass(frozen=True, slots=True)
class BlockReplayOutcome:
    report: ValidationReport
    block_result: BlockExecutionResult | None = None
    error: Exception | None = None

    @property
    def passed(self) -> bool:
        return self.report.passed and self.error is None
