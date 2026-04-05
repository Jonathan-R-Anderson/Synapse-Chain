from __future__ import annotations

import math

from .config import ConsensusConfig
from .state import BeaconState
from .types import Validator
from .utils import bounded_log_normalize, clamp


def _identity_gate(validator: Validator, config: ConsensusConfig) -> float:
    return 1.0 if validator.identity_verified else config.score_decay.unverified_identity_multiplier


def compute_identity_weight(validator: Validator, config: ConsensusConfig) -> float:
    if validator.slashed:
        return 0.0
    burn_component = bounded_log_normalize(
        validator.burned_amount,
        config.score_windows.burn_reference,
        config.score_windows.burn_cap_multiplier,
    )
    reputation_component = clamp(validator.reputation_score / config.score_windows.reputation_reference, 0.0, 1.0)
    registration_component = 1.0 if validator.identity_verified else config.score_decay.unverified_identity_multiplier
    return clamp((0.45 * registration_component) + (0.30 * burn_component) + (0.25 * reputation_component), 0.0, 1.0)


def compute_stake_weight(validator: Validator, config: ConsensusConfig) -> float:
    if validator.slashed:
        return 0.0
    capped = config.score_windows.stake_cap
    base = min(validator.effective_stake, capped) / capped
    if validator.effective_stake <= capped:
        return clamp(base, 0.0, 1.0)
    excess = validator.effective_stake - capped
    excess_component = config.score_windows.excess_stake_diminishing_factor * math.log1p(excess / capped)
    return clamp(base + excess_component, 0.0, 1.25)


def compute_compute_weight(validator: Validator, config: ConsensusConfig) -> float:
    if validator.slashed:
        return 0.0
    pow_component = clamp(validator.pow_score / config.score_windows.pow_reference, 0.0, 1.0)
    useful_component = clamp(
        validator.useful_compute_score / config.score_windows.useful_compute_reference,
        0.0,
        1.0,
    )
    return clamp((0.40 * pow_component) + (0.60 * useful_component), 0.0, 1.0)


def compute_storage_weight(validator: Validator, config: ConsensusConfig) -> float:
    if validator.slashed:
        return 0.0
    committed_component = bounded_log_normalize(
        validator.storage_committed_bytes,
        config.score_windows.storage_reference_bytes,
        8.0,
    )
    replication_component = clamp(validator.storage_replication_score / 100.0, 0.0, 1.0)
    success_component = clamp(validator.storage_challenge_success_rate, 0.0, 1.0)
    combined = (0.45 * committed_component) + (0.25 * replication_component) + (0.30 * success_component)
    if success_component < 0.5:
        combined *= max(config.score_decay.storage_failure_floor, success_component)
    return clamp(combined, 0.0, 1.0)


def compute_network_weight(validator: Validator, config: ConsensusConfig) -> float:
    if validator.slashed:
        return 0.0
    relay_component = clamp(validator.network_relay_score / config.score_windows.relay_reference, 0.0, 1.0)
    uptime_component = clamp(validator.network_uptime_score / config.score_windows.uptime_reference, 0.0, 1.0)
    diversity_component = clamp(
        validator.network_peer_diversity_score / config.score_windows.peer_diversity_reference,
        0.0,
        1.0,
    )
    coverage_component = clamp(
        validator.network_coverage_score / config.score_windows.coverage_reference,
        0.0,
        1.0,
    )
    density_penalty = clamp(
        validator.network_density_penalty,
        0.0,
        config.density_penalty.maximum_penalty,
    )
    combined = (0.25 * relay_component) + (0.30 * uptime_component) + (0.20 * diversity_component) + (0.25 * coverage_component)
    if uptime_component < 0.5:
        combined *= max(config.score_decay.network_failure_floor, uptime_component)
    return clamp(combined * (1.0 - density_penalty), 0.0, 1.0)


def compute_activity_weight(validator: Validator, config: ConsensusConfig, *, current_epoch: int | None = None) -> float:
    if validator.slashed:
        return 0.0
    attestation_component = clamp(
        validator.activity_attestation_score / config.score_windows.activity_reference,
        0.0,
        1.0,
    )
    service_component = clamp(
        validator.activity_service_score / config.score_windows.activity_reference,
        0.0,
        1.0,
    )
    challenge_component = clamp(
        validator.activity_challenge_score / config.score_windows.activity_reference,
        0.0,
        1.0,
    )
    combined = (0.50 * attestation_component) + (0.25 * service_component) + (0.25 * challenge_component)
    if current_epoch is not None:
        idle_epochs = max(0, current_epoch - validator.last_active_epoch)
        combined *= config.score_decay.idle_epoch_decay ** idle_epochs
        combined = max(config.score_decay.activity_floor, combined) if validator.active else 0.0
    return clamp(combined, 0.0, 1.0)


def compute_total_score(validator: Validator, config: ConsensusConfig, *, current_epoch: int | None = None) -> float:
    if validator.slashed or not validator.active:
        return config.slashing.minimum_slash_score
    identity_weight = compute_identity_weight(validator, config)
    stake_weight = compute_stake_weight(validator, config)
    compute_weight = compute_compute_weight(validator, config)
    storage_weight = compute_storage_weight(validator, config)
    network_weight = compute_network_weight(validator, config)
    activity_weight = compute_activity_weight(validator, config, current_epoch=current_epoch)
    weighted_sum = (
        (config.score_weights.identity * identity_weight)
        + (config.score_weights.stake * stake_weight)
        + (config.score_weights.compute * compute_weight)
        + (config.score_weights.storage * storage_weight)
        + (config.score_weights.network * network_weight)
        + (config.score_weights.activity * activity_weight)
    )
    gate = _identity_gate(validator, config)
    return max(config.slashing.minimum_slash_score, weighted_sum * gate)


def recompute_all_scores(state: BeaconState) -> None:
    for index, validator in enumerate(state.validators):
        score = compute_total_score(validator, state.config, current_epoch=state.epoch)
        state.validators[index] = validator.with_cached_score(score, epoch=state.epoch)
