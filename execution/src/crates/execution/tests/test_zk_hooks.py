from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, addr, make_legacy_tx

from evm import StateDB
from execution import BlockBuilder, ChainConfig, ExecutionPayload, Receipt
from execution.transaction import transaction_type
from execution.zk_hooks import DeterministicMockZKProofBackend, attach_zk_proof, derive_public_inputs, verify_zk_proof_stub


class ZKHookTests(unittest.TestCase):
    def test_proof_bundle_attachment_and_verification_are_deterministic(self) -> None:
        builder = BlockBuilder(ChainConfig())
        transaction = make_legacy_tx(PRIVATE_KEY_ONE, 0, addr("0x1234000000000000000000000000000000001234"))
        block = builder.build_block(
            parent_block=None,
            transactions=(transaction,),
            execution_result=ExecutionPayload(
                receipts=(Receipt(status=1, cumulative_gas_used=21_000, gas_used=21_000, transaction_type=transaction_type(transaction)),),
                gas_used=21_000,
                state=StateDB(),
            ),
            timestamp=1,
            gas_limit=30_000_000,
            beneficiary=addr("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        )

        backend = DeterministicMockZKProofBackend()
        proof_bundle = backend.create_proof_bundle(block, chain_id=1, pre_state_root=block.header.parent_hash)
        extended = attach_zk_proof(block, proof_bundle)

        self.assertEqual(derive_public_inputs(extended, chain_id=1), proof_bundle.public_inputs)
        self.assertTrue(verify_zk_proof_stub(extended, chain_id=1, backend=backend))
        self.assertEqual(extended.hash(), block.hash())


if __name__ == "__main__":
    unittest.main()
