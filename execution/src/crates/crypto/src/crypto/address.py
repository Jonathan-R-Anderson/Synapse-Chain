from __future__ import annotations

from primitives import Address

from .keccak import keccak256
from .secp256k1 import PublicKey, public_key_from_private_key


def address_from_public_key(public_key: PublicKey | bytes | bytearray | memoryview) -> Address:
    parsed = public_key if isinstance(public_key, PublicKey) else PublicKey.from_bytes(public_key)
    digest = keccak256(parsed.to_bytes(compressed=False, include_prefix=False))
    return Address(digest.to_bytes()[-Address.SIZE :])


def address_from_private_key(private_key: bytes | bytearray | memoryview | int) -> Address:
    return address_from_public_key(public_key_from_private_key(private_key))
