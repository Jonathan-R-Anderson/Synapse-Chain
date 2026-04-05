from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class KeyValueStore(Protocol):
    def get(self, key: bytes) -> bytes | None:
        ...

    def set(self, key: bytes, value: bytes) -> None:
        ...

    def delete(self, key: bytes) -> None:
        ...

    def clear(self) -> None:
        ...

    def clone(self) -> "KeyValueStore":
        ...


@dataclass(slots=True)
class MemoryKeyValueStore:
    _entries: dict[bytes, bytes] = field(default_factory=dict)

    def get(self, key: bytes) -> bytes | None:
        value = self._entries.get(bytes(key))
        return None if value is None else bytes(value)

    def set(self, key: bytes, value: bytes) -> None:
        self._entries[bytes(key)] = bytes(value)

    def delete(self, key: bytes) -> None:
        self._entries.pop(bytes(key), None)

    def clear(self) -> None:
        self._entries.clear()

    def items(self) -> tuple[tuple[bytes, bytes], ...]:
        return tuple((bytes(key), bytes(value)) for key, value in self._entries.items())

    def clone(self) -> "MemoryKeyValueStore":
        return MemoryKeyValueStore(dict(self._entries))
