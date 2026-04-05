from __future__ import annotations

from dataclasses import dataclass

from execution import EIP1559Transaction, LegacyTransaction, ZKTransaction
from execution.transaction import Transaction
from primitives import Address

from .errors import AlreadyKnownError, ReplacementUnderpricedError


@dataclass(frozen=True, slots=True)
class PendingTransaction:
    transaction: Transaction
    sender: Address
    insertion_id: int

    @property
    def nonce(self) -> int:
        return int(self.transaction.nonce)

    @property
    def tx_hash(self) -> str:
        return self.transaction.tx_hash().to_hex()


def _replacement_effective_gas_price(transaction: Transaction, base_fee: int | None) -> tuple[int, int]:
    if isinstance(transaction, LegacyTransaction):
        gas_price = int(transaction.gas_price)
        return gas_price, gas_price
    if isinstance(transaction, ZKTransaction):
        transaction = transaction.base_tx
    assert isinstance(transaction, EIP1559Transaction)
    priority = int(transaction.max_priority_fee_per_gas)
    if base_fee is None:
        return int(transaction.max_fee_per_gas), priority
    effective = base_fee + min(priority, int(transaction.max_fee_per_gas) - base_fee)
    return effective, priority


class TxPool:
    def __init__(self, *, replacement_bump_percent: int = 10) -> None:
        self._replacement_bump_percent = replacement_bump_percent
        self._next_insertion_id = 0
        self._by_hash: dict[str, PendingTransaction] = {}
        self._by_sender_nonce: dict[tuple[Address, int], PendingTransaction] = {}

    def __len__(self) -> int:
        return len(self._by_hash)

    def contains_hash(self, tx_hash: str) -> bool:
        return tx_hash in self._by_hash

    def get_by_hash(self, tx_hash: str) -> PendingTransaction | None:
        return self._by_hash.get(tx_hash)

    def get_by_sender_nonce(self, sender: Address, nonce: int) -> PendingTransaction | None:
        return self._by_sender_nonce.get((sender, nonce))

    def pending_nonce(self, sender: Address, confirmed_nonce: int) -> int:
        next_nonce = confirmed_nonce
        while (sender, next_nonce) in self._by_sender_nonce:
            next_nonce += 1
        return next_nonce

    def ordered(self) -> tuple[PendingTransaction, ...]:
        return tuple(sorted(self._by_hash.values(), key=lambda item: item.insertion_id))

    def _replacement_allowed(
        self,
        existing: PendingTransaction,
        candidate: Transaction,
        *,
        base_fee: int | None,
    ) -> bool:
        old_effective, old_priority = _replacement_effective_gas_price(existing.transaction, base_fee)
        new_effective, new_priority = _replacement_effective_gas_price(candidate, base_fee)
        threshold = 100 + self._replacement_bump_percent
        return new_effective * 100 >= old_effective * threshold and new_priority * 100 >= old_priority * threshold

    def add(
        self,
        transaction: Transaction,
        *,
        sender: Address,
        base_fee: int | None,
    ) -> PendingTransaction:
        tx_hash = transaction.tx_hash().to_hex()
        if tx_hash in self._by_hash:
            raise AlreadyKnownError(tx_hash)
        key = (sender, int(transaction.nonce))
        existing = self._by_sender_nonce.get(key)
        if existing is None:
            pending = PendingTransaction(transaction=transaction, sender=sender, insertion_id=self._next_insertion_id)
            self._next_insertion_id += 1
        else:
            if not self._replacement_allowed(existing, transaction, base_fee=base_fee):
                raise ReplacementUnderpricedError(tx_hash)
            del self._by_hash[existing.tx_hash]
            pending = PendingTransaction(transaction=transaction, sender=sender, insertion_id=existing.insertion_id)
        self._by_hash[pending.tx_hash] = pending
        self._by_sender_nonce[key] = pending
        return pending

    def remove(self, transaction: Transaction) -> None:
        tx_hash = transaction.tx_hash().to_hex()
        pending = self._by_hash.pop(tx_hash, None)
        if pending is None:
            return
        self._by_sender_nonce.pop((pending.sender, pending.nonce), None)

    def clear_included(self, transactions: tuple[Transaction, ...]) -> None:
        for transaction in transactions:
            self.remove(transaction)
