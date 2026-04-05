from __future__ import annotations

from dataclasses import dataclass, field

from primitives import U256


def coerce_u256(value: U256 | int) -> U256:
    if isinstance(value, U256):
        return value
    if isinstance(value, int):
        return U256(value)
    raise TypeError("expected a U256-compatible integer")


@dataclass(slots=True)
class StorageMap:
    _slots: dict[U256, U256] = field(default_factory=dict)

    def get(self, key: U256 | int) -> U256:
        return self._slots.get(coerce_u256(key), U256.zero())

    def set(self, key: U256 | int, value: U256 | int) -> None:
        normalized_key = coerce_u256(key)
        normalized_value = coerce_u256(value)
        if normalized_value.is_zero():
            self._slots.pop(normalized_key, None)
            return
        self._slots[normalized_key] = normalized_value

    def delete(self, key: U256 | int) -> None:
        self._slots.pop(coerce_u256(key), None)

    def items(self) -> tuple[tuple[U256, U256], ...]:
        return tuple(sorted(self._slots.items(), key=lambda entry: int(entry[0])))

    def clone(self) -> "StorageMap":
        return StorageMap(dict(self._slots))

    def is_empty(self) -> bool:
        return not self._slots
