from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .state import BeaconState
from .types import Attestation, BeaconBlock, LatestMessage


@dataclass(slots=True)
class ForkChoiceStore:
    justified_root: str
    finalized_root: str
    blocks: dict[str, BeaconBlock] = field(default_factory=dict)
    children: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    latest_messages: dict[int, LatestMessage] = field(default_factory=dict)
    proposer_boost_root: str | None = None
    proposer_boost_score: float = 0.0

    def on_block(self, block: BeaconBlock, state: BeaconState) -> str:
        root = block.root()
        self.blocks[root] = block
        self.children[block.parent_root].add(root)
        self.justified_root = state.justified_checkpoint.root
        self.finalized_root = state.finalized_checkpoint.root
        self.proposer_boost_root = root
        self.proposer_boost_score = max(
            self.proposer_boost_score,
            state.validators[block.proposer_index].cached_total_score * state.config.fork_choice.proposer_boost,
        )
        return root

    def on_attestation(self, attestation: Attestation, state: BeaconState) -> None:
        for index in attestation.attester_indices:
            self.latest_messages[index] = LatestMessage(
                epoch=attestation.target_checkpoint.epoch,
                block_root=attestation.beacon_block_root,
            )
        self.justified_root = state.justified_checkpoint.root
        self.finalized_root = state.finalized_checkpoint.root

    def _subtree_vote_weight(self, state: BeaconState, root: str) -> float:
        weight = 0.0
        descendants = self._descendants_of(root)
        descendants.add(root)
        for index, message in self.latest_messages.items():
            if message.block_root in descendants:
                weight += state.validators[index].cached_total_score
        if root == self.proposer_boost_root:
            weight += self.proposer_boost_score
        return weight

    def _descendants_of(self, root: str) -> set[str]:
        descendants: set[str] = set()
        frontier = list(self.children.get(root, set()))
        while frontier:
            child = frontier.pop()
            if child in descendants:
                continue
            descendants.add(child)
            frontier.extend(self.children.get(child, set()))
        return descendants

    def get_head(self, state: BeaconState) -> str:
        current = self.justified_root
        if current not in self.blocks and current != self.finalized_root:
            return state.latest_block_root
        while self.children.get(current):
            best_child = max(
                self.children[current],
                key=lambda child: (
                    self._subtree_vote_weight(state, child),
                    self.blocks[child].slot if child in self.blocks else -1,
                    child,
                ),
            )
            current = best_child
        return current


def on_block(store: ForkChoiceStore, block: BeaconBlock, state: BeaconState) -> str:
    return store.on_block(block, state)


def on_attestation(store: ForkChoiceStore, attestation: Attestation, state: BeaconState) -> None:
    store.on_attestation(attestation, state)


def get_head(store: ForkChoiceStore, state: BeaconState) -> str:
    return store.get_head(state)
