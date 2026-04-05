from __future__ import annotations

import unittest

from crypto import address_from_private_key
from execution_test_helpers import ROOT, addr, build_counter_runtime, build_reverting_sstore_runtime, encode_call
from execution_tests import (
    AccountFixture,
    ExecutionFixtureCase,
    ExecutionTestRunner,
    ExpectedResult,
    TestEnvironment,
    TestTransaction,
    load_fixture_file,
)
from primitives import Address


class ExecutionRunnerTests(unittest.TestCase):
    def test_runner_executes_loaded_fixture(self) -> None:
        case = load_fixture_file(ROOT / "tests" / "fixtures" / "simple_execution_fixture.json")["legacy_zero_fee_transfer"]
        report, actual = ExecutionTestRunner().run_case(case)
        self.assertTrue(report.passed, report.render_text())
        self.assertTrue(actual.success)
        self.assertEqual(actual.gas_used, 21_000)

    def test_runner_persists_contract_storage_changes(self) -> None:
        sender = address_from_private_key(1)
        contract = addr("0x9000000000000000000000000000000000000009")
        runtime = build_counter_runtime()
        case = ExecutionFixtureCase(
            name="counter_setter",
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
                AccountFixture(address=contract, nonce=1, balance=0, code=runtime),
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
            expected=ExpectedResult(
                success=True,
                post_state=(
                    AccountFixture(address=sender, nonce=1, balance=1_000_000),
                    AccountFixture(
                        address=contract,
                        nonce=1,
                        balance=0,
                        code=runtime,
                        storage=((0, 7),),
                    ),
                ),
            ),
            fork_name="berlin",
        )
        report, actual = ExecutionTestRunner().run_case(case)
        self.assertTrue(report.passed, report.render_text())
        self.assertEqual(actual.state.get_storage(contract, 0), 7)

    def test_runner_reverts_state_changes_but_keeps_execution_failure(self) -> None:
        sender = address_from_private_key(1)
        contract = addr("0xa00000000000000000000000000000000000000a")
        runtime = build_reverting_sstore_runtime(9)
        case = ExecutionFixtureCase(
            name="reverting_contract_call",
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
                AccountFixture(address=contract, nonce=1, balance=0, code=runtime),
            ),
            transactions=(
                TestTransaction(
                    tx_type="legacy",
                    secret_key=1,
                    nonce=0,
                    to=contract,
                    gas_limit=100_000,
                    gas_price=0,
                    chain_id=1,
                ),
            ),
            expected=ExpectedResult(
                success=False,
                post_state=(
                    AccountFixture(address=sender, nonce=1, balance=1_000_000),
                    AccountFixture(address=contract, nonce=1, balance=0, code=runtime),
                ),
            ),
            fork_name="berlin",
        )
        report, actual = ExecutionTestRunner().run_case(case)
        self.assertTrue(report.passed, report.render_text())
        self.assertFalse(actual.success)
        self.assertEqual(actual.state.get_storage(contract, 0), 0)
        self.assertEqual(actual.receipts[0].status, 0)


if __name__ == "__main__":
    unittest.main()
