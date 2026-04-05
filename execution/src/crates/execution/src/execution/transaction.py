from __future__ import annotations

from transactions import (
    AccessListEntry,
    EIP1559Transaction,
    LegacyTransaction,
    Transaction,
    ZKTransaction,
    decode_transaction,
)

from .hashing import bytes_to_hex, hex_to_bytes


def is_contract_creation(transaction: Transaction) -> bool:
    return transaction.to is None


def transaction_type(transaction: Transaction) -> int | None:
    return transaction.type_byte


def encode_transaction(transaction: Transaction) -> bytes:
    return transaction.encode()


def decode_transaction_payload(payload: bytes | bytearray | memoryview) -> Transaction:
    return decode_transaction(bytes(payload))


def transaction_to_dict(transaction: Transaction) -> dict[str, object]:
    return {
        "type": transaction_type(transaction),
        "hash": transaction.tx_hash().to_hex(),
        "raw": bytes_to_hex(transaction.encode()),
    }


def transaction_from_dict(data: dict[str, object]) -> Transaction:
    raw = data.get("raw")
    if raw is None:
        raise ValueError("transaction dictionaries must include a raw payload")
    return decode_transaction_payload(hex_to_bytes(str(raw), label="transaction raw payload"))


__all__ = [
    "AccessListEntry",
    "EIP1559Transaction",
    "LegacyTransaction",
    "Transaction",
    "ZKTransaction",
    "decode_transaction_payload",
    "encode_transaction",
    "is_contract_creation",
    "transaction_from_dict",
    "transaction_to_dict",
    "transaction_type",
]
