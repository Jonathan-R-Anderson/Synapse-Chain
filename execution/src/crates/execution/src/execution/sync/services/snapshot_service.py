from __future__ import annotations

import logging

from ..config import NodeConfig
from ..models import SnapshotManifest
from ..state.snapshot_store import SnapshotStore
from ..state.state_store import StateStore


class SnapshotService:
    """Generate periodic state snapshots for snap-sync consumers."""

    def __init__(self, config: NodeConfig, state_store: StateStore, snapshot_store: SnapshotStore) -> None:
        self._config = config
        self._state_store = state_store
        self._snapshot_store = snapshot_store
        self._logger = logging.getLogger(__name__)

    def generate_snapshot(self, *, block_height: int, block_hash: str) -> SnapshotManifest:
        manifest = self._snapshot_store.generate_snapshot(
            state_store=self._state_store,
            block_height=block_height,
            block_hash=block_hash,
            chunk_size=self._config.snapshot_chunk_size,
        )
        self._logger.info("generated snapshot %s at height %s", manifest.snapshot_id, block_height)
        return manifest

    def generate_if_due(self, *, block_height: int, block_hash: str) -> SnapshotManifest | None:
        latest = self._snapshot_store.latest_manifest()
        if latest is None or block_height % self._config.snapshot_interval == 0:
            return self.generate_snapshot(block_height=block_height, block_hash=block_hash)
        return latest

    def latest_manifest(self) -> SnapshotManifest | None:
        return self._snapshot_store.latest_manifest()
