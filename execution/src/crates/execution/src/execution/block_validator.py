from __future__ import annotations

from dataclasses import dataclass, field

from .base_fee import compute_gas_target, compute_next_base_fee
from .block import Block, BlockHeader, ChainConfig, FeeModel
from .exceptions import BlockValidationError
from .result import ExecutionPayload
from .trie import compute_logs_bloom, compute_ommers_hash, compute_receipts_root, compute_transactions_root


def _parent_header(parent_block: Block | BlockHeader | None) -> BlockHeader | None:
    if parent_block is None:
        return None
    return parent_block.header if isinstance(parent_block, Block) else parent_block


@dataclass(slots=True)
class BlockValidator:
    chain_config: ChainConfig = field(default_factory=ChainConfig)

    def validate_header(self, header: BlockHeader) -> None:
        try:
            header.validate()
        except ValueError as exc:
            raise BlockValidationError(str(exc)) from exc
        if len(header.extra_data) > self.chain_config.max_extra_data_bytes:
            raise BlockValidationError(
                f"extra_data exceeds the configured limit of {self.chain_config.max_extra_data_bytes} bytes"
            )

    def validate_roots(self, block: Block, execution_payload: ExecutionPayload | None = None) -> None:
        if block.header.transactions_root != compute_transactions_root(block.transactions):
            raise BlockValidationError("header transactions_root does not match the transaction list")
        if block.header.receipts_root != compute_receipts_root(block.receipts):
            raise BlockValidationError("header receipts_root does not match the receipt list")
        if block.header.logs_bloom != compute_logs_bloom(block.receipts):
            raise BlockValidationError("header logs_bloom does not match the receipt blooms")
        if block.header.ommers_hash != compute_ommers_hash(block.ommers):
            raise BlockValidationError("header ommers_hash does not match the ommer list")
        if execution_payload is not None and block.header.state_root != execution_payload.resolved_state_root():
            raise BlockValidationError("header state_root does not match the provided execution result")

    def validate_gas(self, block: Block, execution_payload: ExecutionPayload | None = None) -> None:
        if block.header.gas_used > block.header.gas_limit:
            raise BlockValidationError("block gas_used exceeds gas_limit")
        if execution_payload is not None and block.header.gas_used != execution_payload.gas_used:
            raise BlockValidationError("header gas_used does not match the execution result gas usage")
        if not block.receipts:
            if block.header.gas_used != 0:
                raise BlockValidationError("blocks without receipts must have gas_used=0")
            return
        if len(block.receipts) != len(block.transactions):
            raise BlockValidationError("receipt count must match transaction count")
        cumulative = 0
        for receipt in block.receipts:
            cumulative += receipt.gas_used
            if receipt.cumulative_gas_used != cumulative:
                raise BlockValidationError("receipt cumulative_gas_used sequence is inconsistent")
        if cumulative != block.header.gas_used:
            raise BlockValidationError("header gas_used does not match receipt gas totals")

    def validate_against_parent(self, block: Block, parent_block: Block | BlockHeader | None) -> None:
        parent_header = _parent_header(parent_block)
        if parent_header is None:
            return
        if block.header.parent_hash != parent_header.hash():
            raise BlockValidationError("block parent_hash does not match the supplied parent header hash")
        if block.header.number != parent_header.number + 1:
            raise BlockValidationError("child block number must increment the parent block number by exactly one")
        if block.header.timestamp < parent_header.timestamp:
            raise BlockValidationError("block timestamp must be monotonic relative to its parent")
        gas_limit_delta = abs(block.header.gas_limit - parent_header.gas_limit)
        max_delta = max(parent_header.gas_limit // self.chain_config.gas_limit_bound_divisor, 1)
        if gas_limit_delta > max_delta:
            raise BlockValidationError("block gas_limit changes exceed the configured parent-bound delta")

    def validate_base_fee(self, block: Block, parent_block: Block | BlockHeader | None) -> None:
        if self.chain_config.fee_model is FeeModel.LEGACY:
            if block.header.base_fee is not None:
                raise BlockValidationError("legacy-fee blocks must not include base_fee_per_gas")
            return

        if block.header.base_fee is None:
            raise BlockValidationError("EIP-1559 blocks must include base_fee_per_gas")

        parent_header = _parent_header(parent_block)
        if parent_header is None:
            if block.header.base_fee != self.chain_config.initial_base_fee_per_gas:
                raise BlockValidationError("genesis base_fee_per_gas does not match the chain configuration")
            return

        parent_base_fee = (
            self.chain_config.initial_base_fee_per_gas
            if parent_header.base_fee is None
            else parent_header.base_fee
        )
        expected = compute_next_base_fee(
            parent_base_fee,
            parent_header.gas_used,
            compute_gas_target(parent_header.gas_limit, self.chain_config.elasticity_multiplier),
            base_fee_max_change_denominator=self.chain_config.base_fee_max_change_denominator,
        )
        if block.header.base_fee != expected:
            raise BlockValidationError(
                f"block base_fee_per_gas {block.header.base_fee} does not match expected value {expected}"
            )

    def validate_block_structure(
        self,
        block: Block,
        *,
        parent_block: Block | BlockHeader | None = None,
        execution_payload: ExecutionPayload | None = None,
    ) -> None:
        self.validate_header(block.header)
        self.validate_against_parent(block, parent_block)
        self.validate_base_fee(block, parent_block)
        self.validate_gas(block, execution_payload)
        self.validate_roots(block, execution_payload)
