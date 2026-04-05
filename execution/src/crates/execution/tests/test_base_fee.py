from __future__ import annotations

import unittest

from execution_test_helpers import addr  # noqa: F401 - imports shared path bootstrap

from execution.base_fee import compute_gas_target, compute_next_base_fee


class BaseFeeTests(unittest.TestCase):
    def test_base_fee_is_unchanged_at_target(self) -> None:
        self.assertEqual(compute_next_base_fee(100, 15_000_000, 15_000_000), 100)

    def test_base_fee_increases_above_target(self) -> None:
        self.assertGreater(compute_next_base_fee(100, 20_000_000, 15_000_000), 100)

    def test_base_fee_decreases_below_target(self) -> None:
        self.assertLess(compute_next_base_fee(100, 10_000_000, 15_000_000), 100)

    def test_gas_target_uses_elasticity_multiplier(self) -> None:
        self.assertEqual(compute_gas_target(30_000_000), 15_000_000)


if __name__ == "__main__":
    unittest.main()
