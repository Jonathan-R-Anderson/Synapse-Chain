from __future__ import annotations

from dataclasses import replace

from .finality import process_justification_and_finalization
from .penalties import apply_attestation_rewards, apply_inactivity_penalties, apply_resource_penalties
from .scoring import recompute_all_scores
from .state import BeaconState


def process_epoch(state: BeaconState) -> None:
    previous_epoch = max(0, state.epoch - 1)
    apply_attestation_rewards(state, previous_epoch)
    apply_inactivity_penalties(state, previous_epoch)
    apply_resource_penalties(state)
    for index, validator in enumerate(state.validators):
        idle_epochs = max(0, state.epoch - validator.last_active_epoch)
        if idle_epochs <= 0 or validator.slashed:
            continue
        decay = state.config.score_decay.idle_epoch_decay ** idle_epochs
        state.validators[index] = replace(
            validator,
            activity_attestation_score=max(0.0, validator.activity_attestation_score * decay),
            activity_service_score=max(0.0, validator.activity_service_score * decay),
            activity_challenge_score=max(0.0, validator.activity_challenge_score * decay),
        )
    process_justification_and_finalization(state)
    recompute_all_scores(state)
    minimum_epoch = max(0, state.epoch - 1)
    state.pending_attestations = [
        attestation for attestation in state.pending_attestations if attestation.target_checkpoint.epoch >= minimum_epoch
    ]
    state.checkpoint_votes = {
        epoch: list(attestations)
        for epoch, attestations in state.checkpoint_votes.items()
        if epoch >= minimum_epoch
    }
    state.epoch_participation = {
        epoch: set(indices)
        for epoch, indices in state.epoch_participation.items()
        if epoch >= minimum_epoch
    }


def process_slot(state: BeaconState) -> None:
    state.slot += 1
    state.block_roots.setdefault(state.slot, state.latest_block_root)
    state.record_state_root()
    new_epoch = state.current_epoch()
    if new_epoch != state.epoch:
        state.epoch = new_epoch
        process_epoch(state)


def process_slots_until(state: BeaconState, target_slot: int) -> None:
    if target_slot < state.slot:
        raise ValueError("target_slot must not be behind the current slot")
    while state.slot < target_slot:
        process_slot(state)
