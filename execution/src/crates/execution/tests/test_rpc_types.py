from __future__ import annotations

import unittest

from rpc.errors import ExecutionReverted
from rpc.types import BlockSelector, parse_block_selector, to_quantity


class RpcTypesTests(unittest.TestCase):
    def test_to_quantity_uses_minimal_lowercase_hex(self) -> None:
        self.assertEqual(to_quantity(0), "0x0")
        self.assertEqual(to_quantity(1), "0x1")
        self.assertEqual(to_quantity(21_000), "0x5208")

    def test_block_selector_supports_tags_and_numbers(self) -> None:
        self.assertEqual(parse_block_selector("latest"), BlockSelector(tag="latest"))
        self.assertEqual(parse_block_selector("earliest"), BlockSelector(tag="earliest"))
        self.assertEqual(parse_block_selector("pending"), BlockSelector(tag="pending"))
        self.assertEqual(parse_block_selector("0x2"), BlockSelector(number=2))
        with self.assertRaises(ValueError):
            parse_block_selector("finalized")

    def test_execution_reverted_error_serializes_revert_data(self) -> None:
        payload = ExecutionReverted("0xdeadbeef").to_response(7)
        self.assertEqual(payload["error"]["code"], 3)
        self.assertEqual(payload["error"]["message"], "execution reverted")
        self.assertEqual(payload["error"]["data"], "0xdeadbeef")


if __name__ == "__main__":
    unittest.main()
