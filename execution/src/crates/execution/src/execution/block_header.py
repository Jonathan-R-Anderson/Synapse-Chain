from __future__ import annotations

from dataclasses import dataclass, field

from primitives import Address, Hash
from state import EMPTY_TRIE_ROOT

from .hashing import address_from_hex, bytes_to_hex, hash_from_hex, hex_to_bytes, keccak256_hash
from .rlp_codec import RlpDecodingError, rlp_decode, rlp_encode
from .trie import EMPTY_OMMERS_HASH


ZERO_HASH = Hash.zero()
EMPTY_LOGS_BLOOM = bytes(256)
EMPTY_NONCE = bytes(8)
MAX_EXTRA_DATA_BYTES = 32


def _coerce_hash(
    value: Hash | bytes | bytearray | memoryview | None,
    *,
    default: Hash,
    label: str,
) -> Hash:
    if value is None:
        return default
    if isinstance(value, Hash):
        return value
    raw = bytes(value)
    if len(raw) != Hash.SIZE:
        raise ValueError(f"{label} must be exactly {Hash.SIZE} bytes")
    return Hash(raw)


def _coerce_address(value: Address | bytes | bytearray | memoryview, *, label: str) -> Address:
    if isinstance(value, Address):
        return value
    raw = bytes(value)
    if len(raw) != Address.SIZE:
        raise ValueError(f"{label} must be exactly {Address.SIZE} bytes")
    return Address(raw)


def _coerce_bytes(
    value: bytes | bytearray | memoryview,
    *,
    label: str,
    expected_length: int | None = None,
) -> bytes:
    raw = bytes(value)
    if expected_length is not None and len(raw) != expected_length:
        raise ValueError(f"{label} must be exactly {expected_length} bytes")
    return raw


@dataclass(frozen=True, slots=True)
class BlockHeader:
    parent_hash: Hash | bytes | bytearray | memoryview | None = None
    ommers_hash: Hash | bytes | bytearray | memoryview | None = None
    coinbase: Address | bytes | bytearray | memoryview = field(default_factory=Address.zero)
    state_root: Hash | bytes | bytearray | memoryview | None = None
    transactions_root: Hash | bytes | bytearray | memoryview | None = None
    receipts_root: Hash | bytes | bytearray | memoryview | None = None
    logs_bloom: bytes | bytearray | memoryview = EMPTY_LOGS_BLOOM
    difficulty: int = 0
    number: int = 0
    gas_limit: int = 0
    gas_used: int = 0
    timestamp: int = 0
    extra_data: bytes | bytearray | memoryview = b""
    mix_hash: Hash | bytes | bytearray | memoryview | None = None
    nonce: bytes | bytearray | memoryview = EMPTY_NONCE
    base_fee: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_hash", _coerce_hash(self.parent_hash, default=ZERO_HASH, label="parent_hash"))
        object.__setattr__(self, "ommers_hash", _coerce_hash(self.ommers_hash, default=EMPTY_OMMERS_HASH, label="ommers_hash"))
        object.__setattr__(self, "coinbase", _coerce_address(self.coinbase, label="coinbase"))
        object.__setattr__(self, "state_root", _coerce_hash(self.state_root, default=EMPTY_TRIE_ROOT, label="state_root"))
        object.__setattr__(
            self,
            "transactions_root",
            _coerce_hash(self.transactions_root, default=EMPTY_TRIE_ROOT, label="transactions_root"),
        )
        object.__setattr__(
            self,
            "receipts_root",
            _coerce_hash(self.receipts_root, default=EMPTY_TRIE_ROOT, label="receipts_root"),
        )
        object.__setattr__(self, "logs_bloom", _coerce_bytes(self.logs_bloom, label="logs_bloom", expected_length=256))
        object.__setattr__(self, "extra_data", _coerce_bytes(self.extra_data, label="extra_data"))
        object.__setattr__(self, "mix_hash", _coerce_hash(self.mix_hash, default=ZERO_HASH, label="mix_hash"))
        object.__setattr__(self, "nonce", _coerce_bytes(self.nonce, label="nonce", expected_length=8))
        object.__setattr__(self, "difficulty", int(self.difficulty))
        object.__setattr__(self, "number", int(self.number))
        object.__setattr__(self, "gas_limit", int(self.gas_limit))
        object.__setattr__(self, "gas_used", int(self.gas_used))
        object.__setattr__(self, "timestamp", int(self.timestamp))
        if self.base_fee is not None:
            object.__setattr__(self, "base_fee", int(self.base_fee))
        self.validate()

    @property
    def beneficiary(self) -> Address:
        return self.coinbase

    @property
    def base_fee_per_gas(self) -> int | None:
        return self.base_fee

    @property
    def prev_randao(self) -> Hash:
        return self.mix_hash

    def validate(self) -> None:
        if self.difficulty < 0:
            raise ValueError("difficulty must be non-negative")
        if self.number < 0:
            raise ValueError("number must be non-negative")
        if self.gas_limit < 0:
            raise ValueError("gas_limit must be non-negative")
        if self.gas_used < 0:
            raise ValueError("gas_used must be non-negative")
        if self.gas_used > self.gas_limit:
            raise ValueError("gas_used must not exceed gas_limit")
        if self.timestamp < 0:
            raise ValueError("timestamp must be non-negative")
        if len(self.extra_data) > MAX_EXTRA_DATA_BYTES:
            raise ValueError(f"extra_data must be at most {MAX_EXTRA_DATA_BYTES} bytes")
        if self.base_fee is not None and self.base_fee < 0:
            raise ValueError("base_fee must be non-negative")

    def to_ordered_field_list(self) -> list[object]:
        fields: list[object] = [
            self.parent_hash.to_bytes(),
            self.ommers_hash.to_bytes(),
            self.coinbase.to_bytes(),
            self.state_root.to_bytes(),
            self.transactions_root.to_bytes(),
            self.receipts_root.to_bytes(),
            self.logs_bloom,
            self.difficulty,
            self.number,
            self.gas_limit,
            self.gas_used,
            self.timestamp,
            self.extra_data,
            self.mix_hash.to_bytes(),
            self.nonce,
        ]
        if self.base_fee is not None:
            fields.append(self.base_fee)
        return fields

    def rlp_encode(self) -> bytes:
        return rlp_encode(self.to_ordered_field_list())

    def hash(self) -> Hash:
        return keccak256_hash(self.rlp_encode())

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_hash": self.parent_hash.to_hex(),
            "ommers_hash": self.ommers_hash.to_hex(),
            "coinbase": self.coinbase.to_hex(),
            "state_root": self.state_root.to_hex(),
            "transactions_root": self.transactions_root.to_hex(),
            "receipts_root": self.receipts_root.to_hex(),
            "logs_bloom": bytes_to_hex(self.logs_bloom),
            "difficulty": self.difficulty,
            "number": self.number,
            "gas_limit": self.gas_limit,
            "gas_used": self.gas_used,
            "timestamp": self.timestamp,
            "extra_data": bytes_to_hex(self.extra_data),
            "mix_hash": self.mix_hash.to_hex(),
            "nonce": bytes_to_hex(self.nonce),
            "base_fee_per_gas": self.base_fee,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BlockHeader":
        return cls(
            parent_hash=hash_from_hex(str(data.get("parent_hash", ZERO_HASH.to_hex())), label="parent_hash"),
            ommers_hash=hash_from_hex(str(data.get("ommers_hash", EMPTY_OMMERS_HASH.to_hex())), label="ommers_hash"),
            coinbase=address_from_hex(str(data.get("coinbase", data.get("beneficiary", Address.zero().to_hex()))), label="coinbase"),
            state_root=hash_from_hex(str(data.get("state_root", EMPTY_TRIE_ROOT.to_hex())), label="state_root"),
            transactions_root=hash_from_hex(
                str(data.get("transactions_root", EMPTY_TRIE_ROOT.to_hex())),
                label="transactions_root",
            ),
            receipts_root=hash_from_hex(str(data.get("receipts_root", EMPTY_TRIE_ROOT.to_hex())), label="receipts_root"),
            logs_bloom=hex_to_bytes(
                str(data.get("logs_bloom", bytes_to_hex(EMPTY_LOGS_BLOOM))),
                expected_length=256,
                label="logs_bloom",
            ),
            difficulty=int(data.get("difficulty", 0)),
            number=int(data.get("number", 0)),
            gas_limit=int(data.get("gas_limit", 0)),
            gas_used=int(data.get("gas_used", 0)),
            timestamp=int(data.get("timestamp", 0)),
            extra_data=hex_to_bytes(str(data.get("extra_data", "0x")), label="extra_data"),
            mix_hash=hash_from_hex(str(data.get("mix_hash", ZERO_HASH.to_hex())), label="mix_hash"),
            nonce=hex_to_bytes(str(data.get("nonce", bytes_to_hex(EMPTY_NONCE))), expected_length=8, label="nonce"),
            base_fee=data.get("base_fee_per_gas", data.get("base_fee")),
        )

    @classmethod
    def from_ordered_field_list(cls, fields: list[object]) -> "BlockHeader":
        if len(fields) not in {15, 16}:
            raise ValueError("block header must decode to a 15-item or 16-item RLP list")
        if any(isinstance(field, list) for field in fields):
            raise ValueError("block header fields must be RLP scalar values")
        base_fee_raw = fields[15] if len(fields) == 16 else None
        return cls(
            parent_hash=fields[0],
            ommers_hash=fields[1],
            coinbase=fields[2],
            state_root=fields[3],
            transactions_root=fields[4],
            receipts_root=fields[5],
            logs_bloom=fields[6],
            difficulty=int.from_bytes(fields[7], byteorder="big", signed=False) if fields[7] else 0,
            number=int.from_bytes(fields[8], byteorder="big", signed=False) if fields[8] else 0,
            gas_limit=int.from_bytes(fields[9], byteorder="big", signed=False) if fields[9] else 0,
            gas_used=int.from_bytes(fields[10], byteorder="big", signed=False) if fields[10] else 0,
            timestamp=int.from_bytes(fields[11], byteorder="big", signed=False) if fields[11] else 0,
            extra_data=fields[12],
            mix_hash=fields[13],
            nonce=fields[14],
            base_fee=(int.from_bytes(base_fee_raw, byteorder="big", signed=False) if base_fee_raw else 0)
            if base_fee_raw is not None
            else None,
        )

    @classmethod
    def rlp_decode(cls, payload: bytes | bytearray | memoryview) -> "BlockHeader":
        try:
            decoded = rlp_decode(bytes(payload))
        except RlpDecodingError as exc:
            raise ValueError("invalid RLP block header payload") from exc
        if not isinstance(decoded, list):
            raise ValueError("block header RLP payload must decode to a list")
        return cls.from_ordered_field_list(decoded)
