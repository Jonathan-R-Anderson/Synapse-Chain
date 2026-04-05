from __future__ import annotations

import hashlib
import math
from typing import Iterable

from .eligibility import get_eligible_validators
from .state import BeaconState
from .types import Validator
from .utils import deterministic_float


def derive_epoch_randomness(state: BeaconState, epoch: int) -> bytes:
    payload = (
        state.randao_mix
        + epoch.to_bytes(8, byteorder="big", signed=False)
        + state.justified_checkpoint.root.encode("utf-8")
        + state.finalized_checkpoint.root.encode("utf-8")
    )
    return hashlib.sha256(payload).digest()


def _selection_entropy(randomness: bytes, validator_id: int, slot: int, domain: bytes, position: int = 0) -> float:
    payload = (
        randomness
        + domain
        + slot.to_bytes(8, byteorder="big", signed=False)
        + position.to_bytes(4, byteorder="big", signed=False)
        + validator_id.to_bytes(8, byteorder="big", signed=False)
    )
    return deterministic_float(payload)


def compute_validator_lottery_ticket(validator: Validator, randomness: bytes, slot: int) -> float:
    weight = max(validator.cached_total_score, 0.0)
    if weight <= 0:
        return math.inf
    entropy = _selection_entropy(randomness, validator.validator_id, slot, b"proposer")
    return -math.log(entropy) / weight


def _weighted_keys(
    state: BeaconState,
    slot: int,
    eligible_indices: Iterable[int],
    *,
    domain: bytes,
    position: int = 0,
) -> list[tuple[float, int]]:
    randomness = derive_epoch_randomness(state, slot // state.config.slots_per_epoch)
    keys: list[tuple[float, int]] = []
    for index in eligible_indices:
        validator = state.validators[index]
        weight = max(validator.cached_total_score, 0.0)
        if weight <= 0:
            continue
        entropy = _selection_entropy(randomness, validator.validator_id, slot, domain, position=position)
        key = -math.log(entropy) / weight
        keys.append((key, index))
    return keys


def select_weighted_proposer(state: BeaconState, slot: int, eligible_indices: list[int] | None = None) -> int:
    candidates = get_eligible_validators(state, state.config) if eligible_indices is None else list(eligible_indices)
    keys = _weighted_keys(state, slot, candidates, domain=b"proposer")
    if not keys:
        raise ValueError("no eligible validators available for proposer selection")
    keys.sort()
    return keys[0][1]


def sample_weighted_committee(
    state: BeaconState,
    slot: int,
    committee_size: int,
    eligible_indices: list[int] | None = None,
    *,
    committee_index: int = 0,
) -> list[int]:
    if committee_size < 1:
        return []
    candidates = get_eligible_validators(state, state.config) if eligible_indices is None else list(eligible_indices)
    keys = _weighted_keys(state, slot, candidates, domain=b"committee", position=committee_index)
    keys.sort()
    return [index for _, index in keys[: min(committee_size, len(keys))]]
