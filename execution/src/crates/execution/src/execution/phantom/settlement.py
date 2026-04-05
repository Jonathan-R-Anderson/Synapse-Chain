from __future__ import annotations

import logging
from dataclasses import dataclass

from evm import StateDB
from execution.block import Block, ChainConfig
from execution.block_builder import BlockBuilder
from execution.result import ExecutionPayload
from primitives import Address, Hash

from .errors import MempoolError
from .manager import ChannelManager
from .models import PhantomReceipt, SettlementTransaction
from .signing import verify_signature


LOGGER = logging.getLogger("execution.phantom.settlement")


@dataclass(frozen=True, slots=True)
class PendingSettlementTransaction:
    transaction: SettlementTransaction
    insertion_id: int


class PhantomMempool:
    """Nonce-ordered settlement mempool for on-chain phantom operations."""

    def __init__(self) -> None:
        self._by_hash: dict[str, PendingSettlementTransaction] = {}
        self._by_sender_nonce: dict[tuple[Address, int], PendingSettlementTransaction] = {}
        self._next_insertion_id = 0

    def __len__(self) -> int:
        return len(self._by_hash)

    def get_by_sender_nonce(self, sender: Address, nonce: int) -> PendingSettlementTransaction | None:
        return self._by_sender_nonce.get((sender, nonce))

    def pending_nonce(self, sender: Address, confirmed_nonce: int) -> int:
        next_nonce = confirmed_nonce
        while (sender, next_nonce) in self._by_sender_nonce:
            next_nonce += 1
        return next_nonce

    def ordered(self) -> tuple[PendingSettlementTransaction, ...]:
        return tuple(sorted(self._by_hash.values(), key=lambda item: item.insertion_id))

    def add(self, transaction: SettlementTransaction, *, state: StateDB) -> PendingSettlementTransaction:
        if transaction.signature is None:
            raise MempoolError("settlement transaction must be signed")
        verify_signature(transaction.signing_hash(), transaction.signature, transaction.sender)
        tx_hash = transaction.tx_hash().to_hex()
        if tx_hash in self._by_hash:
            raise MempoolError(f"duplicate settlement transaction {tx_hash}")
        confirmed_nonce = state.get_nonce(transaction.sender)
        expected_nonce = self.pending_nonce(transaction.sender, confirmed_nonce)
        if transaction.nonce != expected_nonce:
            raise MempoolError(
                f"invalid settlement nonce for {transaction.sender.to_hex()}: expected {expected_nonce}, got {transaction.nonce}"
            )
        pending = PendingSettlementTransaction(transaction=transaction, insertion_id=self._next_insertion_id)
        self._next_insertion_id += 1
        self._by_hash[tx_hash] = pending
        self._by_sender_nonce[(transaction.sender, transaction.nonce)] = pending
        return pending

    def remove(self, transaction: SettlementTransaction) -> None:
        tx_hash = transaction.tx_hash().to_hex()
        pending = self._by_hash.pop(tx_hash, None)
        if pending is None:
            return
        self._by_sender_nonce.pop((pending.transaction.sender, pending.transaction.nonce), None)

    def clear(self, transactions: tuple[SettlementTransaction, ...]) -> None:
        for transaction in transactions:
            self.remove(transaction)


@dataclass(frozen=True, slots=True)
class PhantomBlockRecord:
    block: Block
    post_state: StateDB
    total_difficulty: int
    size: int
    transactions: tuple[SettlementTransaction, ...]
    receipts: tuple[PhantomReceipt, ...]


class PhantomSettlementChain:
    """Block-timeline settlement layer for phantom channel opens, closes, and disputes."""

    def __init__(
        self,
        manager: ChannelManager,
        *,
        state: StateDB | None = None,
        chain_config: ChainConfig | None = None,
        genesis_timestamp: int = 0,
    ) -> None:
        self.manager = manager
        self.state = StateDB() if state is None else state.clone()
        self.chain_config = ChainConfig() if chain_config is None else chain_config
        self.block_builder = BlockBuilder(self.chain_config)
        self.mempool = PhantomMempool()
        self._records_by_number: dict[int, PhantomBlockRecord] = {}
        self._records_by_hash: dict[str, PhantomBlockRecord] = {}
        genesis_payload = ExecutionPayload(receipts=(), gas_used=0, state=self.state.clone())
        genesis_block = self.block_builder.build_block(
            parent_block=None,
            transactions=(),
            execution_result=genesis_payload,
            timestamp=genesis_timestamp,
            gas_limit=30_000_000,
            beneficiary=Address.zero(),
        )
        self._store_block(
            PhantomBlockRecord(
                block=genesis_block,
                post_state=self.state.clone(),
                total_difficulty=genesis_block.header.difficulty,
                size=len(genesis_block.serialize()),
                transactions=(),
                receipts=(),
            )
        )

    @property
    def head(self) -> PhantomBlockRecord:
        return self._records_by_number[max(self._records_by_number)]

    def _store_block(self, record: PhantomBlockRecord) -> None:
        self._records_by_number[record.block.header.number] = record
        self._records_by_hash[record.block.hash().to_hex()] = record
        self.state = record.post_state.clone()

    def submit(self, transaction: SettlementTransaction) -> PendingSettlementTransaction:
        return self.mempool.add(transaction, state=self.head.post_state)

    def receipt_by_tx_hash(self, tx_hash: Hash | str) -> PhantomReceipt | None:
        key = tx_hash if isinstance(tx_hash, str) else tx_hash.to_hex()
        for record in self._records_by_number.values():
            for receipt in record.receipts:
                if receipt.tx_hash.to_hex() == key:
                    return receipt
        return None

    def mine_pending_block(self, *, max_transactions: int | None = None) -> PhantomBlockRecord | None:
        if not self.mempool.ordered():
            return None
        next_block_number = self.head.block.header.number + 1
        next_timestamp = self.head.block.header.timestamp + 1
        working_state = self.head.post_state.clone()
        included: list[SettlementTransaction] = []
        receipts: list[PhantomReceipt] = []
        discarded: list[SettlementTransaction] = []

        for pending in self.mempool.ordered():
            if max_transactions is not None and len(included) >= max_transactions:
                break
            state_snapshot = working_state.snapshot()
            manager_snapshot = self.manager.snapshot()
            try:
                channel = self.manager.apply_operation(
                    pending.transaction.operation,
                    chain_state=working_state,
                    current_block=next_block_number,
                    sender=pending.transaction.sender,
                )
                working_state.increment_nonce(pending.transaction.sender)
                included.append(pending.transaction)
                receipts.append(
                    PhantomReceipt(
                        tx_hash=pending.transaction.tx_hash(),
                        block_number=next_block_number,
                        sender=pending.transaction.sender,
                        success=True,
                        result=pending.transaction.operation.op_type.value,
                        channel_id=channel.channel_id,
                    )
                )
            except Exception as exc:
                discarded.append(pending.transaction)
                working_state.restore(state_snapshot)
                self.manager.restore(manager_snapshot)
                LOGGER.warning("discarded phantom settlement tx %s: %s", pending.transaction.tx_hash().to_hex(), exc)

        self.mempool.clear(tuple((*included, *discarded)))
        if not included:
            return None

        payload = ExecutionPayload(receipts=(), gas_used=0, state=working_state.clone())
        block = self.block_builder.build_block(
            parent_block=self.head.block,
            transactions=(),
            execution_result=payload,
            timestamp=next_timestamp,
            gas_limit=self.head.block.header.gas_limit,
            beneficiary=self.head.block.header.coinbase,
        )
        record = PhantomBlockRecord(
            block=block,
            post_state=working_state.clone(),
            total_difficulty=self.head.total_difficulty + block.header.difficulty,
            size=len(block.serialize()),
            transactions=tuple(included),
            receipts=tuple(receipts),
        )
        self._store_block(record)
        return record
