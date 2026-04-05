from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from primitives import Address, Hash

from .base_fee import compute_gas_target, compute_next_base_fee
from .block import Block, BlockHeader, ChainConfig, FeeModel
from .block_validator import BlockValidator
from .result import BlockExecutionResult, ExecutionPayload
from .trie import compute_logs_bloom, compute_ommers_hash, compute_receipts_root, compute_transactions_root
from .transaction import Transaction


def _normalize_execution_payload(execution_result: ExecutionPayload | BlockExecutionResult) -> ExecutionPayload:
    if isinstance(execution_result, BlockExecutionResult):
        return execution_result.to_execution_payload()
    return execution_result


@dataclass(slots=True)
class BlockBuilder:
    chain_config: ChainConfig = field(default_factory=ChainConfig)
    validator: BlockValidator = field(init=False)

    def __post_init__(self) -> None:
        self.validator = BlockValidator(self.chain_config)

    def build_block(
        self,
        *,
        parent_block: Block | BlockHeader | None,
        transactions: Sequence[Transaction],
        execution_result: ExecutionPayload | BlockExecutionResult,
        timestamp: int,
        gas_limit: int,
        beneficiary: Address,
        extra_data: bytes = b"",
        ommers: Sequence[BlockHeader] = (),
        difficulty: int = 0,
        mix_hash: Hash | None = None,
        nonce: bytes = bytes(8),
    ) -> Block:
        payload = _normalize_execution_payload(execution_result)
        transactions = tuple(transactions)
        receipts = payload.receipts
        if len(receipts) != len(transactions):
            raise ValueError("execution_result receipts must align one-for-one with transactions")

        parent_header = parent_block.header if isinstance(parent_block, Block) else parent_block
        parent_hash = Hash.zero() if parent_header is None else parent_header.hash()
        number = 0 if parent_header is None else parent_header.number + 1

        if self.chain_config.fee_model is FeeModel.EIP1559:
            if parent_header is None:
                base_fee = self.chain_config.initial_base_fee_per_gas
            else:
                parent_base_fee = (
                    self.chain_config.initial_base_fee_per_gas
                    if parent_header.base_fee is None
                    else parent_header.base_fee
                )
                base_fee = compute_next_base_fee(
                    parent_base_fee,
                    parent_header.gas_used,
                    compute_gas_target(parent_header.gas_limit, self.chain_config.elasticity_multiplier),
                    base_fee_max_change_denominator=self.chain_config.base_fee_max_change_denominator,
                )
        else:
            base_fee = None

        header = BlockHeader(
            parent_hash=parent_hash,
            ommers_hash=compute_ommers_hash(tuple(ommers)),
            coinbase=beneficiary,
            state_root=payload.resolved_state_root(),
            transactions_root=compute_transactions_root(transactions),
            receipts_root=compute_receipts_root(receipts),
            logs_bloom=payload.resolved_logs_bloom() or compute_logs_bloom(receipts),
            difficulty=difficulty,
            number=number,
            gas_limit=gas_limit,
            gas_used=payload.gas_used,
            timestamp=timestamp,
            extra_data=extra_data,
            mix_hash=Hash.zero() if mix_hash is None else mix_hash,
            nonce=nonce,
            base_fee=base_fee,
        )
        block = Block(header=header, transactions=transactions, receipts=receipts, ommers=tuple(ommers))
        self.validator.validate_block_structure(block, parent_block=parent_block, execution_payload=payload)
        return block
