from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primitives import Address, Hash, U256


class U256Tests(unittest.TestCase):
    def test_from_and_to_bytes_roundtrip(self) -> None:
        value = U256.from_hex("0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        self.assertEqual(value.to_bytes(), bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"))
        self.assertEqual(U256.from_bytes(value.to_bytes()), value)

    def test_addition_wraps_and_checked_add_detects_overflow(self) -> None:
        maximum = U256.max_value()
        self.assertEqual(maximum + U256.one(), U256.zero())
        with self.assertRaises(OverflowError):
            maximum.checked_add(U256.one())

    def test_subtraction_wraps_and_checked_sub_detects_underflow(self) -> None:
        self.assertEqual(U256.zero() - U256.one(), U256.max_value())
        with self.assertRaises(OverflowError):
            U256.zero().checked_sub(U256.one())

    def test_multiplication_wraps_and_checked_mul_detects_overflow(self) -> None:
        value = U256.max_value()
        self.assertEqual(value * 2, U256.from_hex("0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffe"))
        with self.assertRaises(OverflowError):
            value.checked_mul(2)

    def test_division_and_modulo(self) -> None:
        dividend = U256(100)
        divisor = U256(9)
        quotient, remainder = divmod(dividend, divisor)
        self.assertEqual(quotient, U256(11))
        self.assertEqual(remainder, U256(1))
        self.assertEqual(dividend // divisor, U256(11))
        self.assertEqual(dividend % divisor, U256(1))

    def test_division_by_zero_raises(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            _ = U256.one() // U256.zero()

    def test_bitwise_operations(self) -> None:
        left = U256(0b1010)
        right = U256(0b1100)
        self.assertEqual(left & right, U256(0b1000))
        self.assertEqual(left | right, U256(0b1110))
        self.assertEqual(left ^ right, U256(0b0110))
        self.assertEqual(~U256.zero(), U256.max_value())
        self.assertEqual(U256.one() << 255, U256.from_hex("0x8000000000000000000000000000000000000000000000000000000000000000"))
        self.assertEqual((U256.one() << 255) >> 255, U256.one())

    def test_invalid_byte_length_rejected(self) -> None:
        with self.assertRaises(ValueError):
            U256.from_bytes(b"\x00" * 33)

    def test_to_bytes_rejects_too_small_length(self) -> None:
        with self.assertRaises(OverflowError):
            U256(256).to_bytes(length=1)


class FixedByteTests(unittest.TestCase):
    def test_address_and_hash_roundtrip(self) -> None:
        address = Address.from_hex("0x00112233445566778899aabbccddeeff00112233")
        digest = Hash.from_hex("0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(address.to_bytes(), bytes.fromhex("00112233445566778899aabbccddeeff00112233"))
        self.assertEqual(digest.to_bytes(), bytes.fromhex("c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"))

    def test_zero_helpers(self) -> None:
        self.assertEqual(Address.zero().to_hex(), "0x" + "00" * 20)
        self.assertEqual(Hash.zero().to_hex(), "0x" + "00" * 32)

    def test_invalid_lengths_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Address(b"\x00" * 19)
        with self.assertRaises(ValueError):
            Hash(b"\x00" * 31)

    def test_invalid_hex_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Address.from_hex("0x1234")
        with self.assertRaises(ValueError):
            Hash.from_hex("not-hex")


if __name__ == "__main__":
    unittest.main()
