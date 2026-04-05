from __future__ import annotations

import unittest

from test_helpers import make_state, make_validator

from consensus.eligibility import get_eligible_validators
from consensus.scoring import (
    compute_activity_weight,
    compute_identity_weight,
    compute_network_weight,
    compute_storage_weight,
    compute_total_score,
)


class ScoringTests(unittest.TestCase):
    def test_balanced_verified_validator_outscores_unverified_stake_whale(self) -> None:
        whale = make_validator(
            0,
            identity_verified=False,
            effective_stake=1024,
            burned_amount=0,
            reputation_score=5.0,
            storage_committed_bytes=0,
            network_relay_score=0.0,
            network_uptime_score=0.2,
            network_peer_diversity_score=0.0,
            network_coverage_score=0.0,
            activity_attestation_score=2.0,
            activity_service_score=1.0,
            activity_challenge_score=1.0,
        )
        balanced = make_validator(1, effective_stake=96, burned_amount=32, reputation_score=80.0)
        state = make_state([whale, balanced])
        whale_score = compute_total_score(state.validators[0], state.config, current_epoch=state.epoch)
        balanced_score = compute_total_score(state.validators[1], state.config, current_epoch=state.epoch)
        self.assertLess(whale_score, balanced_score)

    def test_storage_network_and_activity_components_are_positive_for_healthy_validator(self) -> None:
        state = make_state([make_validator(0)])
        validator = state.validators[0]
        self.assertGreater(compute_identity_weight(validator, state.config), 0.0)
        self.assertGreater(compute_storage_weight(validator, state.config), 0.0)
        self.assertGreater(compute_network_weight(validator, state.config), 0.0)
        self.assertGreater(compute_activity_weight(validator, state.config, current_epoch=state.epoch), 0.0)

    def test_zero_score_validator_is_not_eligible(self) -> None:
        validator = make_validator(
            0,
            effective_stake=1,
            identity_verified=False,
            storage_committed_bytes=0,
            network_relay_score=0.0,
            network_uptime_score=0.0,
            activity_attestation_score=0.0,
            activity_service_score=0.0,
            activity_challenge_score=0.0,
        )
        state = make_state([validator])
        self.assertEqual(get_eligible_validators(state), [])


if __name__ == "__main__":
    unittest.main()
