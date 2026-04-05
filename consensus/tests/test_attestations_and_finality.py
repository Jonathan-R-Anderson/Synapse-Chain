from __future__ import annotations

import unittest

from test_helpers import make_state, make_validator, produce_block_with_full_attestations

from consensus.attestations import create_attestation, process_attestation, validate_attestation
from consensus.finality import get_attesting_weight, get_total_active_weight


class AttestationAndFinalityTests(unittest.TestCase):
    def test_attestation_processing_updates_participation_and_votes(self) -> None:
        state = make_state([make_validator(index) for index in range(6)])
        block_root = produce_block_with_full_attestations(state, 1)
        committee = [0, 1, 2]
        attestation = create_attestation(state, 1, committee, block_root, committee_index=0)
        self.assertTrue(validate_attestation(state, attestation))
        process_attestation(state, attestation)
        self.assertGreaterEqual(len(state.pending_attestations), 2)
        self.assertIn(0, state.epoch_participation[state.epoch])

    def test_finality_advances_under_full_participation(self) -> None:
        state = make_state([make_validator(index) for index in range(12)])
        for slot in range(1, state.config.slots_per_epoch * 4 + 1):
            produce_block_with_full_attestations(state, slot)
        self.assertGreaterEqual(state.justified_checkpoint.epoch, 3)
        self.assertGreaterEqual(state.finalized_checkpoint.epoch, 2)

    def test_attesting_weight_is_bounded_by_total_active_weight(self) -> None:
        state = make_state([make_validator(index) for index in range(8)])
        for slot in range(1, state.config.slots_per_epoch + 1):
            produce_block_with_full_attestations(state, slot)
        checkpoint = state.checkpoint_for_epoch(1)
        self.assertLessEqual(
            get_attesting_weight(state, checkpoint),
            get_total_active_weight(state) + 1e-9,
        )


if __name__ == "__main__":
    unittest.main()
