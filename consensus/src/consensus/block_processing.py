from __future__ import annotations

import hashlib

from .penalties import apply_proposer_rewards
from .proposer import get_beacon_proposer_index
from .state import BeaconState
from .types import BeaconBlock
from .utils import xor_bytes


def validate_block_basic(state: BeaconState, block: BeaconBlock) -> bool:
    if block.slot < state.slot:
        return False
    if block.parent_root != state.latest_block_root:
        return False
    if block.proposer_index != get_beacon_proposer_index(state, block.slot):
        return False
    validator = state.validators[block.proposer_index]
    if validator.slashed or not validator.active:
        return False
    if len(block.randao_reveal) != state.config.randao_mix_length:
        return False
    return True


def process_block(state: BeaconState, block: BeaconBlock) -> str:
    if not validate_block_basic(state, block):
        raise ValueError("block failed basic validation")
    state.randao_mix = xor_bytes(
        state.randao_mix,
        hashlib.sha256(block.randao_reveal).digest()[: state.config.randao_mix_length],
    )
    block_root = state.record_block(block)
    apply_proposer_rewards(state, block.proposer_index, block.slot)
    state.record_state_root()
    return block_root
