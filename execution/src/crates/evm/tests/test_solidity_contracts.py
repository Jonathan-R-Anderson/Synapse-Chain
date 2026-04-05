from __future__ import annotations

import unittest

from helpers import (
    ROOT,
    addr,
    build_counter_runtime,
    build_delegate_proxy_runtime,
    build_reverter_runtime,
    deploy_code,
    encode_call,
)
from evm import Interpreter


class SolidityStyleContractTests(unittest.TestCase):
    def test_counter_storage_setter_and_getter(self) -> None:
        interpreter = Interpreter()
        contract = addr("0x9000000000000000000000000000000000000009")
        deploy_code(interpreter, contract, build_counter_runtime())

        set_result = interpreter.call(contract, calldata=encode_call("set(uint256)", 123))
        self.assertTrue(set_result.success)
        get_result = interpreter.call(contract, calldata=encode_call("get()"))
        self.assertTrue(get_result.success)
        self.assertEqual(int.from_bytes(get_result.output, "big"), 123)

    def test_delegatecall_proxy_style_contract(self) -> None:
        interpreter = Interpreter()
        logic = addr("0xa00000000000000000000000000000000000000a")
        proxy = addr("0xb00000000000000000000000000000000000000b")
        deploy_code(interpreter, logic, build_counter_runtime())
        deploy_code(interpreter, proxy, build_delegate_proxy_runtime(logic))

        interpreter.call(proxy, calldata=encode_call("set(uint256)", 55))
        result = interpreter.call(proxy, calldata=encode_call("get()"))
        self.assertEqual(int.from_bytes(result.output, "big"), 55)

    def test_revert_data_propagates(self) -> None:
        interpreter = Interpreter()
        contract = addr("0xc00000000000000000000000000000000000000c")
        deploy_code(interpreter, contract, build_reverter_runtime())
        result = interpreter.call(contract)
        self.assertFalse(result.success)
        self.assertTrue(result.reverted)
        self.assertEqual(result.output, bytes.fromhex("deadbeef"))


if __name__ == "__main__":
    unittest.main()
