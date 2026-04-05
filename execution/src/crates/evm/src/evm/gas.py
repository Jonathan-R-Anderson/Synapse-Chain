from __future__ import annotations

from dataclasses import dataclass

from .exceptions import OutOfGasError


GAS_ZERO = 0
GAS_BASE = 2
GAS_VERYLOW = 3
GAS_LOW = 5
GAS_MID = 8
GAS_HIGH = 10
GAS_JUMPDEST = 1
GAS_SLOAD = 100
GAS_BALANCE = 100
GAS_SSTORE_SET = 20_000
GAS_SSTORE_RESET = 5_000
GAS_SSTORE_CLEAR_REFUND = 4_800
GAS_MEMORY = 3
GAS_COPY = 3
GAS_KECCAK256 = 30
GAS_KECCAK256_WORD = 6
GAS_LOG = 375
GAS_LOG_TOPIC = 375
GAS_LOG_DATA = 8
GAS_CALL = 700
GAS_CALL_VALUE = 9_000
GAS_NEW_ACCOUNT = 25_000
GAS_CALL_STIPEND = 2_300
GAS_CREATE = 32_000
GAS_CREATE2_WORD = 6
GAS_SHA256 = 60
GAS_SHA256_WORD = 12
GAS_RIPEMD160 = 600
GAS_RIPEMD160_WORD = 120
GAS_IDENTITY = 15
GAS_IDENTITY_WORD = 3
GAS_ECRECOVER = 3_000


BASE_OPCODE_GAS = {
    0x00: GAS_ZERO,
    0x01: GAS_VERYLOW,
    0x02: GAS_LOW,
    0x03: GAS_VERYLOW,
    0x04: GAS_LOW,
    0x06: GAS_LOW,
    0x10: GAS_VERYLOW,
    0x11: GAS_VERYLOW,
    0x14: GAS_VERYLOW,
    0x15: GAS_VERYLOW,
    0x16: GAS_VERYLOW,
    0x17: GAS_VERYLOW,
    0x18: GAS_VERYLOW,
    0x19: GAS_VERYLOW,
    0x1A: GAS_VERYLOW,
    0x1B: GAS_VERYLOW,
    0x1C: GAS_VERYLOW,
    0x1D: GAS_VERYLOW,
    0x20: GAS_KECCAK256,
    0x30: GAS_BASE,
    0x31: GAS_BALANCE,
    0x32: GAS_BASE,
    0x33: GAS_BASE,
    0x34: GAS_BASE,
    0x35: GAS_VERYLOW,
    0x36: GAS_BASE,
    0x37: GAS_VERYLOW,
    0x38: GAS_BASE,
    0x39: GAS_VERYLOW,
    0x3A: GAS_BASE,
    0x3D: GAS_BASE,
    0x3E: GAS_VERYLOW,
    0x46: GAS_BASE,
    0x50: GAS_BASE,
    0x51: GAS_VERYLOW,
    0x52: GAS_VERYLOW,
    0x53: GAS_VERYLOW,
    0x54: GAS_SLOAD,
    0x56: GAS_MID,
    0x57: GAS_HIGH,
    0x58: GAS_BASE,
    0x59: GAS_BASE,
    0x5A: GAS_BASE,
    0x5B: GAS_JUMPDEST,
    0x5F: GAS_BASE,
    0xF1: 0,
    0xF0: 0,
    0xF3: 0,
    0xF4: 0,
    0xF5: 0,
    0xFA: 0,
    0xFD: 0,
}

for opcode in range(0x60, 0x80):
    BASE_OPCODE_GAS[opcode] = GAS_VERYLOW
for opcode in range(0x80, 0xA0):
    BASE_OPCODE_GAS[opcode] = GAS_VERYLOW


def memory_cost(word_count: int) -> int:
    return (GAS_MEMORY * word_count) + (word_count * word_count // 512)


def memory_expansion_cost(current_words: int, new_words: int) -> int:
    if new_words <= current_words:
        return 0
    return memory_cost(new_words) - memory_cost(current_words)


def copy_cost(size: int) -> int:
    return ((size + 31) // 32) * GAS_COPY


@dataclass(slots=True)
class GasMeter:
    remaining: int
    refund: int = 0

    def charge(self, amount: int, reason: str | None = None) -> None:
        if amount < 0:
            raise ValueError("gas charge must be non-negative")
        if self.remaining < amount:
            raise OutOfGasError(reason or f"out of gas while charging {amount}")
        self.remaining -= amount

    def add_refund(self, amount: int) -> None:
        self.refund += amount

    def return_gas(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("returned gas must be non-negative")
        self.remaining += amount

    def consume_all(self) -> None:
        self.remaining = 0
