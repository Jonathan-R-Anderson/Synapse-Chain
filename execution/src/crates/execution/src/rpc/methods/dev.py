from __future__ import annotations

from dataclasses import replace

from rpc.types import parse_address, parse_bool, parse_data, parse_quantity, to_data, to_quantity

from . import RpcContext, RpcHandler, require_params


SUPPORTED_MINING_ALGORITHMS = {"manual", "hybrid-beacon", "pbft-committee"}


def register(methods: dict[str, RpcHandler]) -> None:
    methods["dev_getConfig"] = dev_get_config
    methods["dev_mine"] = dev_mine
    methods["dev_setCoinbase"] = dev_set_coinbase


def dev_get_config(context: RpcContext, params: list[object]) -> dict[str, object]:
    require_params(params, min_count=0, max_count=0)
    return {
        "coinbase": context.compat.default_coinbase.to_hex(),
        "miningMode": context.compat.mining_mode,
        "blockGasLimit": to_quantity(context.compat.block_gas_limit),
        "defaultGasPrice": to_quantity(context.compat.default_gas_price),
        "defaultMaxPriorityFeePerGas": to_quantity(context.compat.default_max_priority_fee_per_gas),
        "extraData": to_data(context.compat.extra_data),
        "localAccounts": [account.to_hex() for account in context.compat.local_accounts],
    }


def dev_set_coinbase(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=1, max_count=1)
    beneficiary = parse_address(params[0], label="coinbase")
    updated = replace(context.compat, default_coinbase=beneficiary)
    context.compat = updated
    context.node.compat_config = updated
    return beneficiary.to_hex()


def _mine_options(params: list[object]) -> dict[str, object]:
    require_params(params, min_count=0, max_count=1)
    if not params:
        return {}
    payload = params[0]
    if isinstance(payload, dict):
        return payload
    return {"count": payload}


def dev_mine(context: RpcContext, params: list[object]) -> list[dict[str, object]]:
    options = _mine_options(params)
    count = 1 if options.get("count") is None else parse_quantity(options.get("count"), label="count")
    if count < 1:
        raise ValueError("count must be at least 1")

    reward = 0 if options.get("reward") is None else parse_quantity(options.get("reward"), label="reward")
    allow_empty = True if options.get("allowEmpty") is None else parse_bool(options.get("allowEmpty"), label="allowEmpty")
    beneficiary = context.compat.default_coinbase if options.get("beneficiary") is None else parse_address(
        options.get("beneficiary"), label="beneficiary"
    )
    algorithm = "manual" if options.get("algorithm") is None else str(options.get("algorithm"))
    if algorithm not in SUPPORTED_MINING_ALGORITHMS:
        raise ValueError(f"unsupported mining algorithm tag: {algorithm}")

    extra_data: bytes | None
    if options.get("extraData") is not None:
        extra_data = parse_data(options.get("extraData"), label="extraData")
    else:
        extra_data = f"mine:{algorithm}".encode("ascii")
    if len(extra_data) > 32:
        raise ValueError("extraData must be at most 32 bytes")

    mined: list[dict[str, object]] = []
    for _ in range(count):
        record = context.node.mine_block(
            beneficiary=beneficiary,
            block_reward=reward,
            allow_empty=allow_empty,
            extra_data=extra_data,
        )
        if record is None:
            break
        mined.append(
            {
                "number": to_quantity(record.block.header.number),
                "hash": record.block.hash().to_hex(),
                "miner": record.block.header.coinbase.to_hex(),
                "reward": to_quantity(reward),
                "algorithm": algorithm,
                "transactionCount": to_quantity(len(record.transaction_records)),
                "gasUsed": to_quantity(record.block.header.gas_used),
                "baseFeePerGas": to_quantity(record.block.header.base_fee or 0),
                "extraData": to_data(record.block.header.extra_data),
                "totalDifficulty": to_quantity(record.total_difficulty),
            }
        )
    return mined


__all__ = ["register"]
