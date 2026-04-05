from __future__ import annotations

from dataclasses import dataclass, field

from .gas import GAS_SLOAD, GAS_SSTORE_CLEAR_REFUND, GAS_SSTORE_RESET, GAS_SSTORE_SET
from .utils import UINT256_MASK


@dataclass(slots=True)
class StorageSlot:
    original: int = 0
    current: int = 0

    def clone(self) -> "StorageSlot":
        return StorageSlot(original=self.original, current=self.current)


@dataclass(slots=True)
class Storage:
    _slots: dict[int, StorageSlot] = field(default_factory=dict)

    def _normalize(self, value: int) -> int:
        return value & UINT256_MASK

    def get(self, key: int) -> int:
        return self._slots.get(self._normalize(key), StorageSlot()).current

    def original(self, key: int) -> int:
        return self._slots.get(self._normalize(key), StorageSlot()).original

    def estimate_sstore_cost(self, key: int, new_value: int) -> tuple[int, int]:
        normalized_key = self._normalize(key)
        normalized_new_value = self._normalize(new_value)
        slot = self._slots.get(normalized_key)
        if slot is None:
            current = 0
            original = 0
        else:
            current = slot.current
            original = slot.original

        if normalized_new_value == current:
            return GAS_SLOAD, 0

        if original == current:
            if original == 0:
                return GAS_SSTORE_SET, 0
            if normalized_new_value == 0:
                return GAS_SSTORE_RESET, GAS_SSTORE_CLEAR_REFUND
            return GAS_SSTORE_RESET, 0

        refund = 0
        if original != 0:
            if current == 0:
                refund -= GAS_SSTORE_CLEAR_REFUND
            if normalized_new_value == 0:
                refund += GAS_SSTORE_CLEAR_REFUND
        if original == normalized_new_value:
            if original == 0:
                refund += GAS_SSTORE_SET - GAS_SLOAD
            else:
                refund += GAS_SSTORE_RESET - GAS_SLOAD
        return GAS_SLOAD, refund

    def set(self, key: int, value: int) -> tuple[int, int]:
        normalized_key = self._normalize(key)
        normalized_value = self._normalize(value)
        cost, refund = self.estimate_sstore_cost(normalized_key, normalized_value)
        slot = self._slots.get(normalized_key)
        if slot is None:
            slot = StorageSlot(original=0, current=0)
            self._slots[normalized_key] = slot
        slot.current = normalized_value
        return cost, refund

    def items(self, include_zero: bool = False) -> tuple[tuple[int, int], ...]:
        items = [
            (key, slot.current)
            for key, slot in self._slots.items()
            if include_zero or slot.current != 0
        ]
        return tuple(sorted(items, key=lambda item: item[0]))

    def commit(self) -> None:
        committed: dict[int, StorageSlot] = {}
        for key, slot in self._slots.items():
            if slot.current == 0:
                continue
            committed[key] = StorageSlot(original=slot.current, current=slot.current)
        self._slots = committed

    def clone(self) -> "Storage":
        return Storage({key: slot.clone() for key, slot in self._slots.items()})
