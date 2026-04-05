from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...block import Block


class BlockStore:
    """Persistent block-body storage backed by SQLite."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                hash TEXT PRIMARY KEY,
                number INTEGER NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS blocks_by_number ON blocks(number)")
        self._connection.commit()

    def put(self, block: Block) -> None:
        payload = json.dumps(block.to_dict(), sort_keys=True, separators=(",", ":"))
        self._connection.execute(
            "INSERT OR REPLACE INTO blocks(hash, number, data) VALUES(?, ?, ?)",
            (block.hash().to_hex(), block.header.number, payload),
        )
        self._connection.commit()

    def has(self, block_hash: str) -> bool:
        row = self._connection.execute("SELECT 1 FROM blocks WHERE hash = ?", (block_hash,)).fetchone()
        return row is not None

    def get(self, block_hash: str) -> Block | None:
        row = self._connection.execute("SELECT data FROM blocks WHERE hash = ?", (block_hash,)).fetchone()
        if row is None:
            return None
        return Block.from_dict(json.loads(str(row["data"])))

    def delete_before_height(self, height: int) -> None:
        self._connection.execute("DELETE FROM blocks WHERE number < ?", (int(height),))
        self._connection.commit()
