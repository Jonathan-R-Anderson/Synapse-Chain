from __future__ import annotations

from dataclasses import dataclass

from transactions import EIP1559Transaction, LegacyTransaction, Transaction, ZKTransaction

from .block import BlockEnvironment, ChainConfig, FeeModel
from .exceptions import FeeRuleViolationError


@dataclass(frozen=True, slots=True)
class GasPricing:
    max_upfront_gas_price: int
    effective_gas_price: int
    priority_fee_per_gas: int
    base_fee_per_gas: int

    def max_gas_cost(self, gas_limit: int) -> int:
        return self.max_upfront_gas_price * gas_limit

    def prepaid_gas_cost(self, gas_limit: int) -> int:
        return self.effective_gas_price * gas_limit

    def refund_amount(self, gas_units: int) -> int:
        return self.effective_gas_price * gas_units

    def tip_reward(self, gas_used: int) -> int:
        return self.priority_fee_per_gas * gas_used

    def base_fee_burn(self, gas_used: int) -> int:
        return self.base_fee_per_gas * gas_used


class FeeMarket:
    def __init__(self, chain_config: ChainConfig) -> None:
        self.chain_config = chain_config

    def pricing_for_transaction(self, transaction: Transaction, block_env: BlockEnvironment) -> GasPricing:
        if isinstance(transaction, ZKTransaction):
            transaction = transaction.base_tx

        if isinstance(transaction, LegacyTransaction):
            return self._legacy_pricing(transaction, block_env)
        if isinstance(transaction, EIP1559Transaction):
            return self._eip1559_pricing(transaction, block_env)
        raise FeeRuleViolationError("unsupported transaction type for fee pricing")

    def _legacy_pricing(self, transaction: LegacyTransaction, block_env: BlockEnvironment) -> GasPricing:
        gas_price = int(transaction.gas_price)
        if gas_price < 0:
            raise FeeRuleViolationError("legacy gas_price must be non-negative")
        if self.chain_config.fee_model is FeeModel.EIP1559:
            if block_env.base_fee is None:
                raise FeeRuleViolationError("base_fee is required for EIP-1559 blocks")
            if gas_price < block_env.base_fee:
                raise FeeRuleViolationError("legacy gas_price is below the block base fee")
            return GasPricing(
                max_upfront_gas_price=gas_price,
                effective_gas_price=gas_price,
                priority_fee_per_gas=gas_price - block_env.base_fee,
                base_fee_per_gas=block_env.base_fee,
            )
        return GasPricing(
            max_upfront_gas_price=gas_price,
            effective_gas_price=gas_price,
            priority_fee_per_gas=gas_price,
            base_fee_per_gas=0,
        )

    def _eip1559_pricing(self, transaction: EIP1559Transaction, block_env: BlockEnvironment) -> GasPricing:
        if block_env.base_fee is None:
            raise FeeRuleViolationError("base_fee is required for EIP-1559 transactions")
        max_priority = int(transaction.max_priority_fee_per_gas)
        max_fee = int(transaction.max_fee_per_gas)
        if max_fee < max_priority:
            raise FeeRuleViolationError("max_fee_per_gas must be at least max_priority_fee_per_gas")
        if max_fee < block_env.base_fee:
            raise FeeRuleViolationError("max_fee_per_gas is below the block base fee")
        priority = min(max_priority, max_fee - block_env.base_fee)
        effective = block_env.base_fee + priority
        return GasPricing(
            max_upfront_gas_price=max_fee,
            effective_gas_price=effective,
            priority_fee_per_gas=priority,
            base_fee_per_gas=block_env.base_fee,
        )
