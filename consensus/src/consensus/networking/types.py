from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from typing import Any

from ..utils import stable_json_bytes


class MessageType(str, Enum):
    TX_GOSSIP = "TX_GOSSIP"
    BLOCK_GOSSIP = "BLOCK_GOSSIP"
    ATTESTATION = "ATTESTATION"
    PEER_DISCOVERY = "PEER_DISCOVERY"
    SYNC_REQUEST = "SYNC_REQUEST"
    SYNC_RESPONSE = "SYNC_RESPONSE"
    SCORE_REPORT = "SCORE_REPORT"
    BFT_PROPOSE = "BFT_PROPOSE"
    BFT_PREVOTE = "BFT_PREVOTE"
    BFT_PRECOMMIT = "BFT_PRECOMMIT"
    BFT_COMMIT = "BFT_COMMIT"


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}
    if is_dataclass(value):
        return {key: _primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(stable_json_bytes(_primitive(payload))).hexdigest()


@dataclass(frozen=True, slots=True)
class Transaction:
    sender: str
    payload: dict[str, Any]
    hash: str | None = None

    def __post_init__(self) -> None:
        computed = payload_hash({"sender": self.sender, "payload": self.payload})
        object.__setattr__(self, "hash", computed if self.hash is None else self.hash)


@dataclass(frozen=True, slots=True)
class Block:
    parent_hash: str
    transactions: tuple[Transaction, ...]
    signatures: tuple[str, ...]
    committee_id: str | None
    proposer_id: int
    height: int
    committee_members: tuple[int, ...] = ()
    hash: str | None = None

    def __post_init__(self) -> None:
        computed = payload_hash(
            {
                "parent_hash": self.parent_hash,
                "transactions": self.transactions,
                "committee_id": self.committee_id,
                "proposer_id": self.proposer_id,
                "height": self.height,
                "committee_members": self.committee_members,
            }
        )
        object.__setattr__(self, "hash", computed if self.hash is None else self.hash)
        object.__setattr__(self, "transactions", tuple(self.transactions))
        object.__setattr__(self, "signatures", tuple(self.signatures))
        object.__setattr__(self, "committee_members", tuple(self.committee_members))

    def with_signatures(self, signatures: tuple[str, ...]) -> "Block":
        return replace(self, signatures=tuple(signatures))


@dataclass(frozen=True, slots=True)
class NodeScore:
    node_id: int
    coverage_radius: float
    packets_relayed: int
    uptime: float
    storage_provided: int
    density: float
    computed_score: float
    signature: str
    observed_by: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Message:
    type: MessageType
    payload: Any
    sender: int
    message_id: str | None = None
    timestamp: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        computed = payload_hash(
            {
                "type": self.type.value,
                "payload": self.payload,
            }
        )
        object.__setattr__(self, "message_id", computed if self.message_id is None else self.message_id)

    def forwarded(self, *, sender: int) -> "Message":
        return Message(
            type=self.type,
            payload=self.payload,
            sender=sender,
            message_id=self.message_id,
            timestamp=self.timestamp,
        )


@dataclass(slots=True)
class PeerHealth:
    latency_ms: float = 0.0
    successful_deliveries: int = 0
    failed_deliveries: int = 0
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def uptime(self) -> float:
        age = max(time.monotonic() - self.first_seen, 1e-6)
        return min(1.0, (self.successful_deliveries / max(1, self.successful_deliveries + self.failed_deliveries)) * (1.0 - (1.0 / (1.0 + age))))


@dataclass(slots=True)
class PeerRecord:
    node_id: int
    region: str
    operator_id: str
    endpoint: str
    health: PeerHealth = field(default_factory=PeerHealth)


@dataclass(frozen=True, slots=True)
class CommitteeSelection:
    committee_id: str
    key: str
    member_ids: tuple[int, ...]
    score_distribution: dict[int, float]
    candidate_ids: tuple[int, ...]
    leader_id: int


@dataclass(frozen=True, slots=True)
class Vote:
    phase: MessageType
    block_hash: str
    committee_id: str
    voter_id: int
    signature: str


@dataclass(frozen=True, slots=True)
class SyncRequest:
    from_height: int
    request_id: str


@dataclass(frozen=True, slots=True)
class SyncResponse:
    request_id: str
    blocks: tuple[Block, ...]
    peer_ids: tuple[int, ...]
