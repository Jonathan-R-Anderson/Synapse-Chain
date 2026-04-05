from __future__ import annotations

import argparse
import json
from pathlib import Path

from execution import apply_block
from execution_tests import ResultComparator, chain_config_for_rules, rules_for_block, rules_for_name
from execution_tests.models import ActualExecutionArtifacts, ValidationMismatch, ValidationReport
from execution_tests.roots import build_state_db

from .block_loader import ReplayLoadingError, load_block_bundle
from .reference import ReferenceBlockBundle
from .report import BlockReplayOutcome


class BlockReplayExecutor:
    def __init__(self, comparator: ResultComparator | None = None) -> None:
        self.comparator = comparator or ResultComparator()

    def _resolve_rules(self, bundle: ReferenceBlockBundle, fork_override: str | None) -> object:
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
                prev_randao_present=bundle.block.header.mix_hash != bundle.block.header.parent_hash or bundle.block.header.difficulty == 0,
            )
        return chain_config_for_rules(rules, chain_id=bundle.chain_id)

    def replay(self, bundle: ReferenceBlockBundle, *, fork_override: str | None = None) -> BlockReplayOutcome:
        if not bundle.transaction_bodies_complete and (bundle.transaction_count or 0) > 0:
            report = ValidationReport(
                passed=False,
                test_name=bundle.name,
                block_number=bundle.block.header.number,
                mismatches=(
                    ValidationMismatch(
                        category="replay",
                        path="transactions",
                        expected="full transaction bodies",
                        actual="hash-only transaction list",
                        detail="block replay requires transaction bodies, not only transaction hashes",
                    ),
                ),
            )
            return BlockReplayOutcome(report=report)

        state = build_state_db(bundle.pre_state)
        chain_config = self._resolve_rules(bundle, fork_override)
        try:
            block_result = apply_block(
                old_state=state,
                block=bundle.block,
                chain_config=chain_config,  # type: ignore[arg-type]
                parent_header=bundle.parent_header,
            )
        except Exception as exc:
            report = ValidationReport(
                passed=False,
                test_name=bundle.name,
                block_number=bundle.block.header.number,
                mismatches=(
                    ValidationMismatch(
                        category="execution",
                        path="block",
                        expected="successful replay",
                        actual=str(exc),
                        detail="block replay raised an exception",
                    ),
                ),
            )
            return BlockReplayOutcome(report=report, error=exc)

        actual = ActualExecutionArtifacts(
            success=all(result.success for result in block_result.transaction_results) if block_result.transaction_results else True,
            gas_used=block_result.gas_used,
            output=b"",
            logs=block_result.logs,
            receipts=block_result.receipts,
            state=block_result.state,
            state_root=block_result.state_root or bundle.block.header.state_root,
            logs_bloom=block_result.logs_bloom or bytes(256),
            receipts_root=block_result.receipts_root,
            transactions_root=block_result.transactions_root,
            block_number=bundle.block.header.number,
        )
        report = self.comparator.compare_expected_result(expected=bundle.expected, actual=actual, test_name=bundle.name)
        return BlockReplayOutcome(report=report, block_result=block_result)


def _outcome_to_dict(outcome: BlockReplayOutcome) -> dict[str, object]:
    return {
        "report": outcome.report.to_dict(),
        "error": None if outcome.error is None else str(outcome.error),
    }


def _bundle_paths(args: argparse.Namespace) -> list[Path]:
    if args.blocks is not None:
        return sorted(Path(args.blocks).glob("*.json"))
    return [Path(args.block)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay block bundles through the Python execution engine.")
    parser.add_argument("--block", help="Path to a single block bundle JSON file")
    parser.add_argument("--blocks", help="Directory containing block bundle JSON files")
    parser.add_argument("--state", help="Optional separate pre-state JSON file")
    parser.add_argument("--fork", help="Override the fork rules")
    parser.add_argument("--json", action="store_true", help="Emit JSON reports")
    parser.add_argument("--stop-on-first-error", action="store_true", help="Stop after the first failing replay")
    parser.add_argument("--trace-on-failure", action="store_true", help="Emit a trace for the first failing transaction")
    args = parser.parse_args(argv)

    if not args.block and not args.blocks:
        parser.error("either --block or --blocks is required")

    executor = BlockReplayExecutor()
    outcomes = []
    traces: list[dict[str, object] | None] = []
    exit_code = 0
    for path in _bundle_paths(args):
        bundle = load_block_bundle(path, state_path=args.state)
        outcome = executor.replay(bundle, fork_override=args.fork)
        outcomes.append(outcome)
        trace_payload = None
        if args.trace_on_failure and not outcome.passed and bundle.transaction_bodies_complete and (bundle.transaction_count or 0) > 0:
            from debug.trace import trace_block_transaction

            trace_payload = trace_block_transaction(path, tx_index=0, fork_override=args.fork).to_dict()
        traces.append(trace_payload)
        if not outcome.passed:
            exit_code = 1
            if args.stop_on_first_error:
                break

    if args.json:
        payload = []
        for outcome, trace_payload in zip(outcomes, traces):
            item = _outcome_to_dict(outcome)
            if trace_payload is not None:
                item["trace"] = trace_payload
            payload.append(item)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for outcome, trace_payload in zip(outcomes, traces):
            print(outcome.report.render_text())
            if trace_payload is not None:
                print(json.dumps(trace_payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
