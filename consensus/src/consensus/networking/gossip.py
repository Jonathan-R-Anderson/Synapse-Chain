from __future__ import annotations

from collections import deque

from .types import Message, MessageType


class GossipService:
    def __init__(self, node: "Node") -> None:
        self.node = node
        self.recent_messages: deque[Message] = deque(maxlen=node.config.gossip.recent_cache_size)

    async def gossip(self, message_type: MessageType, payload: object) -> Message:
        message = Message(type=message_type, payload=payload, sender=self.node.node_id)
        await self.node._handle_incoming_message(message)
        self.recent_messages.append(message)
        await self.forward(message)
        return message

    async def forward(self, message: Message, *, exclude: set[int] | None = None) -> None:
        self.recent_messages.append(message)
        peer_ids = self.node.random_peer_subset(
            self.node.config.gossip.fanout,
            exclude=exclude,
        )
        for peer_id in peer_ids:
            await self.node.send_message(peer_id, message.forwarded(sender=self.node.node_id))

    async def push_pull(self) -> None:
        peer_ids = self.node.random_peer_subset(self.node.config.gossip.pull_fanout)
        if not peer_ids:
            return
        payload = {
            "kind": "recent",
            "from_height": max(0, self.node.head_height - self.node.config.sync.batch_size),
            "known_blocks": tuple(sorted(self.node.blocks_by_hash)[-self.node.config.gossip.recent_cache_size :]),
        }
        for peer_id in peer_ids:
            await self.node.send_message(
                peer_id,
                Message(type=MessageType.SYNC_REQUEST, payload=payload, sender=self.node.node_id),
            )
