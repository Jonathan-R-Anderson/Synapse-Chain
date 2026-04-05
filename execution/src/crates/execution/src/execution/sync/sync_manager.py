from __future__ import annotations

import logging

from .chain.store import ChainStore
from .config import NodeConfig
from .exceptions import CheckpointCorruptionError
from .networking.peer_manager import PeerManager
from .node_types import NodeType, SyncMode
from .persistence.checkpoints import CheckpointStore
from .state.proofs import MerkleProofVerifier, ProofVerifier
from .state.reconstruction import StateReconstructor
from .state.snapshot_store import SnapshotStore
from .state.state_store import StateStore
from .sync_strategies.base import SyncContext, SyncStrategy
from .sync_strategies.full_sync import FullSyncStrategy
from .sync_strategies.light_sync import LightSyncStrategy
from .sync_strategies.snap_sync import SnapSyncStrategy


class SyncManager:
    """Select and run the correct sync strategy for the configured role set."""

    def __init__(
        self,
        *,
        config: NodeConfig,
        chain_store: ChainStore,
        state_store: StateStore,
        snapshot_store: SnapshotStore,
        peer_manager: PeerManager,
        checkpoint_store: CheckpointStore,
        proof_verifier: ProofVerifier | None = None,
    ) -> None:
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.context = SyncContext(
            config=config,
            chain_store=chain_store,
            state_store=state_store,
            snapshot_store=snapshot_store,
            peer_manager=peer_manager,
            checkpoint_store=checkpoint_store,
            proof_verifier=proof_verifier or MerkleProofVerifier(),
            reconstruction=StateReconstructor(chain_store, state_store, snapshot_store),
        )
        self.strategy = self._select_strategy()

    def _select_strategy(self) -> SyncStrategy:
        if NodeType.LIGHT in self.config.node_types or self.config.sync_mode is SyncMode.LIGHT:
            return LightSyncStrategy(self.context)
        if self.config.sync_mode is SyncMode.SNAP:
            return SnapSyncStrategy(self.context)
        archive_mode = NodeType.ARCHIVE in self.config.node_types or self.config.sync_mode is SyncMode.ARCHIVE
        return FullSyncStrategy(self.context, archive_mode=archive_mode)

    async def run(self):
        if not self.config.requires_chain_sync:
            self.logger.info("runtime %s is discovery-only; skipping chain sync", self.config.node_name)
            return self.strategy.get_progress()
        try:
            await self.context.peer_manager.start_discovery()
            await self.strategy.prepare()
            await self.strategy.sync_headers()
            await self.strategy.sync_bodies()
            await self.strategy.sync_state()
            await self.strategy.finalize()
        except CheckpointCorruptionError:
            self.logger.warning("checkpoint for %s is corrupted; resetting it", self.config.node_name)
            self.context.checkpoint_store.clear(self.config.node_name)
            self.strategy = self._select_strategy()
            await self.strategy.prepare()
            await self.strategy.sync_headers()
            await self.strategy.sync_bodies()
            await self.strategy.sync_state()
            await self.strategy.finalize()
        return self.strategy.get_progress()

    def get_progress(self):
        return self.strategy.get_progress()
