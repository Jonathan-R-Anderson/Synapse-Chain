from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChainCandidate:
    header_hash: str
    number: int
    total_score: int


class ForkChoice(ABC):
    """Abstract canonical-chain selection policy."""

    @abstractmethod
    def prefers(self, candidate: ChainCandidate, current: ChainCandidate | None) -> bool:
        ...


class HeaviestChainForkChoice(ForkChoice):
    """Prefer the highest accumulated score, then longest height, then deterministic hash."""

    def prefers(self, candidate: ChainCandidate, current: ChainCandidate | None) -> bool:
        if current is None:
            return True
        if candidate.total_score != current.total_score:
            return candidate.total_score > current.total_score
        if candidate.number != current.number:
            return candidate.number > current.number
        return candidate.header_hash < current.header_hash
