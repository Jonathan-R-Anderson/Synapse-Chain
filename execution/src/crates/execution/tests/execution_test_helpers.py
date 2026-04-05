from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    sys.path.insert(0, str(CRATES / crate / "src"))

from crypto import address_from_private_key
from evm.opcodes import PUSH0
from evm.utils import selector
from execution import EIP1559Transaction, LegacyTransaction
from primitives import Address


PRIVATE_KEY_ONE = 1
PRIVATE_KEY_TWO = 2
PRIVATE_KEY_THREE = 3
SENDER_ONE = address_from_private_key(PRIVATE_KEY_ONE)
SENDER_TWO = address_from_private_key(PRIVATE_KEY_TWO)
SENDER_THREE = address_from_private_key(PRIVATE_KEY_THREE)


def addr(hex_value: str) -> Address:
    return Address.from_hex(hex_value)


def abi_uint(value: int) -> bytes:
    return int(value).to_bytes(32, byteorder="big", signed=False)


def encode_call(signature: str, *args: int) -> bytes:
    return selector(signature) + b"".join(abi_uint(argument) for argument in args)


@dataclass(frozen=True)
class Label:
    name: str


@dataclass(frozen=True)
class LabelRef:
    name: str
    size: int = 2


@dataclass(frozen=True)
class RawBytes:
    data: bytes


def label(name: str) -> Label:
    return Label(name)


def ref(name: str, size: int = 2) -> LabelRef:
    return LabelRef(name, size)


def raw(data: bytes) -> RawBytes:
    return RawBytes(bytes(data))


def push(value: int | bytes | bytearray | memoryview | LabelRef, size: int | None = None):
    if isinstance(value, LabelRef):
        return ("PUSH_LABEL", value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw_value = bytes(value)
        selected_size = len(raw_value) if size is None else size
        return (f"PUSH{selected_size}", raw_value)
    normalized = int(value)
    if normalized == 0 and size in {None, 0}:
        return ("PUSH0",)
    selected_size = max(1, (normalized.bit_length() + 7) // 8) if size is None else size
    return (f"PUSH{selected_size}", normalized)


def op(name: str):
    return name.upper()


def _opcode_value(name: str) -> int:
    if name == "PUSH0":
        return PUSH0
    if name.startswith("PUSH"):
        return 0x5F + int(name[4:])
    if name.startswith("DUP"):
        return 0x7F + int(name[3:])
    if name.startswith("SWAP"):
        return 0x8F + int(name[4:])
    if name.startswith("LOG"):
        return 0xA0 + int(name[3:])
    module = __import__("evm.opcodes", fromlist=[name])
    return getattr(module, name)


def assemble(items: list[object]) -> bytes:
    labels: dict[str, int] = {}
    offset = 0

    for item in items:
        if isinstance(item, Label):
            labels[item.name] = offset
            continue
        if isinstance(item, RawBytes):
            offset += len(item.data)
            continue
        if isinstance(item, str):
            offset += 1
            continue
        if isinstance(item, tuple):
            name = item[0]
            if name == "PUSH_LABEL":
                offset += 1 + item[1].size
            elif name.startswith("PUSH"):
                offset += 1 + (0 if name == "PUSH0" else int(name[4:]))
            else:
                offset += 1
            continue
        raise TypeError(f"unsupported assembly item: {item!r}")

    output = bytearray()
    for item in items:
        if isinstance(item, Label):
            continue
        if isinstance(item, RawBytes):
            output.extend(item.data)
            continue
        if isinstance(item, str):
            output.append(_opcode_value(item))
            continue
        name = item[0]
        if name == "PUSH_LABEL":
            label_ref = item[1]
            value = labels[label_ref.name]
            output.append(0x5F + label_ref.size)
            output.extend(value.to_bytes(label_ref.size, byteorder="big", signed=False))
            continue
        if name == "PUSH0":
            output.append(PUSH0)
            continue
        if name.startswith("PUSH"):
            size = int(name[4:])
            value = item[1]
            if isinstance(value, int):
                encoded = value.to_bytes(size, byteorder="big", signed=False)
            else:
                encoded = bytes(value)
                if len(encoded) > size:
                    raise ValueError(f"value {encoded.hex()} does not fit in {name}")
                encoded = encoded.rjust(size, b"\x00")
            output.append(_opcode_value(name))
            output.extend(encoded)
            continue
        output.append(_opcode_value(name))

    return bytes(output)


def build_counter_runtime() -> bytes:
    set_selector = int.from_bytes(selector("set(uint256)"), byteorder="big", signed=False)
    get_selector = int.from_bytes(selector("get()"), byteorder="big", signed=False)
    return assemble(
        [
            push(0),
            op("CALLDATALOAD"),
            push(224),
            op("SHR"),
            op("DUP1"),
            push(set_selector, 4),
            op("EQ"),
            ("PUSH_LABEL", ref("set")),
            op("JUMPI"),
            push(get_selector, 4),
            op("EQ"),
            ("PUSH_LABEL", ref("get")),
            op("JUMPI"),
            push(0),
            push(0),
            op("REVERT"),
            label("set"),
            op("JUMPDEST"),
            push(4),
            op("CALLDATALOAD"),
            push(0),
            op("SSTORE"),
            op("STOP"),
            label("get"),
            op("JUMPDEST"),
            push(0),
            op("SLOAD"),
            push(0),
            op("MSTORE"),
            push(32),
            push(0),
            op("RETURN"),
        ]
    )


def build_reverter_runtime(revert_data: bytes = bytes.fromhex("deadbeef")) -> bytes:
    offset = 32 - len(revert_data)
    return assemble(
        [
            push(revert_data, 32),
            push(0),
            op("MSTORE"),
            push(len(revert_data)),
            push(offset),
            op("REVERT"),
        ]
    )


def build_reverting_sstore_runtime(value: int = 1) -> bytes:
    return assemble(
        [
            push(value),
            push(0),
            op("SSTORE"),
            push(0),
            push(0),
            op("REVERT"),
        ]
    )


def build_logger_runtime(topic: int = 0xAA) -> bytes:
    return assemble(
        [
            push(0),
            op("CALLDATALOAD"),
            push(0),
            op("MSTORE"),
            push(topic),
            push(32),
            push(0),
            op("LOG1"),
            op("STOP"),
        ]
    )


def build_return_runtime(value: int) -> bytes:
    return assemble(
        [
            push(value, 32),
            push(0),
            op("MSTORE"),
            push(32),
            push(0),
            op("RETURN"),
        ]
    )


def build_init_code(runtime: bytes) -> bytes:
    return assemble(
        [
            push(len(runtime)),
            ("PUSH_LABEL", ref("runtime")),
            push(0),
            op("CODECOPY"),
            push(len(runtime)),
            push(0),
            op("RETURN"),
            label("runtime"),
            raw(runtime),
        ]
    )


def make_legacy_tx(
    private_key: int,
    nonce: int,
    to: Address | None,
    *,
    value: int = 0,
    data: bytes = b"",
    gas_limit: int = 21_000,
    gas_price: int = 1,
    chain_id: int | None = 1,
) -> LegacyTransaction:
    return LegacyTransaction(
        nonce=nonce,
        gas_price=gas_price,
        gas_limit=gas_limit,
        to=to,
        value=value,
        data=data,
        chain_id=chain_id,
    ).sign(private_key)


def make_eip1559_tx(
    private_key: int,
    nonce: int,
    to: Address | None,
    *,
    value: int = 0,
    data: bytes = b"",
    gas_limit: int = 21_000,
    max_priority_fee_per_gas: int = 1,
    max_fee_per_gas: int = 2,
    chain_id: int = 1,
) -> EIP1559Transaction:
    return EIP1559Transaction(
        chain_id=chain_id,
        nonce=nonce,
        max_priority_fee_per_gas=max_priority_fee_per_gas,
        max_fee_per_gas=max_fee_per_gas,
        gas_limit=gas_limit,
        to=to,
        value=value,
        data=data,
    ).sign(private_key)
