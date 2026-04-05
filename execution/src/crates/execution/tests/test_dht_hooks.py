from __future__ import annotations

import unittest

from execution_test_helpers import addr

from evm import StateDB
from execution import BlockBuilder, ChainConfig, ExecutionPayload
from execution.dht_hooks import InMemoryDHTBlockStore, attach_distribution_metadata, publish_extended_block
from execution.zk_hooks import DeterministicMockZKProofBackend, attach_zk_proof


class DHTHookTests(unittest.TestCase):
    def test_block_and_sidecars_round_trip_through_in_memory_dht(self) -> None:
        builder = BlockBuilder(ChainConfig())
        block = builder.build_block(
            parent_block=None,
            transactions=(),
            execution_result=ExecutionPayload(receipts=(), gas_used=0, state=StateDB()),
            timestamp=1,
            gas_limit=30_000_000,
            beneficiary=addr("0xcccccccccccccccccccccccccccccccccccccccc"),
        )
        backend = DeterministicMockZKProofBackend()
        extended = attach_zk_proof(block, backend.create_proof_bundle(block, chain_id=1, pre_state_root=block.header.parent_hash))
        store = InMemoryDHTBlockStore()

        record = publish_extended_block(extended, store)
        round_trip = store.get_block(block.hash().to_bytes())
        self.assertIsNotNone(round_trip)
        assert round_trip is not None
        self.assertEqual(round_trip.serialize(), block.serialize())
        self.assertIsNotNone(store.get_sidecar(record.proof_sidecar_cid))
        self.assertIsNotNone(store.get_sidecar(record.receipt_sidecar_cid))

        decorated = attach_distribution_metadata(extended, record)
        self.assertEqual(decorated.hash(), block.hash())
        self.assertEqual(decorated.dht_metadata.content_id, record.content_id)


if __name__ == "__main__":
    unittest.main()
