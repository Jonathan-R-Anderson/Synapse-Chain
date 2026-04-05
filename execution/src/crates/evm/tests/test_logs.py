from __future__ import annotations

import unittest

from helpers import ROOT, addr, assemble, build_logger_runtime, op, push  # noqa: F401
from evm import Interpreter


class LogTests(unittest.TestCase):
    def test_logs_are_emitted(self) -> None:
        interpreter = Interpreter()
        address = addr("0x3000000000000000000000000000000000000003")
        interpreter.state.set_code(address, build_logger_runtime())
        result = interpreter.call(address, calldata=(7).to_bytes(32, "big"))
        self.assertTrue(result.success)
        self.assertEqual(len(result.logs), 1)
        self.assertEqual(int(result.logs[0].topics[0]), 0xAA)
        self.assertEqual(int.from_bytes(result.logs[0].data, "big"), 7)

    def test_logs_revert_with_frame(self) -> None:
        interpreter = Interpreter()
        code = assemble(
            [
                push(1, 32),
                push(0),
                op("MSTORE"),
                push(0xAA),
                push(32),
                push(0),
                op("LOG1"),
                push(0),
                push(0),
                op("REVERT"),
            ]
        )
        address = addr("0x4000000000000000000000000000000000000004")
        interpreter.state.set_code(address, code)
        result = interpreter.call(address)
        self.assertFalse(result.success)
        self.assertEqual(result.logs, ())


if __name__ == "__main__":
    unittest.main()
