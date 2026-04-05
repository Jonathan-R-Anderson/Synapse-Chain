from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, SENDER_ONE, addr, build_counter_runtime, encode_call, make_legacy_tx

from evm import StateDB
from execution import BlockEnvironment, ChainConfig, FeeModel, apply_transaction


class RevertAndOOGTests(unittest.TestCase):
    def test_out_of_gas_reverts_state_and_consumes_all_gas(self) -> None:
        state = StateDB()
        contract = addr("0x9000000000000000000000000000000000000009")
        coinbase = addr("0xa00000000000000000000000000000000000000a")
        state.set_balance(SENDER_ONE, 5_000_000)
        state.set_code(contract, build_counter_runtime())
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            contract,
            gas_limit=25_000,
            gas_price=1,
            data=encode_call("set(uint256)", 5),
        )
        result = apply_transaction(
            state,
            transaction,
            BlockEnvironment(1, 1_700_000_000, 500_000, coinbase, None, 1),
            ChainConfig(fee_model=FeeModel.LEGACY),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.gas_used, 25_000)
        self.assertEqual(state.get_storage(contract, 0), 0)
        self.assertEqual(result.receipt.status, 0)


if __name__ == "__main__":
    unittest.main()
