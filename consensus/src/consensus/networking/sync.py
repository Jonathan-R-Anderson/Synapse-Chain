from __future__ import annotations

import asyncio

from .types import Block, Message, MessageType, SyncRequest


class SyncService:
    def __init__(self, node: "Node") -> None:
        self.node = node

    async def request_sync(self, peer_id: int, *, from_height: int = 0) -> tuple[Block, ...]:
        request_id = f"sync:{self.node.node_id}:{peer_id}:{from_height}:{self.node.head_height}"
        future: asyncio.Future[tuple[Block, ...]] = asyncio.get_running_loop().create_future()
        self.node.sync_waiters[request_id] = future
        request = SyncRequest(from_height=from_height, request_id=request_id)
        try:
            await self.node.send_message(peer_id, Message(type=MessageType.SYNC_REQUEST, payload=request, sender=self.node.node_id))
            blocks = await asyncio.wait_for(future, timeout=self.node.config.sync.request_timeout)
            applied: list[Block] = []
            for block in blocks:
                self.verify_block(block)
                self.node.apply_finalized_block(block)
                applied.append(block)
            return tuple(applied)
        finally:
            self.node.sync_waiters.pop(request_id, None)

    def verify_block(self, block: Block) -> None:
        if block.hash is None:
            raise ValueError("block hash is missing")
        if len(block.signatures) < self.node.bft.quorum_size(max(1, len(block.committee_members))):
            raise ValueError("block does not carry a quorum commit certificate")
