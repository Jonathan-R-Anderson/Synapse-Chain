from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "primitives" / "src"))

from crypto import (
    PublicKey,
    Signature,
    address_from_private_key,
    address_from_public_key,
    generate_private_key,
    keccak256,
    public_key_from_private_key,
    recover_public_key,
    sign_message_hash,
    verify_message_hash,
)


class KeccakTests(unittest.TestCase):
    def test_known_vectors(self) -> None:
        self.assertEqual(
            keccak256(b"").to_hex(),
            "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )
        self.assertEqual(
            keccak256(b"abc").to_hex(),
            "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
        )


class Secp256k1Tests(unittest.TestCase):
    def test_generated_private_key_has_expected_shape(self) -> None:
        private_key = generate_private_key()
        self.assertEqual(len(private_key), 32)
        self.assertGreater(int.from_bytes(private_key, "big"), 0)

    def test_private_key_one_derives_generator_public_key(self) -> None:
        public_key = public_key_from_private_key(1)
        self.assertEqual(
            public_key,
            PublicKey(
                0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
                0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
            ),
        )

    def test_address_derivation_matches_known_vector(self) -> None:
        self.assertEqual(
            address_from_private_key(1).to_hex(),
            "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
        )

    def test_signing_is_deterministic_and_recoverable(self) -> None:
        message_hash = keccak256(b"ethereum execution primitives").to_bytes()
        first_signature = sign_message_hash(message_hash, 1)
        second_signature = sign_message_hash(message_hash, 1)
        self.assertEqual(first_signature, second_signature)
        self.assertTrue(verify_message_hash(message_hash, first_signature, public_key_from_private_key(1)))
        self.assertEqual(recover_public_key(message_hash, first_signature), public_key_from_private_key(1))
        self.assertEqual(address_from_public_key(recover_public_key(message_hash, first_signature)), address_from_private_key(1))

    def test_signature_serialization_roundtrip(self) -> None:
        signature = sign_message_hash(keccak256(b"roundtrip").to_bytes(), 1)
        self.assertEqual(Signature.from_bytes(signature.to_bytes()), signature)

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            public_key_from_private_key(0)
        with self.assertRaises(ValueError):
            public_key_from_private_key(b"\x01" * 31)
        with self.assertRaises(ValueError):
            PublicKey.from_bytes(b"\x04" + b"\x00" * 64 + b"\x00")
        with self.assertRaises(ValueError):
            Signature(1, 1, 4)


if __name__ == "__main__":
    unittest.main()
