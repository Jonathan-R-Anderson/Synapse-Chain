from __future__ import annotations

from dataclasses import dataclass

from evm import StateDB
from primitives import Address
from transactions import EIP1559Transaction, LegacyTransaction, Transaction, TransactionSignatureError, ZKTransaction
from transactions.validation import ZKVerificationTiming
from zk import ZKVerifierRegistry

from .block import BlockEnvironment, ChainConfig
from .exceptions import (
    FeeRuleViolationError,
    InsufficientBalanceError,
    IntrinsicGasTooLowError,
    InvalidSignatureError,
    InvalidTransactionError,
    InvalidZKProofError,
    NonceTooHighError,
    NonceTooLowError,
    UnsupportedTransactionTypeError,
)
from .fee_market import FeeMarket, GasPricing
from .intrinsic_gas import calculate_intrinsic_gas, calculate_zk_verification_gas


@dataclass(frozen=True, slots=True)
class ValidatedTransaction:
    transaction: Transaction
    sender: Address
    intrinsic_gas: int
    zk_verification_gas: int
    total_pre_execution_gas: int
    pricing: GasPricing
    max_total_cost: int
    zk_verified: bool
    zk_verification_deferred: bool

def _validate_supported_type(transaction: Transaction, chain_config: ChainConfig) -> None:
    if isinstance(transaction, LegacyTransaction):
        if not chain_config.support_legacy_transactions:
            raise UnsupportedTransactionTypeError("legacy transactions are disabled by chain configuration")
        return
    if isinstance(transaction, EIP1559Transaction):
        if not chain_config.support_eip1559_transactions:
            raise UnsupportedTransactionTypeError("EIP-1559 transactions are disabled by chain configuration")
        return
    if isinstance(transaction, ZKTransaction):
        if not chain_config.support_zk_transactions:
            raise UnsupportedTransactionTypeError("ZK transactions are disabled by chain configuration")
        if not chain_config.support_eip1559_transactions:
            raise UnsupportedTransactionTypeError("ZK transactions require EIP-1559 transaction support")
        return
    raise UnsupportedTransactionTypeError("unsupported transaction type")


def _validate_chain_id(transaction: Transaction, chain_config: ChainConfig) -> None:
    if isinstance(transaction, LegacyTransaction):
        if transaction.chain_id is None:
            if not chain_config.allow_unprotected_legacy_transactions:
                raise InvalidTransactionError("unprotected legacy transactions are disabled by chain configuration")
            return
        if transaction.chain_id != chain_config.chain_id:
            raise InvalidTransactionError(
                f"legacy transaction chain_id {transaction.chain_id} does not match chain_id {chain_config.chain_id}"
            )
        return

    if transaction.chain_id != chain_config.chain_id:
        raise InvalidTransactionError(
            f"transaction chain_id {transaction.chain_id} does not match chain_id {chain_config.chain_id}"
        )


def _recover_sender(transaction: Transaction, chain_config: ChainConfig) -> Address:
    try:
        return transaction.sender(enforce_low_s=chain_config.enforce_low_s)
    except TransactionSignatureError as exc:
        raise InvalidSignatureError(str(exc)) from exc


def _validate_nonce(transaction: Transaction, sender: Address, state: StateDB) -> None:
    expected_nonce = state.get_nonce(sender)
    actual_nonce = int(transaction.nonce)
    if actual_nonce < expected_nonce:
        raise NonceTooLowError(sender, expected_nonce, actual_nonce)
    if actual_nonce > expected_nonce:
        raise NonceTooHighError(sender, expected_nonce, actual_nonce)


def _validate_zk_proof(
    transaction: ZKTransaction,
    verifier_registry: ZKVerifierRegistry | None,
) -> None:
    if verifier_registry is None:
        raise InvalidZKProofError("ZK transaction validation requires a verifier registry")
    if not verifier_registry.verify(transaction.proof, transaction.public_inputs):
        raise InvalidZKProofError("ZK proof verification failed")


def validate_transaction(
    transaction: Transaction,
    state: StateDB,
    block_env: BlockEnvironment,
    chain_config: ChainConfig,
    verifier_registry: ZKVerifierRegistry | None = None,
) -> ValidatedTransaction:
    _validate_supported_type(transaction, chain_config)
    _validate_chain_id(transaction, chain_config)
    sender = _recover_sender(transaction, chain_config)
    _validate_nonce(transaction, sender, state)

    fee_market = FeeMarket(chain_config)
    try:
        pricing = fee_market.pricing_for_transaction(transaction, block_env)
    except FeeRuleViolationError:
        raise
    except ValueError as exc:
        raise FeeRuleViolationError(str(exc)) from exc

    intrinsic_gas = calculate_intrinsic_gas(transaction)
    zk_verification_gas = calculate_zk_verification_gas(transaction, chain_config.zk_gas_model)
    total_pre_execution_gas = intrinsic_gas + zk_verification_gas
    gas_limit = int(transaction.gas_limit)
    if gas_limit < total_pre_execution_gas:
        raise IntrinsicGasTooLowError(
            f"gas limit {gas_limit} is below required pre-execution gas {total_pre_execution_gas}"
        )
    if gas_limit > block_env.gas_limit:
        raise FeeRuleViolationError("transaction gas limit exceeds the enclosing block gas limit")

    max_total_cost = pricing.max_gas_cost(gas_limit) + int(transaction.value)
    balance = state.get_balance(sender)
    if balance < max_total_cost:
        raise InsufficientBalanceError(sender, balance, max_total_cost)

    zk_verified = True
    zk_verification_deferred = False
    if isinstance(transaction, ZKTransaction):
        if chain_config.zk_verification_timing is ZKVerificationTiming.PRE_EXECUTION:
            _validate_zk_proof(transaction, verifier_registry)
        else:
            zk_verified = False
            zk_verification_deferred = True

    return ValidatedTransaction(
        transaction=transaction,
        sender=sender,
        intrinsic_gas=intrinsic_gas,
        zk_verification_gas=zk_verification_gas,
        total_pre_execution_gas=total_pre_execution_gas,
        pricing=pricing,
        max_total_cost=max_total_cost,
        zk_verified=zk_verified,
        zk_verification_deferred=zk_verification_deferred,
    )
