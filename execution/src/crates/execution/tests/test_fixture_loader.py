from __future__ import annotations

import unittest

from execution_test_helpers import ROOT
from execution_tests.loader import FixtureLoadingError, hex_to_int, load_fixture_file, normalize_address


class FixtureLoaderTests(unittest.TestCase):
    def test_loads_simple_execution_fixture(self) -> None:
        cases = load_fixture_file(ROOT / "tests" / "fixtures" / "simple_execution_fixture.json")
        case = cases["legacy_zero_fee_transfer"]
        self.assertEqual(case.name, "legacy_zero_fee_transfer")
        self.assertEqual(case.environment.number, 0x00BADC0D)
        self.assertEqual(len(case.transactions), 1)
        self.assertEqual(case.transactions[0].gas_limit, 21_000)
        self.assertEqual(case.expected.gas_used, 21_000)
        self.assertEqual(len(case.expected.post_state), 2)

    def test_hex_to_int_rejects_ambiguous_leading_zeroes(self) -> None:
        with self.assertRaises(FixtureLoadingError):
            hex_to_int("0x00", label="ambiguous")

    def test_normalize_address_rejects_wrong_length(self) -> None:
        with self.assertRaises(FixtureLoadingError):
            normalize_address("0x1234", label="bad_address")


if __name__ == "__main__":
    unittest.main()
