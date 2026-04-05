from __future__ import annotations

from dataclasses import dataclass, field

from evm import Account, StateDB
from primitives import Address


@dataclass(slots=True)
class StateJournal:
    state: StateDB
    _snapshots: list[dict[Address, Account]] = field(default_factory=list, init=False, repr=False)

    def checkpoint(self) -> int:
        self._snapshots.append(self.state.snapshot())
        return len(self._snapshots) - 1

    def revert(self, checkpoint: int) -> None:
        try:
            snapshot = self._snapshots[checkpoint]
        except IndexError as exc:
            raise ValueError("invalid journal checkpoint") from exc
        self.state.restore(snapshot)
        del self._snapshots[checkpoint:]

    def commit(self, checkpoint: int) -> None:
        if checkpoint < 0 or checkpoint >= len(self._snapshots):
            raise ValueError("invalid journal checkpoint")
        del self._snapshots[checkpoint:]
