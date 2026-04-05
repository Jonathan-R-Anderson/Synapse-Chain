from __future__ import annotations

import time

from ...block import Block, BlockHeader
from ...block_validator import BlockValidator
from ..exceptions import InvalidPeerDataError, NoSuitablePeerError, StateReconstructionError
from ..node_types import SyncMode
from .base import SyncContext, SyncStrategy


class FullSyncStrategy(SyncStrategy):
    """Genesis-to-head full validation strategy with checkpointed state replay."""

    def __init__(self, context: SyncContext, *, archive_mode: bool = False) -> None:
        super().__init__(context)
        self._archive_mode = archive_mode
        self._validator = BlockValidator(self.context.config.chain_config)

    async def prepare(self) -> None:
        self.load_checkpoint()
        self.ensure_anchor()
        self.checkpoint.mode = SyncMode.ARCHIVE if self._archive_mode else SyncMode.FULL
        self.checkpoint.last_synced_header_height = max(self.checkpoint.last_synced_header_height, 0)
        self.checkpoint.last_applied_block_height = max(self.checkpoint.last_applied_block_height, 0)
        self.save_checkpoint()
        self.update_progress(stage="prepare")

    async def sync_headers(self) -> None:
        self.update_progress(stage="headers")
        peers = self.context.peer_manager.peers_for_sync_mode(
            SyncMode.ARCHIVE if self._archive_mode else SyncMode.FULL,
            archive_required=self._archive_mode,
        )
        if not peers:
            raise NoSuitablePeerError("full sync requires block-serving peers")
        for peer in peers:
            started = time.perf_counter()
            try:
                headers = await peer.get_headers(0, 10_000)
            except Exception as exc:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason=f"header fetch failed: {exc}")
                continue
            try:
                for header in headers:
                    self.context.chain_store.add_header(header, trusted=header.number == 0)
            except Exception as exc:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason=f"invalid header: {exc}")
                continue
            latency_ms = (time.perf_counter() - started) * 1_000.0
            self.context.peer_manager.reward_peer(
                peer.peer_info.peer_id,
                latency_ms=latency_ms,
                completeness=1.0 if headers else 0.2,
                correctness=1.0,
            )
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise NoSuitablePeerError("no canonical head available after header sync")
        self.checkpoint.last_synced_header_height = head.number
        self.checkpoint.last_synced_header_hash = head.hash().to_hex()
        self.checkpoint.canonical_head_height = head.number
        self.checkpoint.canonical_head_hash = head.hash().to_hex()
        self.save_checkpoint()
        self.update_progress(stage="headers", target_height=head.number)

    async def sync_bodies(self) -> None:
        self.update_progress(stage="bodies")
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise NoSuitablePeerError("cannot sync bodies without a canonical head")
        await self._ensure_applied_chain_is_canonical(head.hash().to_hex())
        start_height = self.checkpoint.last_applied_block_height + 1
        parent_header = self.context.chain_store.get_canonical_header(start_height - 1) if start_height > 0 else None
        processed = 0
        for height in range(start_height, head.number + 1):
            if self.context.config.max_blocks_per_cycle is not None and processed >= self.context.config.max_blocks_per_cycle:
                break
            header = self.context.chain_store.get_canonical_header(height)
            if header is None:
                raise StateReconstructionError(f"missing canonical header at height {height}")
            block = await self._fetch_block_for_header(header)
            self.context.chain_store.add_block(block)
            result = self.context.state_store.apply_block(
                block,
                chain_config=self.context.config.chain_config,
                parent_header=parent_header,
            )
            self._validator.validate_block_structure(block, parent_block=parent_header, execution_payload=result.to_execution_payload())
            self.context.state_store.record_history(
                height,
                block_hash=block.hash().to_hex(),
                pruning_enabled=self.context.config.pruning_enabled and not self._archive_mode,
                retention=self.context.config.history_retention,
            )
            if self.context.config.pruning_enabled and not self._archive_mode and height > self.context.config.prune_depth:
                self.context.chain_store.prune_block_bodies(keep_from_height=height - self.context.config.prune_depth)
            self.checkpoint.last_applied_block_height = height
            self.checkpoint.last_applied_block_hash = block.hash().to_hex()
            self.checkpoint.state_reconstruction_complete = height >= head.number
            self.save_checkpoint()
            self.update_progress(stage="bodies", target_height=head.number)
            parent_header = block.header
            processed += 1

    async def sync_state(self) -> None:
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise StateReconstructionError("cannot validate state without a canonical head")
        if self.checkpoint.last_applied_block_height < head.number:
            self.checkpoint.state_reconstruction_complete = False
            self.save_checkpoint()
            self.update_progress(stage="state_pending", target_height=head.number)
            return
        current_root = self.context.state_store.current_root()
        if current_root != head.state_root.to_hex():
            raise StateReconstructionError(
                f"state root mismatch after replay: expected {head.state_root.to_hex()}, got {current_root}"
            )
        self.checkpoint.state_reconstruction_complete = True
        self.save_checkpoint()
        self.update_progress(stage="state", target_height=head.number)

    async def finalize(self) -> None:
        head = self.context.chain_store.get_canonical_head()
        target_height = 0 if head is None else head.number
        complete = self.checkpoint.last_applied_block_height >= target_height and self.checkpoint.state_reconstruction_complete
        self.checkpoint.steady_state = complete
        self.checkpoint.stage = "steady_state" if complete else "catching_up"
        self.save_checkpoint()
        self.update_progress(stage=self.checkpoint.stage, target_height=target_height)

    async def _fetch_block_for_header(self, header: BlockHeader) -> Block:
        peers = self.context.peer_manager.peers_for_sync_mode(
            SyncMode.ARCHIVE if self._archive_mode else SyncMode.FULL,
            archive_required=self._archive_mode,
        )
        target_hash = header.hash().to_hex()
        for peer in peers:
            started = time.perf_counter()
            try:
                block = await peer.get_block(target_hash)
            except Exception as exc:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason=f"block fetch failed: {exc}")
                continue
            if block is None:
                continue
            if block.hash().to_hex() != target_hash:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="block hash mismatch")
                continue
            latency_ms = (time.perf_counter() - started) * 1_000.0
            self.context.peer_manager.reward_peer(peer.peer_info.peer_id, latency_ms=latency_ms)
            return block
        raise InvalidPeerDataError(f"no peer served canonical block {target_hash}")

    async def _ensure_applied_chain_is_canonical(self, canonical_head_hash: str) -> None:
        if self.checkpoint.last_applied_block_hash is None or self.checkpoint.last_applied_block_height == 0:
            return
        if self.context.chain_store.is_canonical(
            self.checkpoint.last_applied_block_height,
            self.checkpoint.last_applied_block_hash,
        ):
            return
        ancestor = self.context.chain_store.find_common_ancestor(self.checkpoint.last_applied_block_hash, canonical_head_hash)
        if ancestor is None:
            raise StateReconstructionError("unable to locate reorg common ancestor")
        self.context.reconstruction.restore_to_height(ancestor.number, chain_config=self.context.config.chain_config)
        self.checkpoint.last_applied_block_height = ancestor.number
        self.checkpoint.last_applied_block_hash = ancestor.hash().to_hex()
        self.checkpoint.state_reconstruction_complete = False
        self.save_checkpoint()
