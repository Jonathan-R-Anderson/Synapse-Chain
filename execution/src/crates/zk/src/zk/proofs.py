from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ProofType(IntEnum):
    GROTH16 = 0
    PLONK = 1
    STARK = 2


@dataclass(frozen=True, slots=True)
class ZKProof:
    proof_type: ProofType
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.proof_type, ProofType):
            object.__setattr__(self, "proof_type", ProofType(int(self.proof_type)))
        object.__setattr__(self, "data", bytes(self.data))
