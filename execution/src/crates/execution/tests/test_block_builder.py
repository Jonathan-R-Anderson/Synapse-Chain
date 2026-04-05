from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, addr, make_eip1559_tx

from evm import StateDB
from execution import (
    BlockBuilder,
    BlockHeader,
    ChainConfig,
    ExecutionPayload,
    Receipt,
    compute_logs_bloom,
    compute_receipts_root,
    compute_transactions_root,
)
from execution.base_fee import compute_gas_target, compute_next_base_fee
from execution.transaction import transaction_type


class BlockBuilderTests(unittest.TestCase):
    def test_builder_computes_roots_and_base_fee_deterministically(self) -> None:
        chain_config = ChainConfig()
        builder = BlockBuilder(chain_config)
        parent_header = BlockHeader(
            number=3,
            gas_limit=30_000_000,
            gas_used=20_000_000,
            timestamp=1_700_000_000,
            coinbase=addr("0x1111111111111111111111111111111111111111"),
            base_fee=chain_config.initial_base_fee_per_gas,
        )
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            addr("0x2222222222222222222222222222222222222222"),
            gas_limit=21_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=2_000_000_000,
        )
        post_state = StateDB()
        post_state.set_balance(addr("0x2222222222222222222222222222222222222222"), 1)
        receipt = Receipt(
            status=1,
            cumulative_gas_used=21_000,
            gas_used=21_000,
            transaction_type=transaction_type(transaction),
            effective_gas_price=chain_config.initial_base_fee_per_gas + 2,
        )
        payload = ExecutionPayload(receipts=(receipt,), gas_used=21_000, state=post_state)

        block = builder.build_block(
            parent_block=parent_header,
            transactions=(transaction,),
            execution_result=payload,
            timestamp=1_700_000_100,
            gas_limit=30_000_000,
            beneficiary=addr("0x3333333333333333333333333333333333333333"),
            extra_data=b"builder-test",
        )

        self.assertEqual(block.header.parent_hash, parent_header.hash())
        self.assertEqual(block.header.transactions_root, compute_transactions_root((transaction,)))
        self.assertEqual(block.header.receipts_root, compute_receipts_root((receipt,)))
        self.assertEqual(block.header.logs_bloom, compute_logs_bloom((receipt,)))
        self.assertEqual(
            block.header.base_fee,
            compute_next_base_fee(
                parent_header.base_fee or 0,
                parent_header.gas_used,
                compute_gas_target(parent_header.gas_limit, chain_config.elasticity_multiplier),
                base_fee_max_change_denominator=chain_config.base_fee_max_change_denominator,
            ),
        )
        self.assertEqual(block.serialize(), BlockBuilder(chain_config).build_block(
            parent_block=parent_header,
            transactions=(transaction,),
            execution_result=payload,
            timestamp=1_700_000_100,
            gas_limit=30_000_000,
            beneficiary=addr("0x3333333333333333333333333333333333333333"),
            extra_data=b"builder-test",
        ).serialize())

    def test_block_serialization_roundtrip_preserves_hash(self) -> None:
        builder = BlockBuilder(ChainConfig())
        receipt = Receipt(status=1, cumulative_gas_used=0, gas_used=0)
        block = builder.build_block(
            parent_block=None,
            transactions=(),
            execution_result=ExecutionPayload(receipts=(), gas_used=0, state=StateDB()),
            timestamp=1,
            gas_limit=30_000_000,
            beneficiary=addr("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        )
        decoded = block.deserialize(block.serialize())
        self.assertEqual(decoded.hash(), block.hash())
        self.assertEqual(decoded.header, block.header)
        self.assertEqual(decoded.receipts, block.receipts)


if __name__ == "__main__":
    unittest.main()
