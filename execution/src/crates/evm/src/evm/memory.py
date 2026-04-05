from __future__ import annotations

from dataclasses import dataclass, field

from .gas import memory_expansion_cost
from .utils import int_to_bytes32, words_for_size


@dataclass(slots=True)
class Memory:
    _data: bytearray = field(default_factory=bytearray)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def size_words(self) -> int:
        return words_for_size(self.size)

    def expansion_cost(self, offset: int, size: int) -> int:
        if offset < 0 or size < 0:
            raise ValueError("offset and size must be non-negative")
        if size == 0:
            return 0
        new_words = words_for_size(offset + size)
        return memory_expansion_cost(self.size_words, new_words)

    def expand_for_access(self, offset: int, size: int) -> int:
        cost = self.expansion_cost(offset, size)
        if size == 0:
            return cost
        end = offset + size
        if end > len(self._data):
            self._data.extend(bytes(end - len(self._data)))
        return cost

    def read(self, offset: int, size: int) -> bytes:
        self.expand_for_access(offset, size)
        if size == 0:
            return b""
        return bytes(self._data[offset : offset + size])

    def write(self, offset: int, data: bytes | bytearray | memoryview) -> None:
        raw = bytes(data)
        self.expand_for_access(offset, len(raw))
        self._data[offset : offset + len(raw)] = raw

    def read_word(self, offset: int) -> int:
        return int.from_bytes(self.read(offset, 32), byteorder="big", signed=False)

    def write_word(self, offset: int, value: int) -> None:
        self.write(offset, int_to_bytes32(value))
