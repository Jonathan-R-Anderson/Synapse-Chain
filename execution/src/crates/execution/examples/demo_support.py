from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


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
from execution.sync.state.proofs import account_leaf_map, build_merkle_proofs, build_merkle_root
from primitives import Address, Hash
from transactions import LegacyTransaction


LOGGER = logging.getLogger("execution.sync.demo")


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


def _build_transfer_fixture(length: int = 3):
    chain_config = ChainConfig(
        chain_id=1,
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
            chain_id=1,
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


def _build_light_fixture():
    chain_config = ChainConfig(
        chain_id=1,
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
    state_root = os.environ.get("EXECUTION_STATE_ROOT")
    if state_root:
        state_directory = Path(state_root) / config.node_name
        payload = config.to_dict()
        payload["state_directory"] = str(state_directory)
        payload["database_path"] = str(state_directory / "sync.sqlite3")
        config = NodeConfig.from_dict(payload)
    LOGGER.info("loaded config from %s", Path(path).resolve())
    LOGGER.info("roles: %s", ", ".join(sorted(role.value for role in config.node_types)))
    LOGGER.info("sync mode: %s", config.sync_mode.value)
    return config


async def run_demo(config_path: Path | str) -> dict[str, object]:
    config = load_config(config_path)

    if NodeType.BOOTNODE in config.node_types and not config.requires_chain_sync:
        runtime = NodeRuntime(config)
        runtime.peer_manager.routing_table.add_peer(
            _make_peer_info("demo-known-peer", {NodeType.FULL}, serve_state=True, serve_blocks=True)
        )
        progress = await runtime.start()
        LOGGER.info("bootnode discovery table size: %s", len(runtime.peer_manager.routing_table.all_peers()))
        status = runtime.sync_status()
        LOGGER.info("final sync status: %s", json.dumps(status, indent=2))
        return status

    if config.sync_mode is SyncMode.LIGHT:
        chain_config, genesis_header, head_header, proofs, target = _build_light_fixture()
        if config.genesis_header is None:
            config = NodeConfig.from_dict(
                {
                    **config.to_dict(),
                    "genesis_header": genesis_header.to_dict(),
                    "chain_config": {
                        **config.to_dict()["chain_config"],
                        "fee_model": chain_config.fee_model.value,
                    },
                }
            )
        runtime = NodeRuntime(config)
        peer = DemoPeer(
            peer_info=_make_peer_info("light-source", {NodeType.FULL}, serve_state=True, serve_blocks=False),
            headers=(genesis_header, head_header),
            proofs={(1, target.to_hex()): proofs[target.to_hex()]},
        )
        runtime.attach_peer(peer)
        progress = await runtime.start()
        LOGGER.info("light node steady state: %s", progress.steady_state)
    else:
        chain_config, genesis_header, genesis_state, blocks = _build_transfer_fixture(length=4)
        if config.genesis_header is None:
            config = NodeConfig.from_dict(
                {
                    **config.to_dict(),
                    "genesis_header": genesis_header.to_dict(),
                    "genesis_state": [account.to_dict() for account in genesis_state],
                    "chain_config": {
                        **config.to_dict()["chain_config"],
                        "fee_model": chain_config.fee_model.value,
                    },
                }
            )
        runtime = NodeRuntime(config)
        peer = DemoPeer(
            peer_info=_make_peer_info("full-source", {NodeType.FULL, NodeType.STATE_PROVIDER}, serve_state=True, serve_blocks=True),
            headers=(genesis_header, *(block.header for block in blocks)),
            blocks={block.hash().to_hex(): block for block in blocks},
        )
        runtime.attach_peer(peer)
        progress = await runtime.start()
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
