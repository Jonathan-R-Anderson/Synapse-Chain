from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evm import ExecutionContext, Interpreter, StateDB
from execution import BlockEnvironment, apply_transaction, compute_logs_bloom, compute_receipts_root, compute_state_root, compute_transactions_root

from .comparator import ResultComparator
from .fork_rules import chain_config_for_rules, rules_for_block, rules_for_name
from .loader import load_fixture_file
from .models import ActualExecutionArtifacts, ExecutionFixtureCase, ValidationReport
from .roots import build_state_db


class ExecutionTestRunner:
    def __init__(self, comparator: ResultComparator | None = None) -> None:
        self.comparator = comparator or ResultComparator()

    def _resolve_rules(self, case: ExecutionFixtureCase, fork_override: str | None) -> tuple[object, int]:
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
        return rules, chain_id

    def _block_environment(self, case: ExecutionFixtureCase, chain_id: int) -> BlockEnvironment:
        return BlockEnvironment(
            block_number=case.environment.number,
            timestamp=case.environment.timestamp,
            gas_limit=case.environment.gas_limit,
            coinbase=case.environment.coinbase,
            base_fee=case.environment.base_fee,
            chain_id=chain_id,
            prev_randao=case.environment.prev_randao,
        )

    def _run_message_call(self, case: ExecutionFixtureCase, *, state: StateDB, chain_id: int) -> ActualExecutionArtifacts:
        assert case.message_call is not None
        call = case.message_call
        interpreter = Interpreter(state=state)
        result = interpreter.call(
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
        return ActualExecutionArtifacts(
            success=result.success,
            gas_used=call.gas - result.gas_remaining,
            output=result.output,
            logs=result.logs,
            receipts=(),
            state=state,
            state_root=compute_state_root(state),
            logs_bloom=bytes(256),
            error=result.error,
            block_number=case.environment.number,
        )

    def _run_transactions(self, case: ExecutionFixtureCase, *, state: StateDB, chain_id: int, chain_config: object) -> ActualExecutionArtifacts:
        block_env = self._block_environment(case, chain_id)
        receipts = []
        logs = []
        gas_used = 0
        output = b""
        success = True
        error: Exception | None = None
        transactions = []
        for fixture_tx in case.transactions:
            transaction = fixture_tx.to_transaction(default_chain_id=chain_id)
            transactions.append(transaction)
            try:
                tx_result = apply_transaction(
                    state=state,
                    transaction=transaction,
                    block_env=block_env,
                    chain_config=chain_config,  # type: ignore[arg-type]
                    cumulative_gas_used_before=gas_used,
                )
            except Exception as exc:
                success = False
                error = exc
                break
            gas_used += tx_result.gas_used
            receipts.append(tx_result.receipt)
            logs.extend(tx_result.logs)
            output = tx_result.output if tx_result.success else tx_result.revert_data
            if not tx_result.success:
                success = False
                error = tx_result.error
        receipts_tuple = tuple(receipt for receipt in receipts if receipt is not None)
        return ActualExecutionArtifacts(
            success=success,
            gas_used=gas_used,
            output=output,
            logs=tuple(logs),
            receipts=receipts_tuple,
            state=state,
            state_root=compute_state_root(state),
            logs_bloom=compute_logs_bloom(receipts_tuple),
            receipts_root=compute_receipts_root(receipts_tuple) if receipts_tuple else None,
            transactions_root=compute_transactions_root(tuple(transactions)) if transactions else None,
            error=error,
            block_number=case.environment.number,
        )

    def run_case(self, case: ExecutionFixtureCase, *, fork_override: str | None = None) -> tuple[ValidationReport, ActualExecutionArtifacts]:
        rules, chain_id = self._resolve_rules(case, fork_override)
        chain_config = chain_config_for_rules(rules, chain_id=chain_id)
        state = build_state_db(case.pre_state)
        if case.message_call is not None:
            actual = self._run_message_call(case, state=state, chain_id=chain_id)
        else:
            actual = self._run_transactions(case, state=state, chain_id=chain_id, chain_config=chain_config)
        report = self.comparator.compare_expected_result(expected=case.expected, actual=actual, test_name=case.name)
        return report, actual


def run_fixture_file(
    fixture_path: str | Path,
    *,
    case_name: str | None = None,
    fork_override: str | None = None,
    stop_on_first_error: bool = False,
) -> list[tuple[ValidationReport, ActualExecutionArtifacts]]:
    cases = load_fixture_file(fixture_path)
    selected = [cases[case_name]] if case_name is not None else [cases[name] for name in sorted(cases)]
    runner = ExecutionTestRunner()
    results: list[tuple[ValidationReport, ActualExecutionArtifacts]] = []
    for case in selected:
        outcome = runner.run_case(case, fork_override=fork_override)
        results.append(outcome)
        if stop_on_first_error and not outcome[0].passed:
            break
    return results


def _report_to_json(report: ValidationReport, actual: ActualExecutionArtifacts) -> dict[str, Any]:
    return {
        "report": report.to_dict(),
        "actual": {
            "success": actual.success,
            "gas_used": actual.gas_used,
            "output": "0x" + actual.output.hex(),
            "state_root": actual.state_root.to_hex(),
            "logs_bloom": "0x" + actual.logs_bloom.hex(),
            "receipts_root": None if actual.receipts_root is None else actual.receipts_root.to_hex(),
            "transactions_root": None if actual.transactions_root is None else actual.transactions_root.to_hex(),
            "error": None if actual.error is None else str(actual.error),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run execution correctness fixtures against the Python execution engine.")
    parser.add_argument("--fixture", required=True, help="Path to a JSON fixture file")
    parser.add_argument("--case", help="Optional case name within the fixture file")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text")
    parser.add_argument("--fork", help="Override the fork rule set")
    parser.add_argument("--stop-on-first-error", action="store_true", help="Stop after the first failing case")
    parser.add_argument("--trace-on-failure", action="store_true", help="Emit a trace when a case fails")
    parser.add_argument("--verbose", action="store_true", help="Print actual execution summaries in text mode")
    args = parser.parse_args(argv)

    cases = load_fixture_file(args.fixture)
    selected = [cases[args.case]] if args.case is not None else [cases[name] for name in sorted(cases)]
    runner = ExecutionTestRunner()
    results: list[tuple[ValidationReport, ActualExecutionArtifacts]] = []
    traces: list[dict[str, object] | None] = []
    for case in selected:
        report, actual = runner.run_case(case, fork_override=args.fork)
        results.append((report, actual))
        trace_payload = None
        if args.trace_on_failure and not report.passed:
            from debug.trace import trace_fixture_case

            trace_payload = trace_fixture_case(case, fork_override=args.fork).to_dict()
        traces.append(trace_payload)
        if args.stop_on_first_error and not report.passed:
            break

    if args.json:
        payload = []
        for (report, actual), trace_payload in zip(results, traces):
            item = _report_to_json(report, actual)
            if trace_payload is not None:
                item["trace"] = trace_payload
            payload.append(item)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for (report, actual), trace_payload in zip(results, traces):
            print(report.render_text())
            if args.verbose:
                print(
                    json.dumps(
                        {
                            "success": actual.success,
                            "gas_used": actual.gas_used,
                            "state_root": actual.state_root.to_hex(),
                            "receipts_root": None if actual.receipts_root is None else actual.receipts_root.to_hex(),
                            "transactions_root": None
                            if actual.transactions_root is None
                            else actual.transactions_root.to_hex(),
                            "error": None if actual.error is None else str(actual.error),
                        },
                        sort_keys=True,
                    )
                )
            if trace_payload is not None:
                print(json.dumps(trace_payload, indent=2, sort_keys=True))
    return 0 if all(report.passed for report, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
