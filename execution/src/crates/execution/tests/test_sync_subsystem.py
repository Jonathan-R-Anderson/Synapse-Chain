from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import execution_tests  # noqa: F401
import pytest
from crypto import address_from_private_key
from evm import StateDB
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
    SnapshotStore,
    StateStore,
    SyncCheckpoint,
    SyncMode,
    apply_block,
)
from execution.sync.config import capabilities_for_roles
from execution.sync.models import MerkleProof
from execution.sync.networking.peer_manager import PeerManager
from execution.sync.persistence.checkpoints import CheckpointStore
from execution.sync.persistence.metadata_db import MetadataDB
from execution.sync.state.proofs import account_leaf_map, build_merkle_proofs, build_merkle_root
from primitives import Address, Hash
from transactions import LegacyTransaction


def run(coro):
    return asyncio.run(coro)


@dataclass(frozen=True, slots=True)
class ChainFixture:
    chain_config: ChainConfig
    genesis_header: BlockHeader
    genesis_state: tuple[AccountState, ...]
    blocks: tuple[Block, ...]


@dataclass(slots=True)
class FixturePeer:
    peer_info: PeerInfo
    headers: tuple[BlockHeader, ...]
    blocks: dict[str, Block] = field(default_factory=dict)
    known_peers: tuple[PeerInfo, ...] = ()
    snapshot_manifest: object | None = None
    snapshot_chunks: dict[tuple[str, str], bytes] = field(default_factory=dict)
    account_proofs: dict[tuple[int, str], MerkleProof] = field(default_factory=dict)
    storage_proofs: dict[tuple[int, str, str], MerkleProof] = field(default_factory=dict)
    corrupt_blocks: set[str] = field(default_factory=set)

    async def ping(self) -> float:
        return 1.0

    async def list_known_peers(self):
        return self.known_peers

    async def get_headers(self, start_height: int, limit: int):
        return tuple(
            header
            for header in self.headers
            if header.number >= start_height
        )[:limit]

    async def get_block(self, block_hash: str):
        block = self.blocks.get(block_hash)
        if block is None:
            return None
        if block_hash in self.corrupt_blocks:
            return Block(
                header=BlockHeader(
                    parent_hash=block.header.parent_hash,
                    number=block.header.number,
                    gas_limit=block.header.gas_limit,
                    gas_used=block.header.gas_used,
                    timestamp=block.header.timestamp + 1,
                    coinbase=block.header.coinbase,
                    state_root=block.header.state_root,
                    transactions_root=block.header.transactions_root,
                    receipts_root=block.header.receipts_root,
                    difficulty=block.header.difficulty,
                ),
                transactions=block.transactions,
                receipts=block.receipts,
            )
        return block

    async def get_snapshot_manifest(self):
        return self.snapshot_manifest

    async def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        return self.snapshot_chunks[(snapshot_id, chunk_id)]

    async def get_account_proof(self, block_number: int, address: str):
        return self.account_proofs.get((block_number, Address.from_hex(address).to_hex()))

    async def get_storage_proof(self, block_number: int, address: str, slot: str):
        return self.storage_proofs.get((block_number, Address.from_hex(address).to_hex(), slot))


@dataclass(slots=True)
class RuntimePeer:
    peer_info: PeerInfo
    runtime: NodeRuntime

    async def ping(self) -> float:
        return 1.0

    async def list_known_peers(self):
        return ()

    async def get_headers(self, start_height: int, limit: int):
        service = self.runtime.services["block_serving"]
        return service.get_headers(start_height, limit)

    async def get_block(self, block_hash: str):
        service = self.runtime.services["block_serving"]
        return service.get_block(block_hash)

    async def get_snapshot_manifest(self):
        return self.runtime.services["state_provider"].get_snapshot_manifest()

    async def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        return self.runtime.services["state_provider"].get_snapshot_chunk(snapshot_id, chunk_id)

    async def get_account_proof(self, block_number: int, address: str):
        return self.runtime.services["state_provider"].get_account_proof(block_number, address)

    async def get_storage_proof(self, block_number: int, address: str, slot: str):
        return self.runtime.services["state_provider"].get_storage_proof(block_number, address, slot)


def make_peer_info(
    peer_id: str,
    *,
    node_types: set[NodeType],
    serve_state: bool,
    serve_blocks: bool,
    score: float = 0.0,
) -> PeerInfo:
    return PeerInfo(
        peer_id=peer_id,
        endpoint=f"memory://{peer_id}",
        capabilities=capabilities_for_roles(frozenset(node_types), serve_state=serve_state, serve_blocks=serve_blocks),
        score=score,
    )


def account_state_from_state(state: StateDB) -> tuple[AccountState, ...]:
    accounts: list[AccountState] = []
    for address, account in state.accounts():
        accounts.append(
            AccountState(
                address=address,
                nonce=account.nonce,
                balance=account.balance,
                code=account.code,
                storage=tuple((slot, value) for slot, value in account.storage.items()),
            )
        )
    return tuple(sorted(accounts, key=lambda item: item.address.to_hex()))


def state_db_from_accounts(accounts: tuple[AccountState, ...]) -> StateDB:
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


def build_transfer_chain(length: int = 3) -> ChainFixture:
    chain_config = ChainConfig(
        chain_id=1,
        fee_model=FeeModel.LEGACY,
        support_eip1559_transactions=False,
        support_zk_transactions=False,
    )
    sender = address_from_private_key(1)
    recipient = address_from_private_key(2)
    genesis_state = (
        AccountState(address=sender, balance=1_000_000),
        AccountState(address=recipient, balance=0),
    )
    state = state_db_from_accounts(genesis_state)
    genesis_header = BlockHeader(number=0, gas_limit=30_000_000, gas_used=0, timestamp=0, coinbase=Address.zero())
    parent = genesis_header
    builder = BlockBuilder(chain_config)
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
        block = builder.build_block(
            parent_block=parent,
            transactions=(tx,),
            execution_result=result,
            timestamp=index,
            gas_limit=30_000_000,
            beneficiary=Address.zero(),
        )
        blocks.append(block)
        parent = block.header
    return ChainFixture(chain_config=chain_config, genesis_header=genesis_header, genesis_state=genesis_state, blocks=tuple(blocks))


def build_forked_chains():
    fixture = build_transfer_chain(length=1)
    chain_config = fixture.chain_config
    genesis_header = fixture.genesis_header
    block_one = fixture.blocks[0]
    state_after_one = state_db_from_accounts(account_state_from_state(apply_block(
        state_db_from_accounts(fixture.genesis_state),
        Block(
            header=BlockHeader(
                parent_hash=genesis_header.hash(),
                number=1,
                gas_limit=30_000_000,
                gas_used=0,
                timestamp=1,
                coinbase=Address.zero(),
            ),
            transactions=fixture.blocks[0].transactions,
        ),
        chain_config,
        parent_header=genesis_header,
    ).state))
    builder = BlockBuilder(chain_config)
    recipient = address_from_private_key(2)
    alternate = address_from_private_key(3)

    tx_a = LegacyTransaction(nonce=1, gas_price=0, gas_limit=21_000, to=recipient, value=20, data=b"", chain_id=1).sign(1)
    skeleton_a = Block(
        header=BlockHeader(
            parent_hash=block_one.hash(),
            number=2,
            gas_limit=30_000_000,
            gas_used=0,
            timestamp=2,
            coinbase=Address.zero(),
        ),
        transactions=(tx_a,),
    )
    result_a = apply_block(state_after_one.clone(), skeleton_a, chain_config, parent_header=block_one.header)
    block_two_a = builder.build_block(
        parent_block=block_one.header,
        transactions=(tx_a,),
        execution_result=result_a,
        timestamp=2,
        gas_limit=30_000_000,
        beneficiary=Address.zero(),
    )

    tx_b1 = LegacyTransaction(nonce=1, gas_price=0, gas_limit=21_000, to=alternate, value=25, data=b"", chain_id=1).sign(1)
    skeleton_b1 = Block(
        header=BlockHeader(
            parent_hash=block_one.hash(),
            number=2,
            gas_limit=30_000_000,
            gas_used=0,
            timestamp=2,
            coinbase=Address.zero(),
        ),
        transactions=(tx_b1,),
    )
    result_b1 = apply_block(state_after_one.clone(), skeleton_b1, chain_config, parent_header=block_one.header)
    block_two_b = builder.build_block(
        parent_block=block_one.header,
        transactions=(tx_b1,),
        execution_result=result_b1,
        timestamp=2,
        gas_limit=30_000_000,
        beneficiary=Address.zero(),
    )
    tx_b2 = LegacyTransaction(nonce=2, gas_price=0, gas_limit=21_000, to=alternate, value=5, data=b"", chain_id=1).sign(1)
    skeleton_b2 = Block(
        header=BlockHeader(
            parent_hash=block_two_b.hash(),
            number=3,
            gas_limit=30_000_000,
            gas_used=0,
            timestamp=3,
            coinbase=Address.zero(),
        ),
        transactions=(tx_b2,),
    )
    result_b2 = apply_block(result_b1.state, skeleton_b2, chain_config, parent_header=block_two_b.header)
    block_three_b = builder.build_block(
        parent_block=block_two_b.header,
        transactions=(tx_b2,),
        execution_result=result_b2,
        timestamp=3,
        gas_limit=30_000_000,
        beneficiary=Address.zero(),
    )
    return fixture, (block_one, block_two_a), (block_one, block_two_b, block_three_b)


def build_light_headers_and_proofs():
    chain_config = ChainConfig(
        chain_id=1,
        fee_model=FeeModel.LEGACY,
        support_eip1559_transactions=False,
        support_zk_transactions=False,
    )
    target = address_from_private_key(9)
    other = address_from_private_key(10)
    genesis_accounts = (
        AccountState(address=target, balance=100),
        AccountState(address=other, balance=5),
    )
    genesis_root = build_merkle_root(account_leaf_map(genesis_accounts))
    genesis_header = BlockHeader(
        number=0,
        gas_limit=30_000_000,
        gas_used=0,
        timestamp=0,
        coinbase=Address.zero(),
        state_root=Hash.from_hex(genesis_root),
        difficulty=1,
    )
    height_one_accounts = (
        AccountState(address=target, balance=125),
        AccountState(address=other, balance=1),
    )
    height_one_root = build_merkle_root(account_leaf_map(height_one_accounts))
    header_one = BlockHeader(
        parent_hash=genesis_header.hash(),
        number=1,
        gas_limit=30_000_000,
        gas_used=0,
        timestamp=1,
        coinbase=Address.zero(),
        state_root=Hash.from_hex(height_one_root),
        difficulty=1,
    )
    proofs = build_merkle_proofs(account_leaf_map(height_one_accounts))
    return chain_config, genesis_header, (header_one,), proofs, target


def config_for(
    tmp_path: Path,
    *,
    node_name: str,
    node_types: set[NodeType],
    sync_mode: SyncMode,
    fixture: ChainFixture | None,
    serve_state: bool = False,
    serve_blocks: bool = False,
    pruning_enabled: bool = True,
    max_blocks_per_cycle: int | None = None,
) -> NodeConfig:
    return NodeConfig(
        node_name=node_name,
        node_types=frozenset(node_types),
        sync_mode=sync_mode,
        pruning_enabled=pruning_enabled,
        snapshot_interval=2,
        max_peers=8,
        state_directory=tmp_path / node_name,
        serve_state=serve_state,
        serve_blocks=serve_blocks,
        chain_config=ChainConfig() if fixture is None else fixture.chain_config,
        genesis_header=None if fixture is None else fixture.genesis_header.to_dict(),
        genesis_state=() if fixture is None else fixture.genesis_state,
        history_retention=32,
        prune_depth=32,
        max_blocks_per_cycle=max_blocks_per_cycle,
    )


def peer_for_fixture(fixture: ChainFixture, *, peer_id: str = "peer", serve_state: bool = True, serve_blocks: bool = True, score: float = 0.0) -> FixturePeer:
    headers = (fixture.genesis_header, *(block.header for block in fixture.blocks))
    blocks = {block.hash().to_hex(): block for block in fixture.blocks}
    return FixturePeer(
        peer_info=make_peer_info(peer_id, node_types={NodeType.FULL, NodeType.STATE_PROVIDER}, serve_state=serve_state, serve_blocks=serve_blocks, score=score),
        headers=headers,
        blocks=blocks,
    )


def test_full_node_syncs_from_empty_database(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=3)
    runtime = NodeRuntime(config_for(tmp_path, node_name="full", node_types={NodeType.FULL}, sync_mode=SyncMode.FULL, fixture=fixture))
    runtime.attach_peer(peer_for_fixture(fixture))
    progress = run(runtime.start())
    assert progress.steady_state is True
    assert runtime.chain_store.get_canonical_head().number == 3
    assert runtime.state_store.current_root() == fixture.blocks[-1].header.state_root.to_hex()


def test_restart_mid_sync_and_resume(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=3)
    partial_runtime = NodeRuntime(
        config_for(
            tmp_path,
            node_name="restartable",
            node_types={NodeType.FULL},
            sync_mode=SyncMode.FULL,
            fixture=fixture,
            max_blocks_per_cycle=1,
        )
    )
    partial_runtime.attach_peer(peer_for_fixture(fixture))
    partial = run(partial_runtime.start())
    assert partial.steady_state is False
    assert partial.applied_blocks == 1

    resumed_runtime = NodeRuntime(
        config_for(
            tmp_path,
            node_name="restartable",
            node_types={NodeType.FULL},
            sync_mode=SyncMode.FULL,
            fixture=fixture,
        )
    )
    resumed_runtime.attach_peer(peer_for_fixture(fixture))
    resumed = run(resumed_runtime.start())
    assert resumed.steady_state is True
    assert resumed_runtime.chain_store.get_canonical_head().number == 3


def test_light_node_header_sync_and_proof_verification(tmp_path: Path) -> None:
    chain_config, genesis_header, extra_headers, proofs, target = build_light_headers_and_proofs()
    headers = (genesis_header, *extra_headers)
    light_peer = FixturePeer(
        peer_info=make_peer_info("light-provider", node_types={NodeType.FULL, NodeType.STATE_PROVIDER}, serve_state=True, serve_blocks=False),
        headers=headers,
        account_proofs={(1, target.to_hex()): proofs[target.to_hex()]},
    )
    config = NodeConfig(
        node_name="light",
        node_types=frozenset({NodeType.LIGHT}),
        sync_mode=SyncMode.LIGHT,
        pruning_enabled=True,
        snapshot_interval=8,
        max_peers=8,
        state_directory=tmp_path / "light",
        serve_state=False,
        serve_blocks=False,
        chain_config=chain_config,
        genesis_header=genesis_header.to_dict(),
    )
    runtime = NodeRuntime(config)
    runtime.attach_peer(light_peer)
    progress = run(runtime.start())
    assert progress.steady_state is True
    proof = run(runtime.request_account_fragment(target, block_number=1))
    assert proof.value is not None
    assert runtime.state_store.get_cached_account_fragment(1, target) is not None


def test_snapshot_creation_and_restoration(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=3)
    metadata = MetadataDB(tmp_path / "snapshot.sqlite3")
    state_store = StateStore(tmp_path / "state_a", metadata)
    state_store.ensure_genesis(fixture.genesis_state, block_hash=fixture.genesis_header.hash().to_hex())
    parent = fixture.genesis_header
    for block in fixture.blocks:
        state_store.apply_block(block, chain_config=fixture.chain_config, parent_header=parent)
        state_store.record_history(block.header.number, block_hash=block.hash().to_hex(), pruning_enabled=False, retention=100)
        parent = block.header
    snapshot_store = SnapshotStore(tmp_path / "state_a", metadata)
    manifest = snapshot_store.generate_snapshot(
        state_store=state_store,
        block_height=fixture.blocks[-1].header.number,
        block_hash=fixture.blocks[-1].hash().to_hex(),
        chunk_size=2,
    )
    metadata_b = MetadataDB(tmp_path / "restore.sqlite3")
    restored_state = StateStore(tmp_path / "state_b", metadata_b)
    snapshot_store_b = SnapshotStore(tmp_path / "state_a", metadata)
    snapshot_store_b.restore_snapshot(manifest, state_store=restored_state)
    assert restored_state.current_root() == fixture.blocks[-1].header.state_root.to_hex()


def test_snap_sync_from_manifest_chunks_and_replay(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=4)
    provider_runtime = NodeRuntime(
        config_for(
            tmp_path,
            node_name="provider",
            node_types={NodeType.FULL, NodeType.STATE_PROVIDER, NodeType.SNAPSHOT_GENERATOR},
            sync_mode=SyncMode.FULL,
            fixture=fixture,
            serve_state=True,
            serve_blocks=True,
        )
    )
    provider_runtime.attach_peer(peer_for_fixture(fixture))
    run(provider_runtime.start())
    snapshot_service = provider_runtime.services["snapshot"]
    manifest = snapshot_service.generate_snapshot(block_height=2, block_hash=fixture.blocks[1].hash().to_hex())
    provider_peer = RuntimePeer(
        peer_info=make_peer_info(
            "provider-peer",
            node_types={NodeType.FULL, NodeType.STATE_PROVIDER, NodeType.SNAPSHOT_GENERATOR},
            serve_state=True,
            serve_blocks=True,
        ),
        runtime=provider_runtime,
    )
    snap_runtime = NodeRuntime(
        config_for(
            tmp_path,
            node_name="snap-node",
            node_types={NodeType.FULL},
            sync_mode=SyncMode.SNAP,
            fixture=fixture,
        )
    )
    snap_runtime.attach_peer(provider_peer)
    progress = run(snap_runtime.start())
    assert progress.steady_state is True
    assert snap_runtime.chain_store.get_canonical_head().number == 4
    assert snap_runtime.state_store.current_root() == fixture.blocks[-1].header.state_root.to_hex()
    assert manifest.block_height == 2


def test_archive_node_retains_old_history(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=3)
    runtime = NodeRuntime(
        config_for(
            tmp_path,
            node_name="archive",
            node_types={NodeType.ARCHIVE},
            sync_mode=SyncMode.ARCHIVE,
            fixture=fixture,
            pruning_enabled=False,
        )
    )
    runtime.attach_peer(peer_for_fixture(fixture))
    run(runtime.start())
    assert runtime.chain_store.get_canonical_block(1) is not None
    assert runtime.state_store.load_history_accounts(1)


def test_fork_handling_and_reorg(tmp_path: Path) -> None:
    fixture, chain_a, chain_b = build_forked_chains()
    peer_a = FixturePeer(
        peer_info=make_peer_info("peer-a", node_types={NodeType.FULL}, serve_state=True, serve_blocks=True),
        headers=(fixture.genesis_header, *(block.header for block in chain_a)),
        blocks={block.hash().to_hex(): block for block in chain_a},
    )
    runtime = NodeRuntime(config_for(tmp_path, node_name="reorg", node_types={NodeType.FULL}, sync_mode=SyncMode.FULL, fixture=fixture))
    runtime.attach_peer(peer_a)
    run(runtime.start())
    assert runtime.chain_store.get_canonical_head().number == 2
    peer_b = FixturePeer(
        peer_info=make_peer_info("peer-b", node_types={NodeType.FULL, NodeType.ARCHIVE}, serve_state=True, serve_blocks=True),
        headers=(fixture.genesis_header, *(block.header for block in chain_b)),
        blocks={block.hash().to_hex(): block for block in chain_b},
    )
    runtime.attach_peer(peer_b)
    progress = run(runtime.sync_manager.run())
    assert progress.steady_state is True
    assert runtime.chain_store.get_canonical_head().number == 3
    assert runtime.state_store.current_root() == chain_b[-1].header.state_root.to_hex()


def test_invalid_peer_data_is_rejected(tmp_path: Path) -> None:
    fixture = build_transfer_chain(length=2)
    bad_hash = fixture.blocks[0].hash().to_hex()
    bad_peer = peer_for_fixture(fixture, peer_id="bad", score=5.0)
    bad_peer.corrupt_blocks.add(bad_hash)
    good_peer = peer_for_fixture(fixture, peer_id="good", score=0.0)
    runtime = NodeRuntime(config_for(tmp_path, node_name="invalid", node_types={NodeType.FULL}, sync_mode=SyncMode.FULL, fixture=fixture))
    runtime.attach_peer(bad_peer)
    runtime.attach_peer(good_peer)
    progress = run(runtime.start())
    assert progress.steady_state is True
    assert runtime.peer_manager.is_banned("bad") is True or runtime.peer_manager.get_peer_info("bad").score < 0


def test_role_aware_peer_selection() -> None:
    manager = PeerManager(max_peers=8)
    full_peer = FixturePeer(
        peer_info=make_peer_info("full", node_types={NodeType.FULL}, serve_state=False, serve_blocks=True),
        headers=(),
    )
    snap_peer = FixturePeer(
        peer_info=make_peer_info("snap", node_types={NodeType.FULL, NodeType.STATE_PROVIDER}, serve_state=True, serve_blocks=True),
        headers=(),
    )
    archive_peer = FixturePeer(
        peer_info=make_peer_info("archive", node_types={NodeType.ARCHIVE}, serve_state=True, serve_blocks=True),
        headers=(),
    )
    light_peer = FixturePeer(
        peer_info=make_peer_info("proof", node_types={NodeType.FULL}, serve_state=True, serve_blocks=False),
        headers=(),
    )
    for peer in (full_peer, snap_peer, archive_peer, light_peer):
        manager.register_peer(peer)
    assert {peer.peer_info.peer_id for peer in manager.peers_for_sync_mode(SyncMode.SNAP)} == {"snap"}
    assert {peer.peer_info.peer_id for peer in manager.peers_for_sync_mode(SyncMode.ARCHIVE, archive_required=True)} == {"archive"}
    assert {peer.peer_info.peer_id for peer in manager.peers_for_sync_mode(SyncMode.LIGHT)} == {"proof"}


def test_checkpoint_persistence_correctness(tmp_path: Path) -> None:
    metadata = MetadataDB(tmp_path / "checkpoint.sqlite3")
    store = CheckpointStore(metadata)
    checkpoint = SyncCheckpoint(
        mode=SyncMode.FULL,
        stage="bodies",
        last_synced_header_height=10,
        last_synced_header_hash="0x" + "11" * 32,
        last_applied_block_height=9,
        last_applied_block_hash="0x" + "22" * 32,
        snapshot_point_height=5,
        snapshot_block_hash="0x" + "33" * 32,
        pending_state_chunks=("00000000",),
        peer_scores={"peer": 4.5},
        canonical_head_height=10,
        canonical_head_hash="0x" + "11" * 32,
        state_reconstruction_complete=False,
        steady_state=False,
    )
    store.save("node", checkpoint)
    reloaded = store.load("node")
    assert reloaded is not None
    assert reloaded.to_dict() == checkpoint.to_dict()
