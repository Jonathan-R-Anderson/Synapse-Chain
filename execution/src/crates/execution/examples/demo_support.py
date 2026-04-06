from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


CRATES = Path(__file__).resolve().parents[2]
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    source = CRATES / crate / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from crypto import address_from_private_key
from execution import (
    AccountState,
    Block,
    BlockBuilder,
    BlockHeader,
    ChainConfig,
    FeeModel,
    NodeConfig,
    NodeRuntime,
    NodeType,
    PeerInfo,
    SyncMode,
    apply_block,
)
from execution.sync.config import capabilities_for_roles
from execution.sync.models import MerkleProof
from execution.sync.networking.i2p import (
    I2PNodePeerClient,
    I2POverlayServer,
    advertised_i2p_endpoint,
    i2p_privacy_enabled,
    is_i2p_destination,
    normalize_i2p_destination,
    unique_peers,
    wait_for_configured_bootstrap_references,
)
from execution.sync.state.proofs import account_leaf_map, build_merkle_proofs, build_merkle_root
from primitives import Address, Hash
from transactions import LegacyTransaction


LOGGER = logging.getLogger("execution.sync.demo")
ACTIVE_I2P_OVERLAYS: list[I2POverlayServer] = []


def _parse_env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return ()
    values: list[str] = []
    for item in raw.replace("\n", ",").split(","):
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def _parse_env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw, 0)


def _state_db_from_accounts(accounts: tuple[AccountState, ...]):
    from evm import StateDB

    state = StateDB()
    for account in accounts:
        target = state.get_or_create_account(account.address)
        target.nonce = account.nonce
        target.balance = account.balance
        target.code = account.code
        for slot, value in account.storage:
            target.storage.set(int(slot), int(value))
        target.storage.commit()
    return state


@dataclass(slots=True)
class DemoPeer:
    peer_info: PeerInfo
    headers: tuple[BlockHeader, ...]
    blocks: dict[str, Block] = field(default_factory=dict)
    proofs: dict[tuple[int, str], MerkleProof] = field(default_factory=dict)

    async def ping(self) -> float:
        return 1.0

    async def list_known_peers(self):
        return ()

    async def get_headers(self, start_height: int, limit: int):
        return tuple(header for header in self.headers if header.number >= start_height)[:limit]

    async def get_block(self, block_hash: str):
        return self.blocks.get(block_hash)

    async def get_snapshot_manifest(self):
        return None

    async def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str):
        raise KeyError(chunk_id)

    async def get_account_proof(self, block_number: int, address: str):
        return self.proofs.get((block_number, Address.from_hex(address).to_hex()))

    async def get_storage_proof(self, block_number: int, address: str, slot: str):
        return None


def _make_peer_info(peer_id: str, roles: set[NodeType], *, serve_state: bool, serve_blocks: bool) -> PeerInfo:
    return PeerInfo(
        peer_id=peer_id,
        endpoint=f"memory://{peer_id}",
        capabilities=capabilities_for_roles(frozenset(roles), serve_state=serve_state, serve_blocks=serve_blocks),
    )


def _configured_bootstrap_peers(config: NodeConfig) -> tuple[PeerInfo, ...]:
    references: list[str] = []
    for reference in (*config.bootnodes, *config.static_peers):
        if reference not in references:
            references.append(reference)
    return tuple(
        PeerInfo(
            peer_id=f"bootstrap:{reference}",
            endpoint=advertised_i2p_endpoint(reference) if i2p_privacy_enabled() and is_i2p_destination(reference) else reference,
            capabilities=capabilities_for_roles(frozenset({NodeType.BOOTNODE}), serve_state=False, serve_blocks=False),
        )
        for reference in references
    )


def _seed_routing_table(runtime: NodeRuntime, peers: Iterable[PeerInfo]) -> None:
    for peer in peers:
        runtime.peer_manager.routing_table.add_peer(peer)


def _activate_i2p_overlay(runtime: NodeRuntime, config: NodeConfig) -> I2POverlayServer | None:
    if not i2p_privacy_enabled():
        return None
    overlay = I2POverlayServer(runtime, config)
    destination = overlay.start()
    ACTIVE_I2P_OVERLAYS.append(overlay)
    LOGGER.info("i2p overlay destination for %s: %s", config.node_name, destination)
    return overlay


def _bootstrap_peer_hint(endpoint: str) -> PeerInfo:
    return PeerInfo(
        peer_id=f"bootstrap:{normalize_i2p_destination(endpoint)}",
        endpoint=advertised_i2p_endpoint(endpoint),
        capabilities=capabilities_for_roles(frozenset({NodeType.BOOTNODE}), serve_state=False, serve_blocks=False),
    )


async def _discover_i2p_peer_clients(runtime: NodeRuntime, config: NodeConfig, overlay: I2POverlayServer) -> tuple[I2PNodePeerClient, ...]:
    references = wait_for_configured_bootstrap_references(config, overlay.overlay_config)
    if not references:
        LOGGER.info("no configured i2p bootstrap references for %s", config.node_name)
        return ()

    local_peer = overlay.peer_info()
    deadline = asyncio.get_running_loop().time() + max(1.0, overlay.overlay_config.bootstrap_wait_seconds)
    while True:
        discovered_infos: list[PeerInfo] = []
        for reference in references:
            bootstrap_client = I2PNodePeerClient(overlay.sam_session, _bootstrap_peer_hint(reference))
            try:
                info = await bootstrap_client.get_peer_info()
                bootstrap_client = I2PNodePeerClient(overlay.sam_session, info)
                await bootstrap_client.announce_peer(local_peer)
                discovered_infos.append(info)
                discovered_infos.extend(await bootstrap_client.list_known_peers())
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("i2p bootstrap lookup failed for %s: %s", reference, exc)

        clients: list[I2PNodePeerClient] = []
        seen_endpoints = {normalize_i2p_destination(local_peer.endpoint)}
        for info in unique_peers(discovered_infos):
            endpoint_key = normalize_i2p_destination(info.endpoint)
            if endpoint_key in seen_endpoints:
                continue
            seen_endpoints.add(endpoint_key)
            client = I2PNodePeerClient(overlay.sam_session, info)
            try:
                await client.get_peer_info()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("i2p peer refresh failed for %s: %s", info.endpoint, exc)
                continue
            clients.append(client)
        if clients:
            LOGGER.info("i2p peer discovery for %s found %s peers", config.node_name, len(clients))
            return tuple(clients)
        if asyncio.get_running_loop().time() >= deadline:
            LOGGER.info("i2p peer discovery for %s timed out without remote peers", config.node_name)
            return ()
        await asyncio.sleep(1.0)


def _build_transfer_fixture(*, chain_id: int, length: int = 3):
    chain_config = ChainConfig(
        chain_id=chain_id,
        fee_model=FeeModel.LEGACY,
        support_eip1559_transactions=False,
        support_zk_transactions=False,
    )
    sender = address_from_private_key(1)
    recipient = address_from_private_key(2)
    genesis_state = (AccountState(address=sender, balance=1_000_000), AccountState(address=recipient, balance=0))
    genesis_header = BlockHeader(number=0, gas_limit=30_000_000, gas_used=0, timestamp=0, coinbase=Address.zero())
    state = _state_db_from_accounts(genesis_state)
    builder = BlockBuilder(chain_config)
    parent = genesis_header
    blocks: list[Block] = []
    for index in range(1, length + 1):
        tx = LegacyTransaction(
            nonce=index - 1,
            gas_price=0,
            gas_limit=21_000,
            to=recipient,
            value=10 * index,
            data=b"",
            chain_id=chain_id,
        ).sign(1)
        skeleton = Block(
            header=BlockHeader(
                parent_hash=parent.hash(),
                number=index,
                gas_limit=30_000_000,
                gas_used=0,
                timestamp=index,
                coinbase=Address.zero(),
            ),
            transactions=(tx,),
        )
        result = apply_block(state, skeleton, chain_config, parent_header=parent)
        state = result.state
        blocks.append(
            builder.build_block(
                parent_block=parent,
                transactions=(tx,),
                execution_result=result,
                timestamp=index,
                gas_limit=30_000_000,
                beneficiary=Address.zero(),
            )
        )
        parent = blocks[-1].header
    return chain_config, genesis_header, genesis_state, tuple(blocks)


def _build_light_fixture(*, chain_id: int):
    chain_config = ChainConfig(
        chain_id=chain_id,
        fee_model=FeeModel.LEGACY,
        support_eip1559_transactions=False,
        support_zk_transactions=False,
    )
    target = address_from_private_key(9)
    other = address_from_private_key(10)
    accounts_zero = (AccountState(address=target, balance=100), AccountState(address=other, balance=5))
    accounts_one = (AccountState(address=target, balance=125), AccountState(address=other, balance=1))
    genesis_root = build_merkle_root(account_leaf_map(accounts_zero))
    head_root = build_merkle_root(account_leaf_map(accounts_one))
    genesis_header = BlockHeader(
        number=0,
        gas_limit=30_000_000,
        gas_used=0,
        timestamp=0,
        coinbase=Address.zero(),
        state_root=Hash.from_hex(genesis_root),
        difficulty=1,
    )
    head_header = BlockHeader(
        parent_hash=genesis_header.hash(),
        number=1,
        gas_limit=30_000_000,
        gas_used=0,
        timestamp=1,
        coinbase=Address.zero(),
        state_root=Hash.from_hex(head_root),
        difficulty=1,
    )
    proofs = build_merkle_proofs(account_leaf_map(accounts_one))
    return chain_config, genesis_header, head_header, proofs, target


def load_config(path: Path | str) -> NodeConfig:
    config = NodeConfig.from_file(path)
    payload = config.to_dict()
    changed = False
    state_root = os.environ.get("EXECUTION_STATE_ROOT")
    if state_root:
        state_directory = Path(state_root) / payload["node_name"]
        payload["state_directory"] = str(state_directory)
        payload["database_path"] = str(state_directory / "sync.sqlite3")
        changed = True
    chain_id = _parse_env_int("EXECUTION_CHAIN_ID")
    if chain_id is not None:
        chain_config_payload = dict(payload.get("chain_config", {}))
        chain_config_payload["chain_id"] = chain_id
        payload["chain_config"] = chain_config_payload
        changed = True
    bootnodes = _parse_env_list("EXECUTION_BOOTNODES")
    if bootnodes:
        payload["bootnodes"] = list(bootnodes)
        changed = True
    static_peers = _parse_env_list("EXECUTION_STATIC_PEERS")
    if static_peers:
        payload["static_peers"] = list(static_peers)
        changed = True
    if changed:
        config = NodeConfig.from_dict(payload)
    LOGGER.info("loaded config from %s", Path(path).resolve())
    LOGGER.info("roles: %s", ", ".join(sorted(role.value for role in config.node_types)))
    LOGGER.info("sync mode: %s", config.sync_mode.value)
    LOGGER.info("chain id: %s", config.chain_config.chain_id)
    if config.bootnodes:
        LOGGER.info("configured bootnodes: %s", ", ".join(config.bootnodes))
    if config.static_peers:
        LOGGER.info("configured static peers: %s", ", ".join(config.static_peers))
    return config


async def run_demo(config_path: Path | str) -> dict[str, object]:
    config = load_config(config_path)

    if NodeType.BOOTNODE in config.node_types and not config.requires_chain_sync:
        runtime = NodeRuntime(config)
        overlay = _activate_i2p_overlay(runtime, config)
        configured_peers = _configured_bootstrap_peers(config)
        if configured_peers:
            _seed_routing_table(runtime, configured_peers)
        else:
            runtime.peer_manager.routing_table.add_peer(
                _make_peer_info("demo-known-peer", {NodeType.FULL}, serve_state=True, serve_blocks=True)
            )
        progress = await runtime.start()
        if overlay is not None:
            runtime.peer_manager.routing_table.add_peer(overlay.peer_info())
        LOGGER.info("bootnode discovery table size: %s", len(runtime.peer_manager.routing_table.all_peers()))
        status = runtime.sync_status()
        LOGGER.info("final sync status: %s", json.dumps(status, indent=2))
        return status

    if config.sync_mode is SyncMode.LIGHT:
        chain_config, genesis_header, head_header, proofs, target = _build_light_fixture(chain_id=config.chain_config.chain_id)
        if config.genesis_header is None:
            config = NodeConfig.from_dict(
                {
                    **config.to_dict(),
                    "genesis_header": genesis_header.to_dict(),
                    "chain_config": {
                        **config.to_dict()["chain_config"],
                        "chain_id": chain_config.chain_id,
                        "fee_model": chain_config.fee_model.value,
                        "support_eip1559_transactions": chain_config.support_eip1559_transactions,
                        "support_zk_transactions": chain_config.support_zk_transactions,
                    },
                }
            )
        runtime = NodeRuntime(config)
        overlay = _activate_i2p_overlay(runtime, config)
        discovered_peers: tuple[I2PNodePeerClient, ...] = ()
        if overlay is not None:
            discovered_peers = await _discover_i2p_peer_clients(runtime, config, overlay)
        if discovered_peers:
            for peer in discovered_peers:
                runtime.attach_peer(peer)
        else:
            _seed_routing_table(runtime, _configured_bootstrap_peers(config))
            peer = DemoPeer(
                peer_info=_make_peer_info("light-source", {NodeType.FULL}, serve_state=True, serve_blocks=False),
                headers=(genesis_header, head_header),
                proofs={(1, target.to_hex()): proofs[target.to_hex()]},
            )
            runtime.attach_peer(peer)
        progress = await runtime.start()
        if overlay is not None:
            runtime.peer_manager.routing_table.add_peer(overlay.peer_info())
        LOGGER.info("light node steady state: %s", progress.steady_state)
    else:
        chain_config, genesis_header, genesis_state, blocks = _build_transfer_fixture(
            chain_id=config.chain_config.chain_id,
            length=4,
        )
        if config.genesis_header is None:
            config = NodeConfig.from_dict(
                {
                    **config.to_dict(),
                    "genesis_header": genesis_header.to_dict(),
                    "genesis_state": [account.to_dict() for account in genesis_state],
                    "chain_config": {
                        **config.to_dict()["chain_config"],
                        "chain_id": chain_config.chain_id,
                        "fee_model": chain_config.fee_model.value,
                        "support_eip1559_transactions": chain_config.support_eip1559_transactions,
                        "support_zk_transactions": chain_config.support_zk_transactions,
                    },
                }
            )
        runtime = NodeRuntime(config)
        overlay = _activate_i2p_overlay(runtime, config)
        discovered_peers = ()
        if overlay is not None:
            discovered_peers = await _discover_i2p_peer_clients(runtime, config, overlay)
        if discovered_peers:
            for peer in discovered_peers:
                runtime.attach_peer(peer)
        else:
            _seed_routing_table(runtime, _configured_bootstrap_peers(config))
            peer = DemoPeer(
                peer_info=_make_peer_info("full-source", {NodeType.FULL, NodeType.STATE_PROVIDER}, serve_state=True, serve_blocks=True),
                headers=(genesis_header, *(block.header for block in blocks)),
                blocks={block.hash().to_hex(): block for block in blocks},
            )
            runtime.attach_peer(peer)
        progress = await runtime.start()
        if overlay is not None:
            runtime.peer_manager.routing_table.add_peer(overlay.peer_info())
        LOGGER.info("node steady state: %s", progress.steady_state)
        if "snapshot" in runtime.services:
            manifest = runtime.services["snapshot"].generate_if_due(
                block_height=blocks[-1].header.number,
                block_hash=blocks[-1].hash().to_hex(),
            )
            if manifest is not None:
                LOGGER.info("latest snapshot: %s at height %s", manifest.snapshot_id, manifest.block_height)

    status = runtime.sync_status()
    LOGGER.info("final sync status: %s", json.dumps(status, indent=2))
    return status


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main(config_name: str) -> None:
    configure_logging()
    config_path = Path(__file__).resolve().parent / "configs" / config_name
    asyncio.run(run_demo(config_path))
