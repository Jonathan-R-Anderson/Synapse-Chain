from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, SENDER_ONE, addr, make_legacy_tx

from evm import StateDB
from execution import BlockEnvironment, ChainConfig, FeeModel, apply_transaction


class StateTransitionTests(unittest.TestCase):
    def test_successful_eth_transfer_updates_balances_and_nonce(self) -> None:
        state = StateDB()
        recipient = addr("0x1000000000000000000000000000000000000001")
        coinbase = addr("0x2000000000000000000000000000000000000002")
        state.set_balance(SENDER_ONE, 1_000_000)
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            value=123,
            gas_limit=21_000,
            gas_price=2,
        )
        result = apply_transaction(
            state=state,
            transaction=transaction,
            block_env=BlockEnvironment(
                block_number=1,
                timestamp=1_700_000_000,
                gas_limit=100_000,
                coinbase=coinbase,
                base_fee=None,
                chain_id=1,
            ),
            chain_config=ChainConfig(fee_model=FeeModel.LEGACY),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.gas_used, 21_000)
        self.assertEqual(state.get_nonce(SENDER_ONE), 1)
        self.assertEqual(state.get_balance(recipient), 123)
        self.assertEqual(state.get_balance(coinbase), 42_000)
        self.assertEqual(state.get_balance(SENDER_ONE), 1_000_000 - 42_000 - 123)
        self.assertEqual(result.receipt.status, 1)


if __name__ == "__main__":
    unittest.main()
