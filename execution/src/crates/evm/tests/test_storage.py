from __future__ import annotations

import unittest

from helpers import ROOT, addr  # noqa: F401
from evm.state import StateDB
from evm.storage import Storage


class StorageTests(unittest.TestCase):
    def test_storage_get_and_set(self) -> None:
        storage = Storage()
        self.assertEqual(storage.get(1), 0)
        storage.set(1, 5)
        self.assertEqual(storage.get(1), 5)

    def test_state_scopes_storage_by_address(self) -> None:
        state = StateDB()
        address_a = addr("0x1000000000000000000000000000000000000001")
        address_b = addr("0x2000000000000000000000000000000000000002")
        state.set_storage(address_a, 1, 10)
        state.set_storage(address_b, 1, 20)
        self.assertEqual(state.get_storage(address_a, 1), 10)
        self.assertEqual(state.get_storage(address_b, 1), 20)

    def test_snapshot_and_restore(self) -> None:
        state = StateDB()
        address = addr("0x3000000000000000000000000000000000000003")
        state.set_storage(address, 1, 7)
        snapshot = state.snapshot()
        state.set_storage(address, 1, 9)
        state.restore(snapshot)
        self.assertEqual(state.get_storage(address, 1), 7)


if __name__ == "__main__":
    unittest.main()
