from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..block import ChainConfig, FeeModel
from .models import AccountState, RoleCapabilities
from .node_types import NodeType, SyncMode


def _default_chain_config() -> ChainConfig:
    return ChainConfig()


def _default_state_directory() -> Path:
    return Path(".sync-data")


def capabilities_for_roles(
    node_types: frozenset[NodeType],
    *,
    serve_state: bool,
    serve_blocks: bool,
) -> RoleCapabilities:
    full_state_roles = {
        NodeType.FULL,
        NodeType.ARCHIVE,
        NodeType.VALIDATOR,
        NodeType.BUILDER,
        NodeType.STATE_PROVIDER,
        NodeType.RPC,
        NodeType.INDEXER,
        NodeType.ZK_PROVER,
        NodeType.WATCHTOWER,
        NodeType.SNAPSHOT_GENERATOR,
    }
    stores_full_state = bool(node_types & full_state_roles)
    headers_only = NodeType.LIGHT in node_types and not stores_full_state
    discovery_only = node_types <= {NodeType.BOOTNODE, NodeType.DHT}
    archive_available = NodeType.ARCHIVE in node_types
    generates_snapshots = bool(node_types & {NodeType.STATE_PROVIDER, NodeType.SNAPSHOT_GENERATOR})
    state_provider = NodeType.STATE_PROVIDER in node_types or NodeType.SNAPSHOT_GENERATOR in node_types
    serves_snapshots = generates_snapshots and serve_state
    serves_state_chunks = state_provider and serve_state
    serves_proofs = serve_state and bool(node_types & {NodeType.FULL, NodeType.ARCHIVE, NodeType.STATE_PROVIDER, NodeType.ZK_VERIFIER})
    serves_headers = not discovery_only
    serves_blocks_flag = serve_blocks and bool(node_types & (full_state_roles | {NodeType.BOOTNODE, NodeType.DHT}) - {NodeType.BOOTNODE, NodeType.DHT})
    pruning_allowed = NodeType.ARCHIVE not in node_types and NodeType.LIGHT not in node_types and NodeType.BOOTNODE not in node_types
    requires_full_validation = bool(node_types & {NodeType.FULL, NodeType.ARCHIVE, NodeType.VALIDATOR, NodeType.ZK_PROVER, NodeType.WATCHTOWER})
    return RoleCapabilities(
        node_types=node_types,
        serves_headers=serves_headers,
        serves_blocks=serves_blocks_flag,
        serves_snapshots=serves_snapshots,
        serves_state_chunks=serves_state_chunks,
        serves_proofs=serves_proofs,
        archive_available=archive_available,
        validator=NodeType.VALIDATOR in node_types,
        zk_prover=NodeType.ZK_PROVER in node_types,
        zk_verifier=NodeType.ZK_VERIFIER in node_types,
        stores_full_state=stores_full_state,
        headers_only=headers_only,
        discovery_only=discovery_only,
        requires_full_validation=requires_full_validation,
        pruning_allowed=pruning_allowed,
        generates_snapshots=generates_snapshots,
        exposes_rpc=NodeType.RPC in node_types,
        maintains_indexes=NodeType.INDEXER in node_types,
        supports_dht=NodeType.DHT in node_types,
        state_provider=state_provider,
    )


@dataclass(frozen=True, slots=True)
class NodeConfig:
    """Typed runtime configuration for a role-aware execution sync node."""

    node_name: str
    node_types: frozenset[NodeType]
    sync_mode: SyncMode
    pruning_enabled: bool = True
    snapshot_interval: int = 128
    max_peers: int = 25
    trusted_checkpoints: tuple[str, ...] = ()
    trusted_headers: tuple[dict[str, Any], ...] = ()
    state_directory: Path = field(default_factory=_default_state_directory)
    database_path: Path | None = None
    serve_state: bool = False
    serve_blocks: bool = False
    chain_config: ChainConfig = field(default_factory=_default_chain_config)
    genesis_header: dict[str, Any] | None = None
    genesis_state: tuple[AccountState, ...] = ()
    history_retention: int = 512
    prune_depth: int = 512
    snapshot_chunk_size: int = 64
    static_peers: tuple[str, ...] = ()
    bootnodes: tuple[str, ...] = ()
    max_blocks_per_cycle: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_name", str(self.node_name))
        object.__setattr__(self, "node_types", frozenset(self.node_types))
        object.__setattr__(self, "state_directory", Path(self.state_directory))
        object.__setattr__(
            self,
            "database_path",
            Path(self.database_path) if self.database_path is not None else self.state_directory / "sync.sqlite3",
        )
        object.__setattr__(self, "snapshot_interval", int(self.snapshot_interval))
        object.__setattr__(self, "max_peers", int(self.max_peers))
        object.__setattr__(self, "history_retention", int(self.history_retention))
        object.__setattr__(self, "prune_depth", int(self.prune_depth))
        object.__setattr__(self, "snapshot_chunk_size", int(self.snapshot_chunk_size))
        if self.max_blocks_per_cycle is not None:
            object.__setattr__(self, "max_blocks_per_cycle", int(self.max_blocks_per_cycle))

        if not self.node_types:
            raise ValueError("node_types cannot be empty")
        if NodeType.ARCHIVE in self.node_types and self.pruning_enabled:
            raise ValueError("archive nodes cannot enable pruning")
        if NodeType.LIGHT in self.node_types and self.sync_mode not in {SyncMode.LIGHT, SyncMode.DISCOVERY}:
            raise ValueError("light nodes must use light or discovery sync mode")
        if self.sync_mode is SyncMode.ARCHIVE and NodeType.ARCHIVE not in self.node_types:
            raise ValueError("archive sync mode requires the archive role")
        if self.sync_mode is SyncMode.SNAP and NodeType.LIGHT in self.node_types:
            raise ValueError("light nodes cannot use snap sync")
        if self.snapshot_interval < 1:
            raise ValueError("snapshot_interval must be positive")
        if self.max_peers < 1:
            raise ValueError("max_peers must be positive")
        if self.history_retention < 1:
            raise ValueError("history_retention must be positive")
        if self.prune_depth < 1:
            raise ValueError("prune_depth must be positive")
        if self.snapshot_chunk_size < 1:
            raise ValueError("snapshot_chunk_size must be positive")

    @property
    def capabilities(self) -> RoleCapabilities:
        return capabilities_for_roles(self.node_types, serve_state=self.serve_state, serve_blocks=self.serve_blocks)

    @property
    def requires_chain_sync(self) -> bool:
        return not self.capabilities.discovery_only

    @property
    def canonical_sync_mode(self) -> SyncMode:
        if NodeType.ARCHIVE in self.node_types or self.sync_mode is SyncMode.ARCHIVE:
            return SyncMode.ARCHIVE
        return self.sync_mode

    def with_paths_resolved(self, base_directory: Path) -> "NodeConfig":
        state_directory = self.state_directory
        database_path = self.database_path
        if not state_directory.is_absolute():
            state_directory = (base_directory / state_directory).resolve()
        if database_path is None:
            database_path = state_directory / "sync.sqlite3"
        elif not database_path.is_absolute():
            database_path = (base_directory / database_path).resolve()
        return replace(self, state_directory=state_directory, database_path=database_path)

    def to_dict(self) -> dict[str, Any]:
        chain_config_payload = {
            "chain_id": self.chain_config.chain_id,
            "fee_model": self.chain_config.fee_model.value,
            "support_legacy_transactions": self.chain_config.support_legacy_transactions,
            "support_eip1559_transactions": self.chain_config.support_eip1559_transactions,
            "support_zk_transactions": self.chain_config.support_zk_transactions,
            "allow_unprotected_legacy_transactions": self.chain_config.allow_unprotected_legacy_transactions,
            "enforce_low_s": self.chain_config.enforce_low_s,
            "gas_refund_quotient": self.chain_config.gas_refund_quotient,
            "burn_base_fee": self.chain_config.burn_base_fee,
            "elasticity_multiplier": self.chain_config.elasticity_multiplier,
            "base_fee_max_change_denominator": self.chain_config.base_fee_max_change_denominator,
            "gas_limit_bound_divisor": self.chain_config.gas_limit_bound_divisor,
            "initial_base_fee_per_gas": self.chain_config.initial_base_fee_per_gas,
            "max_extra_data_bytes": self.chain_config.max_extra_data_bytes,
        }
        return {
            "node_name": self.node_name,
            "node_types": sorted(node_type.value for node_type in self.node_types),
            "sync_mode": self.sync_mode.value,
            "pruning_enabled": self.pruning_enabled,
            "snapshot_interval": self.snapshot_interval,
            "max_peers": self.max_peers,
            "trusted_checkpoints": list(self.trusted_checkpoints),
            "trusted_headers": list(self.trusted_headers),
            "state_directory": str(self.state_directory),
            "database_path": str(self.database_path) if self.database_path is not None else None,
            "serve_state": self.serve_state,
            "serve_blocks": self.serve_blocks,
            "chain_config": chain_config_payload,
            "genesis_header": self.genesis_header,
            "genesis_state": [account.to_dict() for account in self.genesis_state],
            "history_retention": self.history_retention,
            "prune_depth": self.prune_depth,
            "snapshot_chunk_size": self.snapshot_chunk_size,
            "static_peers": list(self.static_peers),
            "bootnodes": list(self.bootnodes),
            "max_blocks_per_cycle": self.max_blocks_per_cycle,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "NodeConfig":
        chain_config_payload = dict(payload.get("chain_config", {}))
        chain_config = ChainConfig(
            chain_id=int(chain_config_payload.get("chain_id", 1)),
            fee_model=FeeModel(chain_config_payload.get("fee_model", FeeModel.EIP1559.value)),
            support_legacy_transactions=bool(chain_config_payload.get("support_legacy_transactions", True)),
            support_eip1559_transactions=bool(chain_config_payload.get("support_eip1559_transactions", True)),
            support_zk_transactions=bool(chain_config_payload.get("support_zk_transactions", True)),
            allow_unprotected_legacy_transactions=bool(
                chain_config_payload.get("allow_unprotected_legacy_transactions", True)
            ),
            enforce_low_s=bool(chain_config_payload.get("enforce_low_s", True)),
            gas_refund_quotient=int(chain_config_payload.get("gas_refund_quotient", 5)),
            burn_base_fee=bool(chain_config_payload.get("burn_base_fee", True)),
            elasticity_multiplier=int(chain_config_payload.get("elasticity_multiplier", 2)),
            base_fee_max_change_denominator=int(chain_config_payload.get("base_fee_max_change_denominator", 8)),
            gas_limit_bound_divisor=int(chain_config_payload.get("gas_limit_bound_divisor", 1024)),
            initial_base_fee_per_gas=int(chain_config_payload.get("initial_base_fee_per_gas", 1_000_000_000)),
            max_extra_data_bytes=int(chain_config_payload.get("max_extra_data_bytes", 32)),
        )
        return cls(
            node_name=str(payload["node_name"]),
            node_types=frozenset(NodeType(value) for value in payload.get("node_types", ())),
            sync_mode=SyncMode(str(payload["sync_mode"])),
            pruning_enabled=bool(payload.get("pruning_enabled", True)),
            snapshot_interval=int(payload.get("snapshot_interval", 128)),
            max_peers=int(payload.get("max_peers", 25)),
            trusted_checkpoints=tuple(str(item) for item in payload.get("trusted_checkpoints", ())),
            trusted_headers=tuple(payload.get("trusted_headers", ())),
            state_directory=Path(payload.get("state_directory", ".sync-data")),
            database_path=Path(payload["database_path"]) if payload.get("database_path") else None,
            serve_state=bool(payload.get("serve_state", False)),
            serve_blocks=bool(payload.get("serve_blocks", False)),
            chain_config=chain_config,
            genesis_header=payload.get("genesis_header"),
            genesis_state=tuple(AccountState.from_dict(item) for item in payload.get("genesis_state", ())),
            history_retention=int(payload.get("history_retention", 512)),
            prune_depth=int(payload.get("prune_depth", 512)),
            snapshot_chunk_size=int(payload.get("snapshot_chunk_size", 64)),
            static_peers=tuple(str(item) for item in payload.get("static_peers", ())),
            bootnodes=tuple(str(item) for item in payload.get("bootnodes", ())),
            max_blocks_per_cycle=payload.get("max_blocks_per_cycle"),
        )

    @classmethod
    def from_file(cls, path: Path | str) -> "NodeConfig":
        file_path = Path(path)
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return cls.from_dict(payload).with_paths_resolved(file_path.parent)


__all__ = ["NodeConfig", "capabilities_for_roles"]
