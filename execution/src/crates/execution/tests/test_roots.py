from __future__ import annotations

import unittest

from execution_test_helpers import addr
from execution_tests import AccountFixture, compute_code_hash, compute_state_root_from_accounts, compute_storage_root
from state import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT


class RootUtilityTests(unittest.TestCase):
    def test_state_root_is_deterministic_across_account_order(self) -> None:
        left = (
            AccountFixture(address=addr("0x1000000000000000000000000000000000000001"), nonce=1, balance=10),
            AccountFixture(address=addr("0x1000000000000000000000000000000000000002"), nonce=2, balance=20),
        )
        right = tuple(reversed(left))
        self.assertEqual(compute_state_root_from_accounts(left), compute_state_root_from_accounts(right))

    def test_compute_storage_root_ignores_zero_values(self) -> None:
        self.assertEqual(compute_storage_root({}), EMPTY_TRIE_ROOT)
        self.assertEqual(compute_storage_root({0: 0, 1: 0}), EMPTY_TRIE_ROOT)

    def test_empty_code_hash_matches_state_constant(self) -> None:
        self.assertEqual(compute_code_hash(b""), EMPTY_CODE_HASH)


if __name__ == "__main__":
    unittest.main()
