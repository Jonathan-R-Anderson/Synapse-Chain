from __future__ import annotations

import unittest

from execution_test_helpers import (
    PRIVATE_KEY_ONE,
    PRIVATE_KEY_TWO,
    SENDER_ONE,
    SENDER_TWO,
    addr,
    build_counter_runtime,
    build_init_code,
    build_reverter_runtime,
    encode_call,
    make_legacy_tx,
)

from evm import StateDB
from evm.utils import compute_create_address
from execution import Block, BlockHeader, ChainConfig, FeeModel, apply_block
from execution.exceptions import BlockGasExceededError


class BlockExecutionTests(unittest.TestCase):
    def test_block_executes_multiple_transactions_in_order(self) -> None:
        old_state = StateDB()
        beneficiary = addr("0xd00000000000000000000000000000000000000d")
        recipient = addr("0xe00000000000000000000000000000000000000e")
        old_state.set_balance(SENDER_ONE, 10_000_000)
        old_state.set_balance(SENDER_TWO, 1_000_000)

        create_tx = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            None,
            gas_limit=200_000,
            gas_price=1,
            data=build_init_code(build_counter_runtime()),
        )
        created_address = compute_create_address(SENDER_ONE, 0)
        call_tx = make_legacy_tx(
            PRIVATE_KEY_ONE,
            1,
            created_address,
            gas_limit=100_000,
            gas_price=1,
            data=encode_call("set(uint256)", 42),
        )
        transfer_tx = make_legacy_tx(
            PRIVATE_KEY_TWO,
            0,
            recipient,
            gas_limit=21_000,
            gas_price=1,
            value=77,
        )

        block = Block(
            header=BlockHeader(
                parent_hash=None,
                number=1,
                timestamp=1_700_000_000,
                gas_limit=500_000,
                coinbase=beneficiary,
            ),
            transactions=(create_tx, call_tx, transfer_tx),
        )
        result = apply_block(old_state, block, ChainConfig(fee_model=FeeModel.LEGACY))
        self.assertEqual(result.gas_used, sum(receipt.gas_used for receipt in result.receipts))
        self.assertEqual(result.state.get_storage(created_address, 0), 42)
        self.assertEqual(result.state.get_balance(recipient), 77)
        self.assertEqual(old_state.get_code(created_address), b"")
        self.assertEqual(result.receipts[0].contract_address, created_address)
        self.assertGreater(result.coinbase_fees, 0)

    def test_block_rejects_transaction_over_remaining_gas_budget(self) -> None:
        old_state = StateDB()
        beneficiary = addr("0xf00000000000000000000000000000000000000f")
        old_state.set_balance(SENDER_ONE, 10_000_000)
        old_state.set_balance(SENDER_TWO, 10_000_000)

        first = make_legacy_tx(PRIVATE_KEY_ONE, 0, beneficiary, gas_limit=21_000, gas_price=1)
        second = make_legacy_tx(PRIVATE_KEY_TWO, 0, beneficiary, gas_limit=21_000, gas_price=1)
        block = Block(
            header=BlockHeader(number=1, timestamp=1_700_000_000, gas_limit=30_000, coinbase=beneficiary),
            transactions=(first, second),
        )
        with self.assertRaises(BlockGasExceededError):
            apply_block(old_state, block, ChainConfig(fee_model=FeeModel.LEGACY))

    def test_reverting_transaction_does_not_abort_following_transactions(self) -> None:
        old_state = StateDB()
        beneficiary = addr("0x1234000000000000000000000000000000001234")
        reverting_contract = addr("0x5678000000000000000000000000000000005678")
        recipient = addr("0x9abc000000000000000000000000000000009abc")
        old_state.set_balance(SENDER_ONE, 10_000_000)
        old_state.set_balance(SENDER_TWO, 1_000_000)
        old_state.set_code(reverting_contract, build_reverter_runtime())

        failing = make_legacy_tx(PRIVATE_KEY_ONE, 0, reverting_contract, gas_limit=80_000, gas_price=1)
        succeeding = make_legacy_tx(PRIVATE_KEY_TWO, 0, recipient, gas_limit=21_000, gas_price=1, value=9)
        block = Block(
            header=BlockHeader(number=1, timestamp=1_700_000_000, gas_limit=200_000, coinbase=beneficiary),
            transactions=(failing, succeeding),
        )

        result = apply_block(old_state, block, ChainConfig(fee_model=FeeModel.LEGACY))
        self.assertEqual(result.receipts[0].status, 0)
        self.assertEqual(result.receipts[1].status, 1)
        self.assertEqual(result.state.get_balance(recipient), 9)
        self.assertEqual(result.state.get_nonce(SENDER_ONE), 1)
        self.assertEqual(result.state.get_nonce(SENDER_TWO), 1)


if __name__ == "__main__":
    unittest.main()
