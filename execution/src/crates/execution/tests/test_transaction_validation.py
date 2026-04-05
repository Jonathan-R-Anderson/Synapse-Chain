from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, SENDER_ONE, addr, make_eip1559_tx, make_legacy_tx

from evm import StateDB
from execution import (
    BlockEnvironment,
    ChainConfig,
    FeeModel,
    InsufficientBalanceError,
    IntrinsicGasTooLowError,
    NonceTooHighError,
    NonceTooLowError,
    validate_transaction,
)
from execution.exceptions import FeeRuleViolationError
from zk import ProofType, ZKProof, ZKVerifierRegistry
from execution import EIP1559Transaction, ZKTransaction


class _AcceptVerifier:
    def verify(self, proof: ZKProof, public_inputs: list[object]) -> bool:
        return proof.data == b"ok"


class TransactionValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = StateDB()
        self.state.set_balance(SENDER_ONE, 10_000_000)
        self.block_env = BlockEnvironment(
            block_number=1,
            timestamp=1_700_000_000,
            gas_limit=1_000_000,
            coinbase=addr("0xcccccccccccccccccccccccccccccccccccccccc"),
            base_fee=10,
            chain_id=1,
        )

    def test_valid_eip1559_transaction_recovers_sender(self) -> None:
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x1111111111111111111111111111111111111111"),
            gas_limit=50_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=20,
        )
        validated = validate_transaction(transaction, self.state, self.block_env, ChainConfig())
        self.assertEqual(validated.sender, SENDER_ONE)
        self.assertEqual(validated.intrinsic_gas, transaction.intrinsic_gas())
        self.assertEqual(validated.pricing.effective_gas_price, 12)

    def test_nonce_too_low_is_rejected(self) -> None:
        self.state.increment_nonce(SENDER_ONE)
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x2222222222222222222222222222222222222222"),
        )
        with self.assertRaises(NonceTooLowError):
            validate_transaction(
                transaction,
                self.state,
                self.block_env,
                ChainConfig(fee_model=FeeModel.LEGACY),
            )

    def test_nonce_too_high_is_rejected(self) -> None:
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            1,
            addr("0x3333333333333333333333333333333333333333"),
        )
        with self.assertRaises(NonceTooHighError):
            validate_transaction(
                transaction,
                self.state,
                self.block_env,
                ChainConfig(fee_model=FeeModel.LEGACY),
            )

    def test_balance_check_uses_max_fee_for_affordability(self) -> None:
        self.state.set_balance(SENDER_ONE, 100_000)
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x4444444444444444444444444444444444444444"),
            gas_limit=21_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=100,
        )
        with self.assertRaises(InsufficientBalanceError):
            validate_transaction(transaction, self.state, self.block_env, ChainConfig())

    def test_intrinsic_gas_too_low_is_rejected(self) -> None:
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x5555555555555555555555555555555555555555"),
            data=b"\xff" * 4,
            gas_limit=21_000,
        )
        with self.assertRaises(IntrinsicGasTooLowError):
            validate_transaction(
                transaction,
                self.state,
                self.block_env,
                ChainConfig(fee_model=FeeModel.LEGACY),
            )

    def test_legacy_gas_price_below_base_fee_is_rejected(self) -> None:
        transaction = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x6666666666666666666666666666666666666666"),
            gas_price=9,
        )
        with self.assertRaises(FeeRuleViolationError):
            validate_transaction(transaction, self.state, self.block_env, ChainConfig())

    def test_zk_transaction_can_be_verified_pre_execution(self) -> None:
        registry = ZKVerifierRegistry()
        registry.register(ProofType.GROTH16, _AcceptVerifier())
        transaction = ZKTransaction(
            base_tx=EIP1559Transaction(
                chain_id=1,
                nonce=0,
                max_priority_fee_per_gas=1,
                max_fee_per_gas=20,
                gas_limit=300_000,
                to=addr("0x7777777777777777777777777777777777777777"),
                value=0,
                data=b"",
            ),
            proof=ZKProof(ProofType.GROTH16, b"ok"),
        ).sign(PRIVATE_KEY_ONE)
        validated = validate_transaction(transaction, self.state, self.block_env, ChainConfig(), registry)
        self.assertTrue(validated.zk_verified)
        self.assertEqual(validated.zk_verification_gas, ChainConfig().zk_gas_model.verification_gas(transaction.proof))


if __name__ == "__main__":
    unittest.main()
