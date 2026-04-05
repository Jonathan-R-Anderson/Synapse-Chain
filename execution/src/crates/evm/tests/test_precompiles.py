from __future__ import annotations

import hashlib
import unittest

from helpers import ROOT, addr  # noqa: F401
from crypto import address_from_private_key, keccak256, sign_message_hash
from evm import Interpreter


class PrecompileTests(unittest.TestCase):
    def test_ecrecover(self) -> None:
        interpreter = Interpreter()
        private_key = 1
        message_hash = keccak256(b"precompile-ecrecover").to_bytes()
        signature = sign_message_hash(message_hash, private_key)
        payload = message_hash
        payload += (27 + signature.recovery_id).to_bytes(32, "big")
        payload += signature.r.to_bytes(32, "big")
        payload += signature.s.to_bytes(32, "big")
        result = interpreter.call(addr("0x0000000000000000000000000000000000000001"), calldata=payload, gas=10_000)
        self.assertTrue(result.success)
        self.assertEqual(result.output[-20:], address_from_private_key(private_key).to_bytes())

    def test_sha256(self) -> None:
        interpreter = Interpreter()
        data = b"hello sha"
        result = interpreter.call(addr("0x0000000000000000000000000000000000000002"), calldata=data, gas=10_000)
        self.assertEqual(result.output, hashlib.sha256(data).digest())

    def test_ripemd160(self) -> None:
        interpreter = Interpreter()
        data = b"hello ripemd"
        result = interpreter.call(addr("0x0000000000000000000000000000000000000003"), calldata=data, gas=10_000)
        self.assertEqual(result.output[-20:], hashlib.new("ripemd160", data).digest())

    def test_identity(self) -> None:
        interpreter = Interpreter()
        data = b"identity bytes"
        result = interpreter.call(addr("0x0000000000000000000000000000000000000004"), calldata=data, gas=10_000)
        self.assertEqual(result.output, data)


if __name__ == "__main__":
    unittest.main()
