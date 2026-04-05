from __future__ import annotations

from typing import Protocol, Sequence

from evm import StateDB
from primitives import Hash, U256
from state import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT, MerklePatriciaTrie, MptStateBackend
from state.account import Account as TrieAccount
from state.constants import EMPTY_TRIE_ROOT as STATE_EMPTY_TRIE_ROOT
from state.serialization import serialize_account

from .hashing import keccak256_hash
from .receipt import Receipt
from .rlp_codec import rlp_encode
from .transaction import Transaction


class _HeaderFields(Protocol):
    def to_ordered_field_list(self) -> list[object]:
        ...


EMPTY_OMMERS_HASH = keccak256_hash(rlp_encode([]))


def _identity(data: bytes) -> bytes:
    return data


def compute_transactions_root(transactions: Sequence[Transaction]) -> Hash:
    trie = MerklePatriciaTrie(key_transform=_identity)
    for index, transaction in enumerate(transactions):
        trie.update(rlp_encode(index), transaction.encode())
    return trie.commit()


def compute_receipts_root(receipts: Sequence[Receipt]) -> Hash:
    trie = MerklePatriciaTrie(key_transform=_identity)
    for index, receipt in enumerate(receipts):
        trie.update(rlp_encode(index), receipt.rlp_encode())
    return trie.commit()


def compute_ommers_hash(ommers: Sequence[_HeaderFields]) -> Hash:
    return keccak256_hash(rlp_encode([ommer.to_ordered_field_list() for ommer in ommers]))


def compute_logs_bloom(receipts: Sequence[Receipt]) -> bytes:
    from .logs_bloom import combine_blooms

    if not receipts:
        return bytes(256)
    return combine_blooms(*(receipt.logs_bloom for receipt in receipts))


def compute_state_root(state: StateDB) -> Hash:
    """Derive an Ethereum-style state root from the in-memory EVM state database.

    The execution engine still uses `evm.StateDB`, so this adapter materializes the
    equivalent account/storage data into the trie-backed state model to preserve the
    account/storage commitment interface.
    """

    backend = MptStateBackend()
    for address, account in state.accounts():
        if account.is_empty:
            continue
        backend.create_account(address)
        backend.set_account(
            address,
            TrieAccount(
                nonce=U256(account.nonce),
                balance=U256(account.balance),
                code_hash=EMPTY_CODE_HASH,
                storage_root=STATE_EMPTY_TRIE_ROOT,
            ),
        )
        if account.code:
            backend.set_code(address, account.code)
        for slot, value in account.storage.items():
            backend.set_storage(address, U256(slot), U256(value))
    return backend.commit()


def compute_receipt_hash(receipt: Receipt) -> Hash:
    return keccak256_hash(receipt.rlp_encode())
