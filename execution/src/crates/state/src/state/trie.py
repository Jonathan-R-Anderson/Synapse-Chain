from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from crypto import keccak256
from encoding import encode
from primitives import Hash

from .constants import EMPTY_TRIE_ROOT
from .database import MemoryKeyValueStore


def _identity(data: bytes) -> bytes:
    return data


def _bytes_to_nibbles(data: bytes) -> tuple[int, ...]:
    nibbles: list[int] = []
    for byte in data:
        nibbles.append(byte >> 4)
        nibbles.append(byte & 0x0F)
    return tuple(nibbles)


def _compact_encode(nibbles: tuple[int, ...], is_leaf: bool) -> bytes:
    odd_length = len(nibbles) % 2 == 1
    flags = 2 if is_leaf else 0
    encoded_nibbles: list[int] = []

    if odd_length:
        encoded_nibbles.extend([flags + 1, nibbles[0]])
        encoded_nibbles.extend(nibbles[1:])
    else:
        encoded_nibbles.extend([flags, 0])
        encoded_nibbles.extend(nibbles)

    output = bytearray()
    for index in range(0, len(encoded_nibbles), 2):
        output.append((encoded_nibbles[index] << 4) | encoded_nibbles[index + 1])
    return bytes(output)


def _longest_common_prefix(paths: list[tuple[int, ...]]) -> int:
    if not paths:
        return 0
    limit = min(len(path) for path in paths)
    for offset in range(limit):
        nibble = paths[0][offset]
        if any(path[offset] != nibble for path in paths[1:]):
            return offset
    return limit


@dataclass(slots=True)
class MerklePatriciaTrie:
    key_transform: Callable[[bytes], bytes] = _identity
    node_store: MemoryKeyValueStore = field(default_factory=MemoryKeyValueStore)
    _entries: dict[bytes, bytes] = field(default_factory=dict)
    _root_hash: Hash = field(default_factory=lambda: EMPTY_TRIE_ROOT)

    def get(self, key: bytes | bytearray | memoryview) -> bytes | None:
        value = self._entries.get(bytes(key))
        return None if value is None else bytes(value)

    def update(self, key: bytes | bytearray | memoryview, value: bytes | bytearray | memoryview) -> None:
        self._entries[bytes(key)] = bytes(value)

    def delete(self, key: bytes | bytearray | memoryview) -> None:
        self._entries.pop(bytes(key), None)

    def clear(self) -> None:
        self._entries.clear()
        self.node_store.clear()
        self._root_hash = EMPTY_TRIE_ROOT

    def items(self) -> tuple[tuple[bytes, bytes], ...]:
        return tuple(sorted(((key, value) for key, value in self._entries.items()), key=lambda item: item[0]))

    def clone(self) -> "MerklePatriciaTrie":
        return MerklePatriciaTrie(
            key_transform=self.key_transform,
            node_store=self.node_store.clone(),
            _entries=dict(self._entries),
            _root_hash=self._root_hash,
        )

    @property
    def root_hash(self) -> Hash:
        return self.commit()

    def commit(self) -> Hash:
        self.node_store.clear()
        if not self._entries:
            self._root_hash = EMPTY_TRIE_ROOT
            return self._root_hash

        items = [
            (_bytes_to_nibbles(self.key_transform(key)), value)
            for key, value in sorted(self._entries.items(), key=lambda item: self.key_transform(item[0]))
        ]
        encoded_root = self._build_node(items)
        root_hash = keccak256(encoded_root)
        self.node_store.set(root_hash.to_bytes(), encoded_root)
        self._root_hash = root_hash
        return self._root_hash

    def _store_reference(self, encoded_node: bytes) -> bytes:
        if len(encoded_node) < 32:
            return encoded_node
        digest = keccak256(encoded_node)
        self.node_store.set(digest.to_bytes(), encoded_node)
        return digest.to_bytes()

    def _build_node(self, items: list[tuple[tuple[int, ...], bytes]]) -> bytes:
        if not items:
            return encode(b"")

        if len(items) == 1:
            path, value = items[0]
            return encode([_compact_encode(path, True), value])

        paths = [path for path, _ in items]
        prefix_length = _longest_common_prefix(paths)
        if prefix_length:
            prefix = paths[0][:prefix_length]
            child_items = [(path[prefix_length:], value) for path, value in items]
            child_encoded = self._build_node(child_items)
            return encode([_compact_encode(prefix, False), self._store_reference(child_encoded)])

        branch: list[bytes] = [b""] * 17
        terminal_value = b""
        for nibble in range(16):
            child_items = [(path[1:], value) for path, value in items if path and path[0] == nibble]
            if child_items:
                child_encoded = self._build_node(child_items)
                branch[nibble] = self._store_reference(child_encoded)

        for path, value in items:
            if not path:
                terminal_value = value
                break

        branch[16] = terminal_value
        return encode(branch)
