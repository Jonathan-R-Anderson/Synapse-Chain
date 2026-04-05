from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass


P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HALF_N = N // 2
A = 0
B = 7
GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G = (GX, GY)
_INFINITY = None
_FIELD_BYTE_LENGTH = 32

SECP256K1_P = P
SECP256K1_N = N
SECP256K1_HALF_N = HALF_N


def _mod_inverse(value: int, modulus: int) -> int:
    return pow(value % modulus, -1, modulus)


def _is_on_curve(point: tuple[int, int] | None) -> bool:
    if point is _INFINITY:
        return True
    x, y = point
    return (pow(y, 2, P) - (pow(x, 3, P) + A * x + B)) % P == 0


def _point_neg(point: tuple[int, int] | None) -> tuple[int, int] | None:
    if point is _INFINITY:
        return _INFINITY
    x, y = point
    return x, (-y) % P


def _point_add(
    first: tuple[int, int] | None,
    second: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if first is _INFINITY:
        return second
    if second is _INFINITY:
        return first

    x1, y1 = first
    x2, y2 = second

    if x1 == x2 and (y1 + y2) % P == 0:
        return _INFINITY

    if first == second:
        if y1 == 0:
            return _INFINITY
        slope = (3 * x1 * x1) * _mod_inverse(2 * y1, P)
    else:
        slope = (y2 - y1) * _mod_inverse(x2 - x1, P)

    slope %= P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return x3, y3


def _scalar_mult(scalar: int, point: tuple[int, int] | None) -> tuple[int, int] | None:
    if point is _INFINITY or scalar % N == 0:
        return _INFINITY
    if scalar < 0:
        return _scalar_mult(-scalar, _point_neg(point))

    result = _INFINITY
    addend = point
    current = scalar

    while current:
        if current & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        current >>= 1

    return result


def _normalize_private_key(private_key: bytes | bytearray | memoryview | int) -> int:
    if isinstance(private_key, int):
        scalar = private_key
    else:
        raw = bytes(private_key)
        if len(raw) != _FIELD_BYTE_LENGTH:
            raise ValueError("private key must be exactly 32 bytes")
        scalar = int.from_bytes(raw, byteorder="big", signed=False)

    if not 1 <= scalar < N:
        raise ValueError("private key must be in the range [1, secp256k1.n)")
    return scalar


def _private_key_to_bytes(private_key: int) -> bytes:
    return private_key.to_bytes(_FIELD_BYTE_LENGTH, byteorder="big", signed=False)


def _normalize_message_hash(message_hash: bytes | bytearray | memoryview) -> tuple[bytes, int]:
    raw = bytes(message_hash)
    if len(raw) != _FIELD_BYTE_LENGTH:
        raise ValueError("message hash must be exactly 32 bytes")
    return raw, int.from_bytes(raw, byteorder="big", signed=False)


def _bits2octets(message_hash: bytes) -> bytes:
    return (int.from_bytes(message_hash, byteorder="big", signed=False) % N).to_bytes(
        _FIELD_BYTE_LENGTH,
        byteorder="big",
        signed=False,
    )


def _rfc6979_nonce_stream(private_key: int, message_hash: bytes):
    key_bytes = _private_key_to_bytes(private_key)
    hash_bytes = _bits2octets(message_hash)
    v = b"\x01" * hashlib.sha256().digest_size
    k = b"\x00" * hashlib.sha256().digest_size

    k = hmac.digest(k, v + b"\x00" + key_bytes + hash_bytes, "sha256")
    v = hmac.digest(k, v, "sha256")
    k = hmac.digest(k, v + b"\x01" + key_bytes + hash_bytes, "sha256")
    v = hmac.digest(k, v, "sha256")

    while True:
        candidate = bytearray()
        while len(candidate) < _FIELD_BYTE_LENGTH:
            v = hmac.digest(k, v, "sha256")
            candidate.extend(v)
        nonce = int.from_bytes(candidate[:_FIELD_BYTE_LENGTH], byteorder="big", signed=False)
        if 1 <= nonce < N:
            yield nonce
        k = hmac.digest(k, v + b"\x00", "sha256")
        v = hmac.digest(k, v, "sha256")


def _lift_x(x_coordinate: int, odd: bool) -> tuple[int, int]:
    if not 0 <= x_coordinate < P:
        raise ValueError("x-coordinate is outside the field")
    alpha = (pow(x_coordinate, 3, P) + B) % P
    beta = pow(alpha, (P + 1) // 4, P)
    if pow(beta, 2, P) != alpha:
        raise ValueError("x-coordinate does not correspond to a curve point")
    y_coordinate = beta if bool(beta & 1) == odd else P - beta
    point = (x_coordinate, y_coordinate)
    if not _is_on_curve(point):
        raise ValueError("recovered point is not on the curve")
    return point


@dataclass(frozen=True, slots=True)
class PublicKey:
    x: int
    y: int

    def __post_init__(self) -> None:
        if not (0 <= self.x < P and 0 <= self.y < P):
            raise ValueError("public key coordinates must be field elements")
        if not _is_on_curve((self.x, self.y)):
            raise ValueError("public key is not on the secp256k1 curve")

    @classmethod
    def from_point(cls, point: tuple[int, int] | None) -> "PublicKey":
        if point is _INFINITY:
            raise ValueError("point at infinity is not a valid public key")
        return cls(*point)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> "PublicKey":
        raw = bytes(data)
        if len(raw) == 64:
            return cls(
                int.from_bytes(raw[:32], byteorder="big", signed=False),
                int.from_bytes(raw[32:], byteorder="big", signed=False),
            )
        if len(raw) == 65 and raw[0] == 0x04:
            return cls.from_bytes(raw[1:])
        if len(raw) == 33 and raw[0] in {0x02, 0x03}:
            x_coordinate = int.from_bytes(raw[1:], byteorder="big", signed=False)
            return cls.from_point(_lift_x(x_coordinate, odd=bool(raw[0] & 1)))
        raise ValueError("public key must be 64-byte raw, 65-byte uncompressed, or 33-byte compressed")

    def to_point(self) -> tuple[int, int]:
        return self.x, self.y

    def to_bytes(self, compressed: bool = False, include_prefix: bool = False) -> bytes:
        x_bytes = self.x.to_bytes(_FIELD_BYTE_LENGTH, byteorder="big", signed=False)
        y_bytes = self.y.to_bytes(_FIELD_BYTE_LENGTH, byteorder="big", signed=False)
        if compressed:
            prefix = 0x03 if self.y & 1 else 0x02
            return bytes([prefix]) + x_bytes
        if include_prefix:
            return b"\x04" + x_bytes + y_bytes
        return x_bytes + y_bytes

    def __bytes__(self) -> bytes:
        return self.to_bytes()


@dataclass(frozen=True, slots=True)
class Signature:
    r: int
    s: int
    recovery_id: int

    def __post_init__(self) -> None:
        if not 1 <= self.r < N:
            raise ValueError("signature.r must be in the range [1, secp256k1.n)")
        if not 1 <= self.s < N:
            raise ValueError("signature.s must be in the range [1, secp256k1.n)")
        if not 0 <= self.recovery_id < 4:
            raise ValueError("recovery_id must be in the range [0, 4)")

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> "Signature":
        raw = bytes(data)
        if len(raw) == 64:
            return cls(
                int.from_bytes(raw[:32], byteorder="big", signed=False),
                int.from_bytes(raw[32:], byteorder="big", signed=False),
                0,
            )
        if len(raw) == 65:
            return cls(
                int.from_bytes(raw[:32], byteorder="big", signed=False),
                int.from_bytes(raw[32:64], byteorder="big", signed=False),
                raw[64],
            )
        raise ValueError("signature must be 64 or 65 bytes")

    def to_bytes(self, include_recovery_id: bool = True) -> bytes:
        payload = self.r.to_bytes(_FIELD_BYTE_LENGTH, byteorder="big", signed=False)
        payload += self.s.to_bytes(_FIELD_BYTE_LENGTH, byteorder="big", signed=False)
        if include_recovery_id:
            payload += bytes([self.recovery_id])
        return payload

    def ethereum_v(self) -> int:
        return 27 + self.recovery_id


def generate_private_key() -> bytes:
    while True:
        scalar = secrets.randbelow(N - 1) + 1
        if 1 <= scalar < N:
            return _private_key_to_bytes(scalar)


def public_key_from_private_key(private_key: bytes | bytearray | memoryview | int) -> PublicKey:
    scalar = _normalize_private_key(private_key)
    return PublicKey.from_point(_scalar_mult(scalar, _G))


def sign_message_hash(
    message_hash: bytes | bytearray | memoryview,
    private_key: bytes | bytearray | memoryview | int,
) -> Signature:
    hash_bytes, hash_int = _normalize_message_hash(message_hash)
    private_scalar = _normalize_private_key(private_key)

    for nonce in _rfc6979_nonce_stream(private_scalar, hash_bytes):
        point = _scalar_mult(nonce, _G)
        if point is _INFINITY:
            continue
        x_coordinate, y_coordinate = point
        r_value = x_coordinate % N
        if r_value == 0:
            continue

        s_value = (_mod_inverse(nonce, N) * (hash_int + r_value * private_scalar)) % N
        if s_value == 0:
            continue

        recovery_id = (2 if x_coordinate >= N else 0) | (y_coordinate & 1)
        if s_value > N // 2:
            s_value = N - s_value
            recovery_id ^= 1

        return Signature(r_value, s_value, recovery_id)

    raise RuntimeError("unable to generate a valid signature")


def verify_message_hash(
    message_hash: bytes | bytearray | memoryview,
    signature: Signature | bytes | bytearray | memoryview,
    public_key: PublicKey | bytes | bytearray | memoryview,
) -> bool:
    _, hash_int = _normalize_message_hash(message_hash)
    parsed_signature = signature if isinstance(signature, Signature) else Signature.from_bytes(signature)
    parsed_public_key = public_key if isinstance(public_key, PublicKey) else PublicKey.from_bytes(public_key)

    inverse = _mod_inverse(parsed_signature.s, N)
    u1 = (hash_int * inverse) % N
    u2 = (parsed_signature.r * inverse) % N
    point = _point_add(_scalar_mult(u1, _G), _scalar_mult(u2, parsed_public_key.to_point()))

    if point is _INFINITY:
        return False
    return point[0] % N == parsed_signature.r


def recover_public_key(
    message_hash: bytes | bytearray | memoryview,
    signature: Signature | bytes | bytearray | memoryview,
) -> PublicKey:
    _, hash_int = _normalize_message_hash(message_hash)
    parsed_signature = signature if isinstance(signature, Signature) else Signature.from_bytes(signature)

    x_coordinate = parsed_signature.r + (parsed_signature.recovery_id >> 1) * N
    if x_coordinate >= P:
        raise ValueError("recovery_id selects an x-coordinate outside the secp256k1 field")

    point_r = _lift_x(x_coordinate, odd=bool(parsed_signature.recovery_id & 1))
    if _scalar_mult(N, point_r) is not _INFINITY:
        raise ValueError("invalid signature recovery point")

    r_inverse = _mod_inverse(parsed_signature.r, N)
    z_neg = (-hash_int) % N
    candidate = _point_add(
        _scalar_mult(parsed_signature.s, point_r),
        _scalar_mult(z_neg, _G),
    )
    if candidate is _INFINITY:
        raise ValueError("unable to recover public key")

    recovered = _scalar_mult(r_inverse, candidate)
    return PublicKey.from_point(recovered)
