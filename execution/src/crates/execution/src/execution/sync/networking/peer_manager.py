from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from ..exceptions import NoSuitablePeerError
from ..models import PeerInfo
from ..node_types import SyncMode
from .dht import DHTDiscovery, DHTRoutingTable
from .protocols import PeerClient


@dataclass(slots=True)
class _ManagedPeer:
    client: PeerClient
    info: PeerInfo
    banned_until: float = 0.0

    @property
    def banned(self) -> bool:
        return time.time() < self.banned_until


class PeerManager:
    """Discovery, scoring, filtering, and ban-management for sync peers."""

    def __init__(
        self,
        *,
        max_peers: int,
        discovery_backends: Iterable[object] = (),
        routing_table: DHTRoutingTable | None = None,
    ) -> None:
        self._max_peers = int(max_peers)
        self._routing_table = routing_table or DHTRoutingTable()
        self._discovery_backends = list(discovery_backends)
        self._peers: dict[str, _ManagedPeer] = {}
        self._logger = logging.getLogger(__name__)

    @property
    def routing_table(self) -> DHTRoutingTable:
        return self._routing_table

    def add_discovery_backend(self, backend: object) -> None:
        self._discovery_backends.append(backend)

    def register_peer(self, client: PeerClient) -> None:
        info = client.peer_info
        managed = self._peers.get(info.peer_id)
        if managed is None and len(self._peers) >= self._max_peers:
            lowest = min(self._peers.values(), key=lambda peer: peer.info.score)
            if lowest.info.score > info.score:
                return
            self._peers.pop(lowest.info.peer_id, None)
        self._peers[info.peer_id] = _ManagedPeer(client=client, info=info)
        self._routing_table.add_peer(info)

    def import_scores(self, peer_scores: dict[str, float]) -> None:
        for peer_id, score in peer_scores.items():
            if peer_id in self._peers:
                self._peers[peer_id].info.score = float(score)

    def export_scores(self) -> dict[str, float]:
        return {peer_id: managed.info.score for peer_id, managed in self._peers.items()}

    async def start_discovery(self) -> None:
        for backend in self._discovery_backends:
            discover = getattr(backend, "discover", None)
            if discover is None:
                continue
            try:
                peers = await discover()
            except Exception as exc:
                self._logger.warning("peer discovery backend %r failed: %s", backend, exc)
                continue
            for info in peers:
                self._routing_table.add_peer(info)

    def peer_count(self) -> int:
        return len(self._peers)

    def get_peer(self, peer_id: str) -> PeerClient | None:
        managed = self._peers.get(peer_id)
        return None if managed is None else managed.client

    def get_peer_info(self, peer_id: str) -> PeerInfo | None:
        managed = self._peers.get(peer_id)
        return None if managed is None else managed.info

    def all_peer_info(self) -> tuple[PeerInfo, ...]:
        return tuple(managed.info for managed in self._peers.values())

    def is_banned(self, peer_id: str) -> bool:
        managed = self._peers.get(peer_id)
        return False if managed is None else managed.banned

    def ban_peer(self, peer_id: str, *, duration_seconds: float = 300.0) -> None:
        managed = self._peers.get(peer_id)
        if managed is None:
            return
        managed.banned_until = time.time() + float(duration_seconds)
        self._logger.warning("peer %s banned for %.1fs", peer_id, duration_seconds)

    def reward_peer(
        self,
        peer_id: str,
        *,
        latency_ms: float | None = None,
        completeness: float = 1.0,
        correctness: float = 1.0,
    ) -> None:
        managed = self._peers.get(peer_id)
        if managed is None:
            return
        if latency_ms is not None:
            managed.info.latency_ms = float(latency_ms)
            responsiveness_bonus = max(0.0, 50.0 - min(latency_ms, 50.0)) / 50.0
        else:
            responsiveness_bonus = 0.25
        managed.info.responsiveness += responsiveness_bonus
        managed.info.correctness += max(0.0, correctness)
        managed.info.completeness += max(0.0, completeness)
        managed.info.score = (
            managed.info.correctness * 2.5
            + managed.info.completeness * 1.0
            + managed.info.responsiveness * 1.5
            - managed.info.invalid_responses * 4.0
            - ((managed.info.latency_ms or 0.0) / 1000.0)
        )
        managed.info.last_seen = time.time()

    def penalize_peer(self, peer_id: str, *, reason: str, severity: float = 1.0) -> None:
        managed = self._peers.get(peer_id)
        if managed is None:
            return
        managed.info.invalid_responses += 1
        managed.info.score -= max(1.0, severity * 3.0)
        self._logger.warning("peer %s penalized for %s", peer_id, reason)
        if managed.info.invalid_responses >= 2 or managed.info.score <= -4.0:
            self.ban_peer(peer_id, duration_seconds=300.0)

    def select_peers(
        self,
        *,
        limit: int | None = None,
        predicate: Callable[[PeerInfo], bool] | None = None,
        **requirements: bool | None,
    ) -> tuple[PeerClient, ...]:
        candidates: list[_ManagedPeer] = []
        for managed in self._peers.values():
            if managed.banned:
                continue
            if not managed.info.capabilities.matches(**requirements):
                continue
            if predicate is not None and not predicate(managed.info):
                continue
            candidates.append(managed)
        ordered = tuple(
            managed.client
            for managed in sorted(
                candidates,
                key=lambda peer: (
                    peer.info.score,
                    -(peer.info.latency_ms or 10_000.0),
                    peer.info.peer_id,
                ),
                reverse=True,
            )
        )
        return ordered if limit is None else ordered[:limit]

    def require_peers(self, **requirements: bool | None) -> tuple[PeerClient, ...]:
        peers = self.select_peers(**requirements)
        if not peers:
            raise NoSuitablePeerError(f"no peers satisfy requirements: {requirements}")
        return peers

    def peers_for_sync_mode(self, mode: SyncMode, *, archive_required: bool = False) -> tuple[PeerClient, ...]:
        if mode is SyncMode.LIGHT:
            return self.require_peers(serves_headers=True, serves_proofs=True)
        if mode is SyncMode.SNAP:
            return self.require_peers(serves_headers=True, serves_snapshots=True, serves_state_chunks=True)
        if mode is SyncMode.ARCHIVE or archive_required:
            return self.require_peers(serves_headers=True, serves_blocks=True, archive_available=True)
        if mode is SyncMode.DISCOVERY:
            return tuple()
        return self.require_peers(serves_headers=True, serves_blocks=True)

    async def measure_peer(self, peer_id: str) -> None:
        managed = self._peers.get(peer_id)
        if managed is None:
            return
        start = time.perf_counter()
        try:
            await managed.client.ping()
        except Exception as exc:
            self.penalize_peer(peer_id, reason=f"ping failure: {exc}", severity=1.0)
            return
        latency_ms = (time.perf_counter() - start) * 1_000.0
        self.reward_peer(peer_id, latency_ms=latency_ms)


__all__ = ["PeerManager", "DHTDiscovery", "DHTRoutingTable"]
