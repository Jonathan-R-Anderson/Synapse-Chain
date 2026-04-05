from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class MetadataDB:
    """Small inspectable SQLite-backed key/value store used by sync components."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(namespace, key)
            )
            """
        )
        self._connection.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def put_json(self, namespace: str, key: str, payload: Any) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self._connection.execute(
            "INSERT OR REPLACE INTO metadata(namespace, key, value) VALUES(?, ?, ?)",
            (namespace, key, encoded),
        )
        self._connection.commit()

    def get_json(self, namespace: str, key: str) -> Any | None:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE namespace = ? AND key = ?",
            (namespace, key),
        ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["value"]))

    def delete(self, namespace: str, key: str) -> None:
        self._connection.execute("DELETE FROM metadata WHERE namespace = ? AND key = ?", (namespace, key))
        self._connection.commit()

    def list_namespace(self, namespace: str) -> dict[str, Any]:
        rows = self._connection.execute(
            "SELECT key, value FROM metadata WHERE namespace = ? ORDER BY key",
            (namespace,),
        ).fetchall()
        return {str(row["key"]): json.loads(str(row["value"])) for row in rows}

    def close(self) -> None:
        self._connection.close()
