from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(CRATES / "primitives" / "src"))

from primitives import U256
from zk import ProofType, ZKGasModel, ZKProof, ZKVerifierRegistry


class _AcceptVerifier:
    def verify(self, proof: ZKProof, public_inputs: list[U256]) -> bool:
        return proof.data == b"ok" and public_inputs == [U256(1), U256(2)]


class ZkTests(unittest.TestCase):
    def test_registry_verifies_registered_proof(self) -> None:
        registry = ZKVerifierRegistry()
        registry.register(ProofType.GROTH16, _AcceptVerifier())
        self.assertTrue(registry.verify(ZKProof(ProofType.GROTH16, b"ok"), [U256(1), U256(2)]))

    def test_registry_rejects_missing_verifier(self) -> None:
        registry = ZKVerifierRegistry()
        with self.assertRaises(LookupError):
            registry.verify(ZKProof(ProofType.PLONK, b"proof"), [])

    def test_gas_model_scales_with_proof_size(self) -> None:
        gas_model = ZKGasModel()
        short = gas_model.verification_gas(ZKProof(ProofType.STARK, b"a" * 10))
        long = gas_model.verification_gas(ZKProof(ProofType.STARK, b"a" * 20))
        self.assertEqual(long - short, 120)


if __name__ == "__main__":
    unittest.main()
