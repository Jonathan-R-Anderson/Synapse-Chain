from __future__ import annotations

import unittest

from helpers import ROOT, addr, assemble, op, push  # noqa: F401
from evm import Interpreter


class ReturnRevertTests(unittest.TestCase):
    def test_return_outputs_memory_slice(self) -> None:
        interpreter = Interpreter()
        code = assemble([push(0x2A, 32), push(0), op("MSTORE"), push(32), push(0), op("RETURN")])
        address = addr("0x1000000000000000000000000000000000000001")
        interpreter.state.set_code(address, code)
        result = interpreter.call(address)
        self.assertTrue(result.success)
        self.assertEqual(int.from_bytes(result.output, "big"), 0x2A)

    def test_revert_rolls_back_state(self) -> None:
        interpreter = Interpreter()
        code = assemble(
            [
                push(1),
                push(0),
                op("SSTORE"),
                push(0xDEADBEEF, 32),
                push(0),
                op("MSTORE"),
                push(4),
                push(28),
                op("REVERT"),
            ]
        )
        address = addr("0x2000000000000000000000000000000000000002")
        interpreter.state.set_code(address, code)
        result = interpreter.call(address)
        self.assertFalse(result.success)
        self.assertTrue(result.reverted)
        self.assertEqual(result.output, bytes.fromhex("deadbeef"))
        self.assertEqual(interpreter.state.get_storage(address, 0), 0)


if __name__ == "__main__":
    unittest.main()
