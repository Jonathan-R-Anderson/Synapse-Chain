from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto import address_from_private_key
from execution import Block, BlockBuilder, BlockHeader, ChainConfig, FeeModel, apply_block
from execution_tests import AccountFixture
from replay import BlockReplayExecutor, load_block_bundle
from primitives import Address
from transactions import LegacyTransaction


class BlockReplayTests(unittest.TestCase):
    def test_replay_executor_matches_synthetic_block_bundle(self) -> None:
        sender = address_from_private_key(1)
        recipient = address_from_private_key(2)
        pre_state = (
            AccountFixture(address=sender, nonce=0, balance=100_000),
            AccountFixture(address=recipient, nonce=0, balance=0),
        )
        transaction = LegacyTransaction(
            nonce=0,
            gas_price=0,
            gas_limit=21_000,
            to=recipient,
            value=123,
            data=b"",
            chain_id=1,
        ).sign(1)
        chain_config = ChainConfig(
            chain_id=1,
            fee_model=FeeModel.LEGACY,
            support_legacy_transactions=True,
            support_eip1559_transactions=False,
            support_zk_transactions=False,
        )
        parent_header = BlockHeader(
            number=0,
            gas_limit=30_000_000,
            gas_used=0,
            timestamp=0,
            coinbase=Address.zero(),
        )
        skeleton_block = Block(
            header=BlockHeader(
                parent_hash=parent_header.hash(),
                number=1,
                gas_limit=30_000_000,
                gas_used=0,
                timestamp=1,
                coinbase=Address.zero(),
            ),
            transactions=(transaction,),
        )
        from execution_tests.roots import build_state_db

        block_result = apply_block(build_state_db(pre_state), skeleton_block, chain_config, parent_header=parent_header)
        canonical_block = BlockBuilder(chain_config).build_block(
            parent_block=parent_header,
            transactions=(transaction,),
            execution_result=block_result,
            timestamp=1,
            gas_limit=30_000_000,
            beneficiary=Address.zero(),
        )
        bundle_payload = {
            "name": "synthetic_replay",
            "fork": "berlin",
            "chain_id": 1,
            "parent_header": parent_header.to_dict(),
            "pre_state": {account.address.to_hex(): account.to_dict() for account in pre_state},
            "block": canonical_block.to_dict(),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bundle.json"
            path.write_text(json.dumps(bundle_payload), encoding="utf-8")
            bundle = load_block_bundle(path)
            outcome = BlockReplayExecutor().replay(bundle)
        self.assertTrue(outcome.passed, outcome.report.render_text())

    def test_hash_only_transaction_lists_are_rejected_for_execution_replay(self) -> None:
        block_payload = {
            "name": "hash_only_block",
            "fork": "london",
            "chain_id": 1,
            "pre_state": {},
            "block": {
                "parentHash": "0x" + ("11" * 32),
                "sha3Uncles": "0x" + ("22" * 32),
                "miner": "0x0000000000000000000000000000000000000000",
                "stateRoot": "0x" + ("33" * 32),
                "transactionsRoot": "0x" + ("44" * 32),
                "receiptsRoot": "0x" + ("55" * 32),
                "logsBloom": "0x" + ("00" * 256),
                "difficulty": "0x0",
                "number": "0x1",
                "gasLimit": "0x1c9c380",
                "gasUsed": "0x0",
                "timestamp": "0x1",
                "extraData": "0x",
                "mixHash": "0x" + ("66" * 32),
                "nonce": "0x0000000000000000",
                "baseFeePerGas": "0x1",
                "transactions": ["0x" + ("77" * 32)]
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "hash_only.json"
            path.write_text(json.dumps(block_payload), encoding="utf-8")
            bundle = load_block_bundle(path)
            outcome = BlockReplayExecutor().replay(bundle)
        self.assertFalse(outcome.passed)
        self.assertIn("transaction bodies", outcome.report.render_text())


if __name__ == "__main__":
    unittest.main()
