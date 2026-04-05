from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .utils import sha256_hex


@dataclass(frozen=True, slots=True)
class Validator:
    validator_id: int
    node_id: str
    reward_address: str
    active: bool = True
    slashed: bool = False
    exit_epoch: int | None = None
    activation_epoch: int = 0
    effective_stake: int = 32
    burned_amount: int = 0
    reputation_score: float = 0.0
    identity_verified: bool = False
    pow_score: float = 0.0
    useful_compute_score: float = 0.0
    storage_committed_bytes: int = 0
    storage_challenge_success_rate: float = 1.0
    storage_replication_score: float = 0.0
    network_relay_score: float = 0.0
    network_uptime_score: float = 1.0
    network_peer_diversity_score: float = 0.0
    network_coverage_score: float = 0.0
    network_density_penalty: float = 0.0
    activity_attestation_score: float = 0.0
    activity_service_score: float = 0.0
    activity_challenge_score: float = 0.0
    cached_total_score: float = 0.0
    last_active_epoch: int = 0
    last_attested_epoch: int = 0
    last_proposed_slot: int = 0
    last_updated_epoch: int = 0

    def with_cached_score(self, score: float, *, epoch: int) -> "Validator":
        return replace(self, cached_total_score=score, last_updated_epoch=epoch)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    epoch: int
    root: str


@dataclass(frozen=True, slots=True)
class BeaconBlockBody:
    operations: tuple[dict[str, Any], ...] = ()
    attestation_roots: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Attestation:
    slot: int
    committee_index: int
    beacon_block_root: str
    source_checkpoint: Checkpoint
    target_checkpoint: Checkpoint
    attester_indices: tuple[int, ...]
    aggregated_weight: float
    signature: str = ""
    participant_bits: tuple[bool, ...] = ()

    def root(self) -> str:
        return sha256_hex(
            {
                "slot": self.slot,
                "committee_index": self.committee_index,
                "beacon_block_root": self.beacon_block_root,
                "source": self.source_checkpoint,
                "target": self.target_checkpoint,
                "attester_indices": self.attester_indices,
                "aggregated_weight": round(self.aggregated_weight, 12),
                "signature": self.signature,
                "participant_bits": self.participant_bits,
            }
        )


@dataclass(frozen=True, slots=True)
class BeaconBlock:
    slot: int
    proposer_index: int
    parent_root: str
    state_root: str
    body: BeaconBlockBody
    attestations: tuple[Attestation, ...]
    randao_reveal: bytes
    execution_payload_root: str | None = None
    signature: str = ""

    def root(self) -> str:
        return sha256_hex(
            {
                "slot": self.slot,
                "proposer_index": self.proposer_index,
                "parent_root": self.parent_root,
                "state_root": self.state_root,
                "body": self.body,
                "attestations": self.attestations,
                "randao_reveal": self.randao_reveal.hex(),
                "execution_payload_root": self.execution_payload_root,
                "signature": self.signature,
            }
        )


@dataclass(frozen=True, slots=True)
class LatestMessage:
    epoch: int
    block_root: str
