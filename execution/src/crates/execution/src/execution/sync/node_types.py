from __future__ import annotations

from enum import Enum


class NodeType(str, Enum):
    """Execution-client runtime roles that influence storage and sync behavior."""

    FULL = "full"
    LIGHT = "light"
    ARCHIVE = "archive"
    VALIDATOR = "validator"
    BUILDER = "builder"
    BOOTNODE = "bootnode"
    DHT = "dht"
    STATE_PROVIDER = "state_provider"
    RPC = "rpc"
    INDEXER = "indexer"
    ZK_PROVER = "zk_prover"
    ZK_VERIFIER = "zk_verifier"
    WATCHTOWER = "watchtower"
    SNAPSHOT_GENERATOR = "snapshot_generator"


class SyncMode(str, Enum):
    """Supported chain sync modes."""

    FULL = "full"
    SNAP = "snap"
    LIGHT = "light"
    ARCHIVE = "archive"
    DISCOVERY = "discovery"
