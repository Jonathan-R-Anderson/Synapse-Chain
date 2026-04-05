from __future__ import annotations

import argparse
import hashlib
from dataclasses import replace

from .attestations import create_attestation, process_attestation
from .committees import get_slot_committees
from .config import ConsensusConfig
from .epoch_processing import process_slots_until
from .fork_choice import ForkChoiceStore
from .proposer import build_block, get_beacon_proposer_index
from .block_processing import process_block
from .scoring import recompute_all_scores
from .state import BeaconState, create_genesis_state
from .types import Validator
from .utils import deterministic_float


def make_demo_validators(count: int, config: ConsensusConfig) -> list[Validator]:
    validators: list[Validator] = []
    for index in range(count):
        profile = index % 6
        base = Validator(
            validator_id=index,
            node_id=f"node-{index}",
            reward_address=f"reward-{index}",
            identity_verified=profile != 5,
            effective_stake=32 + (index % 10) * 8,
            burned_amount=8 * (index % 7),
            reputation_score=40.0 + (index % 9) * 7.0,
            pow_score=20.0 + (profile == 1) * 60.0,
            useful_compute_score=30.0 + (profile == 2) * 70.0,
            storage_committed_bytes=(1 << 38) * (1 + (profile == 3) * 4 + (index % 3)),
            storage_challenge_success_rate=0.55 + (0.10 * (profile != 5)),
            storage_replication_score=25.0 + (profile == 3) * 55.0,
            network_relay_score=20.0 + (profile == 4) * 65.0,
            network_uptime_score=0.50 + (profile != 5) * 0.45,
            network_peer_diversity_score=20.0 + (profile == 4) * 50.0,
            network_coverage_score=20.0 + (profile == 4) * 60.0,
            network_density_penalty=0.05 if profile != 4 else 0.15,
            activity_attestation_score=25.0 + (profile != 5) * 25.0,
            activity_service_score=20.0 + (profile == 4) * 30.0,
            activity_challenge_score=20.0 + (profile == 3) * 30.0,
            last_active_epoch=0,
            last_attested_epoch=0,
        )
        if profile == 0:
            base = replace(base, effective_stake=256, burned_amount=4, network_uptime_score=0.65)
        if profile == 5:
            base = replace(
                base,
                activity_attestation_score=5.0,
                activity_service_score=5.0,
                activity_challenge_score=5.0,
                reputation_score=10.0,
            )
        validators.append(base)
    state = create_genesis_state(validators, config)
    recompute_all_scores(state)
    return state.validators


def _participant_sample(state: BeaconState, slot: int, validator_index: int) -> bool:
    validator = state.validators[validator_index]
    availability = min(
        0.98,
        0.35 + (validator.cached_total_score * 0.35) + (validator.network_uptime_score * 0.20),
    )
    entropy = deterministic_float(
        hashlib.sha256(f"{slot}:{validator_index}:{validator.node_id}".encode("utf-8")).digest()
    )
    return entropy <= availability


def run_simulation(num_validators: int = 24, epochs: int = 6, *, config: ConsensusConfig | None = None) -> BeaconState:
    resolved_config = ConsensusConfig() if config is None else config
    validators = make_demo_validators(num_validators, resolved_config)
    state = create_genesis_state(validators, resolved_config)
    recompute_all_scores(state)
    store = ForkChoiceStore(
        justified_root=state.justified_checkpoint.root,
        finalized_root=state.finalized_checkpoint.root,
    )
    total_slots = epochs * resolved_config.slots_per_epoch

    for slot in range(1, total_slots + 1):
        process_slots_until(state, slot)
        proposer_index = get_beacon_proposer_index(state, slot)
        block = build_block(state, slot, proposer_index, state.latest_block_root, operations=[{"slot": slot}])
        block_root = process_block(state, block)
        store.on_block(block, state)
        committees = get_slot_committees(state, slot)
        for committee_index, committee in enumerate(committees):
            participants = [index for index in committee if _participant_sample(state, slot, index)]
            if not participants:
                continue
            attestation = create_attestation(
                state,
                slot,
                committee,
                block_root,
                committee_index=committee_index,
                participants=participants,
            )
            process_attestation(state, attestation)
            store.on_attestation(attestation, state)
        if slot % resolved_config.slots_per_epoch == 0:
            head = store.get_head(state)
            print(
                f"epoch={state.epoch:02d} slot={slot:03d} head={head[:10]} "
                f"justified={state.justified_checkpoint.epoch}:{state.justified_checkpoint.root[:8]} "
                f"finalized={state.finalized_checkpoint.epoch}:{state.finalized_checkpoint.root[:8]}"
            )
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the hybrid consensus simulation.")
    parser.add_argument("--validators", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=6)
    args = parser.parse_args(argv)
    run_simulation(num_validators=args.validators, epochs=args.epochs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
