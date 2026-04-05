from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, Sequence

from primitives import Hash, U256
from zk import ProofType

from .block import Block, ExtendedBlock
from .hashing import bytes_to_hex, hash_from_hex, hex_to_bytes, keccak256
from .rlp_codec import rlp_encode


@dataclass(frozen=True, slots=True)
class BlockProofBundle:
    proof_type: ProofType
    proof_bytes: bytes
    public_inputs: tuple[U256, ...]
    verification_key_id: str | None
    pre_state_root: Hash
    post_state_root: Hash
    transactions_commitment: Hash
    receipts_commitment: Hash

    def __post_init__(self) -> None:
        if not isinstance(self.proof_type, ProofType):
            object.__setattr__(self, "proof_type", ProofType(int(self.proof_type)))
        object.__setattr__(self, "proof_bytes", bytes(self.proof_bytes))
        object.__setattr__(self, "public_inputs", tuple(value if isinstance(value, U256) else U256(value) for value in self.public_inputs))

    def serialize(self) -> bytes:
        return rlp_encode(
            [
                int(self.proof_type),
                b"" if self.verification_key_id is None else self.verification_key_id.encode("utf-8"),
                self.proof_bytes,
                [int(value) for value in self.public_inputs],
                self.pre_state_root.to_bytes(),
                self.post_state_root.to_bytes(),
                self.transactions_commitment.to_bytes(),
                self.receipts_commitment.to_bytes(),
            ]
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proof_type": int(self.proof_type),
            "proof_bytes": bytes_to_hex(self.proof_bytes),
            "public_inputs": [value.to_hex() for value in self.public_inputs],
            "verification_key_id": self.verification_key_id,
            "pre_state_root": self.pre_state_root.to_hex(),
            "post_state_root": self.post_state_root.to_hex(),
            "transactions_commitment": self.transactions_commitment.to_hex(),
            "receipts_commitment": self.receipts_commitment.to_hex(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BlockProofBundle":
        return cls(
            proof_type=ProofType(int(data["proof_type"])),
            proof_bytes=hex_to_bytes(str(data["proof_bytes"]), label="proof_bytes"),
            public_inputs=tuple(U256.from_hex(str(value)) for value in data.get("public_inputs", [])),
            verification_key_id=data.get("verification_key_id"),
            pre_state_root=hash_from_hex(str(data["pre_state_root"]), label="pre_state_root"),
            post_state_root=hash_from_hex(str(data["post_state_root"]), label="post_state_root"),
            transactions_commitment=hash_from_hex(str(data["transactions_commitment"]), label="transactions_commitment"),
            receipts_commitment=hash_from_hex(str(data["receipts_commitment"]), label="receipts_commitment"),
        )


class ZKProofBackend(Protocol):
    def verify(self, proof_bundle: BlockProofBundle, public_inputs: Sequence[U256]) -> bool:
        ...


def derive_public_inputs(
    block: Block | ExtendedBlock,
    *,
    chain_id: int,
    pre_state_root: Hash | None = None,
) -> tuple[U256, ...]:
    canonical_block = block.block if isinstance(block, ExtendedBlock) else block
    state_root = canonical_block.header.state_root
    pre_root = pre_state_root
    if pre_root is None and isinstance(block, ExtendedBlock) and block.zk_proof_bundle is not None:
        pre_root = block.zk_proof_bundle.pre_state_root
    if pre_root is None:
        pre_root = Hash.zero()
    return (
        U256.from_bytes(pre_root.to_bytes()),
        U256.from_bytes(state_root.to_bytes()),
        U256.from_bytes(canonical_block.header.transactions_root.to_bytes()),
        U256.from_bytes(canonical_block.header.receipts_root.to_bytes()),
        U256(canonical_block.header.gas_used),
        U256(canonical_block.header.number),
        U256(chain_id),
    )


class DeterministicMockZKProofBackend:
    def _expected_proof_bytes(self, proof_bundle: BlockProofBundle, public_inputs: Sequence[U256]) -> bytes:
        payload = rlp_encode(
            [
                int(proof_bundle.proof_type),
                b"" if proof_bundle.verification_key_id is None else proof_bundle.verification_key_id.encode("utf-8"),
                [int(value) for value in public_inputs],
                proof_bundle.pre_state_root.to_bytes(),
                proof_bundle.post_state_root.to_bytes(),
                proof_bundle.transactions_commitment.to_bytes(),
                proof_bundle.receipts_commitment.to_bytes(),
            ]
        )
        return keccak256(payload)

    def create_proof_bundle(
        self,
        block: Block,
        *,
        chain_id: int,
        pre_state_root: Hash,
        proof_type: ProofType = ProofType.GROTH16,
        verification_key_id: str | None = "mock-vk",
    ) -> BlockProofBundle:
        public_inputs = derive_public_inputs(block, chain_id=chain_id, pre_state_root=pre_state_root)
        bundle = BlockProofBundle(
            proof_type=proof_type,
            proof_bytes=b"",
            public_inputs=public_inputs,
            verification_key_id=verification_key_id,
            pre_state_root=pre_state_root,
            post_state_root=block.header.state_root,
            transactions_commitment=block.header.transactions_root,
            receipts_commitment=block.header.receipts_root,
        )
        return replace(bundle, proof_bytes=self._expected_proof_bytes(bundle, public_inputs))

    def verify(self, proof_bundle: BlockProofBundle, public_inputs: Sequence[U256]) -> bool:
        return tuple(public_inputs) == proof_bundle.public_inputs and proof_bundle.proof_bytes == self._expected_proof_bytes(
            proof_bundle,
            public_inputs,
        )


def attach_zk_proof(block: Block | ExtendedBlock, proof_bundle: BlockProofBundle) -> ExtendedBlock:
    if isinstance(block, ExtendedBlock):
        return ExtendedBlock(
            block=block.block,
            zk_proof_bundle=proof_bundle,
            dht_metadata=block.dht_metadata,
            execution_witness=block.execution_witness,
        )
    return ExtendedBlock(block=block, zk_proof_bundle=proof_bundle)


def verify_zk_proof_stub(
    block: Block | ExtendedBlock,
    *,
    chain_id: int,
    backend: ZKProofBackend | None = None,
    pre_state_root: Hash | None = None,
) -> bool:
    if not isinstance(block, ExtendedBlock) or block.zk_proof_bundle is None:
        return False
    verifier = backend or DeterministicMockZKProofBackend()
    public_inputs = derive_public_inputs(block, chain_id=chain_id, pre_state_root=pre_state_root)
    return verifier.verify(block.zk_proof_bundle, public_inputs)
