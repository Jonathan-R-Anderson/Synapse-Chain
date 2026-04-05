from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from primitives import Address

from ...block import BlockHeader
from ..chain.store import ChainStore
from ..config import NodeConfig
from ..models import SyncCheckpoint, SyncProgress
from ..networking.peer_manager import PeerManager
from ..persistence.checkpoints import CheckpointStore
from ..state.proofs import ProofVerifier
from ..state.reconstruction import StateReconstructor
from ..state.snapshot_store import SnapshotStore
from ..state.state_store import StateStore


@dataclass(slots=True)
class SyncContext:
    config: NodeConfig
    chain_store: ChainStore
    state_store: StateStore
    snapshot_store: SnapshotStore
    peer_manager: PeerManager
    checkpoint_store: CheckpointStore
    proof_verifier: ProofVerifier
    reconstruction: StateReconstructor


class SyncStrategy(ABC):
    """Abstract sync-strategy interface used by the sync manager."""

    def __init__(self, context: SyncContext) -> None:
        self.context = context
        self.logger = logging.getLogger(self.__class__.__name__)
        self.checkpoint = SyncCheckpoint(mode=self.context.config.canonical_sync_mode)
        self.progress = SyncProgress(mode=self.checkpoint.mode, stage="created")

    @property
    def checkpoint_name(self) -> str:
        return self.context.config.node_name

    def load_checkpoint(self) -> SyncCheckpoint:
        checkpoint = self.context.checkpoint_store.load(self.checkpoint_name)
        if checkpoint is None:
            checkpoint = SyncCheckpoint(mode=self.context.config.canonical_sync_mode)
        self.checkpoint = checkpoint
        self.progress = SyncProgress.from_checkpoint(checkpoint)
        self.context.peer_manager.import_scores(checkpoint.peer_scores)
        return checkpoint

    def save_checkpoint(self) -> None:
        self.checkpoint.peer_scores = self.context.peer_manager.export_scores()
        self.context.checkpoint_store.save(self.checkpoint_name, self.checkpoint)
        self.progress = SyncProgress.from_checkpoint(self.checkpoint)

    def update_progress(self, *, stage: str, target_height: int | None = None, details: dict | None = None) -> None:
        self.checkpoint.stage = stage
        if details:
            self.progress.details.update(details)
        self.progress = SyncProgress(
            mode=self.checkpoint.mode,
            stage=stage,
            current_height=max(self.checkpoint.last_synced_header_height, self.checkpoint.last_applied_block_height),
            target_height=self.checkpoint.canonical_head_height if target_height is None else int(target_height),
            synced_headers=self.checkpoint.last_synced_header_height,
            applied_blocks=self.checkpoint.last_applied_block_height,
            state_complete=self.checkpoint.state_reconstruction_complete,
            steady_state=self.checkpoint.steady_state,
            details=dict(self.progress.details),
        )

    def ensure_anchor(self) -> BlockHeader:
        head = self.context.chain_store.get_canonical_head()
        if head is not None:
            return self.context.chain_store.get_canonical_header(0) or head
        if self.context.config.genesis_header is not None:
            genesis = BlockHeader.from_dict(self.context.config.genesis_header)
        else:
            genesis = BlockHeader(number=0, gas_limit=30_000_000, gas_used=0, timestamp=0, coinbase=Address.zero())
        self.context.chain_store.seed_anchor(genesis)
        self.context.state_store.ensure_genesis(self.context.config.genesis_state, block_hash=genesis.hash().to_hex())
        return genesis

    @abstractmethod
    async def prepare(self) -> None:
        ...

    @abstractmethod
    async def sync_headers(self) -> None:
        ...

    @abstractmethod
    async def sync_bodies(self) -> None:
        ...

    @abstractmethod
    async def sync_state(self) -> None:
        ...

    @abstractmethod
    async def finalize(self) -> None:
        ...

    def get_progress(self) -> SyncProgress:
        return self.progress
