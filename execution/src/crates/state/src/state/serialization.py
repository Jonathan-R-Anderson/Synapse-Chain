from __future__ import annotations

from encoding import decode, encode
from crypto import keccak256
from primitives import Address, Hash, U256

from .account import Account


def serialize_account(account: Account) -> bytes:
    return encode(
        [
            int(account.nonce),
            int(account.balance),
            account.storage_root.to_bytes(),
            account.code_hash.to_bytes(),
        ]
    )


def deserialize_account(payload: bytes | bytearray | memoryview) -> Account:
    value = decode(payload)
    if not isinstance(value, list) or len(value) != 4 or any(isinstance(item, list) for item in value):
        raise ValueError("serialized account must decode to a 4-item RLP list")

    nonce_raw, balance_raw, storage_root_raw, code_hash_raw = value
    if len(storage_root_raw) != Hash.SIZE:
        raise ValueError("account.storage_root must be 32 bytes")
    if len(code_hash_raw) != Hash.SIZE:
        raise ValueError("account.code_hash must be 32 bytes")

    return Account(
        nonce=U256(int.from_bytes(nonce_raw, byteorder="big", signed=False)) if nonce_raw else U256.zero(),
        balance=U256(int.from_bytes(balance_raw, byteorder="big", signed=False)) if balance_raw else U256.zero(),
        storage_root=Hash(storage_root_raw),
        code_hash=Hash(code_hash_raw),
    )


def serialize_storage_value(value: U256 | int) -> bytes:
    normalized = value if isinstance(value, U256) else U256(value)
    return encode(int(normalized))


def deserialize_storage_value(payload: bytes | bytearray | memoryview) -> U256:
    value = decode(payload)
    if isinstance(value, list):
        raise ValueError("storage value must decode to an RLP byte string")
    if not value:
        return U256.zero()
    if value[0] == 0:
        raise ValueError("storage values must be minimally encoded")
    return U256(int.from_bytes(value, byteorder="big", signed=False))


def address_trie_key(address: Address | bytes | bytearray | memoryview) -> bytes:
    raw = address.to_bytes() if isinstance(address, Address) else bytes(address)
    if len(raw) != Address.SIZE:
        raise ValueError("account trie keys must be 20-byte addresses")
    return keccak256(raw).to_bytes()


def storage_trie_key(slot: U256 | int | bytes | bytearray | memoryview) -> bytes:
    if isinstance(slot, (bytes, bytearray, memoryview)):
        raw = bytes(slot)
        if len(raw) != U256.BYTE_LENGTH:
            raise ValueError("storage slot keys must be 32 bytes")
    else:
        normalized = slot if isinstance(slot, U256) else U256(slot)
        raw = normalized.to_bytes(U256.BYTE_LENGTH)
    return keccak256(raw).to_bytes()
