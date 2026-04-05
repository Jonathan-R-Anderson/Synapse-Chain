from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from primitives import Address, Hash, U256

from ..block import Block, BlockHeader
from ..receipt import Receipt
from .node_types import NodeType, SyncMode


def _stable_json_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def _normalize_hex(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = str(value).lower()
    return lowered if lowered.startswith("0x") else f"0x{lowered}"


@dataclass(frozen=True, slots=True)
class AccountState:
    """Serializable execution-state account representation used by sync and snapshots."""

    address: Address
    nonce: int = 0
    balance: int = 0
    code: bytes = b""
    storage: tuple[tuple[U256, U256], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", self.address if isinstance(self.address, Address) else Address(bytes(self.address)))
        object.__setattr__(self, "nonce", int(self.nonce))
        object.__setattr__(self, "balance", int(self.balance))
        object.__setattr__(self, "code", bytes(self.code))
        normalized_storage = tuple(
            sorted(
                (
                    slot if isinstance(slot, U256) else U256(int(slot)),
                    value if isinstance(value, U256) else U256(int(value)),
                )
                for slot, value in self.storage
            )
        )
        object.__setattr__(self, "storage", normalized_storage)
        if self.nonce < 0:
            raise ValueError("account nonce must be non-negative")
        if self.balance < 0:
            raise ValueError("account balance must be non-negative")

    def key(self) -> str:
        return self.address.to_hex()

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address.to_hex(),
            "nonce": self.nonce,
            "balance": self.balance,
            "code": "0x" + self.code.hex(),
            "storage": {slot.to_hex(): value.to_hex() for slot, value in self.storage},
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AccountState":
        storage_payload = payload.get("storage", {})
        storage_items: list[tuple[U256, U256]] = []
        if isinstance(storage_payload, dict):
            for key, value in storage_payload.items():
                storage_items.append((U256.from_hex(str(key)), U256.from_hex(str(value))))
        else:
            for key, value in storage_payload:
                storage_items.append((U256.from_hex(str(key)), U256.from_hex(str(value))))
        code_hex = str(payload.get("code", "0x"))
        normalized_code = code_hex[2:] if code_hex.startswith("0x") else code_hex
        return cls(
            address=Address.from_hex(str(payload["address"])),
            nonce=int(payload.get("nonce", 0)),
            balance=int(payload.get("balance", 0)),
            code=bytes.fromhex(normalized_code),
            storage=tuple(storage_items),
        )


@dataclass(frozen=True, slots=True)
class RoleCapabilities:
    """Advertised runtime and peer capabilities derived from configured roles."""

    node_types: frozenset[NodeType] = field(default_factory=frozenset)
    serves_headers: bool = False
    serves_blocks: bool = False
    serves_snapshots: bool = False
    serves_state_chunks: bool = False
    serves_proofs: bool = False
    archive_available: bool = False
    validator: bool = False
    zk_prover: bool = False
    zk_verifier: bool = False
    stores_full_state: bool = False
    headers_only: bool = False
    discovery_only: bool = False
    requires_full_validation: bool = False
    pruning_allowed: bool = True
    generates_snapshots: bool = False
    exposes_rpc: bool = False
    maintains_indexes: bool = False
    supports_dht: bool = False
    state_provider: bool = False

    def matches(self, **requirements: bool | None) -> bool:
        for attribute, expected in requirements.items():
            if expected is None:
                continue
            if getattr(self, attribute) is not expected:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_types": sorted(node_type.value for node_type in self.node_types),
            "serves_headers": self.serves_headers,
            "serves_blocks": self.serves_blocks,
            "serves_snapshots": self.serves_snapshots,
            "serves_state_chunks": self.serves_state_chunks,
            "serves_proofs": self.serves_proofs,
            "archive_available": self.archive_available,
            "validator": self.validator,
            "zk_prover": self.zk_prover,
            "zk_verifier": self.zk_verifier,
            "stores_full_state": self.stores_full_state,
            "headers_only": self.headers_only,
            "discovery_only": self.discovery_only,
            "requires_full_validation": self.requires_full_validation,
            "pruning_allowed": self.pruning_allowed,
            "generates_snapshots": self.generates_snapshots,
            "exposes_rpc": self.exposes_rpc,
            "maintains_indexes": self.maintains_indexes,
            "supports_dht": self.supports_dht,
            "state_provider": self.state_provider,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RoleCapabilities":
        return cls(
            node_types=frozenset(NodeType(value) for value in payload.get("node_types", ())),
            serves_headers=bool(payload.get("serves_headers", False)),
            serves_blocks=bool(payload.get("serves_blocks", False)),
            serves_snapshots=bool(payload.get("serves_snapshots", False)),
            serves_state_chunks=bool(payload.get("serves_state_chunks", False)),
            serves_proofs=bool(payload.get("serves_proofs", False)),
            archive_available=bool(payload.get("archive_available", False)),
            validator=bool(payload.get("validator", False)),
            zk_prover=bool(payload.get("zk_prover", False)),
            zk_verifier=bool(payload.get("zk_verifier", False)),
            stores_full_state=bool(payload.get("stores_full_state", False)),
            headers_only=bool(payload.get("headers_only", False)),
            discovery_only=bool(payload.get("discovery_only", False)),
            requires_full_validation=bool(payload.get("requires_full_validation", False)),
            pruning_allowed=bool(payload.get("pruning_allowed", True)),
            generates_snapshots=bool(payload.get("generates_snapshots", False)),
            exposes_rpc=bool(payload.get("exposes_rpc", False)),
            maintains_indexes=bool(payload.get("maintains_indexes", False)),
            supports_dht=bool(payload.get("supports_dht", False)),
            state_provider=bool(payload.get("state_provider", False)),
        )


@dataclass(slots=True)
class PeerInfo:
    """Peer metadata tracked by the peer manager and persisted in checkpoints."""

    peer_id: str
    endpoint: str
    capabilities: RoleCapabilities
    latency_ms: float | None = None
    score: float = 0.0
    responsiveness: float = 0.0
    correctness: float = 0.0
    completeness: float = 0.0
    invalid_responses: int = 0
    last_seen: float | None = None
    head_number: int | None = None
    head_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.endpoint = str(self.endpoint)
        self.score = float(self.score)
        if self.latency_ms is not None:
            self.latency_ms = float(self.latency_ms)
        if self.head_number is not None:
            self.head_number = int(self.head_number)
        self.head_hash = _normalize_hex(self.head_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "endpoint": self.endpoint,
            "capabilities": self.capabilities.to_dict(),
            "latency_ms": self.latency_ms,
            "score": self.score,
            "responsiveness": self.responsiveness,
            "correctness": self.correctness,
            "completeness": self.completeness,
            "invalid_responses": self.invalid_responses,
            "last_seen": self.last_seen,
            "head_number": self.head_number,
            "head_hash": self.head_hash,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PeerInfo":
        return cls(
            peer_id=str(payload["peer_id"]),
            endpoint=str(payload.get("endpoint", payload["peer_id"])),
            capabilities=RoleCapabilities.from_dict(payload.get("capabilities", {})),
            latency_ms=payload.get("latency_ms"),
            score=float(payload.get("score", 0.0)),
            responsiveness=float(payload.get("responsiveness", 0.0)),
            correctness=float(payload.get("correctness", 0.0)),
            completeness=float(payload.get("completeness", 0.0)),
            invalid_responses=int(payload.get("invalid_responses", 0)),
            last_seen=payload.get("last_seen"),
            head_number=payload.get("head_number"),
            head_hash=payload.get("head_hash"),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(frozen=True, slots=True)
class StateChunk:
    """Deterministic snapshot chunk metadata."""

    snapshot_id: str
    chunk_id: str
    first_key: str | None
    last_key: str | None
    chunk_hash: str
    record_count: int
    compression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "chunk_id": self.chunk_id,
            "first_key": self.first_key,
            "last_key": self.last_key,
            "chunk_hash": self.chunk_hash,
            "record_count": self.record_count,
            "compression": self.compression,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StateChunk":
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            chunk_id=str(payload["chunk_id"]),
            first_key=payload.get("first_key"),
            last_key=payload.get("last_key"),
            chunk_hash=str(payload["chunk_hash"]),
            record_count=int(payload.get("record_count", 0)),
            compression=payload.get("compression"),
        )


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """State snapshot metadata and chunk catalog."""

    snapshot_id: str
    block_height: int
    block_hash: str
    state_root: str
    created_at: float
    chunks: tuple[StateChunk, ...]
    compression: str | None = None
    manifest_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "block_height", int(self.block_height))
        object.__setattr__(self, "block_hash", str(self.block_hash))
        object.__setattr__(self, "state_root", str(self.state_root))
        object.__setattr__(self, "chunks", tuple(self.chunks))
        if self.manifest_hash is None:
            object.__setattr__(self, "manifest_hash", self.compute_hash())

    def content_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "block_height": self.block_height,
            "block_hash": self.block_hash,
            "state_root": self.state_root,
            "created_at": self.created_at,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "compression": self.compression,
        }

    def compute_hash(self) -> str:
        return _stable_json_digest(self.content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self.content_dict()
        payload["manifest_hash"] = self.manifest_hash
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SnapshotManifest":
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            block_height=int(payload["block_height"]),
            block_hash=str(payload["block_hash"]),
            state_root=str(payload["state_root"]),
            created_at=float(payload["created_at"]),
            chunks=tuple(StateChunk.from_dict(chunk) for chunk in payload.get("chunks", ())),
            compression=payload.get("compression"),
            manifest_hash=payload.get("manifest_hash"),
        )


@dataclass(frozen=True, slots=True)
class StateProof:
    """Abstract state-proof payload."""

    proof_type: str
    root: str
    key: str
    value: str | None
    exists: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "proof_type": self.proof_type,
            "root": self.root,
            "key": self.key,
            "value": self.value,
            "exists": self.exists,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StateProof":
        return cls(
            proof_type=str(payload["proof_type"]),
            root=str(payload["root"]),
            key=str(payload["key"]),
            value=payload.get("value"),
            exists=bool(payload.get("exists", True)),
        )


@dataclass(frozen=True, slots=True)
class MerkleProof(StateProof):
    """Concrete Merkle proof used for light-client fragment verification."""

    siblings: tuple[str, ...] = ()
    path: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "siblings", tuple(str(item) for item in self.siblings))
        object.__setattr__(self, "path", tuple(int(bit) for bit in self.path))
        if self.proof_type != "merkle":
            object.__setattr__(self, "proof_type", "merkle")

    def to_dict(self) -> dict[str, Any]:
        payload = StateProof.to_dict(self)
        payload["siblings"] = list(self.siblings)
        payload["path"] = list(self.path)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MerkleProof":
        return cls(
            proof_type="merkle",
            root=str(payload["root"]),
            key=str(payload["key"]),
            value=payload.get("value"),
            exists=bool(payload.get("exists", True)),
            siblings=tuple(str(item) for item in payload.get("siblings", ())),
            path=tuple(int(item) for item in payload.get("path", ())),
        )


@dataclass(frozen=True, slots=True)
class ChainSegment:
    """Partially downloaded or competing header segment."""

    start_height: int
    end_height: int
    header_hashes: tuple[str, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "start_height", int(self.start_height))
        object.__setattr__(self, "end_height", int(self.end_height))
        object.__setattr__(self, "header_hashes", tuple(str(item) for item in self.header_hashes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_height": self.start_height,
            "end_height": self.end_height,
            "header_hashes": list(self.header_hashes),
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChainSegment":
        return cls(
            start_height=int(payload["start_height"]),
            end_height=int(payload["end_height"]),
            header_hashes=tuple(str(item) for item in payload.get("header_hashes", ())),
            complete=bool(payload.get("complete", True)),
        )


@dataclass(slots=True)
class SyncCheckpoint:
    """Persistent sync checkpoint used for crash-safe restart and resume."""

    mode: SyncMode
    stage: str = "idle"
    last_synced_header_height: int = 0
    last_synced_header_hash: str | None = None
    last_applied_block_height: int = 0
    last_applied_block_hash: str | None = None
    snapshot_point_height: int | None = None
    snapshot_block_hash: str | None = None
    pending_state_chunks: tuple[str, ...] = ()
    peer_scores: dict[str, float] = field(default_factory=dict)
    canonical_head_height: int = 0
    canonical_head_hash: str | None = None
    state_reconstruction_complete: bool = False
    steady_state: bool = False
    last_error: str | None = None

    def __post_init__(self) -> None:
        self.last_synced_header_height = int(self.last_synced_header_height)
        self.last_applied_block_height = int(self.last_applied_block_height)
        self.canonical_head_height = int(self.canonical_head_height)
        if self.snapshot_point_height is not None:
            self.snapshot_point_height = int(self.snapshot_point_height)
        self.last_synced_header_hash = _normalize_hex(self.last_synced_header_hash)
        self.last_applied_block_hash = _normalize_hex(self.last_applied_block_hash)
        self.snapshot_block_hash = _normalize_hex(self.snapshot_block_hash)
        self.canonical_head_hash = _normalize_hex(self.canonical_head_hash)
        self.pending_state_chunks = tuple(str(item) for item in self.pending_state_chunks)
        self.peer_scores = {str(peer_id): float(score) for peer_id, score in self.peer_scores.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "stage": self.stage,
            "last_synced_header_height": self.last_synced_header_height,
            "last_synced_header_hash": self.last_synced_header_hash,
            "last_applied_block_height": self.last_applied_block_height,
            "last_applied_block_hash": self.last_applied_block_hash,
            "snapshot_point_height": self.snapshot_point_height,
            "snapshot_block_hash": self.snapshot_block_hash,
            "pending_state_chunks": list(self.pending_state_chunks),
            "peer_scores": dict(self.peer_scores),
            "canonical_head_height": self.canonical_head_height,
            "canonical_head_hash": self.canonical_head_hash,
            "state_reconstruction_complete": self.state_reconstruction_complete,
            "steady_state": self.steady_state,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SyncCheckpoint":
        return cls(
            mode=SyncMode(str(payload["mode"])),
            stage=str(payload.get("stage", "idle")),
            last_synced_header_height=int(payload.get("last_synced_header_height", 0)),
            last_synced_header_hash=payload.get("last_synced_header_hash"),
            last_applied_block_height=int(payload.get("last_applied_block_height", 0)),
            last_applied_block_hash=payload.get("last_applied_block_hash"),
            snapshot_point_height=payload.get("snapshot_point_height"),
            snapshot_block_hash=payload.get("snapshot_block_hash"),
            pending_state_chunks=tuple(payload.get("pending_state_chunks", ())),
            peer_scores=dict(payload.get("peer_scores", {})),
            canonical_head_height=int(payload.get("canonical_head_height", 0)),
            canonical_head_hash=payload.get("canonical_head_hash"),
            state_reconstruction_complete=bool(payload.get("state_reconstruction_complete", False)),
            steady_state=bool(payload.get("steady_state", False)),
            last_error=payload.get("last_error"),
        )


@dataclass(slots=True)
class SyncProgress:
    """Current observable sync state exposed to runtimes and RPC-style surfaces."""

    mode: SyncMode
    stage: str
    current_height: int = 0
    target_height: int = 0
    synced_headers: int = 0
    applied_blocks: int = 0
    state_complete: bool = False
    steady_state: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.current_height = int(self.current_height)
        self.target_height = int(self.target_height)
        self.synced_headers = int(self.synced_headers)
        self.applied_blocks = int(self.applied_blocks)

    @property
    def fraction_complete(self) -> float:
        if self.target_height <= 0:
            return 1.0 if self.steady_state else 0.0
        return min(1.0, max(self.current_height, self.applied_blocks) / float(self.target_height))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "stage": self.stage,
            "current_height": self.current_height,
            "target_height": self.target_height,
            "synced_headers": self.synced_headers,
            "applied_blocks": self.applied_blocks,
            "state_complete": self.state_complete,
            "steady_state": self.steady_state,
            "fraction_complete": self.fraction_complete,
            "details": self.details,
        }

    @classmethod
    def from_checkpoint(cls, checkpoint: SyncCheckpoint) -> "SyncProgress":
        return cls(
            mode=checkpoint.mode,
            stage=checkpoint.stage,
            current_height=max(checkpoint.last_synced_header_height, checkpoint.last_applied_block_height),
            target_height=checkpoint.canonical_head_height,
            synced_headers=checkpoint.last_synced_header_height,
            applied_blocks=checkpoint.last_applied_block_height,
            state_complete=checkpoint.state_reconstruction_complete,
            steady_state=checkpoint.steady_state,
            details={
                "snapshot_point_height": checkpoint.snapshot_point_height,
                "pending_state_chunks": list(checkpoint.pending_state_chunks),
            },
        )


__all__ = [
    "AccountState",
    "Block",
    "BlockHeader",
    "ChainSegment",
    "MerkleProof",
    "PeerInfo",
    "Receipt",
    "RoleCapabilities",
    "SnapshotManifest",
    "StateChunk",
    "StateProof",
    "SyncCheckpoint",
    "SyncProgress",
]
