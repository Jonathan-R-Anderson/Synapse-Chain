from __future__ import annotations

from ...block import Block, BlockHeader
from ..chain.store import ChainStore


class BlockServingService:
    """Serve canonical headers and blocks to downstream peers."""

    def __init__(self, chain_store: ChainStore) -> None:
        self._chain_store = chain_store

    def get_headers(self, start_height: int, limit: int) -> tuple[BlockHeader, ...]:
        headers: list[BlockHeader] = []
        for height in range(int(start_height), int(start_height) + int(limit)):
            header = self._chain_store.get_canonical_header(height)
            if header is None:
                break
            headers.append(header)
        return tuple(headers)

    def get_block(self, block_hash: str) -> Block | None:
        return self._chain_store.get_block(block_hash)
