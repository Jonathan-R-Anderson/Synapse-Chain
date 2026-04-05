from __future__ import annotations

from typing import Mapping, Sequence

from crypto import keccak256
from evm import StateDB
from execution import Receipt, Transaction, compute_receipts_root, compute_state_root, compute_transactions_root
from primitives import Address, Hash, U256
from state import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT, MerklePatriciaTrie
from state.serialization import serialize_storage_value, storage_trie_key

from .models import AccountFixture


def build_state_db(accounts: Sequence[AccountFixture]) -> StateDB:
    state = StateDB()
    for account in accounts:
        target = state.get_or_create_account(account.address)
        target.nonce = account.nonce
        target.balance = account.balance
        target.code = account.code
        for slot, value in account.storage:
            target.storage.set(int(slot), int(value))
        target.storage.commit()
    return state


def export_state_accounts(state: StateDB) -> tuple[AccountFixture, ...]:
    accounts: list[AccountFixture] = []
    for address, account in state.accounts():
        accounts.append(
            AccountFixture(
                address=address,
                nonce=account.nonce,
                balance=account.balance,
                code=account.code,
                storage=tuple((U256(slot), U256(value)) for slot, value in account.storage.items()),
            )
        )
    return tuple(accounts)


def compute_code_hash(code: bytes | bytearray | memoryview) -> Hash:
    raw = bytes(code)
    return EMPTY_CODE_HASH if not raw else keccak256(raw)


def compute_storage_root(storage: Mapping[int | U256, int | U256]) -> Hash:
    trie = MerklePatriciaTrie(key_transform=storage_trie_key)
    for key, value in sorted(storage.items(), key=lambda item: int(item[0])):
        normalized_value = value if isinstance(value, U256) else U256(int(value))
        if normalized_value.is_zero():
            continue
        trie.update(U256(int(key)).to_bytes(U256.BYTE_LENGTH), serialize_storage_value(normalized_value))
    return trie.commit()


def compute_state_root_from_accounts(accounts: Sequence[AccountFixture]) -> Hash:
    return compute_state_root(build_state_db(accounts))


def compute_receipts_root_from_receipts(receipts: Sequence[Receipt]) -> Hash:
    return compute_receipts_root(tuple(receipts))


def compute_transactions_root_from_transactions(transactions: Sequence[Transaction]) -> Hash:
    return compute_transactions_root(tuple(transactions))


def account_code_hashes(state: StateDB) -> dict[Address, Hash]:
    return {address: compute_code_hash(account.code) for address, account in state.accounts()}


def account_storage_roots(state: StateDB) -> dict[Address, Hash]:
    roots: dict[Address, Hash] = {}
    for address, account in state.accounts():
        storage = {slot: value for slot, value in account.storage.items()}
        roots[address] = EMPTY_TRIE_ROOT if not storage else compute_storage_root(storage)
    return roots
