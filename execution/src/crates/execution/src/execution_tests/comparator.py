from __future__ import annotations

from typing import Iterable, Sequence

from evm import LogEntry
from execution import Receipt

from .models import AccountFixture, ActualExecutionArtifacts, ExpectedResult, ValidationMismatch, ValidationReport
from .roots import account_code_hashes, account_storage_roots, compute_code_hash, compute_storage_root, export_state_accounts


def _hex_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return "0x" + bytes(value).hex()


class ResultComparator:
    def compare_state_roots(self, expected: bytes | None, actual: bytes | None) -> list[ValidationMismatch]:
        if expected is None:
            return []
        if actual != expected:
            return [
                ValidationMismatch(
                    category="state_root",
                    path="state_root",
                    expected=_hex_bytes(expected),
                    actual=_hex_bytes(actual),
                    detail="post-state root mismatch",
                )
            ]
        return []

    def compare_receipts_roots(self, expected: bytes | None, actual: bytes | None) -> list[ValidationMismatch]:
        if expected is None:
            return []
        if actual != expected:
            return [
                ValidationMismatch(
                    category="receipts_root",
                    path="receipts_root",
                    expected=_hex_bytes(expected),
                    actual=_hex_bytes(actual),
                    detail="receipts root mismatch",
                )
            ]
        return []

    def compare_transactions_roots(self, expected: bytes | None, actual: bytes | None) -> list[ValidationMismatch]:
        if expected is None:
            return []
        if actual != expected:
            return [
                ValidationMismatch(
                    category="transactions_root",
                    path="transactions_root",
                    expected=_hex_bytes(expected),
                    actual=_hex_bytes(actual),
                    detail="transactions root mismatch",
                )
            ]
        return []

    def compare_bloom(self, expected: bytes | None, actual: bytes | None) -> list[ValidationMismatch]:
        if expected is None:
            return []
        if actual != expected:
            return [
                ValidationMismatch(
                    category="logs_bloom",
                    path="logs_bloom",
                    expected=_hex_bytes(expected),
                    actual=_hex_bytes(actual),
                    detail="logs bloom mismatch",
                )
            ]
        return []

    def compare_logs(self, expected: Sequence[LogEntry], actual: Sequence[LogEntry]) -> list[ValidationMismatch]:
        mismatches: list[ValidationMismatch] = []
        if len(expected) != len(actual):
            mismatches.append(
                ValidationMismatch(
                    category="logs",
                    path="logs.length",
                    expected=len(expected),
                    actual=len(actual),
                    detail="log count mismatch",
                )
            )
        for index, (exp, act) in enumerate(zip(expected, actual)):
            if exp.address != act.address:
                mismatches.append(
                    ValidationMismatch(
                        category="logs",
                        path=f"logs[{index}].address",
                        expected=exp.address.to_hex(),
                        actual=act.address.to_hex(),
                        detail="log address mismatch",
                    )
                )
            if exp.topics != act.topics:
                mismatches.append(
                    ValidationMismatch(
                        category="logs",
                        path=f"logs[{index}].topics",
                        expected=[topic.to_hex() for topic in exp.topics],
                        actual=[topic.to_hex() for topic in act.topics],
                        detail="log topics mismatch",
                    )
                )
            if exp.data != act.data:
                mismatches.append(
                    ValidationMismatch(
                        category="logs",
                        path=f"logs[{index}].data",
                        expected=_hex_bytes(exp.data),
                        actual=_hex_bytes(act.data),
                        detail="log data mismatch",
                    )
                )
        return mismatches

    def compare_receipts(self, expected: Sequence[Receipt], actual: Sequence[Receipt]) -> list[ValidationMismatch]:
        mismatches: list[ValidationMismatch] = []
        if len(expected) != len(actual):
            mismatches.append(
                ValidationMismatch(
                    category="receipts",
                    path="receipts.length",
                    expected=len(expected),
                    actual=len(actual),
                    detail="receipt count mismatch",
                )
            )
        for index, (exp, act) in enumerate(zip(expected, actual)):
            if exp.status != act.status:
                mismatches.append(
                    ValidationMismatch(
                        category="receipts",
                        path=f"receipts[{index}].status",
                        expected=exp.status,
                        actual=act.status,
                        detail="receipt status mismatch",
                    )
                )
            if exp.cumulative_gas_used != act.cumulative_gas_used:
                mismatches.append(
                    ValidationMismatch(
                        category="receipts",
                        path=f"receipts[{index}].cumulative_gas_used",
                        expected=exp.cumulative_gas_used,
                        actual=act.cumulative_gas_used,
                        detail="receipt cumulative gas mismatch",
                    )
                )
            if exp.gas_used and exp.gas_used != act.gas_used:
                mismatches.append(
                    ValidationMismatch(
                        category="receipts",
                        path=f"receipts[{index}].gas_used",
                        expected=exp.gas_used,
                        actual=act.gas_used,
                        detail="receipt gas_used mismatch",
                    )
                )
            mismatches.extend(self.compare_bloom(exp.logs_bloom, act.logs_bloom))
            mismatches.extend(self.compare_logs(exp.logs, act.logs))
        return mismatches

    def compare_accounts(
        self,
        expected: Sequence[AccountFixture],
        actual: Sequence[AccountFixture],
        *,
        actual_state_root_source: ActualExecutionArtifacts | None = None,
    ) -> list[ValidationMismatch]:
        mismatches: list[ValidationMismatch] = []
        expected_by_address = {account.address: account for account in expected}
        actual_by_address = {account.address: account for account in actual}

        for address in sorted(expected_by_address, key=lambda item: item.to_bytes()):
            if address not in actual_by_address:
                mismatches.append(
                    ValidationMismatch(
                        category="accounts",
                        path=f"accounts[{address.to_hex()}]",
                        expected="present",
                        actual="missing",
                        detail="expected account is missing from post-state",
                    )
                )
                continue
            exp = expected_by_address[address]
            act = actual_by_address[address]
            if exp.nonce != act.nonce:
                mismatches.append(
                    ValidationMismatch(
                        category="accounts",
                        path=f"accounts[{address.to_hex()}].nonce",
                        expected=exp.nonce,
                        actual=act.nonce,
                        detail="nonce mismatch",
                    )
                )
            if exp.balance != act.balance:
                mismatches.append(
                    ValidationMismatch(
                        category="accounts",
                        path=f"accounts[{address.to_hex()}].balance",
                        expected=exp.balance,
                        actual=act.balance,
                        detail="balance mismatch",
                    )
                )
            if exp.code != act.code:
                mismatches.append(
                    ValidationMismatch(
                        category="accounts",
                        path=f"accounts[{address.to_hex()}].code",
                        expected=_hex_bytes(exp.code),
                        actual=_hex_bytes(act.code),
                        detail="bytecode mismatch",
                    )
                )
            exp_storage = {int(key): int(value) for key, value in exp.storage}
            act_storage = {int(key): int(value) for key, value in act.storage}
            keys = sorted(set(exp_storage) | set(act_storage))
            for key in keys:
                if exp_storage.get(key, 0) != act_storage.get(key, 0):
                    mismatches.append(
                        ValidationMismatch(
                            category="storage",
                            path=f"accounts[{address.to_hex()}].storage[{key:#x}]",
                            expected=hex(exp_storage.get(key, 0)),
                            actual=hex(act_storage.get(key, 0)),
                            detail="storage slot mismatch",
                        )
                    )

        unexpected = sorted(
            set(actual_by_address) - set(expected_by_address),
            key=lambda item: item.to_bytes(),
        )
        for address in unexpected:
            mismatches.append(
                ValidationMismatch(
                    category="accounts",
                    path=f"accounts[{address.to_hex()}]",
                    expected="missing",
                    actual="present",
                    detail="unexpected account present in post-state",
                )
            )

        if actual_state_root_source is not None:
            code_hashes = account_code_hashes(actual_state_root_source.state)
            storage_roots = account_storage_roots(actual_state_root_source.state)
            for account in expected:
                actual_account = actual_by_address.get(account.address)
                if actual_account is None:
                    continue
                expected_code_hash = compute_code_hash(account.code).to_hex()
                actual_code_hash = code_hashes[account.address].to_hex()
                if expected_code_hash != actual_code_hash:
                    mismatches.append(
                        ValidationMismatch(
                            category="code_hash",
                            path=f"accounts[{account.address.to_hex()}].code_hash",
                            expected=expected_code_hash,
                            actual=actual_code_hash,
                            detail="code hash mismatch",
                        )
                    )
                if account.storage or actual_account.storage:
                    expected_root = compute_storage_root({int(key): int(value) for key, value in account.storage}).to_hex()
                    actual_root = storage_roots[account.address].to_hex()
                    if expected_root != actual_root:
                        mismatches.append(
                            ValidationMismatch(
                                category="storage_root",
                                path=f"accounts[{account.address.to_hex()}].storage_root",
                                expected=expected_root,
                                actual=actual_root,
                                detail="storage root mismatch",
                            )
                        )
        return mismatches

    def compare_expected_result(
        self,
        *,
        expected: ExpectedResult,
        actual: ActualExecutionArtifacts,
        test_name: str | None = None,
    ) -> ValidationReport:
        mismatches: list[ValidationMismatch] = []
        if expected.success is not None and expected.success != actual.success:
            mismatches.append(
                ValidationMismatch(
                    category="execution",
                    path="success",
                    expected=expected.success,
                    actual=actual.success,
                    detail="success flag mismatch",
                )
            )
        if expected.gas_used is not None and expected.gas_used != actual.gas_used:
            mismatches.append(
                ValidationMismatch(
                    category="gas",
                    path="gas_used",
                    expected=expected.gas_used,
                    actual=actual.gas_used,
                    detail="gas used mismatch",
                )
            )
        if expected.output is not None and expected.output != actual.output:
            mismatches.append(
                ValidationMismatch(
                    category="output",
                    path="output",
                    expected=_hex_bytes(expected.output),
                    actual=_hex_bytes(actual.output),
                    detail="return data mismatch",
                )
            )
        mismatches.extend(self.compare_state_roots(expected.state_root.to_bytes() if expected.state_root else None, actual.state_root.to_bytes()))
        mismatches.extend(
            self.compare_receipts_roots(
                expected.receipts_root.to_bytes() if expected.receipts_root else None,
                None if actual.receipts_root is None else actual.receipts_root.to_bytes(),
            )
        )
        mismatches.extend(
            self.compare_transactions_roots(
                expected.transactions_root.to_bytes() if expected.transactions_root else None,
                None if actual.transactions_root is None else actual.transactions_root.to_bytes(),
            )
        )
        mismatches.extend(self.compare_bloom(expected.logs_bloom, actual.logs_bloom))
        if expected.logs:
            mismatches.extend(self.compare_logs(expected.logs, actual.logs))
        if expected.receipts:
            mismatches.extend(self.compare_receipts(expected.receipts, actual.receipts))
        if expected.post_state:
            mismatches.extend(
                self.compare_accounts(
                    expected.post_state,
                    export_state_accounts(actual.state),
                    actual_state_root_source=actual,
                )
            )
        if expected.error_substring is not None:
            actual_error = "" if actual.error is None else str(actual.error)
            if expected.error_substring not in actual_error:
                mismatches.append(
                    ValidationMismatch(
                        category="error",
                        path="error",
                        expected=expected.error_substring,
                        actual=actual_error,
                        detail="expected error substring was not present",
                    )
                )
        return ValidationReport(
            passed=not mismatches,
            mismatches=tuple(mismatches),
            block_number=actual.block_number,
            test_name=test_name,
        )
