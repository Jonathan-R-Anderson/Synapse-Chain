from __future__ import annotations

from dataclasses import replace

from .state import BeaconState
from .types import Validator


def get_validator(state: BeaconState, index: int) -> Validator:
    return state.validators[index]


def update_validator(state: BeaconState, index: int, validator: Validator) -> None:
    state.validators[index] = validator


def schedule_exit(state: BeaconState, index: int, exit_epoch: int) -> None:
    validator = get_validator(state, index)
    state.validators[index] = replace(validator, exit_epoch=exit_epoch, active=False)


def activate_validator(state: BeaconState, index: int, activation_epoch: int) -> None:
    validator = get_validator(state, index)
    state.validators[index] = replace(validator, activation_epoch=activation_epoch, active=True, slashed=False)


def touch_activity(state: BeaconState, index: int, epoch: int) -> None:
    validator = get_validator(state, index)
    state.validators[index] = replace(validator, last_active_epoch=epoch)


def set_attested_epoch(state: BeaconState, index: int, epoch: int) -> None:
    validator = get_validator(state, index)
    state.validators[index] = replace(validator, last_attested_epoch=epoch, last_active_epoch=epoch)
