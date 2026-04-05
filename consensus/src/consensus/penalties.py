from __future__ import annotations

from dataclasses import replace

from .scoring import recompute_all_scores
from .state import BeaconState


def apply_proposer_rewards(state: BeaconState, proposer_index: int, slot: int) -> None:
    validator = state.validators[proposer_index]
    reward_units = max(1, int(validator.cached_total_score * 10))
    state.increase_balance(proposer_index, reward_units)
    state.validators[proposer_index] = replace(
        validator,
        activity_service_score=validator.activity_service_score + 2.0,
        reputation_score=validator.reputation_score + 1.0,
        last_active_epoch=state.epoch,
        last_proposed_slot=slot,
    )


def apply_attestation_rewards(state: BeaconState, epoch: int) -> None:
    participants = state.epoch_participation.get(epoch, set())
    for index in participants:
        validator = state.validators[index]
        reward_units = max(1, int(validator.cached_total_score * 4))
        state.increase_balance(index, reward_units)
        state.validators[index] = replace(
            validator,
            activity_attestation_score=validator.activity_attestation_score + 1.5,
            reputation_score=validator.reputation_score + 0.25,
            last_active_epoch=epoch,
            last_attested_epoch=epoch,
        )


def apply_inactivity_penalties(state: BeaconState, epoch: int) -> None:
    participants = state.epoch_participation.get(epoch, set())
    for index, validator in enumerate(state.validators):
        if validator.slashed or not validator.active:
            continue
        if index in participants:
            state.inactivity_scores[index] = max(
                0.0,
                state.inactivity_scores[index] - state.config.inactivity.attestation_reward,
            )
            continue
        state.inactivity_scores[index] += state.config.inactivity.base_penalty
        if epoch - validator.last_attested_epoch >= state.config.inactivity.leak_threshold_epochs:
            state.inactivity_scores[index] += state.config.inactivity.leak_penalty_rate
        penalty_units = max(1, int(max(validator.cached_total_score, 0.05) * 3))
        state.decrease_balance(index, penalty_units)
        state.validators[index] = replace(
            validator,
            activity_attestation_score=max(0.0, validator.activity_attestation_score * state.config.score_decay.idle_epoch_decay),
            activity_service_score=max(0.0, validator.activity_service_score * state.config.score_decay.idle_epoch_decay),
            activity_challenge_score=max(0.0, validator.activity_challenge_score * state.config.score_decay.idle_epoch_decay),
        )


def apply_resource_penalties(state: BeaconState) -> None:
    for index, validator in enumerate(state.validators):
        if validator.slashed:
            continue
        updated = validator
        balance_penalty = 0
        if validator.storage_challenge_success_rate < 0.5:
            updated = replace(
                updated,
                reputation_score=max(0.0, updated.reputation_score - 1.0),
                activity_challenge_score=max(0.0, updated.activity_challenge_score - 1.0),
            )
            balance_penalty += 1
        if validator.network_uptime_score < 0.5:
            updated = replace(
                updated,
                network_relay_score=max(0.0, updated.network_relay_score - 1.0),
                reputation_score=max(0.0, updated.reputation_score - 1.0),
            )
            balance_penalty += 1
        if balance_penalty:
            state.decrease_balance(index, balance_penalty)
            state.validators[index] = updated


def slash_validator(state: BeaconState, index: int, reason: str) -> None:
    validator = state.validators[index]
    if validator.slashed:
        return
    reduced_stake = int(validator.effective_stake * (1.0 - state.config.slashing.stake_penalty_fraction))
    reduced_reputation = validator.reputation_score * (1.0 - state.config.slashing.reputation_penalty_fraction)
    state.validators[index] = replace(
        validator,
        active=False,
        slashed=True,
        exit_epoch=state.epoch + state.config.slashing.force_exit_epochs,
        effective_stake=max(0, reduced_stake),
        reputation_score=max(0.0, reduced_reputation),
        activity_attestation_score=0.0,
        activity_service_score=0.0,
        activity_challenge_score=0.0,
        cached_total_score=state.config.slashing.minimum_slash_score,
    )
    state.decrease_balance(index, max(1, int(validator.effective_stake * state.config.slashing.stake_penalty_fraction)))
    state.slashings.append(index)
    recompute_all_scores(state)
