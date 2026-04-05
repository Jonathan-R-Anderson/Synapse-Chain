from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from primitives import Address
from state import State
from zk import ZKGasModel, ZKVerifierRegistry

from .models import EIP1559Transaction, LegacyTransaction, Transaction, ZKTransaction


class TransactionValidationError(ValueError):
    pass


class ZKProofValidationError(TransactionValidationError):
    pass


class ZKVerificationTiming(str, Enum):
    PRE_EXECUTION = "pre_execution"
    DURING_EXECUTION = "during_execution"


@dataclass(frozen=True, slots=True)
class TransactionValidationResult:
    transaction: Transaction
    sender: Address
    intrinsic_gas: int
    zk_verified: bool
    zk_verification_deferred: bool = False

    def verify_zk(self, verifier_registry: ZKVerifierRegistry) -> "TransactionValidationResult":
        if not isinstance(self.transaction, ZKTransaction):
            return self
        if verifier_registry.verify(self.transaction.proof, list(self.transaction.public_inputs)):
            return TransactionValidationResult(
                transaction=self.transaction,
                sender=self.sender,
                intrinsic_gas=self.intrinsic_gas,
                zk_verified=True,
                zk_verification_deferred=False,
            )
        raise ZKProofValidationError("ZK proof verification failed")


@dataclass(slots=True)
class TransactionValidator:
    chain_id: int | None = None
    zk_verification_timing: ZKVerificationTiming = ZKVerificationTiming.PRE_EXECUTION
    zk_gas_model: ZKGasModel = field(default_factory=ZKGasModel)
    enforce_low_s: bool = True

    def _validate_chain_id(self, transaction: Transaction) -> None:
        if self.chain_id is None:
            return
        transaction_chain_id = transaction.chain_id if not isinstance(transaction, LegacyTransaction) or transaction.chain_id is not None else None
        if transaction_chain_id is not None and transaction_chain_id != self.chain_id:
            raise TransactionValidationError(
                f"transaction chain_id {transaction_chain_id} does not match validator chain_id {self.chain_id}"
            )

    def _validate_nonce(self, sender: Address, transaction: Transaction, state: State) -> None:
        account = state.get_account(sender)
        expected_nonce = 0 if account is None else int(account.nonce)
        if int(transaction.nonce) != expected_nonce:
            raise TransactionValidationError(
                f"invalid nonce for {sender.to_hex()}: expected {expected_nonce}, got {int(transaction.nonce)}"
            )

    def _validate_gas_limit(self, transaction: Transaction, intrinsic_gas: int) -> None:
        if int(transaction.gas_limit) < intrinsic_gas:
            raise TransactionValidationError(
                f"gas limit {int(transaction.gas_limit)} is below intrinsic gas {intrinsic_gas}"
            )

    def intrinsic_gas(self, transaction: Transaction) -> int:
        if isinstance(transaction, ZKTransaction):
            return transaction.intrinsic_gas() + self.zk_gas_model.verification_gas(transaction.proof)
        return transaction.intrinsic_gas()

    def validate(
        self,
        transaction: Transaction,
        state: State,
        verifier_registry: ZKVerifierRegistry | None = None,
    ) -> TransactionValidationResult:
        self._validate_chain_id(transaction)
        sender = transaction.sender(enforce_low_s=self.enforce_low_s)
        self._validate_nonce(sender, transaction, state)
        intrinsic_gas = self.intrinsic_gas(transaction)
        self._validate_gas_limit(transaction, intrinsic_gas)

        if isinstance(transaction, ZKTransaction):
            if self.zk_verification_timing is ZKVerificationTiming.PRE_EXECUTION:
                if verifier_registry is None:
                    raise ZKProofValidationError("a verifier registry is required for pre-execution ZK validation")
                if not verifier_registry.verify(transaction.proof, list(transaction.public_inputs)):
                    raise ZKProofValidationError("ZK proof verification failed")
                return TransactionValidationResult(
                    transaction=transaction,
                    sender=sender,
                    intrinsic_gas=intrinsic_gas,
                    zk_verified=True,
                    zk_verification_deferred=False,
                )

            return TransactionValidationResult(
                transaction=transaction,
                sender=sender,
                intrinsic_gas=intrinsic_gas,
                zk_verified=False,
                zk_verification_deferred=True,
            )

        return TransactionValidationResult(
            transaction=transaction,
            sender=sender,
            intrinsic_gas=intrinsic_gas,
            zk_verified=True,
            zk_verification_deferred=False,
        )
