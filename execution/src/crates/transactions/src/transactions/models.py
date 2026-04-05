from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence, TypeAlias

from crypto import SECP256K1_HALF_N, SECP256K1_N, address_from_public_key, keccak256, recover_public_key, sign_message_hash
from encoding import RlpDecodingError, decode, encode
from primitives import Address, Hash, U256
from zk import ProofType, ZKProof

from .constants import (
    EIP1559_TRANSACTION_TYPE,
    EIP155_CHAIN_ID_OFFSET,
    INTRINSIC_GAS_ACCESS_LIST_ADDRESS,
    INTRINSIC_GAS_ACCESS_LIST_STORAGE_KEY,
    INTRINSIC_GAS_BASE,
    INTRINSIC_GAS_CONTRACT_CREATION,
    INTRINSIC_GAS_NON_ZERO_DATA_BYTE,
    INTRINSIC_GAS_ZERO_DATA_BYTE,
    LEGACY_V_OFFSET,
    ZK_TRANSACTION_TYPE,
)


class TransactionError(ValueError):
    pass


class TransactionDecodeError(TransactionError):
    pass


class TransactionEncodingError(TransactionError):
    pass


class TransactionSignatureError(TransactionError):
    pass


def _coerce_u256(value: U256 | int) -> U256:
    if isinstance(value, U256):
        return value
    if isinstance(value, int):
        return U256(value)
    raise TypeError("expected a U256-compatible integer")


def _coerce_optional_u256(value: U256 | int | None) -> U256 | None:
    if value is None:
        return None
    return _coerce_u256(value)


def _coerce_address(value: Address | bytes | bytearray | memoryview | None) -> Address | None:
    if value is None:
        return None
    if isinstance(value, Address):
        return value
    raw = bytes(value)
    if len(raw) != Address.SIZE:
        raise ValueError("address must be 20 bytes")
    return Address(raw)


def _decode_u256(raw: bytes) -> U256:
    return U256.zero() if raw == b"" else U256(int.from_bytes(raw, byteorder="big", signed=False))


def _decode_int(raw: bytes) -> int:
    return 0 if raw == b"" else int.from_bytes(raw, byteorder="big", signed=False)


def _encode_destination(destination: Address | None) -> bytes:
    return b"" if destination is None else destination.to_bytes()


def _decode_destination(raw: bytes) -> Address | None:
    if raw == b"":
        return None
    if len(raw) != Address.SIZE:
        raise TransactionDecodeError("transaction destination must be 20 bytes or empty")
    return Address(raw)


def _normalize_bytes(data: bytes | bytearray | memoryview) -> bytes:
    return bytes(data)


@dataclass(frozen=True, slots=True)
class AccessListEntry:
    address: Address
    storage_keys: tuple[U256, ...] = ()

    def __post_init__(self) -> None:
        normalized_address = _coerce_address(self.address)
        if normalized_address is None:
            raise ValueError("access list entries require a concrete 20-byte address")
        object.__setattr__(self, "address", normalized_address)
        object.__setattr__(self, "storage_keys", tuple(_coerce_u256(key) for key in self.storage_keys))

    def to_rlp(self) -> list[object]:
        return [self.address.to_bytes(), [key.to_bytes(U256.BYTE_LENGTH) for key in self.storage_keys]]


def _encode_access_list(access_list: Sequence[AccessListEntry]) -> list[object]:
    return [entry.to_rlp() for entry in access_list]


def _decode_access_list(raw: list[object]) -> tuple[AccessListEntry, ...]:
    entries: list[AccessListEntry] = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise TransactionDecodeError("access list entries must be [address, storage_keys]")
        address_raw, storage_keys_raw = item
        if not isinstance(address_raw, bytes) or not isinstance(storage_keys_raw, list):
            raise TransactionDecodeError("access list entry types are invalid")
        storage_keys = []
        for storage_key in storage_keys_raw:
            if not isinstance(storage_key, bytes) or len(storage_key) != U256.BYTE_LENGTH:
                raise TransactionDecodeError("access list storage keys must be 32 bytes")
            storage_keys.append(U256.from_bytes(storage_key))
        decoded_address = _decode_destination(address_raw)
        if decoded_address is None:
            raise TransactionDecodeError("access list addresses must be 20 bytes")
        entries.append(AccessListEntry(address=decoded_address, storage_keys=tuple(storage_keys)))
    return tuple(entries)


def _signature_from_values(message_hash: Hash, v: int, r: U256, s: U256, enforce_low_s: bool = True) -> Address:
    if int(r) == 0 or int(s) == 0:
        raise TransactionSignatureError("signature r and s must be non-zero")
    if int(r) >= SECP256K1_N or int(s) >= SECP256K1_N:
        raise TransactionSignatureError("signature r and s must be within the secp256k1 scalar field")
    if enforce_low_s and int(s) > SECP256K1_HALF_N:
        raise TransactionSignatureError("signature.s exceeds the Ethereum low-s requirement")
    if v not in {0, 1}:
        raise TransactionSignatureError("recovery id must be 0 or 1")
    public_key = recover_public_key(message_hash.to_bytes(), bytes(r.to_bytes(32) + s.to_bytes(32) + bytes([v])))
    return address_from_public_key(public_key)


@dataclass(frozen=True, slots=True)
class LegacyTransaction:
    nonce: U256 = field(default_factory=U256.zero)
    gas_price: U256 = field(default_factory=U256.zero)
    gas_limit: U256 = field(default_factory=U256.zero)
    to: Address | None = None
    value: U256 = field(default_factory=U256.zero)
    data: bytes = b""
    chain_id: int | None = None
    v: int | None = None
    r: U256 | None = None
    s: U256 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "nonce", _coerce_u256(self.nonce))
        object.__setattr__(self, "gas_price", _coerce_u256(self.gas_price))
        object.__setattr__(self, "gas_limit", _coerce_u256(self.gas_limit))
        object.__setattr__(self, "to", _coerce_address(self.to))
        object.__setattr__(self, "value", _coerce_u256(self.value))
        object.__setattr__(self, "data", _normalize_bytes(self.data))
        object.__setattr__(self, "r", _coerce_optional_u256(self.r))
        object.__setattr__(self, "s", _coerce_optional_u256(self.s))
        if self.chain_id is not None and self.chain_id < 1:
            raise ValueError("legacy transaction chain_id must be positive")

    @property
    def is_signed(self) -> bool:
        return self.v is not None and self.r is not None and self.s is not None

    @property
    def type_byte(self) -> None:
        return None

    def with_signature(self, v: int, r: U256 | int, s: U256 | int) -> "LegacyTransaction":
        return replace(self, v=int(v), r=_coerce_u256(r), s=_coerce_u256(s))

    def _unsigned_rlp_fields(self) -> list[object]:
        base = [
            int(self.nonce),
            int(self.gas_price),
            int(self.gas_limit),
            _encode_destination(self.to),
            int(self.value),
            self.data,
        ]
        if self.chain_id is not None:
            base.extend([self.chain_id, 0, 0])
        return base

    def signing_payload(self) -> bytes:
        return encode(self._unsigned_rlp_fields())

    def signing_hash(self) -> Hash:
        return keccak256(self.signing_payload())

    def sign(self, private_key: bytes | bytearray | memoryview | int) -> "LegacyTransaction":
        signature = sign_message_hash(self.signing_hash().to_bytes(), private_key)
        v = signature.recovery_id + LEGACY_V_OFFSET if self.chain_id is None else signature.recovery_id + EIP155_CHAIN_ID_OFFSET + (2 * self.chain_id)
        return self.with_signature(v, signature.r, signature.s)

    def encode(self) -> bytes:
        if not self.is_signed:
            raise TransactionEncodingError("legacy transaction must be signed before encoding")
        return encode(
            [
                int(self.nonce),
                int(self.gas_price),
                int(self.gas_limit),
                _encode_destination(self.to),
                int(self.value),
                self.data,
                self.v,
                int(self.r),
                int(self.s),
            ]
        )

    def tx_hash(self) -> Hash:
        return keccak256(self.encode())

    def recovery_id(self) -> tuple[int, int | None]:
        if not self.is_signed:
            raise TransactionSignatureError("legacy transaction is unsigned")
        assert self.v is not None
        if self.v in {27, 28}:
            return self.v - LEGACY_V_OFFSET, None
        if self.v >= EIP155_CHAIN_ID_OFFSET:
            chain_id = (self.v - EIP155_CHAIN_ID_OFFSET) // 2
            recovery_id = self.v - EIP155_CHAIN_ID_OFFSET - (2 * chain_id)
            if chain_id < 1 or recovery_id not in {0, 1}:
                raise TransactionSignatureError("legacy signature v does not encode a valid chain id")
            return recovery_id, chain_id
        raise TransactionSignatureError("legacy signature v is invalid")

    def sender(self, enforce_low_s: bool = True) -> Address:
        if not self.is_signed:
            raise TransactionSignatureError("legacy transaction is unsigned")
        recovery_id, embedded_chain_id = self.recovery_id()
        message_hash = keccak256(
            encode(
                [
                    int(self.nonce),
                    int(self.gas_price),
                    int(self.gas_limit),
                    _encode_destination(self.to),
                    int(self.value),
                    self.data,
                ]
                + ([] if embedded_chain_id is None else [embedded_chain_id, 0, 0])
            )
        )
        assert self.r is not None and self.s is not None
        return _signature_from_values(message_hash, recovery_id, self.r, self.s, enforce_low_s=enforce_low_s)

    def intrinsic_gas(self) -> int:
        gas = INTRINSIC_GAS_BASE
        if self.to is None:
            gas += INTRINSIC_GAS_CONTRACT_CREATION
        gas += sum(
            INTRINSIC_GAS_ZERO_DATA_BYTE if byte == 0 else INTRINSIC_GAS_NON_ZERO_DATA_BYTE
            for byte in self.data
        )
        return gas


@dataclass(frozen=True, slots=True)
class EIP1559Transaction:
    chain_id: int
    nonce: U256 = field(default_factory=U256.zero)
    max_priority_fee_per_gas: U256 = field(default_factory=U256.zero)
    max_fee_per_gas: U256 = field(default_factory=U256.zero)
    gas_limit: U256 = field(default_factory=U256.zero)
    to: Address | None = None
    value: U256 = field(default_factory=U256.zero)
    data: bytes = b""
    access_list: tuple[AccessListEntry, ...] = ()
    v: int | None = None
    r: U256 | None = None
    s: U256 | None = None

    def __post_init__(self) -> None:
        if self.chain_id < 1:
            raise ValueError("chain_id must be positive")
        object.__setattr__(self, "nonce", _coerce_u256(self.nonce))
        object.__setattr__(self, "max_priority_fee_per_gas", _coerce_u256(self.max_priority_fee_per_gas))
        object.__setattr__(self, "max_fee_per_gas", _coerce_u256(self.max_fee_per_gas))
        object.__setattr__(self, "gas_limit", _coerce_u256(self.gas_limit))
        object.__setattr__(self, "to", _coerce_address(self.to))
        object.__setattr__(self, "value", _coerce_u256(self.value))
        object.__setattr__(self, "data", _normalize_bytes(self.data))
        object.__setattr__(self, "access_list", tuple(self.access_list))
        object.__setattr__(self, "r", _coerce_optional_u256(self.r))
        object.__setattr__(self, "s", _coerce_optional_u256(self.s))
        if int(self.max_fee_per_gas) < int(self.max_priority_fee_per_gas):
            raise ValueError("max_fee_per_gas must be at least max_priority_fee_per_gas")

    @property
    def is_signed(self) -> bool:
        return self.v is not None and self.r is not None and self.s is not None

    @property
    def type_byte(self) -> int:
        return EIP1559_TRANSACTION_TYPE

    def with_signature(self, v: int, r: U256 | int, s: U256 | int) -> "EIP1559Transaction":
        return replace(self, v=int(v), r=_coerce_u256(r), s=_coerce_u256(s))

    def _unsigned_rlp_fields(self) -> list[object]:
        return [
            self.chain_id,
            int(self.nonce),
            int(self.max_priority_fee_per_gas),
            int(self.max_fee_per_gas),
            int(self.gas_limit),
            _encode_destination(self.to),
            int(self.value),
            self.data,
            _encode_access_list(self.access_list),
        ]

    def signing_payload(self) -> bytes:
        return bytes([self.type_byte]) + encode(self._unsigned_rlp_fields())

    def signing_hash(self) -> Hash:
        return keccak256(self.signing_payload())

    def sign(self, private_key: bytes | bytearray | memoryview | int) -> "EIP1559Transaction":
        signature = sign_message_hash(self.signing_hash().to_bytes(), private_key)
        return self.with_signature(signature.recovery_id, signature.r, signature.s)

    def encode(self) -> bytes:
        if not self.is_signed:
            raise TransactionEncodingError("EIP-1559 transaction must be signed before encoding")
        return bytes([self.type_byte]) + encode(
            self._unsigned_rlp_fields() + [self.v, int(self.r), int(self.s)]
        )

    def tx_hash(self) -> Hash:
        return keccak256(self.encode())

    def sender(self, enforce_low_s: bool = True) -> Address:
        if not self.is_signed:
            raise TransactionSignatureError("EIP-1559 transaction is unsigned")
        assert self.v is not None and self.r is not None and self.s is not None
        return _signature_from_values(self.signing_hash(), self.v, self.r, self.s, enforce_low_s=enforce_low_s)

    def intrinsic_gas(self) -> int:
        gas = INTRINSIC_GAS_BASE
        if self.to is None:
            gas += INTRINSIC_GAS_CONTRACT_CREATION
        gas += sum(
            INTRINSIC_GAS_ZERO_DATA_BYTE if byte == 0 else INTRINSIC_GAS_NON_ZERO_DATA_BYTE
            for byte in self.data
        )
        gas += len(self.access_list) * INTRINSIC_GAS_ACCESS_LIST_ADDRESS
        gas += sum(len(entry.storage_keys) * INTRINSIC_GAS_ACCESS_LIST_STORAGE_KEY for entry in self.access_list)
        return gas


@dataclass(frozen=True, slots=True)
class ZKTransaction:
    base_tx: EIP1559Transaction
    proof: ZKProof
    public_inputs: tuple[U256, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_inputs", tuple(_coerce_u256(value) for value in self.public_inputs))

    @property
    def chain_id(self) -> int:
        return self.base_tx.chain_id

    @property
    def nonce(self) -> U256:
        return self.base_tx.nonce

    @property
    def gas_limit(self) -> U256:
        return self.base_tx.gas_limit

    @property
    def max_fee_per_gas(self) -> U256:
        return self.base_tx.max_fee_per_gas

    @property
    def max_priority_fee_per_gas(self) -> U256:
        return self.base_tx.max_priority_fee_per_gas

    @property
    def to(self) -> Address | None:
        return self.base_tx.to

    @property
    def value(self) -> U256:
        return self.base_tx.value

    @property
    def data(self) -> bytes:
        return self.base_tx.data

    @property
    def access_list(self) -> tuple[AccessListEntry, ...]:
        return self.base_tx.access_list

    @property
    def v(self) -> int | None:
        return self.base_tx.v

    @property
    def r(self) -> U256 | None:
        return self.base_tx.r

    @property
    def s(self) -> U256 | None:
        return self.base_tx.s

    @property
    def is_signed(self) -> bool:
        return self.base_tx.is_signed

    @property
    def type_byte(self) -> int:
        return ZK_TRANSACTION_TYPE

    def with_signature(self, v: int, r: U256 | int, s: U256 | int) -> "ZKTransaction":
        return replace(self, base_tx=self.base_tx.with_signature(v, r, s))

    def _unsigned_rlp_fields(self) -> list[object]:
        return [
            self.base_tx.chain_id,
            int(self.base_tx.nonce),
            int(self.base_tx.max_priority_fee_per_gas),
            int(self.base_tx.max_fee_per_gas),
            int(self.base_tx.gas_limit),
            _encode_destination(self.base_tx.to),
            int(self.base_tx.value),
            self.base_tx.data,
            _encode_access_list(self.base_tx.access_list),
            int(self.proof.proof_type),
            self.proof.data,
            [int(value) for value in self.public_inputs],
        ]

    def signing_payload(self) -> bytes:
        return bytes([self.type_byte]) + encode(self._unsigned_rlp_fields())

    def signing_hash(self) -> Hash:
        return keccak256(self.signing_payload())

    def sign(self, private_key: bytes | bytearray | memoryview | int) -> "ZKTransaction":
        signature = sign_message_hash(self.signing_hash().to_bytes(), private_key)
        return self.with_signature(signature.recovery_id, signature.r, signature.s)

    def encode(self) -> bytes:
        if not self.is_signed:
            raise TransactionEncodingError("ZK transaction must be signed before encoding")
        return bytes([self.type_byte]) + encode(
            self._unsigned_rlp_fields() + [self.v, int(self.r), int(self.s)]
        )

    def tx_hash(self) -> Hash:
        return keccak256(self.encode())

    def sender(self, enforce_low_s: bool = True) -> Address:
        if not self.is_signed:
            raise TransactionSignatureError("ZK transaction is unsigned")
        assert self.v is not None and self.r is not None and self.s is not None
        return _signature_from_values(self.signing_hash(), self.v, self.r, self.s, enforce_low_s=enforce_low_s)

    def intrinsic_gas(self) -> int:
        return self.base_tx.intrinsic_gas()


Transaction: TypeAlias = LegacyTransaction | EIP1559Transaction | ZKTransaction


def _expect_rlp_list(value: object, expected_length: int, label: str) -> list[object]:
    if not isinstance(value, list) or len(value) != expected_length:
        raise TransactionDecodeError(f"{label} payload must decode to a {expected_length}-item RLP list")
    return value


def _expect_bytes(value: object, label: str) -> bytes:
    if not isinstance(value, bytes):
        raise TransactionDecodeError(f"{label} must be an RLP byte string")
    return value


def _decode_legacy(raw: bytes) -> LegacyTransaction:
    try:
        fields = _expect_rlp_list(decode(raw), 9, "legacy transaction")
    except RlpDecodingError as exc:
        raise TransactionDecodeError("invalid legacy transaction RLP") from exc

    nonce, gas_price, gas_limit, destination, value, data, v, r, s = (_expect_bytes(field, "legacy field") for field in fields)
    transaction = LegacyTransaction(
        nonce=_decode_u256(nonce),
        gas_price=_decode_u256(gas_price),
        gas_limit=_decode_u256(gas_limit),
        to=_decode_destination(destination),
        value=_decode_u256(value),
        data=data,
        v=_decode_int(v),
        r=_decode_u256(r),
        s=_decode_u256(s),
    )
    _, chain_id = transaction.recovery_id()
    if chain_id is not None:
        transaction = replace(transaction, chain_id=chain_id)
    return transaction


def _decode_eip1559_payload(payload: bytes) -> EIP1559Transaction:
    try:
        fields = _expect_rlp_list(decode(payload), 12, "EIP-1559 transaction")
    except RlpDecodingError as exc:
        raise TransactionDecodeError("invalid EIP-1559 transaction RLP") from exc

    chain_id, nonce, max_priority_fee, max_fee, gas_limit, destination, value, data, access_list, v, r, s = fields
    return EIP1559Transaction(
        chain_id=_decode_int(_expect_bytes(chain_id, "chain_id")),
        nonce=_decode_u256(_expect_bytes(nonce, "nonce")),
        max_priority_fee_per_gas=_decode_u256(_expect_bytes(max_priority_fee, "max_priority_fee_per_gas")),
        max_fee_per_gas=_decode_u256(_expect_bytes(max_fee, "max_fee_per_gas")),
        gas_limit=_decode_u256(_expect_bytes(gas_limit, "gas_limit")),
        to=_decode_destination(_expect_bytes(destination, "to")),
        value=_decode_u256(_expect_bytes(value, "value")),
        data=_expect_bytes(data, "data"),
        access_list=_decode_access_list(_expect_rlp_list(access_list, len(access_list), "access_list")),
        v=_decode_int(_expect_bytes(v, "v")),
        r=_decode_u256(_expect_bytes(r, "r")),
        s=_decode_u256(_expect_bytes(s, "s")),
    )


def _decode_zk_payload(payload: bytes) -> ZKTransaction:
    try:
        fields = _expect_rlp_list(decode(payload), 15, "ZK transaction")
    except RlpDecodingError as exc:
        raise TransactionDecodeError("invalid ZK transaction RLP") from exc

    (
        chain_id,
        nonce,
        max_priority_fee,
        max_fee,
        gas_limit,
        destination,
        value,
        data,
        access_list,
        proof_type,
        proof_data,
        public_inputs,
        v,
        r,
        s,
    ) = fields

    if not isinstance(public_inputs, list):
        raise TransactionDecodeError("public_inputs must be an RLP list")

    parsed_public_inputs = tuple(_decode_u256(_expect_bytes(item, "public_input")) for item in public_inputs)
    base_tx = EIP1559Transaction(
        chain_id=_decode_int(_expect_bytes(chain_id, "chain_id")),
        nonce=_decode_u256(_expect_bytes(nonce, "nonce")),
        max_priority_fee_per_gas=_decode_u256(_expect_bytes(max_priority_fee, "max_priority_fee_per_gas")),
        max_fee_per_gas=_decode_u256(_expect_bytes(max_fee, "max_fee_per_gas")),
        gas_limit=_decode_u256(_expect_bytes(gas_limit, "gas_limit")),
        to=_decode_destination(_expect_bytes(destination, "to")),
        value=_decode_u256(_expect_bytes(value, "value")),
        data=_expect_bytes(data, "data"),
        access_list=_decode_access_list(_expect_rlp_list(access_list, len(access_list), "access_list")),
        v=_decode_int(_expect_bytes(v, "v")),
        r=_decode_u256(_expect_bytes(r, "r")),
        s=_decode_u256(_expect_bytes(s, "s")),
    )
    proof = ZKProof(ProofType(_decode_int(_expect_bytes(proof_type, "proof_type"))), _expect_bytes(proof_data, "proof_data"))
    return ZKTransaction(base_tx=base_tx, proof=proof, public_inputs=parsed_public_inputs)


def decode_transaction(raw: bytes | bytearray | memoryview) -> Transaction:
    payload = bytes(raw)
    if not payload:
        raise TransactionDecodeError("transaction payload cannot be empty")
    if payload[0] >= 0xC0:
        return _decode_legacy(payload)
    if payload[0] == EIP1559_TRANSACTION_TYPE:
        return _decode_eip1559_payload(payload[1:])
    if payload[0] == ZK_TRANSACTION_TYPE:
        return _decode_zk_payload(payload[1:])
    raise TransactionDecodeError(f"unsupported typed transaction 0x{payload[0]:02x}")
