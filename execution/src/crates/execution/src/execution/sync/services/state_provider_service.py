from __future__ import annotations

from primitives import Address, U256

from ..models import MerkleProof, SnapshotManifest
from ..state.proofs import account_leaf_map, build_merkle_proofs, build_merkle_root
from ..state.snapshot_store import SnapshotStore
from ..state.state_store import StateStore


class StateProviderService:
    """Serve snapshots, state chunks, and proof-backed state fragments."""

    def __init__(self, state_store: StateStore, snapshot_store: SnapshotStore) -> None:
        self._state_store = state_store
        self._snapshot_store = snapshot_store

    def get_snapshot_manifest(self) -> SnapshotManifest | None:
        return self._snapshot_store.latest_manifest()

    def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        return self._snapshot_store.read_chunk(snapshot_id, chunk_id)

    def get_account_proof(self, block_number: int, address: str) -> MerkleProof | None:
        accounts = self._state_store.load_history_accounts(block_number)
        leaf_map = account_leaf_map(accounts)
        proof = build_merkle_proofs(leaf_map).get(Address.from_hex(address).to_hex())
        return proof

    def get_storage_proof(self, block_number: int, address: str, slot: str) -> MerkleProof | None:
        accounts = self._state_store.load_history_accounts(block_number)
        target_address = Address.from_hex(address)
        target_slot = U256.from_hex(slot)
        for account in accounts:
            if account.address != target_address:
                continue
            storage_map = {
                f"{target_address.to_hex()}:{slot_key.to_hex()}": slot_value.to_hex()
                for slot_key, slot_value in account.storage
            }
            if not storage_map:
                return None
            proofs = build_merkle_proofs(storage_map)
            proof = proofs.get(f"{target_address.to_hex()}:{target_slot.to_hex()}")
            if proof is not None:
                return proof
            root = build_merkle_root(storage_map)
            return MerkleProof(
                proof_type="merkle",
                root=root,
                key=f"{target_address.to_hex()}:{target_slot.to_hex()}",
                value=None,
                exists=False,
            )
        return None
