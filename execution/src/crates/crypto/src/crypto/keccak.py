from __future__ import annotations

from primitives import Hash


_LANE_MASK = (1 << 64) - 1
_RATE_BYTES = 136
_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
_ROTATION_OFFSETS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotate_left(value: int, shift: int) -> int:
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _LANE_MASK


def _keccak_f1600(state: list[int]) -> None:
    for round_constant in _ROUND_CONSTANTS:
        c = [0] * 5
        for x in range(5):
            c[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]

        d = [0] * 5
        for x in range(5):
            d[x] = c[(x - 1) % 5] ^ _rotate_left(c[(x + 1) % 5], 1)

        for y in range(5):
            base = y * 5
            for x in range(5):
                state[base + x] ^= d[x]

        b = [0] * 25
        for y in range(5):
            for x in range(5):
                destination_x = y
                destination_y = (2 * x + 3 * y) % 5
                b[destination_x + 5 * destination_y] = _rotate_left(
                    state[x + 5 * y],
                    _ROTATION_OFFSETS[x][y],
                )

        for y in range(5):
            base = y * 5
            row = b[base : base + 5]
            for x in range(5):
                state[base + x] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])

        state[0] ^= round_constant


def keccak256(data: bytes | bytearray | memoryview) -> Hash:
    payload = bytearray(bytes(data))
    payload.append(0x01)
    while len(payload) % _RATE_BYTES != _RATE_BYTES - 1:
        payload.append(0x00)
    payload.append(0x80)

    state = [0] * 25
    for offset in range(0, len(payload), _RATE_BYTES):
        block = payload[offset : offset + _RATE_BYTES]
        for lane_index in range(_RATE_BYTES // 8):
            start = lane_index * 8
            state[lane_index] ^= int.from_bytes(block[start : start + 8], byteorder="little", signed=False)
        _keccak_f1600(state)

    output = bytearray()
    while len(output) < Hash.SIZE:
        for lane_index in range(_RATE_BYTES // 8):
            output.extend(state[lane_index].to_bytes(8, byteorder="little", signed=False))
        if len(output) >= Hash.SIZE:
            break
        _keccak_f1600(state)

    return Hash(bytes(output[: Hash.SIZE]))
