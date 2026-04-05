from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from primitives import U256

from .proofs import ProofType, ZKProof


class ZKVerifier(Protocol):
    def verify(self, proof: ZKProof, public_inputs: Sequence[U256]) -> bool:
        ...


def _default_base_costs() -> dict[ProofType, int]:
    return {
        ProofType.GROTH16: 180_000,
        ProofType.PLONK: 220_000,
        ProofType.STARK: 350_000,
    }


def _default_per_byte_costs() -> dict[ProofType, int]:
    return {
        ProofType.GROTH16: 8,
        ProofType.PLONK: 10,
        ProofType.STARK: 12,
    }


@dataclass(frozen=True, slots=True)
class ZKGasModel:
    base_costs: dict[ProofType, int] = field(default_factory=_default_base_costs)
    per_byte_costs: dict[ProofType, int] = field(default_factory=_default_per_byte_costs)

    def verification_gas(self, proof: ZKProof) -> int:
        return self.base_costs[proof.proof_type] + self.per_byte_costs[proof.proof_type] * len(proof.data)


@dataclass(slots=True)
class ZKVerifierRegistry:
    _verifiers: dict[ProofType, ZKVerifier] = field(default_factory=dict)

    def register(self, proof_type: ProofType, verifier: ZKVerifier) -> None:
        self._verifiers[proof_type] = verifier

    def get(self, proof_type: ProofType) -> ZKVerifier:
        try:
            return self._verifiers[proof_type]
        except KeyError as exc:
            raise LookupError(f"no verifier registered for proof type {proof_type.name}") from exc

    def verify(self, proof: ZKProof, public_inputs: Sequence[U256]) -> bool:
        verifier = self.get(proof.proof_type)
        return bool(verifier.verify(proof, public_inputs))
