from __future__ import annotations

from .eligibility import get_eligible_validators, is_validator_eligible
from .randomness import derive_epoch_randomness, select_weighted_proposer
from .state import BeaconState
from .types import BeaconBlock, BeaconBlockBody


def get_beacon_proposer_index(state: BeaconState, slot: int) -> int:
    return select_weighted_proposer(state, slot, get_eligible_validators(state, state.config))


def build_block(
    state: BeaconState,
    slot: int,
    proposer_index: int,
    parent_root: str,
    operations: list[dict[str, object]] | None = None,
) -> BeaconBlock:
    validator = state.validators[proposer_index]
    if not is_validator_eligible(validator, state, state.config):
        raise ValueError("proposer is not eligible")
    if proposer_index != get_beacon_proposer_index(state, slot):
        raise ValueError("proposer index does not match deterministic selection")
    included_attestations = tuple(attestation for attestation in state.pending_attestations if attestation.slot < slot)
    body = BeaconBlockBody(
        operations=tuple(() if operations is None else operations),
        attestation_roots=tuple(attestation.root() for attestation in included_attestations),
    )
    return BeaconBlock(
        slot=slot,
        proposer_index=proposer_index,
        parent_root=parent_root,
        state_root=state.state_root(),
        body=body,
        attestations=included_attestations,
        randao_reveal=derive_epoch_randomness(state, slot // state.config.slots_per_epoch),
        execution_payload_root=None,
        signature=f"sig:{validator.node_id}:{slot}",
    )
