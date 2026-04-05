from __future__ import annotations

import time

from ...block import Block, BlockHeader
from ...block_validator import BlockValidator
from ..exceptions import InvalidPeerDataError, NoSuitablePeerError, SnapshotIntegrityError, StateReconstructionError
from ..models import SnapshotManifest, StateChunk
from ..node_types import SyncMode
from .base import SyncStrategy


class SnapSyncStrategy(SyncStrategy):
    """Header sync plus verified snapshot restore and recent-block replay."""

    def __init__(self, context) -> None:  # type: ignore[override]
        super().__init__(context)
        self._validator = BlockValidator(self.context.config.chain_config)
        self._manifest: SnapshotManifest | None = None
        self._replay_blocks: list[Block] = []

    async def prepare(self) -> None:
        self.load_checkpoint()
        self.ensure_anchor()
        self.checkpoint.mode = SyncMode.SNAP
        self.save_checkpoint()
        self.update_progress(stage="prepare")

    async def sync_headers(self) -> None:
        self.update_progress(stage="headers")
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.SNAP)
        if not peers:
            raise NoSuitablePeerError("snap sync requires snapshot-serving peers")
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
            self.context.peer_manager.reward_peer(peer.peer_info.peer_id, latency_ms=(time.perf_counter() - started) * 1_000.0)
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise NoSuitablePeerError("no canonical head available for snap sync")
        self._manifest = await self._select_manifest()
        self.checkpoint.last_synced_header_height = head.number
        self.checkpoint.last_synced_header_hash = head.hash().to_hex()
        self.checkpoint.canonical_head_height = head.number
        self.checkpoint.canonical_head_hash = head.hash().to_hex()
        self.checkpoint.snapshot_point_height = self._manifest.block_height
        self.checkpoint.snapshot_block_hash = self._manifest.block_hash
        self.checkpoint.pending_state_chunks = tuple(chunk.chunk_id for chunk in self._manifest.chunks)
        self.context.snapshot_store.write_manifest(self._manifest)
        self.save_checkpoint()
        self.update_progress(
            stage="headers",
            target_height=head.number,
            details={"snapshot_point_height": self._manifest.block_height},
        )

    async def sync_bodies(self) -> None:
        if self._manifest is None:
            raise SnapshotIntegrityError("snap sync manifest is unavailable")
        self.update_progress(stage="buffer_recent_blocks", target_height=self.checkpoint.canonical_head_height)
        self._replay_blocks.clear()
        start_height = self._manifest.block_height + 1
        end_height = self.checkpoint.canonical_head_height
        for height in range(start_height, end_height + 1):
            header = self.context.chain_store.get_canonical_header(height)
            if header is None:
                raise StateReconstructionError(f"missing canonical header at height {height}")
            block = await self._fetch_block(header)
            self.context.chain_store.add_block(block)
            self._replay_blocks.append(block)

    async def sync_state(self) -> None:
        if self._manifest is None:
            raise SnapshotIntegrityError("snap sync manifest is unavailable")
        self.update_progress(stage="restore_snapshot", target_height=self.checkpoint.canonical_head_height)
        pending = list(self.checkpoint.pending_state_chunks)
        for chunk_id in tuple(pending):
            if self.context.snapshot_store.has_chunk(self._manifest.snapshot_id, chunk_id):
                pending.remove(chunk_id)
                continue
            chunk = self._chunk_by_id(chunk_id)
            payload = await self._fetch_chunk(chunk)
            if not self.context.snapshot_store.verify_chunk(chunk, payload):
                raise SnapshotIntegrityError(f"snapshot chunk {chunk.chunk_id} failed integrity checks")
            self.context.snapshot_store.write_chunk(self._manifest.snapshot_id, chunk, payload)
            pending.remove(chunk_id)
            self.checkpoint.pending_state_chunks = tuple(pending)
            self.save_checkpoint()
        self.context.snapshot_store.restore_snapshot(self._manifest, state_store=self.context.state_store)
        self.checkpoint.last_applied_block_height = self._manifest.block_height
        self.checkpoint.last_applied_block_hash = self._manifest.block_hash
        parent_header = self.context.chain_store.get_canonical_header(self._manifest.block_height)
        for block in self._replay_blocks:
            result = self.context.state_store.apply_block(
                block,
                chain_config=self.context.config.chain_config,
                parent_header=parent_header,
            )
            self._validator.validate_block_structure(block, parent_block=parent_header, execution_payload=result.to_execution_payload())
            self.context.state_store.record_history(
                block.header.number,
                block_hash=block.hash().to_hex(),
                pruning_enabled=self.context.config.pruning_enabled,
                retention=self.context.config.history_retention,
            )
            self.checkpoint.last_applied_block_height = block.header.number
            self.checkpoint.last_applied_block_hash = block.hash().to_hex()
            parent_header = block.header
            self.save_checkpoint()
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise StateReconstructionError("snap sync lost canonical head during replay")
        if self.context.state_store.current_root() != head.state_root.to_hex():
            raise StateReconstructionError("state root mismatch after snapshot replay")
        self.checkpoint.state_reconstruction_complete = True
        self.checkpoint.pending_state_chunks = ()
        self.save_checkpoint()
        self.update_progress(stage="state", target_height=head.number)

    async def finalize(self) -> None:
        head = self.context.chain_store.get_canonical_head()
        target_height = 0 if head is None else head.number
        self.checkpoint.steady_state = (
            self.checkpoint.state_reconstruction_complete
            and self.checkpoint.last_applied_block_height >= target_height
        )
        self.checkpoint.stage = "steady_state" if self.checkpoint.steady_state else "catching_up"
        self.save_checkpoint()
        self.update_progress(stage=self.checkpoint.stage, target_height=target_height)

    async def _select_manifest(self) -> SnapshotManifest:
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.SNAP)
        best_manifest: SnapshotManifest | None = None
        for peer in peers:
            manifest = await peer.get_snapshot_manifest()
            if manifest is None:
                continue
            if not self.context.snapshot_store.verify_manifest(manifest):
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="invalid snapshot manifest hash")
                continue
            header = self.context.chain_store.get_canonical_header(manifest.block_height)
            if header is None or header.hash().to_hex() != manifest.block_hash or header.state_root.to_hex() != manifest.state_root:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="manifest root or anchor mismatch")
                continue
            if best_manifest is None or manifest.block_height > best_manifest.block_height:
                best_manifest = manifest
        if best_manifest is None:
            raise NoSuitablePeerError("no valid snapshot manifest available")
        return best_manifest

    async def _fetch_block(self, header: BlockHeader) -> Block:
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.FULL)
        target_hash = header.hash().to_hex()
        for peer in peers:
            block = await peer.get_block(target_hash)
            if block is None:
                continue
            if block.hash().to_hex() != target_hash:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="replay block hash mismatch")
                continue
            return block
        raise InvalidPeerDataError(f"missing replay block {target_hash}")

    async def _fetch_chunk(self, chunk: StateChunk) -> bytes:
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.SNAP)
        for peer in peers:
            try:
                payload = await peer.get_snapshot_chunk(chunk.snapshot_id, chunk.chunk_id)
            except Exception as exc:
                self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason=f"chunk fetch failed: {exc}")
                continue
            return payload
        raise NoSuitablePeerError(f"missing snapshot chunk {chunk.chunk_id}")

    def _chunk_by_id(self, chunk_id: str) -> StateChunk:
        if self._manifest is None:
            raise SnapshotIntegrityError("manifest unavailable")
        for chunk in self._manifest.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        raise SnapshotIntegrityError(f"unknown snapshot chunk {chunk_id}")
