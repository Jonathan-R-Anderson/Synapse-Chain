from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..models import MerkleProof, PeerInfo, SnapshotManifest
from ...block import Block, BlockHeader


@runtime_checkable
class PeerClient(Protocol):
    """Network-facing interface implemented by concrete peer transports."""

    @property
    def peer_info(self) -> PeerInfo:
        ...

    async def ping(self) -> float:
        ...

    async def list_known_peers(self) -> Sequence[PeerInfo]:
        ...

    async def get_headers(self, start_height: int, limit: int) -> Sequence[BlockHeader]:
        ...

    async def get_block(self, block_hash: str) -> Block | None:
        ...

    async def get_snapshot_manifest(self) -> SnapshotManifest | None:
        ...

    async def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        ...

    async def get_account_proof(self, block_number: int, address: str) -> MerkleProof | None:
        ...

    async def get_storage_proof(self, block_number: int, address: str, slot: str) -> MerkleProof | None:
        ...
