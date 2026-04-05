from __future__ import annotations

from dataclasses import replace
import unittest

from crypto import address_from_private_key
from evm import ExecutionTraceRow
from execution_test_helpers import addr, build_counter_runtime, encode_call
from execution_tests import AccountFixture, ExecutionFixtureCase, ExpectedResult, TestEnvironment, TestTransaction
from debug.diff_trace import diff_trace_rows
from debug.trace import trace_fixture_case
from primitives import Address


class TraceDebugTests(unittest.TestCase):
    def test_trace_captures_storage_write_for_contract_call(self) -> None:
        sender = address_from_private_key(1)
        contract = addr("0x9000000000000000000000000000000000000009")
        case = ExecutionFixtureCase(
            name="trace_counter_set",
            environment=TestEnvironment(
                coinbase=Address.zero(),
                gas_limit=30_000_000,
                number=12_000_000,
                timestamp=1,
                difficulty=1,
                chain_id=1,
            ),
            pre_state=(
                AccountFixture(address=sender, nonce=0, balance=1_000_000),
                AccountFixture(address=contract, nonce=1, balance=0, code=build_counter_runtime()),
            ),
            transactions=(
                TestTransaction(
                    tx_type="legacy",
                    secret_key=1,
                    nonce=0,
                    to=contract,
                    gas_limit=100_000,
                    gas_price=0,
                    data=encode_call("set(uint256)", 7),
                    chain_id=1,
                ),
            ),
            expected=ExpectedResult(success=True),
            fork_name="berlin",
        )
        sink = trace_fixture_case(case)
        self.assertTrue(any(row.opcode_name == "SSTORE" for row in sink.rows))
        sstore_rows = [row for row in sink.rows if row.opcode_name == "SSTORE"]
        self.assertIn((0, 7), sstore_rows[0].storage_writes)

    def test_diff_trace_rows_reports_first_divergence(self) -> None:
        row = ExecutionTraceRow(
            depth=0,
            pc=0,
            opcode=0x01,
            opcode_name="ADD",
            gas_before=3,
            gas_after=0,
            stack_before=(1, 2),
            stack_after=(3,),
        )
        altered = replace(row, gas_after=1)
        mismatches = diff_trace_rows([row], [altered])
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0].field, "gas_after")


if __name__ == "__main__":
    unittest.main()
