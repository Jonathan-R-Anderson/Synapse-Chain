from __future__ import annotations

from dataclasses import dataclass

from primitives import Address, Hash, U256

from .account import Account
from .backends.base import StateBackend
from .backends.hashmap import HashMapStateBackend
from .constants import EMPTY_CODE_HASH
from .storage import coerce_u256


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    snapshot_id: int


class State:
    def __init__(self, backend: StateBackend | None = None) -> None:
        self._backend: StateBackend = backend or HashMapStateBackend()
        self._snapshots: dict[int, StateBackend] = {}
        self._next_snapshot_id = 1

    @property
    def backend(self) -> StateBackend:
        return self._backend

    @property
    def state_root(self) -> Hash:
        return self._backend.state_root

    def clone(self) -> "State":
        cloned = State(self._backend.clone())
        cloned._snapshots = {snapshot_id: backend.clone() for snapshot_id, backend in self._snapshots.items()}
        cloned._next_snapshot_id = self._next_snapshot_id
        return cloned

    def snapshot(self) -> StateSnapshot:
        snapshot = StateSnapshot(self._next_snapshot_id)
        self._snapshots[snapshot.snapshot_id] = self._backend.clone()
        self._next_snapshot_id += 1
        return snapshot

    def revert(self, snapshot: StateSnapshot | int) -> None:
        snapshot_id = snapshot.snapshot_id if isinstance(snapshot, StateSnapshot) else snapshot
        try:
            backend = self._snapshots[snapshot_id]
        except KeyError as exc:
            raise ValueError(f"unknown snapshot id: {snapshot_id}") from exc

        self._backend = backend.clone()
        self._snapshots = {
            saved_snapshot_id: saved_backend
            for saved_snapshot_id, saved_backend in self._snapshots.items()
            if saved_snapshot_id <= snapshot_id
        }

    def get_account(self, address: Address) -> Account | None:
        return self._backend.get_account(address)

    def set_account(self, address: Address, account: Account) -> None:
        self._backend.set_account(address, account)

    def create_account(self, address: Address) -> Account:
        return self._backend.create_account(address)

    def account_exists(self, address: Address) -> bool:
        return self._backend.account_exists(address)

    def delete_account(self, address: Address) -> None:
        self._backend.delete_account(address)

    def get_balance(self, address: Address) -> U256:
        account = self._backend.get_account(address)
        return U256.zero() if account is None else account.balance

    def set_balance(self, address: Address, value: U256 | int) -> None:
        account = self._backend.get_account(address) or self._backend.create_account(address)
        self._backend.set_account(address, account.with_balance(coerce_u256(value)))

    def increment_nonce(self, address: Address) -> U256:
        account = self._backend.get_account(address) or self._backend.create_account(address)
        next_nonce = account.nonce.checked_add(U256.one())
        self._backend.set_account(address, account.with_nonce(next_nonce))
        return next_nonce

    def get_code_hash(self, address: Address) -> Hash:
        account = self._backend.get_account(address)
        return EMPTY_CODE_HASH if account is None else account.code_hash

    def get_code(self, address: Address) -> bytes:
        return self._backend.get_code(address)

    def set_code(self, address: Address, bytecode: bytes | bytearray | memoryview) -> Hash:
        return self._backend.set_code(address, bytes(bytecode))

    def get_storage(self, address: Address, key: U256 | int) -> U256:
        return self._backend.get_storage(address, coerce_u256(key))

    def set_storage(self, address: Address, key: U256 | int, value: U256 | int) -> None:
        self._backend.set_storage(address, coerce_u256(key), coerce_u256(value))

    def commit(self) -> Hash:
        return self._backend.commit()
