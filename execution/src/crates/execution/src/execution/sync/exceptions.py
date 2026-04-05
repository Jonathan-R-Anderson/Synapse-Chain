from __future__ import annotations


class SyncError(RuntimeError):
    """Base class for sync-subsystem errors."""


class ConfigurationMismatchError(SyncError):
    """Raised when runtime roles and sync configuration are incompatible."""


class NoSuitablePeerError(SyncError):
    """Raised when no peer satisfies the requested capability filter."""


class InvalidPeerDataError(SyncError):
    """Raised when a peer serves malformed or invalid chain/state data."""


class SnapshotIntegrityError(SyncError):
    """Raised when a downloaded or restored snapshot fails integrity checks."""


class StateReconstructionError(SyncError):
    """Raised when state replay or restoration cannot reach the expected root."""


class CheckpointCorruptionError(SyncError):
    """Raised when a stored checkpoint is internally inconsistent."""


class ReorgDetected(SyncError):
    """Raised internally to trigger canonical-chain rollback and replay."""
