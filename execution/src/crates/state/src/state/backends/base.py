from __future__ import annotations

from abc import ABC, abstractmethod

from primitives import Address, Hash, U256

from ..account import Account


class StateBackend(ABC):
    @abstractmethod
    def clone(self) -> "StateBackend":
        ...

    @abstractmethod
    def get_account(self, address: Address) -> Account | None:
        ...

    @abstractmethod
    def set_account(self, address: Address, account: Account) -> None:
        ...

    @abstractmethod
    def create_account(self, address: Address) -> Account:
        ...

    @abstractmethod
    def account_exists(self, address: Address) -> bool:
        ...

    @abstractmethod
    def delete_account(self, address: Address) -> None:
        ...

    @abstractmethod
    def get_code(self, address: Address) -> bytes:
        ...

    @abstractmethod
    def set_code(self, address: Address, bytecode: bytes) -> Hash:
        ...

    @abstractmethod
    def get_storage(self, address: Address, key: U256) -> U256:
        ...

    @abstractmethod
    def set_storage(self, address: Address, key: U256, value: U256) -> None:
        ...

    @abstractmethod
    def commit(self) -> Hash:
        ...

    @property
    @abstractmethod
    def state_root(self) -> Hash:
        ...
