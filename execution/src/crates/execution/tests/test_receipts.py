from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, SENDER_ONE, addr, build_logger_runtime, encode_call, make_legacy_tx

from evm import StateDB
from execution import BlockEnvironment, ChainConfig, FeeModel, Receipt, apply_transaction, logs_bloom


class ReceiptTests(unittest.TestCase):
    def test_receipt_contains_logs_and_bloom(self) -> None:
        state = StateDB()
        contract = addr("0x3000000000000000000000000000000000000003")
        coinbase = addr("0x4000000000000000000000000000000000000004")
        state.set_balance(SENDER_ONE, 5_000_000)
        state.set_code(contract, build_logger_runtime(0xAA))
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            contract,
            gas_limit=80_000,
            gas_price=1,
            data=encode_call("ignored(uint256)", 7),
        )
        result = apply_transaction(
            state,
            transaction,
            BlockEnvironment(
                block_number=1,
                timestamp=1_700_000_000,
                gas_limit=500_000,
                coinbase=coinbase,
                base_fee=None,
                chain_id=1,
            ),
            ChainConfig(fee_model=FeeModel.LEGACY),
        )
        self.assertTrue(result.success)
        self.assertEqual(len(result.logs), 1)
        self.assertEqual(result.receipt.logs, result.logs)
        self.assertEqual(result.receipt.bloom, logs_bloom(result.logs))
        self.assertNotEqual(result.receipt.bloom, bytes(len(result.receipt.bloom)))

    def test_receipt_serialization_roundtrip_preserves_execution_metadata(self) -> None:
        receipt = Receipt(
            status=1,
            cumulative_gas_used=21_000,
            gas_used=21_000,
            transaction_type=2,
            contract_address=addr("0x1234567890abcdef1234567890abcdef12345678"),
            effective_gas_price=17,
        )
        decoded = Receipt.deserialize(receipt.serialize())
        self.assertEqual(decoded, receipt)
        self.assertEqual(Receipt.rlp_decode(receipt.rlp_encode()).cumulative_gas_used, receipt.cumulative_gas_used)
        self.assertEqual(receipt.hash(), Receipt.rlp_decode(receipt.rlp_encode()).hash())


if __name__ == "__main__":
    unittest.main()
