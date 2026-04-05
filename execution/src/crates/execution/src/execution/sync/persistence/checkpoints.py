from __future__ import annotations

from ..exceptions import CheckpointCorruptionError
from ..models import SyncCheckpoint
from .metadata_db import MetadataDB


class CheckpointStore:
    """Persistent sync-checkpoint storage backed by the metadata database."""

    _NAMESPACE = "checkpoints"

    def __init__(self, metadata_db: MetadataDB) -> None:
        self._metadata_db = metadata_db

    def load(self, name: str) -> SyncCheckpoint | None:
        payload = self._metadata_db.get_json(self._NAMESPACE, name)
        if payload is None:
            return None
        try:
            checkpoint = SyncCheckpoint.from_dict(payload)
        except Exception as exc:
            raise CheckpointCorruptionError(f"checkpoint {name!r} is unreadable") from exc
        if checkpoint.last_applied_block_height > checkpoint.last_synced_header_height:
            raise CheckpointCorruptionError("applied block height cannot exceed synced header height")
        if checkpoint.canonical_head_height < checkpoint.last_applied_block_height:
            raise CheckpointCorruptionError("canonical head cannot be behind the applied chain")
        return checkpoint

    def save(self, name: str, checkpoint: SyncCheckpoint) -> None:
        self._metadata_db.put_json(self._NAMESPACE, name, checkpoint.to_dict())

    def clear(self, name: str) -> None:
        self._metadata_db.delete(self._NAMESPACE, name)
