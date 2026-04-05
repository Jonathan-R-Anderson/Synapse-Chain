from __future__ import annotations

from dataclasses import dataclass, field

from evm import LogEntry
from primitives import Address, Hash, U256

from .hashing import address_from_hex, bytes_to_hex, hash_from_hex, hex_to_bytes, keccak256_hash
from .logs_bloom import logs_bloom
from .rlp_codec import RlpDecodingError, rlp_decode, rlp_encode


def _decode_int(raw: bytes) -> int:
    return int.from_bytes(raw, byteorder="big", signed=False) if raw else 0


def _serialize_log(log: LogEntry) -> list[object]:
    return [
        log.address.to_bytes(),
        [topic.to_bytes(U256.BYTE_LENGTH) for topic in log.topics],
        log.data,
    ]


def _deserialize_log(value: object) -> LogEntry:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("receipt logs must decode to [address, topics, data]")
    address_raw, topics_raw, data_raw = value
    if not isinstance(address_raw, bytes) or len(address_raw) != Address.SIZE:
        raise ValueError("receipt log address must be 20 bytes")
    if not isinstance(topics_raw, list):
        raise ValueError("receipt log topics must be a list")
    topics = []
    for topic_raw in topics_raw:
        if not isinstance(topic_raw, bytes) or len(topic_raw) != U256.BYTE_LENGTH:
            raise ValueError("receipt log topics must be 32-byte values")
        topics.append(U256.from_bytes(topic_raw))
    if not isinstance(data_raw, bytes):
        raise ValueError("receipt log data must be bytes")
    return LogEntry(address=Address(address_raw), topics=tuple(topics), data=data_raw)


@dataclass(frozen=True, slots=True)
class Receipt:
    status: int | None = 1
    cumulative_gas_used: int = 0
    gas_used: int = 0
    logs: tuple[LogEntry, ...] = field(default_factory=tuple)
    transaction_type: int | None = None
    contract_address: Address | None = None
    transaction_hash: Hash | None = None
    effective_gas_price: int | None = None
    post_state: Hash | None = None
    bloom: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cumulative_gas_used", int(self.cumulative_gas_used))
        object.__setattr__(self, "gas_used", int(self.gas_used))
        object.__setattr__(self, "logs", tuple(self.logs))
        if self.status is not None:
            object.__setattr__(self, "status", int(self.status))
        if self.effective_gas_price is not None:
            object.__setattr__(self, "effective_gas_price", int(self.effective_gas_price))
        if self.cumulative_gas_used < 0 or self.gas_used < 0:
            raise ValueError("receipt gas values must be non-negative")
        if self.status is None and self.post_state is None:
            raise ValueError("receipt requires either status or post_state")
        if self.status is not None and self.status not in {0, 1}:
            raise ValueError("receipt status must be 0 or 1")
        if self.post_state is not None and not isinstance(self.post_state, Hash):
            object.__setattr__(self, "post_state", Hash(bytes(self.post_state)))
        if self.transaction_hash is not None and not isinstance(self.transaction_hash, Hash):
            object.__setattr__(self, "transaction_hash", Hash(bytes(self.transaction_hash)))
        if self.contract_address is not None and not isinstance(self.contract_address, Address):
            object.__setattr__(self, "contract_address", Address(bytes(self.contract_address)))
        bloom = self.bloom or logs_bloom(self.logs)
        if len(bloom) != 256:
            raise ValueError("receipt bloom must be 256 bytes")
        object.__setattr__(self, "bloom", bytes(bloom))

    @property
    def logs_bloom(self) -> bytes:
        return self.bloom

    def to_rlp_payload(self) -> list[object]:
        first_field: object = self.post_state.to_bytes() if self.post_state is not None else int(self.status or 0)
        return [
            first_field,
            self.cumulative_gas_used,
            self.bloom,
            [_serialize_log(log) for log in self.logs],
        ]

    def rlp_encode(self) -> bytes:
        payload = rlp_encode(self.to_rlp_payload())
        if self.transaction_type is None:
            return payload
        return bytes([self.transaction_type]) + payload

    def serialize(self) -> bytes:
        return rlp_encode(
            [
                self.rlp_encode(),
                self.gas_used,
                b"" if self.contract_address is None else self.contract_address.to_bytes(),
                b"" if self.transaction_hash is None else self.transaction_hash.to_bytes(),
                b"" if self.effective_gas_price is None else self.effective_gas_price,
            ]
        )

    def hash(self) -> Hash:
        return keccak256_hash(self.rlp_encode())

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "post_state": None if self.post_state is None else self.post_state.to_hex(),
            "cumulative_gas_used": self.cumulative_gas_used,
            "gas_used": self.gas_used,
            "transaction_type": self.transaction_type,
            "contract_address": None if self.contract_address is None else self.contract_address.to_hex(),
            "transaction_hash": None if self.transaction_hash is None else self.transaction_hash.to_hex(),
            "effective_gas_price": self.effective_gas_price,
            "logs_bloom": bytes_to_hex(self.bloom),
            "logs": [
                {
                    "address": log.address.to_hex(),
                    "topics": [topic.to_hex() for topic in log.topics],
                    "data": bytes_to_hex(log.data),
                }
                for log in self.logs
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Receipt":
        logs = []
        for log_data in data.get("logs", []):
            assert isinstance(log_data, dict)
            logs.append(
                LogEntry(
                    address=address_from_hex(str(log_data["address"]), label="receipt log address"),
                    topics=tuple(U256.from_hex(str(topic)) for topic in log_data.get("topics", [])),
                    data=hex_to_bytes(str(log_data.get("data", "0x")), label="receipt log data"),
                )
            )
        post_state = data.get("post_state")
        transaction_hash = data.get("transaction_hash")
        contract_address = data.get("contract_address")
        return cls(
            status=data.get("status"),
            post_state=None if post_state is None else hash_from_hex(str(post_state), label="post_state"),
            cumulative_gas_used=int(data.get("cumulative_gas_used", 0)),
            gas_used=int(data.get("gas_used", 0)),
            logs=tuple(logs),
            transaction_type=data.get("transaction_type"),
            contract_address=None if contract_address is None else address_from_hex(str(contract_address), label="contract_address"),
            transaction_hash=None if transaction_hash is None else hash_from_hex(str(transaction_hash), label="transaction_hash"),
            effective_gas_price=data.get("effective_gas_price"),
            bloom=hex_to_bytes(str(data.get("logs_bloom", "0x" + ("00" * 256))), expected_length=256, label="logs_bloom"),
        )

    @classmethod
    def from_rlp_payload(cls, payload: list[object], transaction_type: int | None = None) -> "Receipt":
        if len(payload) != 4 or any(isinstance(item, dict) for item in payload):
            raise ValueError("receipt payload must be a 4-item list")
        state_or_status, cumulative_gas_used_raw, bloom_raw, logs_raw = payload
        if not isinstance(state_or_status, bytes):
            raise ValueError("receipt state/status must be encoded as bytes")
        status: int | None
        post_state: Hash | None
        if len(state_or_status) == Hash.SIZE:
            status = None
            post_state = Hash(state_or_status)
        else:
            status = _decode_int(state_or_status)
            post_state = None
        if not isinstance(cumulative_gas_used_raw, bytes):
            raise ValueError("receipt cumulative_gas_used must be encoded as bytes")
        if not isinstance(bloom_raw, bytes) or len(bloom_raw) != 256:
            raise ValueError("receipt logs bloom must be 256 bytes")
        if not isinstance(logs_raw, list):
            raise ValueError("receipt logs must decode to a list")
        logs = tuple(_deserialize_log(item) for item in logs_raw)
        return cls(
            status=status,
            post_state=post_state,
            cumulative_gas_used=_decode_int(cumulative_gas_used_raw),
            gas_used=0,
            logs=logs,
            transaction_type=transaction_type,
            bloom=bloom_raw,
        )

    @classmethod
    def rlp_decode(cls, payload: bytes | bytearray | memoryview) -> "Receipt":
        raw = bytes(payload)
        transaction_type: int | None = None
        if raw and raw[0] < 0x80:
            transaction_type = raw[0]
            raw = raw[1:]
        try:
            decoded = rlp_decode(raw)
        except RlpDecodingError as exc:
            raise ValueError("invalid receipt RLP payload") from exc
        if not isinstance(decoded, list):
            raise ValueError("receipt RLP payload must decode to a list")
        return cls.from_rlp_payload(decoded, transaction_type=transaction_type)

    @classmethod
    def deserialize(cls, payload: bytes | bytearray | memoryview) -> "Receipt":
        try:
            decoded = rlp_decode(bytes(payload))
        except RlpDecodingError as exc:
            raise ValueError("invalid serialized receipt container") from exc
        if not isinstance(decoded, list) or len(decoded) != 5:
            raise ValueError("serialized receipt container must decode to 5 items")
        raw_receipt, gas_used_raw, contract_address_raw, transaction_hash_raw, effective_gas_price_raw = decoded
        if not isinstance(raw_receipt, bytes):
            raise ValueError("serialized receipt must include canonical receipt bytes")
        receipt = cls.rlp_decode(raw_receipt)
        gas_used = _decode_int(gas_used_raw) if isinstance(gas_used_raw, bytes) else int(gas_used_raw)
        contract_address = None
        if contract_address_raw:
            if not isinstance(contract_address_raw, bytes) or len(contract_address_raw) != Address.SIZE:
                raise ValueError("serialized contract_address must be 20 bytes")
            contract_address = Address(contract_address_raw)
        transaction_hash = None
        if transaction_hash_raw:
            if not isinstance(transaction_hash_raw, bytes) or len(transaction_hash_raw) != Hash.SIZE:
                raise ValueError("serialized transaction_hash must be 32 bytes")
            transaction_hash = Hash(transaction_hash_raw)
        effective_gas_price = None
        if effective_gas_price_raw:
            if not isinstance(effective_gas_price_raw, bytes):
                raise ValueError("serialized effective_gas_price must be minimally encoded bytes")
            effective_gas_price = _decode_int(effective_gas_price_raw)
        return cls(
            status=receipt.status,
            cumulative_gas_used=receipt.cumulative_gas_used,
            gas_used=gas_used,
            logs=receipt.logs,
            transaction_type=receipt.transaction_type,
            contract_address=contract_address,
            transaction_hash=transaction_hash,
            effective_gas_price=effective_gas_price,
            post_state=receipt.post_state,
            bloom=receipt.bloom,
        )
