from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from consensus.attestations import create_attestation, process_attestation
from consensus.block_processing import process_block
from consensus.config import ConsensusConfig
from consensus.epoch_processing import process_slots_until
from consensus.proposer import build_block, get_beacon_proposer_index
from consensus.scoring import recompute_all_scores
from consensus.state import BeaconState, create_genesis_state
from consensus.types import Validator


def make_validator(
    validator_id: int,
    *,
    identity_verified: bool = True,
    effective_stake: int = 64,
    burned_amount: int = 16,
    reputation_score: float = 60.0,
    pow_score: float = 40.0,
    useful_compute_score: float = 60.0,
    storage_committed_bytes: int = 1 << 40,
    storage_challenge_success_rate: float = 0.95,
    storage_replication_score: float = 65.0,
    network_relay_score: float = 60.0,
    network_uptime_score: float = 0.95,
    network_peer_diversity_score: float = 60.0,
    network_coverage_score: float = 60.0,
    network_density_penalty: float = 0.05,
    activity_attestation_score: float = 60.0,
    activity_service_score: float = 50.0,
    activity_challenge_score: float = 50.0,
    active: bool = True,
    slashed: bool = False,
) -> Validator:
    return Validator(
        validator_id=validator_id,
        node_id=f"node-{validator_id}",
        reward_address=f"reward-{validator_id}",
        active=active,
        slashed=slashed,
        effective_stake=effective_stake,
        burned_amount=burned_amount,
        reputation_score=reputation_score,
        identity_verified=identity_verified,
        pow_score=pow_score,
        useful_compute_score=useful_compute_score,
        storage_committed_bytes=storage_committed_bytes,
        storage_challenge_success_rate=storage_challenge_success_rate,
        storage_replication_score=storage_replication_score,
        network_relay_score=network_relay_score,
        network_uptime_score=network_uptime_score,
        network_peer_diversity_score=network_peer_diversity_score,
        network_coverage_score=network_coverage_score,
        network_density_penalty=network_density_penalty,
        activity_attestation_score=activity_attestation_score,
        activity_service_score=activity_service_score,
        activity_challenge_score=activity_challenge_score,
    )


def make_state(
    validators: list[Validator] | None = None,
    *,
    config: ConsensusConfig | None = None,
) -> BeaconState:
    resolved_config = ConsensusConfig() if config is None else config
    resolved_validators = validators or [make_validator(index) for index in range(8)]
    state = create_genesis_state(resolved_validators, resolved_config)
    recompute_all_scores(state)
    return state


def produce_block_with_full_attestations(state: BeaconState, slot: int) -> str:
    process_slots_until(state, slot)
    proposer_index = get_beacon_proposer_index(state, slot)
    block = build_block(state, slot, proposer_index, state.latest_block_root)
    block_root = process_block(state, block)
    committees = __import__("consensus.committees", fromlist=["get_slot_committees"]).get_slot_committees(state, slot)
    for committee_index, committee in enumerate(committees):
        attestation = create_attestation(
            state,
            slot,
            committee,
            block_root,
            committee_index=committee_index,
            participants=list(committee),
        )
        process_attestation(state, attestation)
    return block_root
