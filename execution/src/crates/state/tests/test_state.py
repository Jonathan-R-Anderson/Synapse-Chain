from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(CRATES / "primitives" / "src"))
sys.path.insert(0, str(CRATES / "crypto" / "src"))
sys.path.insert(0, str(CRATES / "encoding" / "src"))

from crypto import keccak256
from primitives import Address, U256
from state import Account, EMPTY_CODE_HASH, EMPTY_TRIE_ROOT, HashMapStateBackend, MptStateBackend, State


ADDRESS_A = Address.from_hex("0x1000000000000000000000000000000000000001")
ADDRESS_B = Address.from_hex("0x2000000000000000000000000000000000000002")
SLOT_1 = U256(1)
SLOT_2 = U256(2)


def _run_common_sequence(state: State) -> tuple[Account, str]:
    state.create_account(ADDRESS_A)
    state.set_balance(ADDRESS_A, 15)
    state.increment_nonce(ADDRESS_A)
    state.set_code(ADDRESS_A, b"\x60\x00\x60\x00")
    state.set_storage(ADDRESS_A, SLOT_1, 9)
    state.set_storage(ADDRESS_A, SLOT_2, 10)
    root = state.commit()
    account = state.get_account(ADDRESS_A)
    assert account is not None
    return account, root.to_hex()


class BackendBehaviorMixin:
    backend_factory = None

    def make_state(self) -> State:
        assert self.backend_factory is not None
        return State(self.backend_factory())

    def test_create_and_retrieve_account(self) -> None:
        state = self.make_state()
        state.create_account(ADDRESS_A)
        account = state.get_account(ADDRESS_A)
        self.assertIsNotNone(account)
        self.assertEqual(account, Account())
        self.assertTrue(state.account_exists(ADDRESS_A))

    def test_increment_nonce(self) -> None:
        state = self.make_state()
        self.assertEqual(state.increment_nonce(ADDRESS_A), U256.one())
        self.assertEqual(state.get_account(ADDRESS_A).nonce, U256.one())

    def test_change_balance(self) -> None:
        state = self.make_state()
        state.set_balance(ADDRESS_A, 99)
        self.assertEqual(state.get_balance(ADDRESS_A), U256(99))

    def test_set_code_updates_code_hash(self) -> None:
        state = self.make_state()
        bytecode = b"\x60\x01\x60\x00\x55"
        expected_hash = keccak256(bytecode)
        state.set_code(ADDRESS_A, bytecode)
        self.assertEqual(state.get_code(ADDRESS_A), bytecode)
        self.assertEqual(state.get_code_hash(ADDRESS_A), expected_hash)

    def test_set_and_get_storage(self) -> None:
        state = self.make_state()
        state.set_storage(ADDRESS_A, SLOT_1, 5)
        self.assertEqual(state.get_storage(ADDRESS_A, SLOT_1), U256(5))

    def test_zeroing_storage_is_deterministic(self) -> None:
        state = self.make_state()
        state.set_storage(ADDRESS_A, SLOT_1, 5)
        state.commit()
        state.set_storage(ADDRESS_A, SLOT_1, 0)
        state.commit()
        account = state.get_account(ADDRESS_A)
        self.assertEqual(state.get_storage(ADDRESS_A, SLOT_1), U256.zero())
        self.assertEqual(account.storage_root, EMPTY_TRIE_ROOT)

        fresh = self.make_state()
        fresh.create_account(ADDRESS_A)
        fresh.commit()
        self.assertEqual(state.state_root, fresh.state_root)

    def test_storage_root_changes_after_storage_mutation(self) -> None:
        state = self.make_state()
        state.create_account(ADDRESS_A)
        empty_root = state.commit()
        state.set_storage(ADDRESS_A, SLOT_1, 1)
        state.commit()
        updated_account = state.get_account(ADDRESS_A)
        self.assertNotEqual(updated_account.storage_root, EMPTY_TRIE_ROOT)
        self.assertNotEqual(empty_root, state.state_root)

    def test_global_state_root_changes_after_account_mutation(self) -> None:
        state = self.make_state()
        first_root = state.commit()
        state.set_balance(ADDRESS_A, 1)
        second_root = state.commit()
        state.increment_nonce(ADDRESS_A)
        third_root = state.commit()
        self.assertNotEqual(first_root, second_root)
        self.assertNotEqual(second_root, third_root)

    def test_same_sequence_produces_same_root(self) -> None:
        left = self.make_state()
        right = self.make_state()
        left_account, left_root = _run_common_sequence(left)
        right_account, right_root = _run_common_sequence(right)
        self.assertEqual(left_account, right_account)
        self.assertEqual(left_root, right_root)

    def test_snapshot_and_revert(self) -> None:
        state = self.make_state()
        state.set_balance(ADDRESS_A, 1)
        baseline_root = state.commit()
        snapshot = state.snapshot()
        state.set_balance(ADDRESS_A, 5)
        state.set_storage(ADDRESS_A, SLOT_1, 7)
        mutated_root = state.commit()
        self.assertNotEqual(mutated_root, baseline_root)
        state.revert(snapshot)
        reverted_root = state.commit()
        self.assertEqual(reverted_root, baseline_root)
        self.assertEqual(state.get_storage(ADDRESS_A, SLOT_1), U256.zero())
        self.assertEqual(state.get_balance(ADDRESS_A), U256.one())

    def test_delete_account(self) -> None:
        state = self.make_state()
        state.set_balance(ADDRESS_A, 10)
        state.commit()
        state.delete_account(ADDRESS_A)
        state.commit()
        self.assertFalse(state.account_exists(ADDRESS_A))
        self.assertIsNone(state.get_account(ADDRESS_A))


class HashMapBackendTests(BackendBehaviorMixin, unittest.TestCase):
    backend_factory = HashMapStateBackend


class MptBackendTests(BackendBehaviorMixin, unittest.TestCase):
    backend_factory = MptStateBackend


class BackendParityTests(unittest.TestCase):
    def test_backends_expose_same_logical_behavior_and_roots(self) -> None:
        left = State(HashMapStateBackend())
        right = State(MptStateBackend())

        for state in (left, right):
            state.create_account(ADDRESS_A)
            state.set_balance(ADDRESS_A, 3)
            state.increment_nonce(ADDRESS_A)
            state.set_code(ADDRESS_A, b"\x60\x0a\x60\x0b")
            state.set_storage(ADDRESS_A, SLOT_1, 11)
            state.set_storage(ADDRESS_A, SLOT_2, 22)
            state.create_account(ADDRESS_B)
            state.set_balance(ADDRESS_B, 7)
            state.set_storage(ADDRESS_B, SLOT_1, 33)
            state.commit()

        self.assertEqual(left.state_root, right.state_root)
        self.assertEqual(left.get_account(ADDRESS_A), right.get_account(ADDRESS_A))
        self.assertEqual(left.get_account(ADDRESS_B), right.get_account(ADDRESS_B))
        self.assertEqual(left.get_storage(ADDRESS_A, SLOT_1), right.get_storage(ADDRESS_A, SLOT_1))
        self.assertEqual(left.get_storage(ADDRESS_B, SLOT_1), right.get_storage(ADDRESS_B, SLOT_1))

    def test_constants_match_known_ethereum_roots(self) -> None:
        self.assertEqual(
            EMPTY_CODE_HASH.to_hex(),
            "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            EMPTY_TRIE_ROOT.to_hex(),
            "0x56e81f171bcc55a6ff8345e692c0f86e5b48e01b996cadc001622fb5e363b421",
        )


if __name__ == "__main__":
    unittest.main()
