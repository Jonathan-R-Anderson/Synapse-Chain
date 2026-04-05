from __future__ import annotations

from dataclasses import dataclass, field

from ..models import PeerInfo


@dataclass(slots=True)
class DHTRoutingTable:
    """Very small in-process DHT-style routing table for peer and content lookup."""

    peer_buckets: dict[str, PeerInfo] = field(default_factory=dict)
    content_index: dict[str, set[str]] = field(default_factory=dict)

    def add_peer(self, peer: PeerInfo) -> None:
        self.peer_buckets[peer.peer_id] = peer

    def remove_peer(self, peer_id: str) -> None:
        self.peer_buckets.pop(peer_id, None)
        for peers in self.content_index.values():
            peers.discard(peer_id)

    def announce_content(self, content_key: str, peer_id: str) -> None:
        self.content_index.setdefault(content_key, set()).add(peer_id)

    def lookup_content(self, content_key: str) -> tuple[str, ...]:
        return tuple(sorted(self.content_index.get(content_key, set())))

    def all_peers(self) -> tuple[PeerInfo, ...]:
        return tuple(self.peer_buckets.values())


class DHTDiscovery:
    """Discovery adaptor that yields peers already present in the local routing table."""

    def __init__(self, routing_table: DHTRoutingTable) -> None:
        self._routing_table = routing_table

    async def discover(self) -> tuple[PeerInfo, ...]:
        return self._routing_table.all_peers()
