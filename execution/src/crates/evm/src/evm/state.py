from __future__ import annotations

from dataclasses import dataclass, field

from primitives import Address

from .account import Account
from .utils import UINT256_MASK


@dataclass(slots=True)
class StateDB:
    _accounts: dict[Address, Account] = field(default_factory=dict)

    def clone(self) -> "StateDB":
        return StateDB({address: account.clone() for address, account in self._accounts.items()})

    def snapshot(self) -> dict[Address, Account]:
        return {address: account.clone() for address, account in self._accounts.items()}

    def restore(self, snapshot: dict[Address, Account]) -> None:
        self._accounts = {address: account.clone() for address, account in snapshot.items()}

    def commit_transaction(self) -> None:
        empty_accounts: list[Address] = []
        for address, account in self._accounts.items():
            account.storage.commit()
            if account.is_empty:
                empty_accounts.append(address)
        for address in empty_accounts:
            del self._accounts[address]

    def accounts(self, include_empty: bool = False) -> tuple[tuple[Address, Account], ...]:
        items = [
            (address, account.clone())
            for address, account in self._accounts.items()
            if include_empty or not account.is_empty
        ]
        return tuple(sorted(items, key=lambda item: item[0].to_bytes()))

    def account_exists(self, address: Address) -> bool:
        return address in self._accounts

    def get_account(self, address: Address) -> Account | None:
        return self._accounts.get(address)

    def get_or_create_account(self, address: Address) -> Account:
        account = self._accounts.get(address)
        if account is None:
            account = Account()
            self._accounts[address] = account
        return account

    def can_deploy_to(self, address: Address) -> bool:
        account = self._accounts.get(address)
        if account is None:
            return True
        return account.nonce == 0 and not account.code

    def get_balance(self, address: Address) -> int:
        account = self._accounts.get(address)
        return 0 if account is None else account.balance

    def set_balance(self, address: Address, value: int) -> None:
        self.get_or_create_account(address).balance = value & UINT256_MASK

    def add_balance(self, address: Address, value: int) -> None:
        self.set_balance(address, self.get_balance(address) + value)

    def subtract_balance(self, address: Address, value: int) -> bool:
        current = self.get_balance(address)
        if current < value:
            return False
        self.set_balance(address, current - value)
        return True

    def transfer(self, sender: Address, recipient: Address, value: int) -> bool:
        if value == 0:
            self.get_or_create_account(recipient)
            return True
        if not self.subtract_balance(sender, value):
            return False
        self.add_balance(recipient, value)
        return True

    def get_nonce(self, address: Address) -> int:
        account = self._accounts.get(address)
        return 0 if account is None else account.nonce

    def increment_nonce(self, address: Address) -> int:
        account = self.get_or_create_account(address)
        account.nonce = (account.nonce + 1) & UINT256_MASK
        return account.nonce

    def get_code(self, address: Address) -> bytes:
        account = self._accounts.get(address)
        return b"" if account is None else bytes(account.code)

    def set_code(self, address: Address, code: bytes) -> None:
        self.get_or_create_account(address).code = bytes(code)

    def get_storage(self, address: Address, key: int) -> int:
        account = self._accounts.get(address)
        if account is None:
            return 0
        return account.storage.get(key)

    def estimate_sstore(self, address: Address, key: int, value: int) -> tuple[int, int]:
        account = self.get_or_create_account(address)
        return account.storage.estimate_sstore_cost(key, value)

    def set_storage(self, address: Address, key: int, value: int) -> tuple[int, int]:
        account = self.get_or_create_account(address)
        return account.storage.set(key, value)
