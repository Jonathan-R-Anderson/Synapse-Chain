from __future__ import annotations

import time
from pathlib import Path

from ...block import Block, BlockHeader, ChainConfig
from ...block_validator import BlockValidator
from ..exceptions import InvalidPeerDataError
from ..models import ChainSegment
from .block_store import BlockStore
from .canonical import CanonicalChain
from .fork_choice import ChainCandidate, HeaviestChainForkChoice
from .header_store import HeaderStore


class ChainStore:
    """Combined chain persistence, validation, canonical mapping, and reorg helpers."""

    def __init__(self, path: Path | str, *, chain_config: ChainConfig | None = None) -> None:
        self._path = Path(path)
        self._headers = HeaderStore(self._path)
        self._blocks = BlockStore(self._path)
        self._canonical = CanonicalChain(self._path)
        self._validator = BlockValidator(chain_config or ChainConfig())
        self._fork_choice = HeaviestChainForkChoice()

    def header_count(self) -> int:
        return self._headers.highest_number() + 1 if self.get_canonical_head() is not None else 0

    def has_header(self, header_hash: str) -> bool:
        return self._headers.has(header_hash)

    def has_block(self, block_hash: str) -> bool:
        return self._blocks.has(block_hash)

    def get_header(self, header_hash: str) -> BlockHeader | None:
        return self._headers.get(header_hash)

    def get_block(self, block_hash: str) -> Block | None:
        return self._blocks.get(block_hash)

    def get_total_score(self, header_hash: str) -> int | None:
        return self._headers.get_total_score(header_hash)

    def parent_hash_of(self, header_hash: str) -> str | None:
        header = self.get_header(header_hash)
        return None if header is None else header.parent_hash.to_hex()

    def number_of(self, header_hash: str) -> int | None:
        header = self.get_header(header_hash)
        return None if header is None else header.number

    def get_canonical_head_hash(self) -> str | None:
        return self._canonical.get_head_hash()

    def get_canonical_head(self) -> BlockHeader | None:
        head_hash = self.get_canonical_head_hash()
        return None if head_hash is None else self.get_header(head_hash)

    def canonical_hash_at(self, number: int) -> str | None:
        return self._canonical.hash_at(number)

    def get_canonical_header(self, number: int) -> BlockHeader | None:
        header_hash = self.canonical_hash_at(number)
        return None if header_hash is None else self.get_header(header_hash)

    def get_canonical_block(self, number: int) -> Block | None:
        header_hash = self.canonical_hash_at(number)
        return None if header_hash is None else self.get_block(header_hash)

    def is_canonical(self, number: int, header_hash: str | None) -> bool:
        if header_hash is None:
            return False
        return self.canonical_hash_at(number) == header_hash

    def _validate_header(self, header: BlockHeader, *, trusted: bool) -> int:
        if header.timestamp > int(time.time()) + 900:
            raise InvalidPeerDataError("header timestamp is unreasonably far in the future")
        self._validator.validate_header(header)
        if header.number == 0 or trusted:
            return max(header.difficulty, 1)
        parent = self.get_header(header.parent_hash.to_hex())
        if parent is None:
            raise InvalidPeerDataError(f"missing parent header {header.parent_hash.to_hex()} for child {header.hash().to_hex()}")
        self._validator.validate_against_parent(Block(header=header), parent)
        self._validator.validate_base_fee(Block(header=header), parent)
        parent_score = self.get_total_score(parent.hash().to_hex())
        if parent_score is None:
            raise InvalidPeerDataError("parent header exists without stored score")
        return parent_score + max(header.difficulty, 1)

    def add_header(self, header: BlockHeader, *, trusted: bool = False) -> bool:
        header_hash = header.hash().to_hex()
        if self.has_header(header_hash):
            return False
        total_score = self._validate_header(header, trusted=trusted)
        self._headers.put(header, total_score)
        candidate = ChainCandidate(header_hash=header_hash, number=header.number, total_score=total_score)
        current_head = self.get_canonical_head()
        current_candidate = None
        if current_head is not None:
            current_score = self.get_total_score(current_head.hash().to_hex()) or 0
            current_candidate = ChainCandidate(
                header_hash=current_head.hash().to_hex(),
                number=current_head.number,
                total_score=current_score,
            )
        if self._fork_choice.prefers(candidate, current_candidate):
            self._canonical.set_head(header_hash, self)
        return True

    def add_block(self, block: Block, *, trusted_header: bool = False) -> None:
        header_hash = block.hash().to_hex()
        if not self.has_header(header_hash):
            self.add_header(block.header, trusted=trusted_header)
        canonical_header = self.get_header(header_hash)
        if canonical_header is None or canonical_header.hash().to_hex() != header_hash:
            raise InvalidPeerDataError("stored header does not match incoming block header")
        self._blocks.put(block)

    def seed_anchor(self, header: BlockHeader) -> None:
        self.add_header(header, trusted=True)
        self._canonical.set_head(header.hash().to_hex(), self)

    def canonical_segment(self, start_height: int, end_height: int) -> ChainSegment:
        header_hashes: list[str] = []
        for height in range(int(start_height), int(end_height) + 1):
            chain_hash = self.canonical_hash_at(height)
            if chain_hash is None:
                break
            header_hashes.append(chain_hash)
        return ChainSegment(
            start_height=int(start_height),
            end_height=int(start_height) + max(0, len(header_hashes) - 1),
            header_hashes=tuple(header_hashes),
            complete=len(header_hashes) == (int(end_height) - int(start_height) + 1),
        )

    def find_common_ancestor(self, left_hash: str, right_hash: str) -> BlockHeader | None:
        left = self.get_header(left_hash)
        right = self.get_header(right_hash)
        if left is None or right is None:
            return None
        while left.number > right.number:
            left = self.get_header(left.parent_hash.to_hex())
            if left is None:
                return None
        while right.number > left.number:
            right = self.get_header(right.parent_hash.to_hex())
            if right is None:
                return None
        while left.hash().to_hex() != right.hash().to_hex():
            if left.number == 0:
                return None
            left = self.get_header(left.parent_hash.to_hex())
            right = self.get_header(right.parent_hash.to_hex())
            if left is None or right is None:
                return None
        return left

    def prune_block_bodies(self, *, keep_from_height: int) -> None:
        self._blocks.delete_before_height(max(0, int(keep_from_height)))


__all__ = ["ChainStore"]
