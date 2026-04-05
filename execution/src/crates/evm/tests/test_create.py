from __future__ import annotations

import unittest

from helpers import (
    ROOT,
    addr,
    build_create2_factory_runtime,
    build_create_factory_runtime,
    build_init_code,
    build_return_runtime,
    deploy_code,
)
from evm import Interpreter
from evm.utils import compute_create2_address, compute_create_address


class CreateTests(unittest.TestCase):
    def test_create_deploys_runtime_code(self) -> None:
        interpreter = Interpreter()
        factory = addr("0x7000000000000000000000000000000000000007")
        child_runtime = build_return_runtime(0x2A)
        init_code = build_init_code(child_runtime)
        deploy_code(interpreter, factory, build_create_factory_runtime(init_code))

        result = interpreter.call(factory)
        self.assertTrue(result.success)
        created = addr("0x" + result.output[-20:].hex())
        self.assertEqual(created, compute_create_address(factory, 0))
        child_result = interpreter.call(created)
        self.assertTrue(child_result.success)
        self.assertEqual(int.from_bytes(child_result.output, "big"), 0x2A)

    def test_create2_uses_deterministic_address(self) -> None:
        interpreter = Interpreter()
        factory = addr("0x8000000000000000000000000000000000000008")
        child_runtime = build_return_runtime(0xBEEF)
        init_code = build_init_code(child_runtime)
        salt = 0x1234
        deploy_code(interpreter, factory, build_create2_factory_runtime(init_code, salt))

        result = interpreter.call(factory)
        self.assertTrue(result.success)
        created = addr("0x" + result.output[-20:].hex())
        self.assertEqual(created, compute_create2_address(factory, salt, init_code))
        child_result = interpreter.call(created)
        self.assertEqual(int.from_bytes(child_result.output, "big"), 0xBEEF)


if __name__ == "__main__":
    unittest.main()
