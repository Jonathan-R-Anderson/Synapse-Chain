from __future__ import annotations

from dataclasses import dataclass

from primitives import Address, U256


@dataclass(frozen=True, slots=True)
class LogEntry:
    address: Address
    topics: tuple[U256, ...]
    data: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "topics", tuple(topic if isinstance(topic, U256) else U256(topic) for topic in self.topics))
        object.__setattr__(self, "data", bytes(self.data))
