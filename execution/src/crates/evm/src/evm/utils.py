from __future__ import annotations

from crypto import keccak256
from encoding import encode
from primitives import Address, U256


UINT256_BITS = 256
UINT256_MODULUS = 1 << UINT256_BITS
UINT256_MASK = UINT256_MODULUS - 1
ZERO_ADDRESS = Address.zero()
MAX_CALL_DEPTH = 1024
MAX_STACK_DEPTH = 1024


def to_uint256(value: int | U256) -> int:
    return int(value) & UINT256_MASK


def ceil32(value: int) -> int:
    if value < 0:
        raise ValueError("value must be non-negative")
    if value == 0:
        return 0
    return ((value + 31) // 32) * 32


def words_for_size(size: int) -> int:
    return ceil32(size) // 32


def to_signed(value: int | U256) -> int:
    normalized = to_uint256(value)
    if normalized >= 1 << 255:
        return normalized - UINT256_MODULUS
    return normalized


def from_signed(value: int) -> int:
    return value & UINT256_MASK


def int_to_bytes32(value: int | U256) -> bytes:
    return to_uint256(value).to_bytes(32, byteorder="big", signed=False)


def address_to_int(address: Address | bytes | bytearray | memoryview) -> int:
    raw = address.to_bytes() if isinstance(address, Address) else bytes(address)
    if len(raw) != Address.SIZE:
        raise ValueError("addresses must be exactly 20 bytes")
    return int.from_bytes(raw, byteorder="big", signed=False)


def int_to_address(value: int | U256) -> Address:
    return Address(to_uint256(value).to_bytes(32, byteorder="big", signed=False)[-Address.SIZE :])


def buffer_read(buffer: bytes | bytearray | memoryview, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0:
        raise ValueError("offset and size must be non-negative")
    raw = bytes(buffer)
    if size == 0:
        return b""
    if offset >= len(raw):
        return bytes(size)
    end = offset + size
    chunk = raw[offset:end]
    if len(chunk) < size:
        chunk += bytes(size - len(chunk))
    return chunk


def collect_jumpdest_offsets(code: bytes) -> frozenset[int]:
    offsets: set[int] = set()
    index = 0
    while index < len(code):
        opcode = code[index]
        if opcode == 0x5B:
            offsets.add(index)
            index += 1
            continue
        if 0x60 <= opcode <= 0x7F:
            index += 1 + (opcode - 0x5F)
            continue
        index += 1
    return frozenset(offsets)


def compute_create_address(sender: Address, nonce: int | U256) -> Address:
    encoded = encode([sender.to_bytes(), int(nonce)])
    return Address(keccak256(encoded).to_bytes()[-Address.SIZE :])


def compute_create2_address(sender: Address, salt: int | U256, init_code: bytes) -> Address:
    salt_bytes = to_uint256(salt).to_bytes(32, byteorder="big", signed=False)
    payload = b"\xff" + sender.to_bytes() + salt_bytes + keccak256(init_code).to_bytes()
    return Address(keccak256(payload).to_bytes()[-Address.SIZE :])


def selector(signature: str) -> bytes:
    return keccak256(signature.encode("utf-8")).to_bytes()[:4]
