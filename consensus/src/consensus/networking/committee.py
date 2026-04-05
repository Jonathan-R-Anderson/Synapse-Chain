from __future__ import annotations

from collections import defaultdict
from math import floor, log
from typing import Iterable

from ..utils import deterministic_float
from .types import CommitteeSelection, payload_hash


class CommitteeSelector:
    def __init__(self, node: "Node") -> None:
        self.node = node

    async def select_committee(self, key: str) -> CommitteeSelection:
        target_id = int(key, 16)
        candidate_ids = await self.node.dht.find_node(target_id)
        if self.node.node_id not in candidate_ids:
            candidate_ids.insert(0, self.node.node_id)
        candidate_ids = candidate_ids[: self.node.config.committee.candidate_count]
        scored = [(candidate_id, self.node.resolve_score(candidate_id).computed_score) for candidate_id in candidate_ids]
        scored.sort(key=lambda item: (-item[1], item[0]))
        members = self._select_members(key, scored)
        score_distribution = {candidate_id: score for candidate_id, score in scored if candidate_id in members}
        committee_id = payload_hash({"key": key, "members": members})
        leader_id = max(members, key=lambda member_id: (score_distribution.get(member_id, 0.0), -member_id))
        return CommitteeSelection(
            committee_id=committee_id,
            key=key,
            member_ids=tuple(members),
            score_distribution=score_distribution,
            candidate_ids=tuple(candidate_ids),
            leader_id=leader_id,
        )

    def _select_members(self, key: str, scored: list[tuple[int, float]]) -> list[int]:
        size = min(self.node.config.committee.committee_size, len(scored))
        if not self.node.config.committee.weighted_selection:
            ranked = [candidate_id for candidate_id, _ in scored]
            return self._enforce_constraints(ranked, size)
        keys: list[tuple[float, int]] = []
        for candidate_id, score in scored:
            if score <= 0:
                continue
            entropy = deterministic_float(f"{key}:{candidate_id}".encode("utf-8"))
            weighted_key = -log(entropy) / score
            keys.append((weighted_key, candidate_id))
        keys.sort()
        ranked = [candidate_id for _, candidate_id in keys]
        return self._enforce_constraints(ranked, size)

    def _enforce_constraints(self, ranked_candidates: Iterable[int], size: int) -> list[int]:
        ranked = list(ranked_candidates)
        strict_limit = max(1, floor(size * self.node.config.committee.max_correlated_fraction))
        relaxed_limit = max(strict_limit, size)
        for prefer_unique_regions in (
            self.node.config.committee.prefer_unique_regions,
            False,
        ):
            for max_correlated in range(strict_limit, relaxed_limit + 1):
                selected = self._pick_members(
                    ranked,
                    size=size,
                    max_correlated=max_correlated,
                    prefer_unique_regions=prefer_unique_regions,
                )
                if len(selected) >= size:
                    return selected[:size]
        return ranked[:size]

    def _pick_members(
        self,
        ranked_candidates: list[int],
        *,
        size: int,
        max_correlated: int,
        prefer_unique_regions: bool,
    ) -> list[int]:
        selected: list[int] = []
        deferred: list[int] = []
        by_operator: dict[str, int] = defaultdict(int)
        used_regions: set[str] = set()

        for candidate_id in ranked_candidates:
            peer = self.node.network.get_node(candidate_id)
            if peer is None:
                continue
            if by_operator[peer.operator_id] >= max_correlated:
                continue
            if prefer_unique_regions and peer.region in used_regions:
                deferred.append(candidate_id)
                continue
            selected.append(candidate_id)
            by_operator[peer.operator_id] += 1
            used_regions.add(peer.region)
            if len(selected) >= size:
                return selected

        for candidate_id in deferred:
            peer = self.node.network.get_node(candidate_id)
            if peer is None:
                continue
            if by_operator[peer.operator_id] >= max_correlated:
                continue
            selected.append(candidate_id)
            by_operator[peer.operator_id] += 1
            if len(selected) >= size:
                break
        return selected
