from __future__ import annotations

from dataclasses import dataclass, field

from crypto import keccak256
from primitives import Address, Hash, U256

from ..account import Account
from ..constants import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT
from ..serialization import (
    address_trie_key,
    deserialize_storage_value,
    serialize_account,
    serialize_storage_value,
    storage_trie_key,
)
from ..trie import MerklePatriciaTrie
from .base import StateBackend


@dataclass(slots=True)
class _MptAccountRecord:
    account: Account = field(default_factory=Account)
    code: bytes = b""
    storage_trie: MerklePatriciaTrie = field(
        default_factory=lambda: MerklePatriciaTrie(key_transform=storage_trie_key)
    )

    def clone(self) -> "_MptAccountRecord":
        return _MptAccountRecord(
            account=self.account,
            code=bytes(self.code),
            storage_trie=self.storage_trie.clone(),
        )


@dataclass(slots=True)
class MptStateBackend(StateBackend):
    _records: dict[Address, _MptAccountRecord] = field(default_factory=dict)
    _account_trie: MerklePatriciaTrie = field(
        default_factory=lambda: MerklePatriciaTrie(key_transform=address_trie_key)
    )
    _code_store: dict[Hash, bytes] = field(default_factory=dict)
    _state_root: Hash = field(default_factory=lambda: EMPTY_TRIE_ROOT)

    def clone(self) -> "MptStateBackend":
        return MptStateBackend(
            _records={address: record.clone() for address, record in self._records.items()},
            _account_trie=self._account_trie.clone(),
            _code_store={digest: bytes(code) for digest, code in self._code_store.items()},
            _state_root=self._state_root,
        )

    def _ensure_record(self, address: Address) -> _MptAccountRecord:
        record = self._records.get(address)
        if record is None:
            record = _MptAccountRecord()
            self._records[address] = record
        return record

    def _materialize_account(self, record: _MptAccountRecord) -> Account:
        return record.account.with_storage_root(record.storage_trie.root_hash)

    def get_account(self, address: Address) -> Account | None:
        record = self._records.get(address)
        if record is None:
            return None
        return self._materialize_account(record)

    def set_account(self, address: Address, account: Account) -> None:
        record = self._records.get(address)
        current_storage_root = EMPTY_TRIE_ROOT if record is None else record.storage_trie.root_hash
        current_code_hash = EMPTY_CODE_HASH if record is None else record.account.code_hash

        if account.storage_root != current_storage_root:
            raise ValueError("account.storage_root is derived from storage; mutate storage through set_storage()")
        if account.code_hash != current_code_hash:
            if account.code_hash == EMPTY_CODE_HASH:
                code = b""
            else:
                raise ValueError("account.code_hash is derived from bytecode; mutate code through set_code()")
        else:
            code = b"" if record is None else record.code

        next_record = self._ensure_record(address)
        next_record.code = code
        next_record.account = Account(
            nonce=account.nonce,
            balance=account.balance,
            code_hash=account.code_hash,
            storage_root=current_storage_root,
        )

    def create_account(self, address: Address) -> Account:
        return self.get_account(address) or self._materialize_account(self._ensure_record(address))

    def account_exists(self, address: Address) -> bool:
        return address in self._records

    def delete_account(self, address: Address) -> None:
        self._records.pop(address, None)
        self._account_trie.delete(address.to_bytes())

    def get_code(self, address: Address) -> bytes:
        record = self._records.get(address)
        return b"" if record is None else bytes(record.code)

    def set_code(self, address: Address, bytecode: bytes) -> Hash:
        record = self._ensure_record(address)
        normalized = bytes(bytecode)
        code_hash = keccak256(normalized) if normalized else EMPTY_CODE_HASH
        record.code = normalized
        record.account = record.account.with_code_hash(code_hash)
        if normalized:
            self._code_store[code_hash] = normalized
        return code_hash

    def get_storage(self, address: Address, key: U256) -> U256:
        record = self._records.get(address)
        if record is None:
            return U256.zero()
        payload = record.storage_trie.get(key.to_bytes(U256.BYTE_LENGTH))
        if payload is None:
            return U256.zero()
        return deserialize_storage_value(payload)

    def set_storage(self, address: Address, key: U256, value: U256) -> None:
        record = self._ensure_record(address)
        raw_key = key.to_bytes(U256.BYTE_LENGTH)
        if value.is_zero():
            record.storage_trie.delete(raw_key)
            return
        record.storage_trie.update(raw_key, serialize_storage_value(value))

    def commit(self) -> Hash:
        self._account_trie.clear()
        for address in sorted(self._records, key=lambda item: item.to_bytes()):
            record = self._records[address]
            storage_root = record.storage_trie.commit()
            materialized = record.account.with_storage_root(storage_root)
            record.account = materialized
            self._account_trie.update(address.to_bytes(), serialize_account(materialized))
        self._state_root = self._account_trie.commit()
        return self._state_root

    @property
    def state_root(self) -> Hash:
        return self._state_root
