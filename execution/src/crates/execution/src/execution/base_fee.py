from __future__ import annotations


DEFAULT_BASE_FEE_MAX_CHANGE_DENOMINATOR = 8
DEFAULT_ELASTICITY_MULTIPLIER = 2
DEFAULT_GAS_LIMIT_BOUND_DIVISOR = 1024
DEFAULT_INITIAL_BASE_FEE = 1_000_000_000


def compute_gas_target(gas_limit: int, elasticity_multiplier: int = DEFAULT_ELASTICITY_MULTIPLIER) -> int:
    if gas_limit < 0:
        raise ValueError("gas_limit must be non-negative")
    if elasticity_multiplier < 1:
        raise ValueError("elasticity_multiplier must be positive")
    return gas_limit // elasticity_multiplier


def compute_next_base_fee(
    parent_base_fee: int,
    parent_gas_used: int,
    parent_gas_target: int,
    *,
    base_fee_max_change_denominator: int = DEFAULT_BASE_FEE_MAX_CHANGE_DENOMINATOR,
) -> int:
    """Compute the child block base fee using the EIP-1559 adjustment rule."""

    if parent_base_fee < 0:
        raise ValueError("parent_base_fee must be non-negative")
    if parent_gas_used < 0:
        raise ValueError("parent_gas_used must be non-negative")
    if parent_gas_target <= 0:
        raise ValueError("parent_gas_target must be positive")
    if base_fee_max_change_denominator <= 0:
        raise ValueError("base_fee_max_change_denominator must be positive")

    if parent_gas_used == parent_gas_target:
        return parent_base_fee

    if parent_gas_used > parent_gas_target:
        gas_used_delta = parent_gas_used - parent_gas_target
        base_fee_delta = max(
            (parent_base_fee * gas_used_delta) // parent_gas_target // base_fee_max_change_denominator,
            1,
        )
        return parent_base_fee + base_fee_delta

    gas_used_delta = parent_gas_target - parent_gas_used
    base_fee_delta = (parent_base_fee * gas_used_delta) // parent_gas_target // base_fee_max_change_denominator
    return max(parent_base_fee - base_fee_delta, 0)
