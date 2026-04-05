from __future__ import annotations

from dataclasses import dataclass, field

from .storage import Storage
from .utils import UINT256_MASK


@dataclass(slots=True)
class Account:
    nonce: int = 0
    balance: int = 0
    code: bytes = b""
    storage: Storage = field(default_factory=Storage)

    def __post_init__(self) -> None:
        self.nonce &= UINT256_MASK
        self.balance &= UINT256_MASK
        self.code = bytes(self.code)

    def clone(self) -> "Account":
        return Account(
            nonce=self.nonce,
            balance=self.balance,
            code=self.code,
            storage=self.storage.clone(),
        )

    @property
    def has_code(self) -> bool:
        return bool(self.code)

    @property
    def is_empty(self) -> bool:
        return self.nonce == 0 and self.balance == 0 and not self.code and not self.storage.items()
