from __future__ import annotations

from .eligibility import get_eligible_validators
from .state import BeaconState
from .types import Checkpoint


def get_total_active_weight(state: BeaconState) -> float:
    return sum(state.validators[index].cached_total_score for index in get_eligible_validators(state, state.config))


def get_attesting_weight(state: BeaconState, checkpoint: Checkpoint) -> float:
    seen: set[int] = set()
    weight = 0.0
    for attestation in state.checkpoint_votes.get(checkpoint.epoch, []):
        if attestation.target_checkpoint != checkpoint:
            continue
        for index in attestation.attester_indices:
            if index in seen:
                continue
            seen.add(index)
            weight += state.validators[index].cached_total_score
    return weight


def process_justification_and_finalization(state: BeaconState) -> None:
    total_weight = get_total_active_weight(state)
    if total_weight <= 0:
        return
    current_epoch = state.epoch
    previous_epoch = max(0, current_epoch - 1)
    previous_checkpoint = state.checkpoint_for_epoch(previous_epoch)
    current_checkpoint = state.checkpoint_for_epoch(current_epoch)

    previous_support = get_attesting_weight(state, previous_checkpoint) / total_weight
    current_support = get_attesting_weight(state, current_checkpoint) / total_weight
    prior_justified = state.justified_checkpoint

    previous_justified = previous_support >= state.config.fork_choice.justified_weight_threshold
    current_justified = current_support >= state.config.fork_choice.justified_weight_threshold

    if previous_justified:
        if prior_justified.epoch + 1 == previous_checkpoint.epoch and prior_justified != state.finalized_checkpoint:
            state.finalized_checkpoint = prior_justified
            state.finalized_history.append(prior_justified)
        if previous_checkpoint != state.justified_checkpoint:
            state.previous_justified_checkpoint = state.justified_checkpoint
            state.justified_checkpoint = previous_checkpoint
            state.justified_history.append(previous_checkpoint)

    if current_justified:
        parent_candidate = previous_checkpoint if previous_justified else prior_justified
        if parent_candidate.epoch + 1 == current_checkpoint.epoch and parent_candidate != state.finalized_checkpoint:
            state.finalized_checkpoint = parent_candidate
            state.finalized_history.append(parent_candidate)
        if current_checkpoint != state.justified_checkpoint:
            state.previous_justified_checkpoint = state.justified_checkpoint
            state.justified_checkpoint = current_checkpoint
            state.justified_history.append(current_checkpoint)
