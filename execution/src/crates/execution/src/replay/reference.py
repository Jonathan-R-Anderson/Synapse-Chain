from __future__ import annotations

from dataclasses import dataclass

from evm import LogEntry
from execution import Block, BlockHeader
from execution_tests.models import AccountFixture, ExpectedResult


@dataclass(frozen=True, slots=True)
class ReferenceBlockBundle:
    name: str
    block: Block
    pre_state: tuple[AccountFixture, ...]
    expected: ExpectedResult
    parent_header: BlockHeader | None = None
    chain_id: int = 1
    fork_name: str | None = None
    transaction_bodies_complete: bool = True
    transaction_count: int | None = None
    description: str | None = None
