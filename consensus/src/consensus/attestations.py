from __future__ import annotations

from dataclasses import replace

from .committees import get_slot_committees
from .eligibility import is_validator_eligible
from .state import BeaconState
from .types import Attestation
from .validator import set_attested_epoch


def create_attestation(
    state: BeaconState,
    slot: int,
    committee: list[int],
    block_root: str,
    *,
    committee_index: int = 0,
    participants: list[int] | None = None,
) -> Attestation:
    target_epoch = slot // state.config.slots_per_epoch
    attesters = tuple(committee if participants is None else participants)
    aggregated_weight = sum(state.validators[index].cached_total_score for index in attesters)
    participant_bits = tuple(index in attesters for index in committee)
    return Attestation(
        slot=slot,
        committee_index=committee_index,
        beacon_block_root=block_root,
        source_checkpoint=state.justified_checkpoint,
        target_checkpoint=state.checkpoint_for_epoch(target_epoch),
        attester_indices=attesters,
        aggregated_weight=aggregated_weight,
        signature=f"aggregate:{committee_index}:{slot}:{len(attesters)}",
        participant_bits=participant_bits,
    )


def validate_attestation(state: BeaconState, attestation: Attestation) -> bool:
    if attestation.slot > state.slot:
        return False
    committees = get_slot_committees(state, attestation.slot)
    if attestation.committee_index >= len(committees):
        return False
    committee = committees[attestation.committee_index]
    if any(index not in committee for index in attestation.attester_indices):
        return False
    if any(not is_validator_eligible(state.validators[index], state, state.config) for index in attestation.attester_indices):
        return False
    expected_weight = sum(state.validators[index].cached_total_score for index in attestation.attester_indices)
    return abs(expected_weight - attestation.aggregated_weight) < 1e-9


def process_attestation(state: BeaconState, attestation: Attestation) -> None:
    if not validate_attestation(state, attestation):
        raise ValueError("invalid attestation")
    state.pending_attestations.append(attestation)
    state.checkpoint_votes.setdefault(attestation.target_checkpoint.epoch, []).append(attestation)
    participants = state.epoch_participation.setdefault(attestation.target_checkpoint.epoch, set())
    for index in attestation.attester_indices:
        participants.add(index)
        validator = state.validators[index]
        updated = replace(
            validator,
            activity_attestation_score=validator.activity_attestation_score + 5.0,
            last_attested_epoch=attestation.target_checkpoint.epoch,
            last_active_epoch=attestation.target_checkpoint.epoch,
        )
        state.validators[index] = updated
