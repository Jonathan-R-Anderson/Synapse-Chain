from __future__ import annotations

import unittest
from dataclasses import replace

from test_helpers import make_state, make_validator, produce_block_with_full_attestations

from consensus.attestations import create_attestation
from consensus.block_processing import process_block
from consensus.epoch_processing import process_slots_until
from consensus.fork_choice import ForkChoiceStore
from consensus.penalties import apply_inactivity_penalties, slash_validator
from consensus.proposer import build_block, get_beacon_proposer_index


class PenaltiesAndForkChoiceTests(unittest.TestCase):
    def test_slashing_removes_validator_from_eligibility(self) -> None:
        state = make_state([make_validator(index) for index in range(4)])
        pre_score = state.validators[0].cached_total_score
        slash_validator(state, 0, "double proposal")
        self.assertTrue(state.validators[0].slashed)
        self.assertFalse(state.validators[0].active)
        self.assertLess(state.validators[0].cached_total_score, pre_score)

    def test_inactivity_penalties_increase_scores_and_reduce_balance(self) -> None:
        state = make_state([make_validator(index) for index in range(4)])
        initial_balance = state.balances[0]
        state.epoch = 3
        apply_inactivity_penalties(state, 2)
        self.assertGreater(state.inactivity_scores[0], 0.0)
        self.assertLess(state.balances[0], initial_balance)

    def test_fork_choice_prefers_branch_with_heavier_latest_messages(self) -> None:
        state = make_state([make_validator(index) for index in range(8)])
        store = ForkChoiceStore(
            justified_root=state.justified_checkpoint.root,
            finalized_root=state.finalized_checkpoint.root,
        )
        process_slots_until(state, 1)
        proposer_index = get_beacon_proposer_index(state, 1)
        block_a = build_block(state, 1, proposer_index, state.latest_block_root)
        root_a = process_block(state, block_a)
        store.on_block(block_a, state)

        process_slots_until(state, 2)
        proposer_index = get_beacon_proposer_index(state, 2)
        block_b = build_block(state, 2, proposer_index, root_a)
        root_b = process_block(state, block_b)
        store.on_block(block_b, state)

        process_slots_until(state, 3)
        proposer_index = get_beacon_proposer_index(state, 3)
        state.validators[0] = replace(state.validators[0], cached_total_score=1.5)
        state.validators[1] = replace(state.validators[1], cached_total_score=1.4)
        state.validators[3] = replace(state.validators[3], cached_total_score=0.2)
        block_c = build_block(state, 3, proposer_index, root_a)
        root_c = block_c.root()
        store.on_block(block_c, state)

        attestation_heavy = create_attestation(state, 3, [0, 1, 2], root_c, committee_index=0, participants=[0, 1, 2])
        store.on_attestation(attestation_heavy, state)
        attestation_light = create_attestation(state, 3, [3], root_b, committee_index=0, participants=[3])
        store.on_attestation(attestation_light, state)
        self.assertEqual(store.get_head(state), root_c)


if __name__ == "__main__":
    unittest.main()
