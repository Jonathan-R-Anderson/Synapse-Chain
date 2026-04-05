from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from primitives import Hash

from .block import Block, ExtendedBlock
from .hashing import bytes_to_hex, hash_from_hex, keccak256
from .rlp_codec import rlp_encode


@dataclass(frozen=True, slots=True)
class BlockDistributionRecord:
    block_hash: Hash
    content_id: str
    provider_nodes: tuple[str, ...] = ()
    replication_factor: int = 1
    availability_status: str = "available"
    proof_sidecar_cid: str | None = None
    receipt_sidecar_cid: str | None = None
    witness_cid: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "block_hash": self.block_hash.to_hex(),
            "content_id": self.content_id,
            "provider_nodes": list(self.provider_nodes),
            "replication_factor": self.replication_factor,
            "availability_status": self.availability_status,
            "proof_sidecar_cid": self.proof_sidecar_cid,
            "receipt_sidecar_cid": self.receipt_sidecar_cid,
            "witness_cid": self.witness_cid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BlockDistributionRecord":
        return cls(
            block_hash=hash_from_hex(str(data["block_hash"]), label="block_hash"),
            content_id=str(data["content_id"]),
            provider_nodes=tuple(str(item) for item in data.get("provider_nodes", [])),
            replication_factor=int(data.get("replication_factor", 1)),
            availability_status=str(data.get("availability_status", "available")),
            proof_sidecar_cid=data.get("proof_sidecar_cid"),
            receipt_sidecar_cid=data.get("receipt_sidecar_cid"),
            witness_cid=data.get("witness_cid"),
        )


class DHTBlockStore(Protocol):
    def put_block(self, block: Block) -> str:
        ...

    def get_block(self, block_hash: bytes) -> Block | None:
        ...

    def put_sidecar(self, name: str, payload: bytes) -> str:
        ...

    def get_sidecar(self, cid: str) -> bytes | None:
        ...


class InMemoryDHTBlockStore:
    def __init__(self) -> None:
        self._blocks_by_hash: dict[bytes, bytes] = {}
        self._sidecars: dict[str, bytes] = {}

    def _cid(self, payload: bytes) -> str:
        return bytes_to_hex(keccak256(payload))

    def put_block(self, block: Block) -> str:
        payload = block.serialize()
        cid = self._cid(payload)
        self._blocks_by_hash[block.hash().to_bytes()] = payload
        self._sidecars[cid] = payload
        return cid

    def get_block(self, block_hash: bytes) -> Block | None:
        payload = self._blocks_by_hash.get(bytes(block_hash))
        return None if payload is None else Block.deserialize(payload)

    def put_sidecar(self, name: str, payload: bytes) -> str:
        cid = self._cid(name.encode("utf-8") + bytes(payload))
        self._sidecars[cid] = bytes(payload)
        return cid

    def get_sidecar(self, cid: str) -> bytes | None:
        payload = self._sidecars.get(cid)
        return None if payload is None else bytes(payload)


def attach_distribution_metadata(
    block: Block | ExtendedBlock,
    metadata: BlockDistributionRecord,
) -> ExtendedBlock:
    if isinstance(block, ExtendedBlock):
        return ExtendedBlock(
            block=block.block,
            zk_proof_bundle=block.zk_proof_bundle,
            dht_metadata=metadata,
            execution_witness=block.execution_witness,
        )
    return ExtendedBlock(block=block, dht_metadata=metadata)


def publish_extended_block(
    block: Block | ExtendedBlock,
    store: DHTBlockStore,
    *,
    provider_nodes: tuple[str, ...] = ("local",),
    replication_factor: int = 1,
) -> BlockDistributionRecord:
    extended = block if isinstance(block, ExtendedBlock) else ExtendedBlock(block=block)
    block_cid = store.put_block(extended.block)
    proof_sidecar_cid = None
    if extended.zk_proof_bundle is not None:
        proof_sidecar_cid = store.put_sidecar("zk-proof", extended.zk_proof_bundle.serialize())
    receipt_payload = rlp_encode([receipt.serialize() for receipt in extended.block.receipts])
    receipt_sidecar_cid = store.put_sidecar("receipts", receipt_payload)
    witness_cid = None
    if extended.execution_witness is not None:
        witness_cid = store.put_sidecar("execution-witness", extended.execution_witness)
    return BlockDistributionRecord(
        block_hash=extended.hash(),
        content_id=block_cid,
        provider_nodes=provider_nodes,
        replication_factor=replication_factor,
        availability_status="available",
        proof_sidecar_cid=proof_sidecar_cid,
        receipt_sidecar_cid=receipt_sidecar_cid,
        witness_cid=witness_cid,
    )
