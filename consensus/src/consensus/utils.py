from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from typing import Any


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def log_normalize(value: float, scale: float) -> float:
    if value <= 0 or scale <= 0:
        return 0.0
    return math.log1p(value / scale)


def bounded_log_normalize(value: float, scale: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return clamp(log_normalize(value, scale) / math.log1p(cap), 0.0, 1.0)


def deterministic_float(data: bytes) -> float:
    digest = hashlib.sha256(data).digest()
    integer = int.from_bytes(digest, byteorder="big", signed=False)
    return (integer + 1) / ((1 << (8 * len(digest))) + 1)


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def stable_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(stable_json_bytes(value)).hexdigest()


def xor_bytes(left: bytes, right: bytes) -> bytes:
    size = max(len(left), len(right))
    padded_left = left.rjust(size, b"\x00")
    padded_right = right.rjust(size, b"\x00")
    return bytes(a ^ b for a, b in zip(padded_left, padded_right, strict=True))
