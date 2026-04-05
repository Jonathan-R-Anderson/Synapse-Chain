from __future__ import annotations

from evm import Interpreter, StateDB
from zk import ZKVerifierRegistry

from .block import Block, BlockEnvironment, BlockHeader, ChainConfig
from .block_validation import validate_block_for_execution
from .exceptions import BlockGasExceededError
from .journal import StateJournal
from .result import BlockExecutionResult
from .state_transition import apply_transaction
from .trie import compute_logs_bloom, compute_receipts_root, compute_state_root, compute_transactions_root


def apply_block(
    old_state: StateDB,
    block: Block,
    chain_config: ChainConfig,
    parent_header: BlockHeader | None = None,
    verifier_registry: ZKVerifierRegistry | None = None,
) -> BlockExecutionResult:
    validate_block_for_execution(block, parent_header, chain_config)

    working_state = old_state.clone()
    journal = StateJournal(working_state)
    checkpoint = journal.checkpoint()
    interpreter = Interpreter(state=working_state)
    block_env = BlockEnvironment.from_block(block, chain_config)

    try:
        cumulative_gas_used = 0
        receipts = []
        tx_results = []
        all_logs = []
        coinbase_fees = 0
        base_fee_burned = 0

        for transaction in block.transactions:
            remaining_block_gas = block.header.gas_limit - cumulative_gas_used
            if int(transaction.gas_limit) > remaining_block_gas:
                raise BlockGasExceededError("transaction gas limit exceeds the remaining block gas budget")
            tx_result = apply_transaction(
                state=working_state,
                transaction=transaction,
                block_env=block_env,
                chain_config=chain_config,
                verifier_registry=verifier_registry,
                interpreter=interpreter,
                cumulative_gas_used_before=cumulative_gas_used,
            )
            cumulative_gas_used += tx_result.gas_used
            if cumulative_gas_used > block.header.gas_limit:
                raise BlockGasExceededError("cumulative gas used exceeds the block gas limit")
            receipts.append(tx_result.receipt)
            tx_results.append(tx_result)
            all_logs.extend(tx_result.logs)
            coinbase_fees += tx_result.coinbase_fee
            base_fee_burned += tx_result.base_fee_burned

        journal.commit(checkpoint)
        return BlockExecutionResult(
            block=block,
            state=working_state,
            gas_used=cumulative_gas_used,
            receipts=tuple(receipts),
            transaction_results=tuple(tx_results),
            logs=tuple(all_logs),
            coinbase_fees=coinbase_fees,
            base_fee_burned=base_fee_burned,
            state_root=compute_state_root(working_state),
            transactions_root=compute_transactions_root(block.transactions),
            receipts_root=compute_receipts_root(tuple(receipts)),
            logs_bloom=compute_logs_bloom(tuple(receipts)),
        )
    except Exception:
        journal.revert(checkpoint)
        raise
