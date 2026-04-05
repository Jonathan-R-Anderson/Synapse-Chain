from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from evm import StateDB

from ...trie import compute_state_root
from ..exceptions import SnapshotIntegrityError
from ..models import AccountState, SnapshotManifest, StateChunk
from ..persistence.metadata_db import MetadataDB
from .state_store import StateStore


class SnapshotStore:
    """Persistent snapshot manifests and chunk payload storage."""

    def __init__(self, base_directory: Path | str, metadata_db: MetadataDB) -> None:
        self._base_directory = Path(base_directory)
        self._metadata_db = metadata_db
        self._snapshots_directory = self._base_directory / "snapshots"
        self._manifests_directory = self._snapshots_directory / "manifests"
        self._chunks_directory = self._snapshots_directory / "chunks"
        self._manifests_directory.mkdir(parents=True, exist_ok=True)
        self._chunks_directory.mkdir(parents=True, exist_ok=True)

    def manifest_path(self, snapshot_id: str) -> Path:
        return self._manifests_directory / f"{snapshot_id}.json"

    def chunk_path(self, snapshot_id: str, chunk_id: str) -> Path:
        target_directory = self._chunks_directory / snapshot_id
        target_directory.mkdir(parents=True, exist_ok=True)
        return target_directory / f"{chunk_id}.json"

    def write_manifest(self, manifest: SnapshotManifest) -> None:
        self.manifest_path(manifest.snapshot_id).write_text(
            json.dumps(manifest.to_dict(), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        self._metadata_db.put_json(
            "snapshots",
            manifest.snapshot_id,
            {"manifest_path": str(self.manifest_path(manifest.snapshot_id)), "block_height": manifest.block_height},
        )

    def read_manifest(self, snapshot_id: str) -> SnapshotManifest:
        return SnapshotManifest.from_dict(json.loads(self.manifest_path(snapshot_id).read_text(encoding="utf-8")))

    def latest_manifest(self) -> SnapshotManifest | None:
        entries = self._metadata_db.list_namespace("snapshots")
        if not entries:
            return None
        latest_snapshot = max(entries.values(), key=lambda item: int(item["block_height"]))
        return SnapshotManifest.from_dict(json.loads(Path(str(latest_snapshot["manifest_path"])).read_text(encoding="utf-8")))

    def write_chunk(self, snapshot_id: str, chunk: StateChunk, payload: bytes) -> None:
        self.chunk_path(snapshot_id, chunk.chunk_id).write_bytes(bytes(payload))

    def read_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        return self.chunk_path(snapshot_id, chunk_id).read_bytes()

    def has_chunk(self, snapshot_id: str, chunk_id: str) -> bool:
        return self.chunk_path(snapshot_id, chunk_id).exists()

    def verify_manifest(self, manifest: SnapshotManifest) -> bool:
        return manifest.compute_hash() == manifest.manifest_hash

    def verify_chunk(self, chunk: StateChunk, payload: bytes) -> bool:
        return _chunk_hash(payload) == chunk.chunk_hash

    def generate_snapshot(
        self,
        *,
        state_store: StateStore,
        block_height: int,
        block_hash: str,
        chunk_size: int,
    ) -> SnapshotManifest:
        accounts = (
            state_store.load_history_accounts(block_height)
            if block_height != state_store.latest_history_height()
            else state_store.export_accounts()
        )
        chunk_payloads: dict[str, bytes] = {}
        chunks: list[StateChunk] = []
        ordered_accounts = tuple(sorted(accounts, key=lambda item: item.address.to_hex()))
        snapshot_id = f"snapshot-{int(block_height):08d}-{block_hash[2:10]}"
        for index in range(0, len(ordered_accounts), int(chunk_size)):
            records = ordered_accounts[index : index + int(chunk_size)]
            encoded = json.dumps([record.to_dict() for record in records], sort_keys=True).encode("utf-8")
            chunk_id = f"{index // int(chunk_size):08d}"
            chunk = StateChunk(
                snapshot_id=snapshot_id,
                chunk_id=chunk_id,
                first_key=None if not records else records[0].address.to_hex(),
                last_key=None if not records else records[-1].address.to_hex(),
                chunk_hash=_chunk_hash(encoded),
                record_count=len(records),
            )
            chunk_payloads[chunk_id] = encoded
            chunks.append(chunk)
        if not chunks:
            chunks.append(
                StateChunk(
                    snapshot_id=snapshot_id,
                    chunk_id="00000000",
                    first_key=None,
                    last_key=None,
                    chunk_hash=_chunk_hash(b"[]"),
                    record_count=0,
                )
            )
            chunk_payloads["00000000"] = b"[]"
        manifest = SnapshotManifest(
            snapshot_id=snapshot_id,
            block_height=int(block_height),
            block_hash=block_hash,
            state_root=_state_root_from_accounts(accounts),
            created_at=float(block_height),
            chunks=tuple(chunks),
        )
        self.write_manifest(manifest)
        for chunk in manifest.chunks:
            self.write_chunk(snapshot_id, chunk, chunk_payloads[chunk.chunk_id])
        return manifest

    def restore_snapshot(
        self,
        manifest: SnapshotManifest,
        *,
        state_store: StateStore,
        chunk_source: Callable[[StateChunk], bytes] | None = None,
    ) -> None:
        if not self.verify_manifest(manifest):
            raise SnapshotIntegrityError("snapshot manifest hash mismatch")
        accounts: list[AccountState] = []
        for chunk in manifest.chunks:
            payload = chunk_source(chunk) if chunk_source is not None else self.read_chunk(manifest.snapshot_id, chunk.chunk_id)
            if _chunk_hash(payload) != chunk.chunk_hash:
                raise SnapshotIntegrityError(f"snapshot chunk {chunk.chunk_id} hash mismatch")
            for account_payload in json.loads(payload.decode("utf-8")):
                accounts.append(AccountState.from_dict(account_payload))
        state_store.import_accounts(accounts)
        if state_store.current_root() != manifest.state_root:
            raise SnapshotIntegrityError("restored snapshot root does not match the manifest state root")
        state_store.record_history(
            manifest.block_height,
            block_hash=manifest.block_hash,
            pruning_enabled=False,
            retention=1_000_000,
        )


def _chunk_hash(payload: bytes) -> str:
    import hashlib

    return "0x" + hashlib.sha256(bytes(payload)).hexdigest()


def _state_root_from_accounts(accounts: tuple[AccountState, ...]) -> str:
    state = StateDB()
    for account in accounts:
        target = state.get_or_create_account(account.address)
        target.nonce = account.nonce
        target.balance = account.balance
        target.code = account.code
        for slot, value in account.storage:
            target.storage.set(int(slot), int(value))
        target.storage.commit()
    return compute_state_root(state).to_hex()
