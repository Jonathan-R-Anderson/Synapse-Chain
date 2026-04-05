from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from execution import ChainConfig
from primitives import Address


def _default_timestamp_source() -> int:
    return int(time.time())


@dataclass(frozen=True, slots=True)
class CompatibilityConfig:
    """Execution-RPC behavior knobs aimed at Ethereum tooling compatibility."""

    client_version: str = "python-execution/phase8"
    network_version: str | None = None
    mining_mode: str = "instant"
    default_gas_price: int = 1
    default_max_priority_fee_per_gas: int = 1
    default_call_gas: int = 30_000_000
    gas_estimation_cap: int = 30_000_000
    fee_history_max_blocks: int = 1_024
    replacement_bump_percent: int = 10
    block_gas_limit: int = 30_000_000
    default_coinbase: Address = field(default_factory=Address.zero)
    extra_data: bytes = b"python-rpc"
    cors_allow_origin: str = "*"
    local_accounts: tuple[Address, ...] = ()
    timestamp_source: Callable[[], int] = field(
        default=_default_timestamp_source,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if self.mining_mode not in {"instant", "mempool"}:
            raise ValueError("mining_mode must be 'instant' or 'mempool'")
        if self.default_gas_price < 0:
            raise ValueError("default_gas_price must be non-negative")
        if self.default_max_priority_fee_per_gas < 0:
            raise ValueError("default_max_priority_fee_per_gas must be non-negative")
        if self.default_call_gas < 21_000:
            raise ValueError("default_call_gas must be at least 21000")
        if self.gas_estimation_cap < 21_000:
            raise ValueError("gas_estimation_cap must be at least 21000")
        if self.replacement_bump_percent < 0:
            raise ValueError("replacement_bump_percent must be non-negative")
        if self.block_gas_limit < 21_000:
            raise ValueError("block_gas_limit must be at least 21000")
        if len(self.extra_data) > 32:
            raise ValueError("extra_data must be at most 32 bytes")
        object.__setattr__(self, "extra_data", bytes(self.extra_data))
        object.__setattr__(self, "local_accounts", tuple(self.local_accounts))

    def resolved_network_version(self, chain_config: ChainConfig) -> str:
        return str(chain_config.chain_id) if self.network_version is None else self.network_version

    def suggested_gas_price(self, base_fee: int | None) -> int:
        if base_fee is None:
            return self.default_gas_price
        return max(self.default_gas_price, base_fee + self.default_max_priority_fee_per_gas)

    def next_timestamp(self, parent_timestamp: int) -> int:
        return max(int(self.timestamp_source()), parent_timestamp + 1)
