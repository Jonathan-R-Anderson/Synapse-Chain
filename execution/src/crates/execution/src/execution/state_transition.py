from __future__ import annotations

from dataclasses import replace

from evm import ExecutionContext, ExecutionResult, Interpreter, StateDB
from evm.utils import compute_create_address
from zk import ZKVerifierRegistry

from .block import BlockEnvironment, ChainConfig
from .exceptions import InvalidZKProofError
from .journal import StateJournal
from .receipt import Receipt
from .result import TransactionExecutionResult
from .transaction import Transaction, ZKTransaction, is_contract_creation, transaction_type
from .tx_validation import ValidatedTransaction, validate_transaction


def _interpreter_for_state(state: StateDB, interpreter: Interpreter | None) -> Interpreter:
    if interpreter is None:
        return Interpreter(state=state)
    if interpreter.state is state:
        return interpreter
    return Interpreter(
        state=state,
        precompiles=interpreter.precompiles,
        trace_sink=interpreter.trace_sink,
    )


def _execute_create_transaction(
    interpreter: Interpreter,
    validated: ValidatedTransaction,
    block_env: BlockEnvironment,
    evm_gas: int,
) -> ExecutionResult:
    state = interpreter.state
    sender = validated.sender
    transaction = validated.transaction
    assert transaction.to is None

    new_address = compute_create_address(sender, int(transaction.nonce))
    if not state.can_deploy_to(new_address):
        return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

    snapshot = state.snapshot()
    state.get_or_create_account(new_address).nonce = 1
    if int(transaction.value) != 0 and not state.transfer(sender, new_address, int(transaction.value)):
        state.restore(snapshot)
        return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

    context = ExecutionContext(
        address=new_address,
        code_address=new_address,
        caller=sender,
        origin=sender,
        value=int(transaction.value),
        calldata=b"",
        code=transaction.data,
        gas=evm_gas,
        static=False,
        depth=0,
        gas_price=validated.pricing.effective_gas_price,
        chain_id=block_env.chain_id,
    )
    result = interpreter.execute(context)
    if not result.success:
        state.restore(snapshot)
        return result
    state.set_code(new_address, result.output)
    return replace(result, created_address=new_address)


def _execute_message_transaction(
    interpreter: Interpreter,
    validated: ValidatedTransaction,
    block_env: BlockEnvironment,
    evm_gas: int,
) -> ExecutionResult:
    transaction = validated.transaction
    assert transaction.to is not None
    return interpreter.call(
        transaction.to,
        caller=validated.sender,
        origin=validated.sender,
        value=int(transaction.value),
        calldata=transaction.data,
        gas=evm_gas,
        static=False,
        gas_price=validated.pricing.effective_gas_price,
        chain_id=block_env.chain_id,
    )


def apply_transaction(
    state: StateDB,
    transaction: Transaction,
    block_env: BlockEnvironment,
    chain_config: ChainConfig,
    verifier_registry: ZKVerifierRegistry | None = None,
    interpreter: Interpreter | None = None,
    cumulative_gas_used_before: int = 0,
) -> TransactionExecutionResult:
    validated = validate_transaction(
        transaction=transaction,
        state=state,
        block_env=block_env,
        chain_config=chain_config,
        verifier_registry=verifier_registry,
    )
    if validated.zk_verification_deferred:
        assert isinstance(transaction, ZKTransaction)
        if verifier_registry is None:
            raise InvalidZKProofError("deferred ZK verification requires a verifier registry")
        if not verifier_registry.verify(transaction.proof, transaction.public_inputs):
            raise InvalidZKProofError("deferred ZK proof verification failed")
        validated = replace(validated, zk_verified=True, zk_verification_deferred=False)

    runtime = _interpreter_for_state(state, interpreter)
    journal = StateJournal(state)
    checkpoint = journal.checkpoint()

    try:
        gas_limit = int(transaction.gas_limit)
        upfront_gas_cost = validated.pricing.prepaid_gas_cost(gas_limit)
        if not state.subtract_balance(validated.sender, upfront_gas_cost):
            raise AssertionError("validated balance was insufficient for upfront gas deduction")
        state.increment_nonce(validated.sender)

        evm_gas = gas_limit - validated.total_pre_execution_gas
        if is_contract_creation(transaction):
            execution_result = _execute_create_transaction(runtime, validated, block_env, evm_gas)
        else:
            execution_result = _execute_message_transaction(runtime, validated, block_env, evm_gas)

        gas_consumed_before_refund = gas_limit - execution_result.gas_remaining
        refund_cap = (
            gas_consumed_before_refund // chain_config.gas_refund_quotient if execution_result.success else 0
        )
        gas_refunded = min(execution_result.gas_refund, refund_cap)
        refunded_gas_units = execution_result.gas_remaining + gas_refunded
        gas_used = gas_limit - refunded_gas_units
        refund_amount = validated.pricing.refund_amount(refunded_gas_units)
        if refund_amount:
            state.add_balance(validated.sender, refund_amount)

        coinbase_fee = validated.pricing.tip_reward(gas_used)
        base_fee_burned = validated.pricing.base_fee_burn(gas_used)
        if not chain_config.burn_base_fee:
            coinbase_fee += base_fee_burned
            base_fee_burned = 0
        if coinbase_fee:
            state.add_balance(block_env.coinbase, coinbase_fee)

        receipt = Receipt(
            status=1 if execution_result.success else 0,
            cumulative_gas_used=cumulative_gas_used_before + gas_used,
            gas_used=gas_used,
            logs=execution_result.logs,
            transaction_type=transaction_type(transaction),
            contract_address=execution_result.created_address,
            transaction_hash=transaction.tx_hash(),
            effective_gas_price=validated.pricing.effective_gas_price,
        )
        state.commit_transaction()
        journal.commit(checkpoint)
        return TransactionExecutionResult(
            transaction=transaction,
            sender=validated.sender,
            success=execution_result.success,
            gas_limit=gas_limit,
            gas_used=gas_used,
            gas_refunded=gas_refunded,
            gas_remaining=execution_result.gas_remaining,
            effective_gas_price=validated.pricing.effective_gas_price,
            total_fee_paid=gas_used * validated.pricing.effective_gas_price,
            coinbase_fee=coinbase_fee,
            base_fee_burned=base_fee_burned,
            logs=execution_result.logs,
            output=execution_result.output if execution_result.success else b"",
            revert_data=execution_result.output if not execution_result.success else b"",
            contract_address=execution_result.created_address,
            receipt=receipt,
            error=execution_result.error,
            zk_verified=validated.zk_verified,
        )
    except Exception:
        journal.revert(checkpoint)
        raise
