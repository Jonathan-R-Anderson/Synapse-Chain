from __future__ import annotations

import sys
from pathlib import Path


CRATES_ROOT = Path(__file__).resolve().parent.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    sys.path.insert(0, str(CRATES_ROOT / crate / "src"))

from evm import StateDB
from execution import (
    BlockBuilder,
    ChainConfig,
    EIP1559Transaction,
    ExecutionPayload,
    InMemoryDHTBlockStore,
    Receipt,
    attach_zk_proof,
    publish_extended_block,
    transaction_type,
)
from execution.zk_hooks import DeterministicMockZKProofBackend
from primitives import Address


def addr(hex_value: str) -> Address:
    return Address.from_hex(hex_value)


def main() -> None:
    chain_config = ChainConfig()
    builder = BlockBuilder(chain_config)

    parent_block = builder.build_block(
        parent_block=None,
        transactions=(),
        execution_result=ExecutionPayload(receipts=(), gas_used=0, state=StateDB()),
        timestamp=1_700_000_000,
        gas_limit=30_000_000,
        beneficiary=addr("0x1111111111111111111111111111111111111111"),
        extra_data=b"demo-parent",
    )

    tx_one = EIP1559Transaction(
        chain_id=chain_config.chain_id,
        nonce=0,
        max_priority_fee_per_gas=2,
        max_fee_per_gas=2_000_000_000,
        gas_limit=21_000,
        to=addr("0x2222222222222222222222222222222222222222"),
        value=5,
        data=b"",
    ).sign(1)
    tx_two = EIP1559Transaction(
        chain_id=chain_config.chain_id,
        nonce=1,
        max_priority_fee_per_gas=2,
        max_fee_per_gas=2_000_000_000,
        gas_limit=50_000,
        to=addr("0x3333333333333333333333333333333333333333"),
        value=0,
        data=b"\xde\xad\xbe\xef",
    ).sign(1)

    post_state = StateDB()
    post_state.set_balance(addr("0x2222222222222222222222222222222222222222"), 5)
    post_state.set_balance(addr("0x4444444444444444444444444444444444444444"), 42)

    effective_gas_price = (parent_block.header.base_fee or 0) + 2
    receipts = (
        Receipt(
            status=1,
            cumulative_gas_used=21_000,
            gas_used=21_000,
            transaction_type=transaction_type(tx_one),
            effective_gas_price=effective_gas_price,
        ),
        Receipt(
            status=1,
            cumulative_gas_used=46_000,
            gas_used=25_000,
            transaction_type=transaction_type(tx_two),
            effective_gas_price=effective_gas_price,
        ),
    )
    execution_payload = ExecutionPayload(receipts=receipts, gas_used=46_000, state=post_state)

    block = builder.build_block(
        parent_block=parent_block,
        transactions=(tx_one, tx_two),
        execution_result=execution_payload,
        timestamp=1_700_000_012,
        gas_limit=30_000_000,
        beneficiary=addr("0x4444444444444444444444444444444444444444"),
        extra_data=b"demo-child",
    )
    block.validate_structure(parent_block=parent_block, execution_payload=execution_payload, chain_config=chain_config)

    proof_backend = DeterministicMockZKProofBackend()
    proof_bundle = proof_backend.create_proof_bundle(
        block,
        chain_id=chain_config.chain_id,
        pre_state_root=parent_block.header.state_root,
    )
    extended = attach_zk_proof(block, proof_bundle)

    dht = InMemoryDHTBlockStore()
    distribution = publish_extended_block(extended, dht)

    print("block_hash:", block.hash().to_hex())
    print("state_root:", block.header.state_root.to_hex())
    print("transactions_root:", block.header.transactions_root.to_hex())
    print("receipts_root:", block.header.receipts_root.to_hex())
    print("block_cid:", distribution.content_id)
    print("proof_sidecar_cid:", distribution.proof_sidecar_cid)
    print("receipt_sidecar_cid:", distribution.receipt_sidecar_cid)


if __name__ == "__main__":
    main()
