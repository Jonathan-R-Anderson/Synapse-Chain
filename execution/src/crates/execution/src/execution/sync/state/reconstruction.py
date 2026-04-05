from __future__ import annotations

from ...block import ChainConfig
from ..exceptions import StateReconstructionError
from ..models import SnapshotManifest
from ..chain.store import ChainStore
from .snapshot_store import SnapshotStore
from .state_store import StateStore


class StateReconstructor:
    """State replay and restoration helpers shared by the sync strategies."""

    def __init__(self, chain_store: ChainStore, state_store: StateStore, snapshot_store: SnapshotStore) -> None:
        self._chain_store = chain_store
        self._state_store = state_store
        self._snapshot_store = snapshot_store

    def replay_canonical_range(self, start_height: int, end_height: int, *, chain_config: ChainConfig) -> None:
        if end_height < start_height:
            return
        parent = self._chain_store.get_canonical_header(start_height - 1) if start_height > 0 else None
        for height in range(int(start_height), int(end_height) + 1):
            block = self._chain_store.get_canonical_block(height)
            if block is None:
                raise StateReconstructionError(f"missing canonical block at height {height}")
            self._state_store.apply_block(block, chain_config=chain_config, parent_header=parent)
            self._state_store.record_history(
                height,
                block_hash=block.hash().to_hex(),
                pruning_enabled=False,
                retention=1_000_000,
            )
            parent = block.header

    def rebuild_from_genesis(self, *, target_height: int, chain_config: ChainConfig) -> None:
        self._state_store.restore_height(0)
        self.replay_canonical_range(1, int(target_height), chain_config=chain_config)

    def restore_to_height(self, height: int, *, chain_config: ChainConfig) -> None:
        try:
            self._state_store.restore_height(height)
        except StateReconstructionError:
            self.rebuild_from_genesis(target_height=height, chain_config=chain_config)

    def restore_snapshot(self, manifest: SnapshotManifest) -> None:
        self._snapshot_store.restore_snapshot(manifest, state_store=self._state_store)
