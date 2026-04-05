from __future__ import annotations

import time

from primitives import Address

from ...block_header import BlockHeader
from ..exceptions import NoSuitablePeerError
from ..models import MerkleProof
from ..node_types import SyncMode
from .base import SyncStrategy


class LightSyncStrategy(SyncStrategy):
    """Header-only sync plus on-demand proof-verified state fragment retrieval."""

    async def prepare(self) -> None:
        self.load_checkpoint()
        self.ensure_anchor()
        self.checkpoint.mode = SyncMode.LIGHT
        self.save_checkpoint()
        self.update_progress(stage="prepare")

    async def sync_headers(self) -> None:
        self.update_progress(stage="headers")
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.LIGHT)
        if not peers:
            raise NoSuitablePeerError("light sync requires header/proof peers")
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
            self.context.peer_manager.reward_peer(
                peer.peer_info.peer_id,
                latency_ms=(time.perf_counter() - started) * 1_000.0,
            )
        head = self.context.chain_store.get_canonical_head()
        if head is None:
            raise NoSuitablePeerError("light sync could not determine a canonical head")
        self.checkpoint.last_synced_header_height = head.number
        self.checkpoint.last_synced_header_hash = head.hash().to_hex()
        self.checkpoint.canonical_head_height = head.number
        self.checkpoint.canonical_head_hash = head.hash().to_hex()
        self.save_checkpoint()
        self.update_progress(stage="headers", target_height=head.number)

    async def sync_bodies(self) -> None:
        self.update_progress(stage="headers_only", target_height=self.checkpoint.canonical_head_height)

    async def sync_state(self) -> None:
        self.checkpoint.state_reconstruction_complete = True
        self.save_checkpoint()
        self.update_progress(stage="light_ready", target_height=self.checkpoint.canonical_head_height)

    async def finalize(self) -> None:
        self.checkpoint.steady_state = True
        self.checkpoint.stage = "steady_state"
        self.save_checkpoint()
        self.update_progress(stage="steady_state", target_height=self.checkpoint.canonical_head_height)

    async def request_account_proof(self, address: Address, *, block_number: int | None = None) -> MerkleProof:
        target_height = self.checkpoint.canonical_head_height if block_number is None else int(block_number)
        header = self.context.chain_store.get_canonical_header(target_height)
        if header is None:
            raise NoSuitablePeerError(f"missing canonical header at height {target_height}")
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.LIGHT)
        for peer in peers:
            proof = await peer.get_account_proof(target_height, address.to_hex())
            if proof is None:
                continue
            if self.context.proof_verifier.verify_account_proof(proof, header.state_root.to_hex()):
                self.context.state_store.cache_account_fragment(target_height, proof)
                return proof
            self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="invalid account proof")
        raise NoSuitablePeerError(f"no valid account proof available for {address.to_hex()} at {target_height}")

    async def request_storage_proof(self, address: Address, slot: int, *, block_number: int | None = None) -> MerkleProof:
        target_height = self.checkpoint.canonical_head_height if block_number is None else int(block_number)
        peers = self.context.peer_manager.peers_for_sync_mode(SyncMode.LIGHT)
        expected_header = self.context.chain_store.get_canonical_header(target_height)
        if expected_header is None:
            raise NoSuitablePeerError(f"missing canonical header at height {target_height}")
        slot_hex = f"0x{int(slot):064x}"
        for peer in peers:
            proof = await peer.get_storage_proof(target_height, address.to_hex(), slot_hex)
            if proof is None:
                continue
            if self.context.proof_verifier.verify_storage_proof(proof, expected_header.state_root.to_hex()):
                self.context.state_store.cache_storage_fragment(target_height, proof)
                return proof
            self.context.peer_manager.penalize_peer(peer.peer_info.peer_id, reason="invalid storage proof")
        raise NoSuitablePeerError(f"no valid storage proof available for {address.to_hex()} slot {slot_hex}")
