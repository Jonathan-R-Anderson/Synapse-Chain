from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .artifact import ContractArtifact, load_contract_abi, load_contract_artifact
from .deployer import ContractDeployer, RpcCallError, RpcTransportError


def _parse_intish(value: str) -> int:
    if not isinstance(value, str) or not value:
        raise argparse.ArgumentTypeError("expected a decimal or 0x-prefixed integer")
    try:
        return int(value, 16 if value.startswith(("0x", "0X")) else 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value}") from exc


def _parse_json_array(value: str) -> list[object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise argparse.ArgumentTypeError("constructor arguments must decode to a JSON array")
    return payload


def _load_artifact_with_optional_abi(
    artifact_path: str,
    *,
    abi_path: str | None,
    contract_name: str | None,
) -> ContractArtifact:
    artifact = load_contract_artifact(artifact_path, contract_name=contract_name)
    if abi_path is None:
        return artifact
    return artifact.with_abi(load_contract_abi(abi_path, contract_name=contract_name))


def _summary_payload(
    artifact: ContractArtifact,
    result: Any,
    *,
    artifact_path: str,
    abi_path: str | None,
    constructor_args: list[object],
    deployed_code: str | None,
    include_receipt: bool,
    include_transaction: bool,
    include_raw_transaction: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact": str(Path(artifact_path).resolve()),
        "contractName": artifact.contract_name,
        "sender": result.sender,
        "nonce": result.nonce,
        "transactionHash": result.transaction_hash,
        "predictedContractAddress": result.predicted_contract_address,
        "contractAddress": result.contract_address,
        "txType": result.tx_type,
        "constructorArgs": constructor_args,
    }
    if abi_path is not None:
        payload["abiPath"] = str(Path(abi_path).resolve())
    if result.receipt is None:
        payload["receiptStatus"] = None
    else:
        payload["receiptStatus"] = result.receipt.get("status")
        payload["blockNumber"] = result.receipt.get("blockNumber")
        payload["gasUsed"] = result.receipt.get("gasUsed")
        payload["effectiveGasPrice"] = result.receipt.get("effectiveGasPrice")
    if deployed_code is not None:
        payload["deployedCodeSize"] = 0 if deployed_code == "0x" else (len(deployed_code) - 2) // 2
    if include_receipt:
        payload["receipt"] = result.receipt
    if include_transaction:
        payload["transaction"] = result.transaction
    if include_raw_transaction:
        payload["rawTransaction"] = result.raw_transaction
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy a compiled smart contract through the execution JSON-RPC API.")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--artifact", required=True, help="Path to a .bin/.hex artifact or a JSON artifact with bytecode.")
    parser.add_argument(
        "--abi-path",
        help="Optional path to a .abi file or JSON artifact that provides the contract ABI. Useful with solc --bin --abi output.",
    )
    parser.add_argument("--contract-name", help="Contract name to select from a multi-contract JSON artifact.")
    parser.add_argument("--private-key", required=True, type=_parse_intish, help="Signing key as decimal or 0x-prefixed integer.")
    parser.add_argument(
        "--constructor-args",
        type=_parse_json_array,
        default=[],
        help='JSON array of constructor arguments, for example \'[42,"hello",true]\'.',
    )
    parser.add_argument("--tx-type", choices=("eip1559", "legacy"), default="eip1559")
    parser.add_argument("--chain-id", type=_parse_intish)
    parser.add_argument("--gas-limit", type=_parse_intish, default=800_000)
    parser.add_argument("--value", type=_parse_intish, default=0)
    parser.add_argument("--gas-price", type=_parse_intish, help="Legacy gas price override.")
    parser.add_argument("--max-priority-fee-per-gas", type=_parse_intish, help="EIP-1559 tip override.")
    parser.add_argument("--max-fee-per-gas", type=_parse_intish, help="EIP-1559 max fee override.")
    parser.add_argument("--wait", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--receipt-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.25)
    parser.add_argument("--http-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--include-receipt", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-transaction", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--include-raw-transaction", action=argparse.BooleanOptionalAction, default=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        artifact = _load_artifact_with_optional_abi(
            args.artifact,
            abi_path=args.abi_path,
            contract_name=args.contract_name,
        )
        if args.constructor_args and not artifact.abi:
            raise ValueError("constructor arguments were provided but no ABI is available; pass --abi-path or use a JSON artifact that includes abi")

        deployer = ContractDeployer.for_rpc_url(args.rpc_url, timeout=args.http_timeout_seconds)
        result = deployer.deploy_contract(
            artifact,
            private_key=args.private_key,
            constructor_args=args.constructor_args,
            tx_type=args.tx_type,
            chain_id=args.chain_id,
            gas_limit=args.gas_limit,
            value=args.value,
            gas_price=args.gas_price,
            max_priority_fee_per_gas=args.max_priority_fee_per_gas,
            max_fee_per_gas=args.max_fee_per_gas,
            wait_for_receipt=args.wait,
            receipt_timeout_seconds=args.receipt_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        deployed_code = None
        if result.receipt is not None and result.receipt.get("status") == "0x1":
            code_result = deployer.client.call("eth_getCode", [result.contract_address, "latest"])
            if isinstance(code_result, str):
                deployed_code = code_result
    except (RpcCallError, RpcTransportError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            _summary_payload(
                artifact,
                result,
                artifact_path=args.artifact,
                abi_path=args.abi_path,
                constructor_args=args.constructor_args,
                deployed_code=deployed_code,
                include_receipt=args.include_receipt,
                include_transaction=args.include_transaction,
                include_raw_transaction=args.include_raw_transaction,
            ),
            indent=2,
            sort_keys=True,
        )
    )

    if not args.wait:
        return 0
    if result.receipt is None:
        return 2
    return 0 if result.receipt.get("status") == "0x1" else 3


__all__ = ["build_parser", "main"]
