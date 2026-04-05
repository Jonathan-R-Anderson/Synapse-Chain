from __future__ import annotations

from dataclasses import dataclass, field

from crypto import keccak256
from primitives import Address, Hash, U256

from ..account import Account
from ..constants import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT
from ..serialization import address_trie_key, serialize_account, serialize_storage_value, storage_trie_key
from ..storage import StorageMap, coerce_u256
from ..trie import MerklePatriciaTrie
from .base import StateBackend


@dataclass(slots=True)
class _HashMapAccountRecord:
    account: Account = field(default_factory=Account)
    code: bytes = b""
    storage: StorageMap = field(default_factory=StorageMap)

    def clone(self) -> "_HashMapAccountRecord":
        return _HashMapAccountRecord(account=self.account, code=bytes(self.code), storage=self.storage.clone())


@dataclass(slots=True)
class HashMapStateBackend(StateBackend):
    _records: dict[Address, _HashMapAccountRecord] = field(default_factory=dict)
    _code_store: dict[Hash, bytes] = field(default_factory=dict)
    _state_root: Hash = field(default_factory=lambda: EMPTY_TRIE_ROOT)

    def clone(self) -> "HashMapStateBackend":
        return HashMapStateBackend(
            _records={address: record.clone() for address, record in self._records.items()},
            _code_store={digest: bytes(code) for digest, code in self._code_store.items()},
            _state_root=self._state_root,
        )

    def _ensure_record(self, address: Address) -> _HashMapAccountRecord:
        record = self._records.get(address)
        if record is None:
            record = _HashMapAccountRecord()
            self._records[address] = record
        return record

    def _current_storage_root(self, storage: StorageMap) -> Hash:
        trie = MerklePatriciaTrie(key_transform=storage_trie_key)
        for key, value in storage.items():
            trie.update(coerce_u256(key).to_bytes(U256.BYTE_LENGTH), serialize_storage_value(value))
        return trie.commit()

    def _materialize_account(self, record: _HashMapAccountRecord) -> Account:
        return record.account.with_storage_root(self._current_storage_root(record.storage))

    def get_account(self, address: Address) -> Account | None:
        record = self._records.get(address)
        if record is None:
            return None
        return self._materialize_account(record)

    def set_account(self, address: Address, account: Account) -> None:
        record = self._records.get(address)
        current_storage_root = EMPTY_TRIE_ROOT if record is None else self._current_storage_root(record.storage)
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
        return record.storage.get(key)

    def set_storage(self, address: Address, key: U256, value: U256) -> None:
        record = self._ensure_record(address)
        record.storage.set(key, value)

    def commit(self) -> Hash:
        account_trie = MerklePatriciaTrie(key_transform=address_trie_key)
        for address in sorted(self._records, key=lambda item: item.to_bytes()):
            record = self._records[address]
            materialized = self._materialize_account(record)
            record.account = materialized
            account_trie.update(address.to_bytes(), serialize_account(materialized))
        self._state_root = account_trie.commit()
        return self._state_root

    @property
    def state_root(self) -> Hash:
        return self._state_root
