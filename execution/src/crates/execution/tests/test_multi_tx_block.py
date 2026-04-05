from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, PRIVATE_KEY_TWO, SENDER_ONE, SENDER_TWO, addr, make_legacy_tx

from evm import StateDB
from execution import Block, BlockHeader, ChainConfig, FeeModel, apply_block


class MultiTransactionBlockTests(unittest.TestCase):
    def test_repeated_execution_from_same_start_state_is_deterministic(self) -> None:
        beneficiary = addr("0x1111000000000000000000000000000000001111")
        recipient = addr("0x2222000000000000000000000000000000002222")
        start_state = StateDB()
        start_state.set_balance(SENDER_ONE, 1_000_000)
        start_state.set_balance(SENDER_TWO, 1_000_000)

        block = Block(
            header=BlockHeader(number=1, timestamp=1_700_000_000, gas_limit=100_000, coinbase=beneficiary),
            transactions=(
                make_legacy_tx(PRIVATE_KEY_ONE, 0, recipient, gas_limit=21_000, gas_price=2, value=10),
                make_legacy_tx(PRIVATE_KEY_ONE, 1, recipient, gas_limit=21_000, gas_price=2, value=11),
                make_legacy_tx(PRIVATE_KEY_TWO, 0, recipient, gas_limit=21_000, gas_price=1, value=12),
            ),
        )
        first = apply_block(start_state, block, ChainConfig(fee_model=FeeModel.LEGACY))
        second = apply_block(start_state, block, ChainConfig(fee_model=FeeModel.LEGACY))
        self.assertEqual(first.gas_used, second.gas_used)
        self.assertEqual(first.coinbase_fees, second.coinbase_fees)
        self.assertEqual(first.state.get_balance(recipient), second.state.get_balance(recipient))
        self.assertEqual(first.state.get_balance(SENDER_ONE), second.state.get_balance(SENDER_ONE))
        self.assertEqual(first.state.get_nonce(SENDER_ONE), 2)
        self.assertEqual(first.state.get_nonce(SENDER_TWO), 1)


if __name__ == "__main__":
    unittest.main()
