from __future__ import annotations

import argparse
import json
from pathlib import Path

from evm import ExecutionContext, Interpreter, TraceCaptureSink
from execution import BlockEnvironment, apply_transaction
from execution_tests import build_state_db, chain_config_for_rules, load_fixture_file, rules_for_block, rules_for_name
from execution_tests.models import ExecutionFixtureCase
from replay.block_loader import load_block_bundle


def _resolve_chain(case: ExecutionFixtureCase, fork_override: str | None) -> tuple[object, int]:
    chain_id = 1 if case.environment.chain_id is None else case.environment.chain_id
    if fork_override is not None:
        rules = rules_for_name(fork_override)
    elif case.fork_name is not None:
        rules = rules_for_name(case.fork_name)
    else:
        rules = rules_for_block(
            case.environment.number,
            case.environment.timestamp,
            base_fee=case.environment.base_fee,
            difficulty=case.environment.difficulty,
            prev_randao_present=case.environment.prev_randao is not None,
        )
    return chain_config_for_rules(rules, chain_id=chain_id), chain_id


def trace_fixture_case(
    case: ExecutionFixtureCase,
    *,
    tx_index: int = 0,
    fork_override: str | None = None,
    capture_memory: bool = False,
) -> TraceCaptureSink:
    state = build_state_db(case.pre_state)
    sink = TraceCaptureSink(capture_memory_snapshots=capture_memory)
    chain_config, chain_id = _resolve_chain(case, fork_override)

    if case.message_call is not None:
        call = case.message_call
        interpreter = Interpreter(state=state, trace_sink=sink)
        interpreter.call(
            address=call.to,
            caller=call.caller,
            origin=call.caller,
            value=call.value,
            calldata=call.data,
            gas=call.gas,
            static=call.static,
            gas_price=0 if case.environment.base_fee is None else case.environment.base_fee,
            chain_id=chain_id,
        )
        return sink

    block_env = BlockEnvironment(
        block_number=case.environment.number,
        timestamp=case.environment.timestamp,
        gas_limit=case.environment.gas_limit,
        coinbase=case.environment.coinbase,
        base_fee=case.environment.base_fee,
        chain_id=chain_id,
        prev_randao=case.environment.prev_randao,
    )
    cumulative_gas = 0
    for index, fixture_tx in enumerate(case.transactions):
        transaction = fixture_tx.to_transaction(default_chain_id=chain_id)
        interpreter = Interpreter(state=state, trace_sink=sink if index == tx_index else None)
        apply_transaction(
            state=state,
            transaction=transaction,
            block_env=block_env,
            chain_config=chain_config,  # type: ignore[arg-type]
            interpreter=interpreter,
            cumulative_gas_used_before=cumulative_gas,
        )
        if index == tx_index:
            break
    return sink


def trace_block_transaction(
    bundle_path: str | Path,
    *,
    tx_index: int,
    fork_override: str | None = None,
    capture_memory: bool = False,
) -> TraceCaptureSink:
    bundle = load_block_bundle(bundle_path)
    if tx_index >= len(bundle.block.transactions):
        raise IndexError(f"block only contains {len(bundle.block.transactions)} transactions")
    if not bundle.transaction_bodies_complete:
        raise ValueError("trace requires full transaction bodies in the block bundle")
    state = build_state_db(bundle.pre_state)
    sink = TraceCaptureSink(capture_memory_snapshots=capture_memory)
    if fork_override is not None:
        rules = rules_for_name(fork_override)
    elif bundle.fork_name is not None:
        rules = rules_for_name(bundle.fork_name)
    else:
        rules = rules_for_block(
            bundle.block.header.number,
            bundle.block.header.timestamp,
            base_fee=bundle.block.header.base_fee,
            difficulty=bundle.block.header.difficulty,
            prev_randao_present=bundle.block.header.difficulty == 0,
        )
    chain_config = chain_config_for_rules(rules, chain_id=bundle.chain_id)
    block_env = BlockEnvironment.from_block(bundle.block, chain_config)  # type: ignore[arg-type]
    cumulative_gas = 0
    for index, transaction in enumerate(bundle.block.transactions):
        interpreter = Interpreter(state=state, trace_sink=sink if index == tx_index else None)
        result = apply_transaction(
            state=state,
            transaction=transaction,
            block_env=block_env,
            chain_config=chain_config,  # type: ignore[arg-type]
            interpreter=interpreter,
            cumulative_gas_used_before=cumulative_gas,
        )
        cumulative_gas += result.gas_used
        if index == tx_index:
            break
    return sink


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit opcode traces for execution fixtures or block bundles.")
    parser.add_argument("--fixture", help="Path to an execution fixture JSON file")
    parser.add_argument("--case", help="Fixture case name")
    parser.add_argument("--block", help="Path to a replay block bundle JSON file")
    parser.add_argument("--tx-index", type=int, default=0, help="Transaction index to trace")
    parser.add_argument("--fork", help="Override the fork rules")
    parser.add_argument("--json", action="store_true", help="Emit trace JSON")
    parser.add_argument("--memory", action="store_true", help="Include full memory snapshots in each trace row")
    args = parser.parse_args(argv)

    if not args.fixture and not args.block:
        parser.error("either --fixture or --block is required")

    if args.fixture:
        cases = load_fixture_file(args.fixture)
        if args.case is None:
            if len(cases) != 1:
                parser.error("--case is required when the fixture file contains multiple cases")
            case = next(iter(cases.values()))
        else:
            case = cases[args.case]
        sink = trace_fixture_case(case, tx_index=args.tx_index, fork_override=args.fork, capture_memory=args.memory)
    else:
        sink = trace_block_transaction(args.block, tx_index=args.tx_index, fork_override=args.fork, capture_memory=args.memory)

    if args.json:
        print(json.dumps(sink.to_dict(), indent=2, sort_keys=True))
    else:
        for event in sink.frame_events:
            print(json.dumps(event.to_dict(), sort_keys=True))
        for row in sink.rows:
            print(json.dumps(row.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
