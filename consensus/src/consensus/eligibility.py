from __future__ import annotations

from .config import ConsensusConfig
from .scoring import (
    compute_activity_weight,
    compute_identity_weight,
    compute_network_weight,
    compute_storage_weight,
    compute_total_score,
)
from .state import BeaconState
from .types import Validator


def is_validator_eligible(validator: Validator, state: BeaconState, config: ConsensusConfig) -> bool:
    current_epoch = state.epoch
    if not validator.active or validator.slashed:
        return False
    if current_epoch < validator.activation_epoch:
        return False
    if validator.exit_epoch is not None and current_epoch >= validator.exit_epoch:
        return False
    if config.eligibility_thresholds.require_identity_verified and not validator.identity_verified:
        return False
    if validator.effective_stake < config.eligibility_thresholds.min_effective_stake:
        return False
    identity_weight = compute_identity_weight(validator, config)
    storage_weight = compute_storage_weight(validator, config)
    network_weight = compute_network_weight(validator, config)
    activity_weight = compute_activity_weight(validator, config, current_epoch=current_epoch)
    total_score = compute_total_score(validator, config, current_epoch=current_epoch)
    if identity_weight < config.eligibility_thresholds.min_identity_weight:
        return False
    if storage_weight < config.eligibility_thresholds.min_storage_weight:
        return False
    if network_weight < config.eligibility_thresholds.min_network_weight:
        return False
    if activity_weight < config.eligibility_thresholds.min_activity_weight:
        return False
    if total_score < config.eligibility_thresholds.min_total_score:
        return False
    participation = activity_weight
    if participation < config.eligibility_thresholds.min_attestation_participation:
        return False
    return True


def get_eligible_validators(state: BeaconState, config: ConsensusConfig | None = None) -> list[int]:
    resolved_config = state.config if config is None else config
    return [
        index
        for index, validator in enumerate(state.validators)
        if is_validator_eligible(validator, state, resolved_config)
    ]
