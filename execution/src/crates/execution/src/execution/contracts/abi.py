from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from evm.utils import selector
from primitives import Address


def _pad32(raw: bytes) -> bytes:
    remainder = len(raw) % 32
    if remainder == 0:
        return raw
    return raw + (b"\x00" * (32 - remainder))


def _int_to_32bytes(value: int) -> bytes:
    return value.to_bytes(32, byteorder="big", signed=False)


def _signed_to_32bytes(value: int) -> bytes:
    return value.to_bytes(32, byteorder="big", signed=True)


@dataclass(frozen=True, slots=True)
class AbiType:
    raw: str
    kind: str
    bits: int | None = None
    bytes_length: int | None = None

    @property
    def is_dynamic(self) -> bool:
        return self.kind in {"string", "bytes"}


def _parse_abi_type(type_name: str) -> AbiType:
    if not isinstance(type_name, str) or not type_name:
        raise ValueError("ABI type names must be non-empty strings")
    if "[" in type_name or "]" in type_name:
        raise ValueError(f"ABI arrays are not supported yet: {type_name}")
    if type_name == "address":
        return AbiType(raw=type_name, kind="address")
    if type_name == "bool":
        return AbiType(raw=type_name, kind="bool")
    if type_name == "string":
        return AbiType(raw=type_name, kind="string")
    if type_name == "bytes":
        return AbiType(raw=type_name, kind="bytes")
    if type_name.startswith("uint"):
        width = 256 if type_name == "uint" else int(type_name[4:])
        if width < 8 or width > 256 or width % 8 != 0:
            raise ValueError(f"invalid uint width: {type_name}")
        return AbiType(raw=type_name, kind="uint", bits=width)
    if type_name.startswith("int"):
        width = 256 if type_name == "int" else int(type_name[3:])
        if width < 8 or width > 256 or width % 8 != 0:
            raise ValueError(f"invalid int width: {type_name}")
        return AbiType(raw=type_name, kind="int", bits=width)
    if type_name.startswith("bytes"):
        width = int(type_name[5:])
        if width < 1 or width > 32:
            raise ValueError(f"invalid fixed bytes width: {type_name}")
        return AbiType(raw=type_name, kind="fixed-bytes", bytes_length=width)
    raise ValueError(f"unsupported ABI type: {type_name}")


def _normalize_integer(value: object, *, bits: int, signed: bool) -> int:
    if isinstance(value, bool):
        raise TypeError("booleans cannot be used where an integer ABI value is required")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        base = 16 if value.startswith(("0x", "0X")) else 10
        normalized = int(value, base)
    else:
        raise TypeError(f"cannot encode {value!r} as an integer ABI value")

    if signed:
        minimum = -(1 << (bits - 1))
        maximum = (1 << (bits - 1)) - 1
    else:
        minimum = 0
        maximum = (1 << bits) - 1
    if not minimum <= normalized <= maximum:
        signedness = "signed" if signed else "unsigned"
        raise ValueError(f"{normalized} is outside range for {signedness} {bits}-bit ABI value")
    return normalized


def _normalize_address(value: object) -> Address:
    if isinstance(value, Address):
        return value
    if not isinstance(value, str):
        raise TypeError("address ABI values must be 0x-prefixed strings")
    return Address.from_hex(value)


def _normalize_bytes_value(value: object, *, fixed_length: int | None = None) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
    elif isinstance(value, str):
        if not value.startswith(("0x", "0X")):
            raise ValueError("bytes ABI values must be 0x-prefixed hex strings")
        raw = bytes.fromhex(value[2:])
    else:
        raise TypeError("bytes ABI values must be hex strings or bytes-like objects")
    if fixed_length is not None and len(raw) != fixed_length:
        raise ValueError(f"expected {fixed_length} bytes, received {len(raw)}")
    return raw


def _encode_single_value(abi_type: AbiType, value: object) -> bytes:
    if abi_type.kind == "address":
        return b"\x00" * 12 + _normalize_address(value).to_bytes()
    if abi_type.kind == "bool":
        if not isinstance(value, bool):
            raise TypeError("bool ABI values must be true or false")
        return _int_to_32bytes(1 if value else 0)
    if abi_type.kind == "uint":
        return _int_to_32bytes(_normalize_integer(value, bits=int(abi_type.bits), signed=False))
    if abi_type.kind == "int":
        return _signed_to_32bytes(_normalize_integer(value, bits=int(abi_type.bits), signed=True))
    if abi_type.kind == "fixed-bytes":
        raw = _normalize_bytes_value(value, fixed_length=int(abi_type.bytes_length))
        return raw.ljust(32, b"\x00")
    if abi_type.kind == "bytes":
        raw = _normalize_bytes_value(value)
        return _int_to_32bytes(len(raw)) + _pad32(raw)
    if abi_type.kind == "string":
        if not isinstance(value, str):
            raise TypeError("string ABI values must be strings")
        raw = value.encode("utf-8")
        return _int_to_32bytes(len(raw)) + _pad32(raw)
    raise ValueError(f"unsupported ABI type kind: {abi_type.kind}")


def encode_abi_arguments(type_names: Sequence[str], values: Sequence[object]) -> bytes:
    if len(type_names) != len(values):
        raise ValueError("ABI encoding requires the same number of types and values")
    parsed = [_parse_abi_type(type_name) for type_name in type_names]
    heads: list[bytes] = []
    tails: list[bytes] = []
    current_tail_offset = 32 * len(parsed)

    for abi_type, value in zip(parsed, values, strict=True):
        encoded = _encode_single_value(abi_type, value)
        if abi_type.is_dynamic:
            heads.append(_int_to_32bytes(current_tail_offset))
            tails.append(encoded)
            current_tail_offset += len(encoded)
        else:
            heads.append(encoded)
    return b"".join(heads + tails)


def _find_abi_entry(abi: Sequence[dict[str, object]], *, entry_type: str, name: str | None = None) -> dict[str, object] | None:
    for entry in abi:
        if entry.get("type") != entry_type:
            continue
        if name is not None and entry.get("name") != name:
            continue
        return entry
    return None


def _input_types_from_entry(entry: dict[str, object] | None) -> list[str]:
    if entry is None:
        return []
    inputs = entry.get("inputs", [])
    if not isinstance(inputs, list):
        raise ValueError("ABI inputs must be a list")
    types: list[str] = []
    for index, item in enumerate(inputs):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError(f"ABI input {index} is missing a valid type")
        types.append(str(item["type"]))
    return types


def encode_constructor_args(abi: Sequence[dict[str, object]], args: Sequence[object]) -> bytes:
    constructor_entry = _find_abi_entry(abi, entry_type="constructor")
    return encode_abi_arguments(_input_types_from_entry(constructor_entry), args)


def encode_function_call(abi: Sequence[dict[str, object]], function_name: str, args: Sequence[object]) -> bytes:
    entry = _find_abi_entry(abi, entry_type="function", name=function_name)
    if entry is None:
        raise ValueError(f"function {function_name!r} is not present in the ABI")
    input_types = _input_types_from_entry(entry)
    signature = f"{function_name}({','.join(input_types)})"
    return selector(signature) + encode_abi_arguments(input_types, args)


__all__ = [
    "encode_abi_arguments",
    "encode_constructor_args",
    "encode_function_call",
]
