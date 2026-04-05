from __future__ import annotations

from transactions import Transaction, ZKTransaction
from zk import ZKGasModel


def calculate_intrinsic_gas(transaction: Transaction) -> int:
    if isinstance(transaction, ZKTransaction):
        return transaction.base_tx.intrinsic_gas()
    return transaction.intrinsic_gas()


def calculate_zk_verification_gas(transaction: Transaction, gas_model: ZKGasModel) -> int:
    if not isinstance(transaction, ZKTransaction):
        return 0
    return gas_model.verification_gas(transaction.proof)
