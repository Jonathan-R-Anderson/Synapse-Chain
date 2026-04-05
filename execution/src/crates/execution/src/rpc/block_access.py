from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from evm import Interpreter, StateDB
from execution import Block, BlockBuilder, BlockEnvironment, ChainConfig, ExecutionPayload, Receipt, apply_transaction
from execution.result import TransactionExecutionResult
from execution.transaction import Transaction
from primitives import Address, Hash
from zk import ZKVerifierRegistry

from .compat import CompatibilityConfig
from .types import BlockSelector, EARLIEST, LATEST, PENDING, parse_block_selector
from .txpool_access import PendingTransaction, TxPool


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    transaction: Transaction
    sender: Address
    block_hash: Hash
    block_number: int
    transaction_index: int
    receipt: Receipt
    execution_result: TransactionExecutionResult
    log_index_start: int


@dataclass(frozen=True, slots=True)
class BlockRecord:
    block: Block
    post_state: StateDB
    total_difficulty: int
    size: int
    transaction_records: tuple[TransactionRecord, ...]


@dataclass(frozen=True, slots=True)
class PendingPreview:
    block: Block
    state: StateDB
    total_difficulty: int
    size: int
    transaction_results: tuple[TransactionExecutionResult, ...]
    pending_transactions: tuple[PendingTransaction, ...]
    block_env: BlockEnvironment


class ExecutionNode:
    """Minimal canonical-chain and txpool facade for the execution RPC."""

    def __init__(
        self,
        *,
        chain_config: ChainConfig | None = None,
        compat_config: CompatibilityConfig | None = None,
        state: StateDB | None = None,
        verifier_registry: ZKVerifierRegistry | None = None,
        genesis_timestamp: int = 0,
    ) -> None:
        self.chain_config = ChainConfig() if chain_config is None else chain_config
        self.compat_config = CompatibilityConfig() if compat_config is None else compat_config
        self.verifier_registry = verifier_registry
        self.block_builder = BlockBuilder(self.chain_config)
        self.txpool = TxPool(replacement_bump_percent=self.compat_config.replacement_bump_percent)
        self._blocks_by_number: dict[int, BlockRecord] = {}
        self._blocks_by_hash: dict[str, BlockRecord] = {}
        self._transactions_by_hash: dict[str, TransactionRecord] = {}

        genesis_state = StateDB() if state is None else state.clone()
        genesis_payload = ExecutionPayload(receipts=(), gas_used=0, state=genesis_state.clone())
        genesis_block = self.block_builder.build_block(
            parent_block=None,
            transactions=(),
            execution_result=genesis_payload,
            timestamp=genesis_timestamp,
            gas_limit=self.compat_config.block_gas_limit,
            beneficiary=self.compat_config.default_coinbase,
            extra_data=self.compat_config.extra_data,
        )
        genesis_record = BlockRecord(
            block=genesis_block,
            post_state=genesis_state,
            total_difficulty=genesis_block.header.difficulty,
            size=len(genesis_block.serialize()),
            transaction_records=(),
        )
        self._store_block(genesis_record)

    @property
    def head(self) -> BlockRecord:
        return self._blocks_by_number[max(self._blocks_by_number)]

    def _store_block(self, record: BlockRecord) -> None:
        self._blocks_by_number[record.block.header.number] = record
        self._blocks_by_hash[record.block.hash().to_hex()] = record
        for tx_record in record.transaction_records:
            self._transactions_by_hash[tx_record.transaction.tx_hash().to_hex()] = tx_record

    def block_by_number(self, number: int) -> BlockRecord | None:
        return self._blocks_by_number.get(number)

    def block_by_hash(self, block_hash: Hash | str) -> BlockRecord | None:
        key = block_hash if isinstance(block_hash, str) else block_hash.to_hex()
        return self._blocks_by_hash.get(key)

    def block_by_selector(self, selector: BlockSelector | object | None) -> BlockRecord | PendingPreview | None:
        normalized = parse_block_selector(selector)
        if normalized.tag == LATEST:
            return self.head
        if normalized.tag == EARLIEST:
            return self._blocks_by_number[0]
        if normalized.tag == PENDING:
            return self.build_pending_preview()
        assert normalized.number is not None
        return self.block_by_number(normalized.number)

    def transaction_by_hash(self, tx_hash: Hash | str) -> TransactionRecord | None:
        key = tx_hash if isinstance(tx_hash, str) else tx_hash.to_hex()
        return self._transactions_by_hash.get(key)

    def pending_by_hash(self, tx_hash: Hash | str) -> PendingTransaction | None:
        key = tx_hash if isinstance(tx_hash, str) else tx_hash.to_hex()
        return self.txpool.get_by_hash(key)

    def _pending_block_env(self) -> BlockEnvironment:
        parent = self.head.block.header
        next_number = parent.number + 1
        next_timestamp = self.compat_config.next_timestamp(parent.timestamp)
        next_block = self.block_builder.build_block(
            parent_block=self.head.block,
            transactions=(),
            execution_result=ExecutionPayload(receipts=(), gas_used=0, state=self.head.post_state.clone()),
            timestamp=next_timestamp,
            gas_limit=parent.gas_limit,
            beneficiary=self.compat_config.default_coinbase,
            extra_data=self.compat_config.extra_data,
        )
        return BlockEnvironment.from_block(next_block, self.chain_config)

    def build_pending_preview(
        self,
        *,
        max_transactions: int | None = None,
        exclude: Callable[[PendingTransaction], bool] | None = None,
    ) -> PendingPreview:
        pending_env = self._pending_block_env()
        working_state = self.head.post_state.clone()
        interpreter = Interpreter(state=working_state)
        included: list[PendingTransaction] = []
        results: list[TransactionExecutionResult] = []
        cumulative_gas = 0

        for pending in self.txpool.ordered():
            if exclude is not None and exclude(pending):
                continue
            if max_transactions is not None and len(included) >= max_transactions:
                break
            if int(pending.transaction.gas_limit) > pending_env.gas_limit - cumulative_gas:
                continue
            try:
                result = apply_transaction(
                    state=working_state,
                    transaction=pending.transaction,
                    block_env=pending_env,
                    chain_config=self.chain_config,
                    verifier_registry=self.verifier_registry,
                    interpreter=interpreter,
                    cumulative_gas_used_before=cumulative_gas,
                )
            except Exception:
                continue
            included.append(pending)
            results.append(result)
            cumulative_gas += result.gas_used

        payload = ExecutionPayload(
            receipts=tuple(result.receipt for result in results if result.receipt is not None),
            gas_used=cumulative_gas,
            state=working_state,
            transaction_results=tuple(results),
        )
        block = self.block_builder.build_block(
            parent_block=self.head.block,
            transactions=tuple(pending.transaction for pending in included),
            execution_result=payload,
            timestamp=pending_env.timestamp,
            gas_limit=pending_env.gas_limit,
            beneficiary=pending_env.coinbase,
            extra_data=self.compat_config.extra_data,
        )
        return PendingPreview(
            block=block,
            state=working_state,
            total_difficulty=self.head.total_difficulty + block.header.difficulty,
            size=len(block.serialize()),
            transaction_results=tuple(results),
            pending_transactions=tuple(included),
            block_env=pending_env,
        )

    def append_pending_block(self, *, max_transactions: int | None = None) -> BlockRecord | None:
        preview = self.build_pending_preview(max_transactions=max_transactions)
        if not preview.pending_transactions:
            return None
        log_index_start = 0
        tx_records: list[TransactionRecord] = []
        block_hash = preview.block.hash()
        for index, result in enumerate(preview.transaction_results):
            receipt = result.receipt
            assert receipt is not None
            tx_records.append(
                TransactionRecord(
                    transaction=result.transaction,
                    sender=result.sender,
                    block_hash=block_hash,
                    block_number=preview.block.header.number,
                    transaction_index=index,
                    receipt=receipt,
                    execution_result=result,
                    log_index_start=log_index_start,
                )
            )
            log_index_start += len(receipt.logs)
        record = BlockRecord(
            block=preview.block,
            post_state=preview.state.clone(),
            total_difficulty=preview.total_difficulty,
            size=preview.size,
            transaction_records=tuple(tx_records),
        )
        self._store_block(record)
        self.txpool.clear_included(tuple(pending.transaction for pending in preview.pending_transactions))
        return record
