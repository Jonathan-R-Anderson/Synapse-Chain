from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401
from evm import OutOfGasError
from evm.gas import GasMeter, copy_cost, memory_cost, memory_expansion_cost


class GasTests(unittest.TestCase):
    def test_gas_meter_charges_and_errors(self) -> None:
        meter = GasMeter(10)
        meter.charge(4)
        self.assertEqual(meter.remaining, 6)
        with self.assertRaises(OutOfGasError):
            meter.charge(7)

    def test_memory_cost_grows_with_size(self) -> None:
        self.assertEqual(memory_cost(0), 0)
        self.assertGreater(memory_cost(2), memory_cost(1))
        self.assertEqual(memory_expansion_cost(1, 1), 0)
        self.assertGreater(memory_expansion_cost(1, 2), 0)

    def test_copy_cost_is_word_based(self) -> None:
        self.assertEqual(copy_cost(0), 0)
        self.assertEqual(copy_cost(1), 3)
        self.assertEqual(copy_cost(33), 6)


if __name__ == "__main__":
    unittest.main()
