from __future__ import annotations

from pathlib import Path
from typing import Any

from primitives import Address

from .chain.store import ChainStore
from .config import NodeConfig
from .exceptions import NoSuitablePeerError
from .networking.peer_manager import PeerManager
from .networking.protocols import PeerClient
from .node_types import NodeType
from .persistence.checkpoints import CheckpointStore
from .persistence.metadata_db import MetadataDB
from .services.block_serving_service import BlockServingService
from .services.snapshot_service import SnapshotService
from .services.state_provider_service import StateProviderService
from .state.snapshot_store import SnapshotStore
from .state.state_store import StateStore
from .sync_manager import SyncManager
from .sync_strategies.light_sync import LightSyncStrategy


class NodeRuntime:
    """Top-level role-aware execution node runtime."""

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.config.state_directory.mkdir(parents=True, exist_ok=True)
        self.metadata_db = MetadataDB(self.config.database_path or self.config.state_directory / "sync.sqlite3")
        self.checkpoint_store = CheckpointStore(self.metadata_db)
        self.chain_store = ChainStore(self.config.database_path or self.config.state_directory / "sync.sqlite3", chain_config=self.config.chain_config)
        self.state_store = StateStore(self.config.state_directory, self.metadata_db)
        self.snapshot_store = SnapshotStore(self.config.state_directory, self.metadata_db)
        self.peer_manager = PeerManager(max_peers=self.config.max_peers)
        self.sync_manager = SyncManager(
            config=self.config,
            chain_store=self.chain_store,
            state_store=self.state_store,
            snapshot_store=self.snapshot_store,
            peer_manager=self.peer_manager,
            checkpoint_store=self.checkpoint_store,
        )
        self.services: dict[str, Any] = {}
        self.indexes: dict[str, Any] = {}
        self._last_progress = self.sync_manager.get_progress()

    def attach_peer(self, peer: PeerClient) -> None:
        self.peer_manager.register_peer(peer)

    async def start(self):
        self._last_progress = await self.sync_manager.run()
        self._expose_services()
        return self._last_progress

    def _expose_services(self) -> None:
        capabilities = self.config.capabilities
        if capabilities.serves_blocks:
            self.services["block_serving"] = BlockServingService(self.chain_store)
        snapshot_service = None
        if capabilities.generates_snapshots or capabilities.state_provider:
            snapshot_service = SnapshotService(self.config, self.state_store, self.snapshot_store)
            self.services["snapshot"] = snapshot_service
        if capabilities.state_provider or capabilities.serves_proofs or capabilities.serves_snapshots:
            if snapshot_service is not None:
                head = self.chain_store.get_canonical_head()
                if head is not None and self._last_progress.state_complete:
                    snapshot_service.generate_if_due(block_height=head.number, block_hash=head.hash().to_hex())
            self.services["state_provider"] = StateProviderService(self.state_store, self.snapshot_store)
        if capabilities.exposes_rpc:
            self.services["rpc_status"] = self.sync_status
        if capabilities.maintains_indexes:
            self.indexes["transactions_by_block"] = self._index_transactions()
        if NodeType.DHT in self.config.node_types:
            self.services["dht"] = self.peer_manager.routing_table
        if NodeType.BOOTNODE in self.config.node_types:
            self.services["discovery"] = self.peer_manager

    def _index_transactions(self) -> dict[int, tuple[str, ...]]:
        head = self.chain_store.get_canonical_head()
        if head is None:
            return {}
        return {
            height: tuple(transaction.tx_hash().to_hex() for transaction in block.transactions)
            for height in range(1, head.number + 1)
            if (block := self.chain_store.get_canonical_block(height)) is not None
        }

    async def request_account_fragment(self, address: Address, *, block_number: int | None = None):
        strategy = self.sync_manager.strategy
        if isinstance(strategy, LightSyncStrategy):
            return await strategy.request_account_proof(address, block_number=block_number)
        account = self.state_store.get_account(address)
        if account is None:
            raise NoSuitablePeerError(f"account {address.to_hex()} is unavailable")
        return account

    def sync_status(self) -> dict[str, Any]:
        progress = self.sync_manager.get_progress()
        return {
            "node_name": self.config.node_name,
            "roles": sorted(role.value for role in self.config.node_types),
            "sync": progress.to_dict(),
            "peer_count": self.peer_manager.peer_count(),
            "capabilities": self.config.capabilities.to_dict(),
        }
