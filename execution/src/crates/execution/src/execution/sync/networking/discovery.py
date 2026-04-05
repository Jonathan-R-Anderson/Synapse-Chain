from __future__ import annotations

import logging
from typing import Iterable

from ..models import PeerInfo
from .protocols import PeerClient


class BootnodeDiscovery:
    """Bootnode-driven discovery using already known discovery peers."""

    def __init__(self, bootnodes: Iterable[PeerClient]) -> None:
        self._bootnodes = tuple(bootnodes)
        self._logger = logging.getLogger(__name__)

    async def discover(self) -> tuple[PeerInfo, ...]:
        discovered: dict[str, PeerInfo] = {}
        for peer in self._bootnodes:
            try:
                for info in await peer.list_known_peers():
                    discovered[info.peer_id] = info
            except Exception as exc:
                self._logger.warning("bootnode discovery failed for %s: %s", peer.peer_info.peer_id, exc)
        return tuple(discovered.values())
