from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ...block import BlockHeader


class HeaderStore:
    """Persistent block-header storage backed by SQLite."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS headers (
                hash TEXT PRIMARY KEY,
                parent_hash TEXT NOT NULL,
                number INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                total_score INTEGER NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._connection.execute("CREATE INDEX IF NOT EXISTS headers_by_number ON headers(number)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS headers_by_parent ON headers(parent_hash)")
        self._connection.commit()

    def put(self, header: BlockHeader, total_score: int) -> None:
        payload = json.dumps(header.to_dict(), sort_keys=True, separators=(",", ":"))
        self._connection.execute(
            """
            INSERT OR REPLACE INTO headers(hash, parent_hash, number, timestamp, total_score, data)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                header.hash().to_hex(),
                header.parent_hash.to_hex(),
                header.number,
                header.timestamp,
                int(total_score),
                payload,
            ),
        )
        self._connection.commit()

    def has(self, header_hash: str) -> bool:
        row = self._connection.execute("SELECT 1 FROM headers WHERE hash = ?", (header_hash,)).fetchone()
        return row is not None

    def get(self, header_hash: str) -> BlockHeader | None:
        row = self._connection.execute("SELECT data FROM headers WHERE hash = ?", (header_hash,)).fetchone()
        if row is None:
            return None
        return BlockHeader.from_dict(json.loads(str(row["data"])))

    def get_total_score(self, header_hash: str) -> int | None:
        row = self._connection.execute("SELECT total_score FROM headers WHERE hash = ?", (header_hash,)).fetchone()
        return None if row is None else int(row["total_score"])

    def by_number(self, number: int) -> tuple[BlockHeader, ...]:
        rows = self._connection.execute(
            "SELECT data FROM headers WHERE number = ? ORDER BY hash",
            (int(number),),
        ).fetchall()
        return tuple(BlockHeader.from_dict(json.loads(str(row["data"]))) for row in rows)

    def children_of(self, parent_hash: str) -> tuple[BlockHeader, ...]:
        rows = self._connection.execute(
            "SELECT data FROM headers WHERE parent_hash = ? ORDER BY number, hash",
            (parent_hash,),
        ).fetchall()
        return tuple(BlockHeader.from_dict(json.loads(str(row["data"]))) for row in rows)

    def highest_number(self) -> int:
        row = self._connection.execute("SELECT COALESCE(MAX(number), 0) AS height FROM headers").fetchone()
        return int(row["height"])
