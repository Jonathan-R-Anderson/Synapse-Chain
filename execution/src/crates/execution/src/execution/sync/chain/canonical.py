from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol


class _HeaderResolver(Protocol):
    def parent_hash_of(self, header_hash: str) -> str | None:
        ...

    def number_of(self, header_hash: str) -> int | None:
        ...


class CanonicalChain:
    """Canonical hash-by-height mapping and head tracking."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_chain (
                number INTEGER PRIMARY KEY,
                hash TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def get_head_hash(self) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM canonical_metadata WHERE key = 'head_hash'"
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_head(self, head_hash: str, resolver: _HeaderResolver) -> None:
        new_head_number = resolver.number_of(head_hash)
        if new_head_number is None:
            raise ValueError(f"unknown header hash {head_hash}")
        cursor_hash = head_hash
        updates: dict[int, str] = {}
        while cursor_hash is not None:
            number = resolver.number_of(cursor_hash)
            if number is None:
                break
            current = self.hash_at(number)
            if current == cursor_hash:
                break
            updates[number] = cursor_hash
            if number == 0:
                break
            cursor_hash = resolver.parent_hash_of(cursor_hash)
        self._connection.execute("DELETE FROM canonical_chain WHERE number > ?", (new_head_number,))
        for number, chain_hash in updates.items():
            self._connection.execute(
                "INSERT OR REPLACE INTO canonical_chain(number, hash) VALUES(?, ?)",
                (number, chain_hash),
            )
        self._connection.execute(
            "INSERT OR REPLACE INTO canonical_metadata(key, value) VALUES('head_hash', ?)",
            (head_hash,),
        )
        self._connection.commit()

    def hash_at(self, number: int) -> str | None:
        row = self._connection.execute(
            "SELECT hash FROM canonical_chain WHERE number = ?",
            (int(number),),
        ).fetchone()
        return None if row is None else str(row["hash"])
