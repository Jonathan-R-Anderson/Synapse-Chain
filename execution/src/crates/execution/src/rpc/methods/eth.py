from __future__ import annotations

from execution import validate_transaction
from primitives import Address
from transactions import decode_transaction

from rpc.errors import AlreadyKnownError, ExecutionReverted, server_error
from rpc.types import (
    parse_address,
    parse_block_selector,
    parse_bool,
    parse_call_request,
    parse_data,
    parse_hash,
    parse_quantity,
    serialize_block,
    serialize_receipt,
    serialize_transaction,
    to_data,
    to_quantity,
    to_word,
)

from . import RpcContext, RpcHandler, require_params


def register(methods: dict[str, RpcHandler]) -> None:
    methods["eth_accounts"] = eth_accounts
    methods["eth_blockNumber"] = eth_block_number
    methods["eth_call"] = eth_call
    methods["eth_chainId"] = eth_chain_id
    methods["eth_estimateGas"] = eth_estimate_gas
    methods["eth_feeHistory"] = eth_fee_history
    methods["eth_gasPrice"] = eth_gas_price
    methods["eth_getBalance"] = eth_get_balance
    methods["eth_getBlockByNumber"] = eth_get_block_by_number
    methods["eth_getCode"] = eth_get_code
    methods["eth_getStorageAt"] = eth_get_storage_at
    methods["eth_getTransactionByHash"] = eth_get_transaction_by_hash
    methods["eth_getTransactionCount"] = eth_get_transaction_count
    methods["eth_getTransactionReceipt"] = eth_get_transaction_receipt
    methods["eth_maxPriorityFeePerGas"] = eth_max_priority_fee_per_gas
    methods["eth_sendRawTransaction"] = eth_send_raw_transaction


def eth_accounts(context: RpcContext, params: list[object]) -> list[str]:
    require_params(params, min_count=0, max_count=0)
    return [account.to_hex() for account in context.compat.local_accounts]


def eth_chain_id(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return to_quantity(context.node.chain_config.chain_id)


def eth_block_number(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return to_quantity(context.node.head.block.header.number)


def eth_gas_price(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return to_quantity(context.compat.suggested_gas_price(context.node.head.block.header.base_fee))


def eth_max_priority_fee_per_gas(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return to_quantity(context.compat.default_max_priority_fee_per_gas)


def eth_get_balance(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=2)
    resolved = context.state.resolve(params[1] if len(params) > 1 else None)
    if resolved is None:
        raise ValueError("block not found")
    address = parse_address(params[0])
    return to_quantity(resolved.state.get_balance(address))


def eth_get_code(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=2)
    resolved = context.state.resolve(params[1] if len(params) > 1 else None)
    if resolved is None:
        raise ValueError("block not found")
    address = parse_address(params[0])
    return to_data(resolved.state.get_code(address))


def eth_get_storage_at(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=2, max_count=3)
    resolved = context.state.resolve(params[2] if len(params) > 2 else None)
    if resolved is None:
        raise ValueError("block not found")
    address = parse_address(params[0])
    key = parse_quantity(params[1], label="position")
    return to_word(resolved.state.get_storage(address, key))


def eth_get_transaction_count(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=2)
    resolved = context.state.resolve(params[1] if len(params) > 1 else None)
    if resolved is None:
        raise ValueError("block not found")
    address = parse_address(params[0])
    return to_quantity(resolved.state.get_nonce(address))


def _serialize_block_response(context: RpcContext, blockish: object, *, full_transactions: bool) -> dict[str, object] | None:
    if blockish is None:
        return None
    if hasattr(blockish, "transaction_records"):
        transactions = (
            [
                serialize_transaction(
                    tx_record.transaction,
                    sender=tx_record.sender,
                    block_hash=tx_record.block_hash,
                    block_number=tx_record.block_number,
                    transaction_index=tx_record.transaction_index,
                    effective_gas_price=tx_record.receipt.effective_gas_price,
                )
                for tx_record in blockish.transaction_records
            ]
            if full_transactions
            else [tx_record.transaction.tx_hash().to_hex() for tx_record in blockish.transaction_records]
        )
        pending = False
        total_difficulty = blockish.total_difficulty
        size = blockish.size
        block = blockish.block
    else:
        transactions = (
            [
                serialize_transaction(
                    pending.transaction,
                    sender=pending.sender,
                    block_hash=None,
                    block_number=None,
                    transaction_index=None,
                )
                for pending in blockish.pending_transactions
            ]
            if full_transactions
            else [pending.transaction.tx_hash().to_hex() for pending in blockish.pending_transactions]
        )
        pending = True
        total_difficulty = blockish.total_difficulty
        size = blockish.size
        block = blockish.block
    return serialize_block(
        block,
        total_difficulty=total_difficulty,
        size=size,
        transactions=transactions,
        pending=pending,
    )


def eth_get_block_by_number(context: RpcContext, params: list[object]) -> dict[str, object] | None:
    require_params(params, min_count=2, max_count=2)
    selector = parse_block_selector(params[0])
    full_transactions = parse_bool(params[1], label="full_transactions")
    return _serialize_block_response(context, context.node.block_by_selector(selector), full_transactions=full_transactions)


def eth_call(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=3)
    call = parse_call_request(params[0])
    selector = None if len(params) < 2 else params[1]
    result = context.state.simulate_call(call, selector=selector)
    if not result.success:
        if result.reverted:
            raise ExecutionReverted("0x" + result.output.hex())
        if result.error is not None:
            raise server_error(str(result.error))
        raise server_error("execution failed")
    return to_data(result.output)


def eth_estimate_gas(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=3)
    selector = None if len(params) < 2 else params[1]
    estimate = context.gas.estimate(parse_call_request(params[0]), selector=selector)
    return to_quantity(estimate)


def eth_send_raw_transaction(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=1)
    raw = parse_data(params[0], label="raw transaction")
    transaction = decode_transaction(raw)
    tx_hash = transaction.tx_hash().to_hex()
    if context.node.transaction_by_hash(tx_hash) is not None:
        raise AlreadyKnownError(tx_hash)
    sender = transaction.sender(enforce_low_s=context.node.chain_config.enforce_low_s)
    confirmed_nonce = context.node.head.post_state.get_nonce(sender)
    pending_nonce = context.node.txpool.pending_nonce(sender, confirmed_nonce)
    nonce = int(transaction.nonce)
    existing = context.node.txpool.get_by_sender_nonce(sender, nonce)
    if existing is None and nonce != pending_nonce:
        if nonce < pending_nonce:
            raise server_error("nonce too low")
        raise server_error("nonce too high")
    validation_state = context.node.head.post_state.clone()
    if existing is None:
        preview = context.node.build_pending_preview()
        validation_state = preview.state.clone()
    block_env = context.node.build_pending_preview().block_env
    if existing is None:
        validate_transaction(
            transaction=transaction,
            state=validation_state,
            block_env=block_env,
            chain_config=context.node.chain_config,
            verifier_registry=context.node.verifier_registry,
        )
    context.node.txpool.add(transaction, sender=sender, base_fee=block_env.base_fee)
    if context.compat.mining_mode == "instant":
        context.node.append_pending_block(max_transactions=1)
    return tx_hash


def eth_get_transaction_by_hash(context: RpcContext, params: list[object]) -> dict[str, object] | None:
    require_params(params, min_count=1, max_count=1)
    tx_hash = parse_hash(params[0]).to_hex()
    record = context.node.transaction_by_hash(tx_hash)
    if record is not None:
        return serialize_transaction(
            record.transaction,
            sender=record.sender,
            block_hash=record.block_hash,
            block_number=record.block_number,
            transaction_index=record.transaction_index,
            effective_gas_price=record.receipt.effective_gas_price,
        )
    pending = context.node.pending_by_hash(tx_hash)
    if pending is None:
        return None
    return serialize_transaction(
        pending.transaction,
        sender=pending.sender,
        block_hash=None,
        block_number=None,
        transaction_index=None,
    )


def eth_get_transaction_receipt(context: RpcContext, params: list[object]) -> dict[str, object] | None:
    require_params(params, min_count=1, max_count=1)
    tx_hash = parse_hash(params[0]).to_hex()
    record = context.node.transaction_by_hash(tx_hash)
    if record is None:
        return None
    return serialize_receipt(
        transaction=record.transaction,
        sender=record.sender,
        receipt=record.receipt,
        block_hash=record.block_hash,
        block_number=record.block_number,
        transaction_index=record.transaction_index,
        log_index_start=record.log_index_start,
    )


def eth_fee_history(context: RpcContext, params: list[object]) -> dict[str, object]:
    require_params(params, min_count=2, max_count=3)
    block_count = parse_quantity(params[0], label="blockCount")
    if block_count < 1:
        raise ValueError("blockCount must be positive")
    if block_count > context.compat.fee_history_max_blocks:
        raise ValueError("blockCount exceeds the configured maximum")
    newest_selector = parse_block_selector(params[1])
    if newest_selector.tag == "pending":
        newest_number = context.node.head.block.header.number + 1
    elif newest_selector.tag is not None:
        newest_number = context.node.head.block.header.number if newest_selector.tag == "latest" else 0
    else:
        newest_number = newest_selector.number
    assert newest_number is not None
    oldest_number = max(0, newest_number - block_count + 1)
    blocks = [context.node.block_by_number(number) for number in range(oldest_number, min(newest_number, context.node.head.block.header.number) + 1)]
    present_blocks = [block for block in blocks if block is not None]
    base_fee_values = [to_quantity(block.block.header.base_fee or 0) for block in present_blocks]
    if newest_number > context.node.head.block.header.number:
        pending_preview = context.node.build_pending_preview()
        base_fee_values.append(to_quantity(pending_preview.block.header.base_fee or 0))
        gas_used_ratio = [block.block.header.gas_used / max(block.block.header.gas_limit, 1) for block in present_blocks]
    else:
        pending_preview = context.node.build_pending_preview()
        base_fee_values.append(to_quantity(pending_preview.block.header.base_fee or 0))
        gas_used_ratio = [block.block.header.gas_used / max(block.block.header.gas_limit, 1) for block in present_blocks]
    reward_percentiles = [] if len(params) < 3 or params[2] is None else list(params[2])
    reward = [["0x0" for _ in reward_percentiles] for _ in present_blocks] if reward_percentiles else []
    result = {
        "oldestBlock": to_quantity(oldest_number),
        "baseFeePerGas": base_fee_values,
        "gasUsedRatio": gas_used_ratio,
    }
    if reward_percentiles:
        result["reward"] = reward
    return result
