from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "primitives" / "src"))

from encoding import RlpDecodingError, decode, decode_bytes, decode_int, decode_str, encode
from primitives import U256


class EncodingTests(unittest.TestCase):
    def test_known_ethereum_examples(self) -> None:
        self.assertEqual(encode(b"dog").hex(), "83646f67")
        self.assertEqual(encode([b"cat", b"dog"]).hex(), "c88363617483646f67")
        self.assertEqual(encode(b"").hex(), "80")
        self.assertEqual(encode([]).hex(), "c0")
        self.assertEqual(encode(0).hex(), "80")
        self.assertEqual(encode(15).hex(), "0f")
        self.assertEqual(encode(1024).hex(), "820400")

    def test_utf8_strings_and_nested_lists(self) -> None:
        encoded = encode(["eth", [b"rlp", 1], "ok"])
        self.assertEqual(decode(encoded), [b"eth", [b"rlp", b"\x01"], b"ok"])
        self.assertEqual(decode_str(encode("dog")), "dog")

    def test_integer_roundtrip_supports_u256(self) -> None:
        value = U256.from_hex("0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")
        encoded = encode(value)
        self.assertEqual(decode_int(encoded), int(value))

    def test_long_string_encoding_uses_long_form(self) -> None:
        payload = b"a" * 56
        encoded = encode(payload)
        self.assertEqual(encoded[:2].hex(), "b838")
        self.assertEqual(decode_bytes(encoded), payload)

    def test_long_list_encoding_uses_long_form(self) -> None:
        payload = [b"a" for _ in range(56)]
        encoded = encode(payload)
        self.assertEqual(encoded[0], 0xF8)
        self.assertEqual(len(decode(encoded)), 56)

    def test_invalid_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode(-1)
        with self.assertRaises(TypeError):
            encode(True)
        with self.assertRaises(RlpDecodingError):
            decode(b"")
        with self.assertRaises(RlpDecodingError):
            decode(bytes.fromhex("8100"))
        with self.assertRaises(RlpDecodingError):
            decode(bytes.fromhex("b80100"))
        with self.assertRaises(RlpDecodingError):
            decode(bytes.fromhex("f80180"))
        with self.assertRaises(RlpDecodingError):
            decode(bytes.fromhex("83"))
        with self.assertRaises(RlpDecodingError):
            decode_int(bytes.fromhex("00"))
        with self.assertRaises(RlpDecodingError):
            decode(bytes.fromhex("8080"))


if __name__ == "__main__":
    unittest.main()
