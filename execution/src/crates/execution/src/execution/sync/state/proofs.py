from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Iterable, Mapping

from ..models import AccountState, MerkleProof


def _hash_bytes(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def _normalize_hex(data: bytes) -> str:
    return "0x" + data.hex()


def _leaf_hash(key: bytes, value: bytes) -> bytes:
    return _hash_bytes(b"\x00" + len(key).to_bytes(2, byteorder="big") + key + value)


def _branch_hash(left: bytes, right: bytes) -> bytes:
    return _hash_bytes(b"\x01" + left + right)


def serialize_account_value(account: AccountState) -> str:
    payload = {
        "nonce": account.nonce,
        "balance": account.balance,
        "code": "0x" + account.code.hex(),
        "storage": {slot.to_hex(): value.to_hex() for slot, value in account.storage},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_merkle_root(leaves: Mapping[str, str]) -> str:
    if not leaves:
        return _normalize_hex(_hash_bytes(b""))
    ordered = sorted((key.encode("utf-8"), value.encode("utf-8")) for key, value in leaves.items())
    level = [_leaf_hash(key, value) for key, value in ordered]
    while len(level) > 1:
        next_level: list[bytes] = []
        for index in range(0, len(level), 2):
            left = level[index]
            right = level[index + 1] if index + 1 < len(level) else level[index]
            next_level.append(_branch_hash(left, right))
        level = next_level
    return _normalize_hex(level[0])


def build_merkle_proofs(leaves: Mapping[str, str]) -> dict[str, MerkleProof]:
    if not leaves:
        return {}
    ordered = sorted((key, value) for key, value in leaves.items())
    leaf_hashes = [_leaf_hash(key.encode("utf-8"), value.encode("utf-8")) for key, value in ordered]
    levels: list[list[bytes]] = [leaf_hashes]
    while len(levels[-1]) > 1:
        current = levels[-1]
        next_level: list[bytes] = []
        for index in range(0, len(current), 2):
            left = current[index]
            right = current[index + 1] if index + 1 < len(current) else current[index]
            next_level.append(_branch_hash(left, right))
        levels.append(next_level)
    root = _normalize_hex(levels[-1][0])
    proofs: dict[str, MerkleProof] = {}
    for index, (key, value) in enumerate(ordered):
        siblings: list[str] = []
        path: list[int] = []
        node_index = index
        for level in levels[:-1]:
            sibling_index = node_index ^ 1
            sibling = level[sibling_index] if sibling_index < len(level) else level[node_index]
            siblings.append(_normalize_hex(sibling))
            path.append(node_index % 2)
            node_index //= 2
        proofs[key] = MerkleProof(
            proof_type="merkle",
            root=root,
            key=key,
            value=value,
            exists=True,
            siblings=tuple(siblings),
            path=tuple(path),
        )
    return proofs


class ProofVerifier(ABC):
    """Interface for state-proof, snapshot-proof, and future zk-proof verification."""

    @abstractmethod
    def verify_account_proof(self, proof: MerkleProof, expected_root: str) -> bool:
        ...

    @abstractmethod
    def verify_storage_proof(self, proof: MerkleProof, expected_root: str) -> bool:
        ...

    @abstractmethod
    def verify_snapshot_with_proof(self, manifest_hash: str, proof_payload: object) -> bool:
        ...

    @abstractmethod
    def verify_state_transition_proof(self, before_root: str, after_root: str, proof_payload: object) -> bool:
        ...


class MerkleProofVerifier(ProofVerifier):
    """Concrete Merkle inclusion verifier used by the light-sync path."""

    def verify_account_proof(self, proof: MerkleProof, expected_root: str) -> bool:
        return self._verify_inclusion(proof, expected_root)

    def verify_storage_proof(self, proof: MerkleProof, expected_root: str) -> bool:
        return self._verify_inclusion(proof, expected_root)

    def verify_snapshot_with_proof(self, manifest_hash: str, proof_payload: object) -> bool:
        return bool(proof_payload) and isinstance(manifest_hash, str)

    def verify_state_transition_proof(self, before_root: str, after_root: str, proof_payload: object) -> bool:
        return bool(proof_payload) and isinstance(before_root, str) and isinstance(after_root, str)

    def _verify_inclusion(self, proof: MerkleProof, expected_root: str) -> bool:
        if not proof.exists or proof.value is None:
            return False
        current = _leaf_hash(proof.key.encode("utf-8"), proof.value.encode("utf-8"))
        for bit, sibling_hex in zip(proof.path, proof.siblings):
            sibling = bytes.fromhex(sibling_hex[2:] if sibling_hex.startswith("0x") else sibling_hex)
            current = _branch_hash(current, sibling) if bit == 0 else _branch_hash(sibling, current)
        return _normalize_hex(current) == expected_root == proof.root


def account_leaf_map(accounts: Iterable[AccountState]) -> dict[str, str]:
    return {account.address.to_hex(): serialize_account_value(account) for account in accounts}


__all__ = [
    "MerkleProofVerifier",
    "ProofVerifier",
    "account_leaf_map",
    "build_merkle_proofs",
    "build_merkle_root",
    "serialize_account_value",
]
