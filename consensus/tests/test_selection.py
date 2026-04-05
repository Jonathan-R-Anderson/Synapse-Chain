from __future__ import annotations

import unittest
from collections import Counter

from test_helpers import make_state, make_validator

from consensus.committees import get_slot_committees
from consensus.eligibility import get_eligible_validators
from consensus.proposer import get_beacon_proposer_index
from consensus.randomness import sample_weighted_committee


class SelectionTests(unittest.TestCase):
    def test_proposer_selection_is_reproducible(self) -> None:
        state = make_state([make_validator(index) for index in range(6)])
        eligible = get_eligible_validators(state)
        proposer_a = get_beacon_proposer_index(state, 3)
        proposer_b = get_beacon_proposer_index(state, 3)
        self.assertIn(proposer_a, eligible)
        self.assertEqual(proposer_a, proposer_b)

    def test_committee_sampling_has_no_duplicates_and_only_eligible_members(self) -> None:
        state = make_state([make_validator(index) for index in range(10)])
        eligible = set(get_eligible_validators(state))
        committee = sample_weighted_committee(state, 2, 5, list(eligible), committee_index=0)
        self.assertEqual(len(committee), len(set(committee)))
        self.assertTrue(set(committee).issubset(eligible))

    def test_fairness_sanity_prefers_higher_score_validators_over_many_slots(self) -> None:
        validators = [
            make_validator(0, effective_stake=64, useful_compute_score=20.0),
            make_validator(1, effective_stake=96, useful_compute_score=60.0),
            make_validator(2, effective_stake=160, useful_compute_score=90.0),
        ]
        state = make_state(validators)
        counts: Counter[int] = Counter()
        for slot in range(1, 257):
            counts[get_beacon_proposer_index(state, slot)] += 1
        self.assertGreater(counts[2], counts[1])
        self.assertGreater(counts[1], counts[0])

    def test_slot_committees_are_constructed_for_active_set(self) -> None:
        state = make_state([make_validator(index) for index in range(12)])
        committees = get_slot_committees(state, 1)
        self.assertGreaterEqual(len(committees), 1)
        self.assertTrue(all(committee for committee in committees))


if __name__ == "__main__":
    unittest.main()
