from __future__ import annotations

from typing import TypeAlias


RlpValue: TypeAlias = bytes | list["RlpValue"]


class RlpDecodingError(ValueError):
    """Raised when the encoded payload violates canonical RLP rules."""


def _big_endian_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("length must be non-negative")
    if value == 0:
        return b"\x00"
    byte_length = (value.bit_length() + 7) // 8
    return value.to_bytes(byte_length, byteorder="big", signed=False)


def _encode_bytes(payload: bytes) -> bytes:
    length = len(payload)
    if length == 1 and payload[0] < 0x80:
        return payload
    if length <= 55:
        return bytes([0x80 + length]) + payload
    length_bytes = _big_endian_length(length)
    return bytes([0xB7 + len(length_bytes)]) + length_bytes + payload


def _encode_int(value: int) -> bytes:
    if value < 0:
        raise ValueError("RLP cannot encode negative integers")
    if value == 0:
        return _encode_bytes(b"")
    raw = value.to_bytes((value.bit_length() + 7) // 8, byteorder="big", signed=False)
    return _encode_bytes(raw)


def _encode_list(items: list[RlpValue]) -> bytes:
    payload = b"".join(encode(item) for item in items)
    length = len(payload)
    if length <= 55:
        return bytes([0xC0 + length]) + payload
    length_bytes = _big_endian_length(length)
    return bytes([0xF7 + len(length_bytes)]) + length_bytes + payload


def encode(value: object) -> bytes:
    if isinstance(value, bool):
        raise TypeError("RLP does not define a canonical boolean encoding")
    if isinstance(value, str):
        return _encode_bytes(value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _encode_bytes(bytes(value))
    if isinstance(value, int):
        return _encode_int(value)
    if isinstance(value, (list, tuple)):
        return _encode_list(list(value))
    if hasattr(value, "__int__"):
        return _encode_int(int(value))
    raise TypeError(f"unsupported RLP type: {type(value)!r}")


def _decode_length(
    data: bytes,
    offset: int,
    prefix: int,
    short_base: int,
    long_base: int,
    kind: str,
) -> tuple[int, int]:
    if prefix < long_base:
        return prefix - short_base, offset + 1

    length_of_length = prefix - long_base
    start = offset + 1
    end = start + length_of_length
    if end > len(data):
        raise RlpDecodingError(f"truncated {kind} length")
    length_bytes = data[start:end]
    if not length_bytes or length_bytes[0] == 0:
        raise RlpDecodingError(f"non-canonical {kind} length")
    length = int.from_bytes(length_bytes, byteorder="big", signed=False)
    return length, end


def _decode_item(data: bytes, offset: int) -> tuple[RlpValue, int]:
    if offset >= len(data):
        raise RlpDecodingError("unexpected end of RLP input")

    prefix = data[offset]

    if prefix <= 0x7F:
        return bytes([prefix]), offset + 1

    if prefix <= 0xBF:
        payload_length, payload_offset = _decode_length(data, offset, prefix, 0x80, 0xB7, "string")
        if prefix > 0xB7 and payload_length <= 55:
            raise RlpDecodingError("non-canonical long string length")
        end = payload_offset + payload_length
        if end > len(data):
            raise RlpDecodingError("truncated string payload")
        payload = data[payload_offset:end]
        if prefix <= 0xB7 and payload_length == 1 and payload[0] < 0x80:
            raise RlpDecodingError("non-canonical single-byte string encoding")
        return payload, end

    if prefix <= 0xFF:
        payload_length, payload_offset = _decode_length(data, offset, prefix, 0xC0, 0xF7, "list")
        if prefix > 0xF7 and payload_length <= 55:
            raise RlpDecodingError("non-canonical long list length")
        end = payload_offset + payload_length
        if end > len(data):
            raise RlpDecodingError("truncated list payload")
        items: list[RlpValue] = []
        cursor = payload_offset
        while cursor < end:
            item, cursor = _decode_item(data, cursor)
            items.append(item)
        if cursor != end:
            raise RlpDecodingError("list payload length mismatch")
        return items, end

    raise RlpDecodingError("invalid RLP prefix")


def decode(data: bytes | bytearray | memoryview) -> RlpValue:
    raw = bytes(data)
    if not raw:
        raise RlpDecodingError("RLP payload cannot be empty")
    value, end = _decode_item(raw, 0)
    if end != len(raw):
        raise RlpDecodingError("RLP payload has trailing bytes")
    return value


def decode_bytes(data: bytes | bytearray | memoryview) -> bytes:
    value = decode(data)
    if isinstance(value, list):
        raise RlpDecodingError("expected byte string, decoded list")
    return value


def decode_str(data: bytes | bytearray | memoryview, encoding: str = "utf-8") -> str:
    return decode_bytes(data).decode(encoding)


def decode_int(data: bytes | bytearray | memoryview) -> int:
    payload = decode_bytes(data)
    if payload == b"":
        return 0
    if payload[0] == 0:
        raise RlpDecodingError("integer payload must be minimally encoded")
    return int.from_bytes(payload, byteorder="big", signed=False)
