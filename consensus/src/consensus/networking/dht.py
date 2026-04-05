from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import DHTConfig


def xor_distance(left: int, right: int) -> int:
    return left ^ right


def bucket_index(local_id: int, remote_id: int) -> int:
    distance = xor_distance(local_id, remote_id)
    if distance == 0:
        return 0
    return distance.bit_length() - 1


@dataclass(slots=True)
class KBucket:
    k: int
    peers: list[int] = field(default_factory=list)

    def add(self, peer_id: int) -> None:
        if peer_id in self.peers:
            self.peers.remove(peer_id)
            self.peers.append(peer_id)
            return
        if len(self.peers) >= self.k:
            self.peers.pop(0)
        self.peers.append(peer_id)


@dataclass(slots=True)
class RoutingTable:
    local_id: int
    config: DHTConfig
    buckets: list[KBucket] = field(init=False)

    def __post_init__(self) -> None:
        self.buckets = [KBucket(self.config.k_bucket_size) for _ in range(self.config.id_bits)]

    def update(self, peer_id: int) -> None:
        if peer_id == self.local_id:
            return
        self.buckets[bucket_index(self.local_id, peer_id)].add(peer_id)

    def all_peers(self) -> list[int]:
        peers: list[int] = []
        for bucket in self.buckets:
            peers.extend(bucket.peers)
        return list(dict.fromkeys(peers))

    def closest_peers(self, target_id: int, count: int | None = None) -> list[int]:
        peers = self.all_peers()
        peers.sort(key=lambda peer_id: xor_distance(peer_id, target_id))
        return peers if count is None else peers[:count]

    def occupied_bucket_ratio(self) -> float:
        occupied = sum(1 for bucket in self.buckets if bucket.peers)
        return occupied / len(self.buckets)


@dataclass(slots=True)
class DHTValue:
    value: Any
    stored_at: float
    expires_at: float


class DHTService:
    def __init__(self, node: "Node", config: DHTConfig) -> None:
        self.node = node
        self.config = config
        self.routing_table = RoutingTable(node.node_id, config)
        self._values: dict[str, DHTValue] = {}

    def observe_peer(self, peer_id: int) -> None:
        self.routing_table.update(peer_id)

    def local_density(self) -> float:
        close_threshold = 2 ** (self.config.id_bits - 8)
        peers = self.routing_table.all_peers()
        if not peers:
            return 0.0
        nearby = sum(1 for peer_id in peers if xor_distance(self.node.node_id, peer_id) < close_threshold)
        return nearby / max(1, len(peers))

    def coverage_radius(self) -> float:
        return self.routing_table.occupied_bucket_ratio()

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [key for key, record in self._values.items() if record.expires_at <= now]
        for key in expired:
            del self._values[key]

    async def store(self, key: str, value: Any) -> None:
        self._prune()
        now = time.monotonic()
        self._values[key] = DHTValue(value=value, stored_at=now, expires_at=now + self.config.value_ttl)
        candidate_ids = await self.find_node(int(key, 16))
        for peer_id in candidate_ids[: self.config.replication_factor]:
            if peer_id == self.node.node_id:
                continue
            peer = self.node.network.get_node(peer_id)
            if peer is not None:
                peer.dht.store_local(key, value)

    def store_local(self, key: str, value: Any) -> None:
        now = time.monotonic()
        self._values[key] = DHTValue(value=value, stored_at=now, expires_at=now + self.config.value_ttl)

    def get_local(self, key: str) -> Any | None:
        self._prune()
        record = self._values.get(key)
        return None if record is None else record.value

    async def find_node(self, target_id: int) -> list[int]:
        candidates = self.routing_table.closest_peers(target_id, self.config.k_bucket_size)
        queried: set[int] = set()
        best = list(candidates)
        while candidates:
            batch = [peer_id for peer_id in candidates if peer_id not in queried][: self.config.alpha]
            if not batch:
                break
            candidates = []
            for peer_id in batch:
                queried.add(peer_id)
                peer = self.node.network.get_node(peer_id)
                if peer is None:
                    continue
                returned = peer.dht.handle_find_node(target_id)
                for candidate in returned:
                    if candidate == self.node.node_id:
                        continue
                    self.observe_peer(candidate)
                    if candidate not in best:
                        best.append(candidate)
                        candidates.append(candidate)
            best.sort(key=lambda peer_id: xor_distance(peer_id, target_id))
            best = best[: self.config.k_bucket_size]
        return best

    def handle_find_node(self, target_id: int) -> list[int]:
        return self.routing_table.closest_peers(target_id, self.config.k_bucket_size)

    async def find_value(self, key: str) -> Any | None:
        local = self.get_local(key)
        if local is not None:
            return local
        target_id = int(key, 16)
        for peer_id in await self.find_node(target_id):
            peer = self.node.network.get_node(peer_id)
            if peer is None:
                continue
            value = peer.dht.get_local(key)
            if value is not None:
                self.store_local(key, value)
                return value
        return None
