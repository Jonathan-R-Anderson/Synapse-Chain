from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence

from crypto import keccak256
from primitives import Address, Hash


def _normalize(value: object) -> object:
    if dataclasses.is_dataclass(value):
        return {field.name: _normalize(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Address | Hash):
        return value.to_hex()
    if isinstance(value, bytes | bytearray | memoryview):
        return "0x" + bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(key): _normalize(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_normalize(item) for item in value]
    return value


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(_normalize(value), separators=(",", ":"), sort_keys=True).encode("utf-8")


def phantom_hash(value: object) -> Hash:
    return keccak256(canonical_json_bytes(value))


def hash_secret(secret: bytes | bytearray | memoryview) -> Hash:
    return keccak256(bytes(secret))

