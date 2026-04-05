from __future__ import annotations

import unittest

from execution_test_helpers import (
    PRIVATE_KEY_ONE,
    SENDER_ONE,
    addr,
    build_reverter_runtime,
    build_reverting_sstore_runtime,
    make_legacy_tx,
)

from evm import StateDB
from execution import BlockEnvironment, ChainConfig, FeeModel, apply_transaction


class FailedTransactionTests(unittest.TestCase):
    def test_revert_returns_data_and_still_charges_gas(self) -> None:
        state = StateDB()
        contract = addr("0x5000000000000000000000000000000000000005")
        coinbase = addr("0x6000000000000000000000000000000000000006")
        state.set_balance(SENDER_ONE, 5_000_000)
        state.set_code(contract, build_reverter_runtime())
        transaction = make_legacy_tx(PRIVATE_KEY_ONE, 0, contract, gas_limit=80_000, gas_price=1)
        result = apply_transaction(
            state,
            transaction,
            BlockEnvironment(1, 1_700_000_000, 500_000, coinbase, None, 1),
            ChainConfig(fee_model=FeeModel.LEGACY),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.revert_data, bytes.fromhex("deadbeef"))
        self.assertGreater(result.gas_used, 21_000)
        self.assertLess(result.gas_used, 80_000)
        self.assertEqual(state.get_nonce(SENDER_ONE), 1)
        self.assertEqual(state.get_balance(coinbase), result.gas_used)

    def test_revert_rolls_back_storage_mutation(self) -> None:
        state = StateDB()
        contract = addr("0x7000000000000000000000000000000000000007")
        coinbase = addr("0x8000000000000000000000000000000000000008")
        state.set_balance(SENDER_ONE, 5_000_000)
        state.set_code(contract, build_reverting_sstore_runtime(9))
        transaction = make_legacy_tx(PRIVATE_KEY_ONE, 0, contract, gas_limit=80_000, gas_price=1)
        result = apply_transaction(
            state,
            transaction,
            BlockEnvironment(1, 1_700_000_000, 500_000, coinbase, None, 1),
            ChainConfig(fee_model=FeeModel.LEGACY),
        )
        self.assertFalse(result.success)
        self.assertEqual(state.get_storage(contract, 0), 0)
        self.assertEqual(result.receipt.status, 0)


if __name__ == "__main__":
    unittest.main()
