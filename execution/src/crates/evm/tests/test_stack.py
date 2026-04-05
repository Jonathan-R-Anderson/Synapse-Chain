from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401
from evm import Stack, StackOverflowError, StackUnderflowError


class StackTests(unittest.TestCase):
    def test_push_masks_to_uint256(self) -> None:
        stack = Stack()
        stack.push((1 << 256) + 5)
        self.assertEqual(stack.pop(), 5)

    def test_underflow_raises(self) -> None:
        stack = Stack()
        with self.assertRaises(StackUnderflowError):
            stack.pop()

    def test_overflow_raises(self) -> None:
        stack = Stack()
        for _ in range(1024):
            stack.push(1)
        with self.assertRaises(StackOverflowError):
            stack.push(1)

    def test_dup_and_swap(self) -> None:
        stack = Stack()
        stack.push(1)
        stack.push(2)
        stack.push(3)
        stack.dup(2)
        self.assertEqual(stack.to_list(), [1, 2, 3, 2])
        stack.swap(1)
        self.assertEqual(stack.to_list(), [1, 2, 2, 3])


if __name__ == "__main__":
    unittest.main()
