from __future__ import annotations

from dataclasses import dataclass, field, replace

from primitives import Hash, U256

from .constants import EMPTY_CODE_HASH, EMPTY_TRIE_ROOT


def _coerce_u256(value: U256 | int) -> U256:
    if isinstance(value, U256):
        return value
    if isinstance(value, int):
        return U256(value)
    raise TypeError("expected U256-compatible integer value")


def _coerce_hash(value: Hash | bytes | bytearray | memoryview) -> Hash:
    if isinstance(value, Hash):
        return value
    return Hash(bytes(value))


@dataclass(frozen=True, slots=True)
class Account:
    nonce: U256 = field(default_factory=U256.zero)
    balance: U256 = field(default_factory=U256.zero)
    code_hash: Hash = field(default_factory=lambda: EMPTY_CODE_HASH)
    storage_root: Hash = field(default_factory=lambda: EMPTY_TRIE_ROOT)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nonce", _coerce_u256(self.nonce))
        object.__setattr__(self, "balance", _coerce_u256(self.balance))
        object.__setattr__(self, "code_hash", _coerce_hash(self.code_hash))
        object.__setattr__(self, "storage_root", _coerce_hash(self.storage_root))

    def with_nonce(self, nonce: U256 | int) -> "Account":
        return replace(self, nonce=_coerce_u256(nonce))

    def with_balance(self, balance: U256 | int) -> "Account":
        return replace(self, balance=_coerce_u256(balance))

    def with_code_hash(self, code_hash: Hash | bytes | bytearray | memoryview) -> "Account":
        return replace(self, code_hash=_coerce_hash(code_hash))

    def with_storage_root(self, storage_root: Hash | bytes | bytearray | memoryview) -> "Account":
        return replace(self, storage_root=_coerce_hash(storage_root))

    def is_empty(self) -> bool:
        return (
            self.nonce.is_zero()
            and self.balance.is_zero()
            and self.code_hash == EMPTY_CODE_HASH
            and self.storage_root == EMPTY_TRIE_ROOT
        )
