from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, SENDER_ONE, addr, make_eip1559_tx

from evm import StateDB
from execution import BlockEnvironment, ChainConfig, apply_transaction


class FeeDistributionTests(unittest.TestCase):
    def test_eip1559_base_fee_is_burned_and_tip_paid_to_coinbase(self) -> None:
        state = StateDB()
        recipient = addr("0xb00000000000000000000000000000000000000b")
        coinbase = addr("0xc00000000000000000000000000000000000000c")
        state.set_balance(SENDER_ONE, 1_000_000)
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            gas_limit=21_000,
            max_priority_fee_per_gas=3,
            max_fee_per_gas=25,
        )
        result = apply_transaction(
            state,
            transaction,
            BlockEnvironment(1, 1_700_000_000, 500_000, coinbase, 10, 1),
            ChainConfig(),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.effective_gas_price, 13)
        self.assertEqual(result.coinbase_fee, 21_000 * 3)
        self.assertEqual(result.base_fee_burned, 21_000 * 10)
        self.assertEqual(state.get_balance(coinbase), 21_000 * 3)
        self.assertEqual(state.get_balance(SENDER_ONE), 1_000_000 - (21_000 * 13))


if __name__ == "__main__":
    unittest.main()
