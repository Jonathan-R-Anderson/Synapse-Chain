from __future__ import annotations

from dataclasses import dataclass, field

from .exceptions import StackOverflowError, StackUnderflowError
from .utils import MAX_STACK_DEPTH, UINT256_MASK


@dataclass(slots=True)
class Stack:
    _values: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self._values)

    def push(self, value: int) -> None:
        if len(self._values) >= MAX_STACK_DEPTH:
            raise StackOverflowError("stack depth exceeds 1024 items")
        self._values.append(value & UINT256_MASK)

    def pop(self) -> int:
        if not self._values:
            raise StackUnderflowError("stack underflow")
        return self._values.pop()

    def peek(self, depth: int = 1) -> int:
        if depth <= 0 or depth > len(self._values):
            raise StackUnderflowError("stack underflow")
        return self._values[-depth]

    def dup(self, depth: int) -> None:
        self.push(self.peek(depth))

    def swap(self, depth: int) -> None:
        if depth <= 0 or len(self._values) <= depth:
            raise StackUnderflowError("stack underflow")
        top_index = -1
        swap_index = -1 - depth
        self._values[top_index], self._values[swap_index] = self._values[swap_index], self._values[top_index]

    def to_list(self) -> list[int]:
        return list(self._values)
