from __future__ import annotations

import unittest

from helpers import (
    ROOT,
    abi_uint,
    addr,
    build_call_wrapper_runtime,
    build_counter_runtime,
    build_delegate_proxy_runtime,
    build_staticcall_wrapper_runtime,
    deploy_code,
    encode_call,
)
from evm import Interpreter


class CallTests(unittest.TestCase):
    def test_call_returns_child_output(self) -> None:
        interpreter = Interpreter()
        callee = addr("0x1000000000000000000000000000000000000001")
        caller = addr("0x2000000000000000000000000000000000000002")
        deploy_code(interpreter, callee, build_counter_runtime())
        deploy_code(interpreter, caller, build_call_wrapper_runtime(callee))

        interpreter.call(callee, calldata=encode_call("set(uint256)", 42))
        result = interpreter.call(caller, calldata=encode_call("get()"))
        self.assertTrue(result.success)
        self.assertEqual(int.from_bytes(result.output, "big"), 42)

    def test_staticcall_rejects_state_change(self) -> None:
        interpreter = Interpreter()
        callee = addr("0x3000000000000000000000000000000000000003")
        wrapper = addr("0x4000000000000000000000000000000000000004")
        deploy_code(interpreter, callee, build_counter_runtime())
        deploy_code(interpreter, wrapper, build_staticcall_wrapper_runtime(callee))

        result = interpreter.call(wrapper, calldata=encode_call("set(uint256)", 7))
        self.assertTrue(result.success)
        self.assertEqual(int.from_bytes(result.output, "big"), 0)
        self.assertEqual(interpreter.state.get_storage(callee, 0), 0)

    def test_delegatecall_preserves_proxy_storage(self) -> None:
        interpreter = Interpreter()
        logic = addr("0x5000000000000000000000000000000000000005")
        proxy = addr("0x6000000000000000000000000000000000000006")
        deploy_code(interpreter, logic, build_counter_runtime())
        deploy_code(interpreter, proxy, build_delegate_proxy_runtime(logic))

        set_result = interpreter.call(proxy, calldata=encode_call("set(uint256)", 99))
        self.assertTrue(set_result.success)
        self.assertEqual(interpreter.state.get_storage(proxy, 0), 99)
        self.assertEqual(interpreter.state.get_storage(logic, 0), 0)

        get_result = interpreter.call(proxy, calldata=encode_call("get()"))
        self.assertTrue(get_result.success)
        self.assertEqual(int.from_bytes(get_result.output, "big"), 99)


if __name__ == "__main__":
    unittest.main()
