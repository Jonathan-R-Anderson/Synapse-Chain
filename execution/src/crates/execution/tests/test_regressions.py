from __future__ import annotations

import unittest

from crypto import address_from_private_key
from execution_tests import AccountFixture, ExecutionFixtureCase, ExecutionTestRunner, ExpectedResult, TestEnvironment, TestTransaction


class RegressionTests(unittest.TestCase):
    def test_invalid_nonce_does_not_mutate_state(self) -> None:
        sender = address_from_private_key(1)
        recipient = address_from_private_key(2)
        case = ExecutionFixtureCase(
            name="nonce_too_high",
            environment=TestEnvironment(
                coinbase=recipient,
                gas_limit=30_000_000,
                number=12_000_000,
                timestamp=1,
                difficulty=1,
                chain_id=1,
            ),
            pre_state=(AccountFixture(address=sender, nonce=0, balance=10_000),),
            transactions=(
                TestTransaction(
                    tx_type="legacy",
                    secret_key=1,
                    nonce=1,
                    to=recipient,
                    gas_limit=21_000,
                    gas_price=0,
                    value=1,
                    chain_id=1,
                ),
            ),
            expected=ExpectedResult(
                success=False,
                error_substring="invalid nonce",
                post_state=(AccountFixture(address=sender, nonce=0, balance=10_000),),
            ),
            fork_name="berlin",
        )
        report, actual = ExecutionTestRunner().run_case(case)
        self.assertTrue(report.passed, report.render_text())
        self.assertEqual(actual.state.get_nonce(sender), 0)
        self.assertEqual(actual.state.get_balance(sender), 10_000)


if __name__ == "__main__":
    unittest.main()
