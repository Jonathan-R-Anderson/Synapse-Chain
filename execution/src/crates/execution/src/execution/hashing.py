from __future__ import annotations

from crypto import keccak256 as _keccak256_hash
from primitives import Address, Hash


def keccak256(data: bytes | bytearray | memoryview) -> bytes:
    """Return the raw Keccak-256 digest bytes for Ethereum-compatible hashing."""

    return _keccak256_hash(bytes(data)).to_bytes()


def keccak256_hash(data: bytes | bytearray | memoryview) -> Hash:
    """Return the Keccak-256 digest as a fixed-width Hash."""

    return _keccak256_hash(bytes(data))


def bytes_to_hex(data: bytes | bytearray | memoryview) -> str:
    return "0x" + bytes(data).hex()


def hex_to_bytes(value: str, *, expected_length: int | None = None, label: str = "value") -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a hex string")
    normalized = value[2:] if value.startswith(("0x", "0X")) else value
    try:
        raw = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be valid hexadecimal") from exc
    if expected_length is not None and len(raw) != expected_length:
        raise ValueError(f"{label} must be exactly {expected_length} bytes")
    return raw


def hash_from_hex(value: str, *, label: str = "hash") -> Hash:
    return Hash(hex_to_bytes(value, expected_length=Hash.SIZE, label=label))


def address_from_hex(value: str, *, label: str = "address") -> Address:
    return Address(hex_to_bytes(value, expected_length=Address.SIZE, label=label))
