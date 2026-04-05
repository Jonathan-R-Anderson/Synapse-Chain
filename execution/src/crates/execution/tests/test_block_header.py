from __future__ import annotations

import unittest

from execution_test_helpers import addr

from execution import BlockHeader


class BlockHeaderTests(unittest.TestCase):
    def test_header_rlp_and_hash_are_deterministic(self) -> None:
        header = BlockHeader(
            number=7,
            gas_limit=30_000_000,
            gas_used=21_000,
            timestamp=1_700_000_000,
            coinbase=addr("0x1111111111111111111111111111111111111111"),
            extra_data=b"phase-6",
            base_fee=1_000_000_000,
        )
        encoded = header.rlp_encode()
        self.assertEqual(encoded, header.rlp_encode())
        self.assertEqual(header.hash(), header.hash())
        self.assertEqual(BlockHeader.rlp_decode(encoded), header)
        self.assertEqual(BlockHeader.from_dict(header.to_dict()), header)

    def test_invalid_lengths_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BlockHeader(parent_hash=b"\x00" * 31)
        with self.assertRaises(ValueError):
            BlockHeader(logs_bloom=b"\x00" * 255)
        with self.assertRaises(ValueError):
            BlockHeader(nonce=b"\x00" * 7)

    def test_gas_and_extra_data_validation(self) -> None:
        with self.assertRaises(ValueError):
            BlockHeader(gas_limit=21_000, gas_used=21_001)
        with self.assertRaises(ValueError):
            BlockHeader(extra_data=b"x" * 33)


if __name__ == "__main__":
    unittest.main()
