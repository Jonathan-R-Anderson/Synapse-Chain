from __future__ import annotations

import math

from .eligibility import get_eligible_validators
from .randomness import sample_weighted_committee
from .state import BeaconState


def get_slot_committees(state: BeaconState, slot: int) -> list[list[int]]:
    eligible = get_eligible_validators(state, state.config)
    if not eligible:
        return []
    committee_count = max(
        1,
        min(
            state.config.target_committee_count,
            math.ceil(len(eligible) / state.config.max_validators_per_committee),
        ),
    )
    committee_size = max(
        1,
        min(
            state.config.max_validators_per_committee,
            math.ceil(len(eligible) / committee_count),
        ),
    )
    committees: list[list[int]] = []
    remaining = list(eligible)
    for committee_index in range(committee_count):
        source = remaining if remaining else eligible
        committee = sample_weighted_committee(
            state,
            slot,
            committee_size,
            source,
            committee_index=committee_index,
        )
        committees.append(committee)
        remaining = [index for index in remaining if index not in committee]
    return committees
