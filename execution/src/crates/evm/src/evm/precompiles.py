from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from crypto import SECP256K1_N, address_from_public_key, recover_public_key
from primitives import Address

from .gas import (
    GAS_ECRECOVER,
    GAS_IDENTITY,
    GAS_IDENTITY_WORD,
    GAS_RIPEMD160,
    GAS_RIPEMD160_WORD,
    GAS_SHA256,
    GAS_SHA256_WORD,
)
from .utils import address_to_int, int_to_address, words_for_size


class Precompile(Protocol):
    def gas_cost(self, input_data: bytes) -> int:
        ...

    def run(self, input_data: bytes) -> bytes:
        ...


class ECRecoverPrecompile:
    def gas_cost(self, input_data: bytes) -> int:
        return GAS_ECRECOVER

    def run(self, input_data: bytes) -> bytes:
        padded = input_data.ljust(128, b"\x00")
        message_hash = padded[:32]
        v_value = int.from_bytes(padded[32:64], byteorder="big", signed=False)
        r_value = int.from_bytes(padded[64:96], byteorder="big", signed=False)
        s_value = int.from_bytes(padded[96:128], byteorder="big", signed=False)
        if v_value in {27, 28}:
            recovery_id = v_value - 27
        elif v_value in {0, 1}:
            recovery_id = v_value
        else:
            return bytes(32)
        if not (1 <= r_value < SECP256K1_N and 1 <= s_value < SECP256K1_N):
            return bytes(32)
        try:
            signature = r_value.to_bytes(32, byteorder="big", signed=False)
            signature += s_value.to_bytes(32, byteorder="big", signed=False)
            signature += bytes([recovery_id])
            public_key = recover_public_key(message_hash, signature)
            address = address_from_public_key(public_key)
            return bytes(12) + address.to_bytes()
        except ValueError:
            return bytes(32)


class SHA256Precompile:
    def gas_cost(self, input_data: bytes) -> int:
        return GAS_SHA256 + (words_for_size(len(input_data)) * GAS_SHA256_WORD)

    def run(self, input_data: bytes) -> bytes:
        return hashlib.sha256(input_data).digest()


class RIPEMD160Precompile:
    def gas_cost(self, input_data: bytes) -> int:
        return GAS_RIPEMD160 + (words_for_size(len(input_data)) * GAS_RIPEMD160_WORD)

    def run(self, input_data: bytes) -> bytes:
        digest = hashlib.new("ripemd160", input_data).digest()
        return bytes(12) + digest


class IdentityPrecompile:
    def gas_cost(self, input_data: bytes) -> int:
        return GAS_IDENTITY + (words_for_size(len(input_data)) * GAS_IDENTITY_WORD)

    def run(self, input_data: bytes) -> bytes:
        return bytes(input_data)


@dataclass(slots=True)
class PrecompileRegistry:
    _registry: dict[int, Precompile] = field(
        default_factory=lambda: {
            1: ECRecoverPrecompile(),
            2: SHA256Precompile(),
            3: RIPEMD160Precompile(),
            4: IdentityPrecompile(),
        }
    )

    def get(self, address: Address | int) -> Precompile | None:
        address_int = address if isinstance(address, int) else address_to_int(address)
        return self._registry.get(address_int)

    def is_precompile(self, address: Address | int) -> bool:
        return self.get(address) is not None

    def address(self, value: int) -> Address:
        return int_to_address(value)
