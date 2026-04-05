from __future__ import annotations

from crypto import keccak256
from evm import LogEntry


BLOOM_BIT_LENGTH = 2048
BLOOM_BYTE_LENGTH = BLOOM_BIT_LENGTH // 8


def _bloom_bits(raw: bytes) -> int:
    digest = keccak256(raw).to_bytes()
    bloom = 0
    for index in range(0, 6, 2):
        bit = ((digest[index] << 8) | digest[index + 1]) & (BLOOM_BIT_LENGTH - 1)
        bloom |= 1 << bit
    return bloom


def bloom_for_log(log: LogEntry) -> bytes:
    bloom = _bloom_bits(log.address.to_bytes())
    for topic in log.topics:
        bloom |= _bloom_bits(topic.to_bytes())
    return bloom.to_bytes(BLOOM_BYTE_LENGTH, byteorder="big", signed=False)


def combine_blooms(*blooms: bytes) -> bytes:
    value = 0
    for bloom in blooms:
        raw = bytes(bloom)
        if len(raw) != BLOOM_BYTE_LENGTH:
            raise ValueError("logs bloom values must be 256 bytes")
        value |= int.from_bytes(raw, byteorder="big", signed=False)
    return value.to_bytes(BLOOM_BYTE_LENGTH, byteorder="big", signed=False)


def logs_bloom(logs: tuple[LogEntry, ...] | list[LogEntry]) -> bytes:
    return combine_blooms(*(bloom_for_log(log) for log in logs)) if logs else bytes(BLOOM_BYTE_LENGTH)
