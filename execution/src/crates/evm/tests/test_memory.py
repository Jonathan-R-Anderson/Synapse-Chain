from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401
from evm import Memory


class MemoryTests(unittest.TestCase):
    def test_zero_initialized_reads_expand_memory(self) -> None:
        memory = Memory()
        self.assertEqual(memory.read(0, 4), b"\x00" * 4)
        self.assertEqual(memory.size, 4)

    def test_write_and_read_word(self) -> None:
        memory = Memory()
        memory.write_word(0, 0x2A)
        self.assertEqual(memory.read_word(0), 0x2A)
        self.assertEqual(memory.read(0, 32)[-1], 0x2A)

    def test_expansion_cost_increases_quadratically(self) -> None:
        memory = Memory()
        first_cost = memory.expansion_cost(0, 32)
        memory.expand_for_access(0, 32)
        second_cost = memory.expansion_cost(32, 32)
        self.assertGreaterEqual(first_cost, 3)
        self.assertGreater(second_cost, 0)


if __name__ == "__main__":
    unittest.main()
