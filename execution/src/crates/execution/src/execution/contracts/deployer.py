from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crypto import address_from_private_key
from evm.utils import compute_create_address
from rpc.server import JsonRpcServer

from ..transaction import EIP1559Transaction, LegacyTransaction
from .abi import encode_constructor_args
from .artifact import ContractArtifact


class RpcTransport(Protocol):
    def request(self, payload: object) -> object | None:
        ...


class RpcTransportError(RuntimeError):
    pass


class RpcCallError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class HttpRpcTransport:
    def __init__(self, url: str, *, timeout: float = 10.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def request(self, payload: object) -> object | None:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RpcTransportError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise RpcTransportError(str(exc.reason)) from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RpcTransportError("RPC endpoint returned invalid JSON") from exc


class InProcessRpcTransport:
    def __init__(self, server: JsonRpcServer) -> None:
        self.server = server

    def request(self, payload: object) -> object | None:
        body = json.dumps(payload).encode("utf-8")
        raw = self.server.handle_json_bytes(body)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))


class RpcClient:
    def __init__(self, transport: RpcTransport) -> None:
        self.transport = transport

    def request(self, payload: object) -> object | None:
        return self.transport.request(payload)

    def call(self, method: str, params: list[object] | None = None, *, request_id: int = 1) -> object | None:
        response = self.request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": [] if params is None else params,
            }
        )
        if response is None:
            return None
        if not isinstance(response, dict):
            raise RpcTransportError("RPC endpoint returned a non-object response")
        if "error" in response:
            error_payload = response["error"]
            if isinstance(error_payload, dict):
                raise RpcCallError(
                    str(error_payload.get("message", "RPC error")),
                    code=error_payload.get("code"),
                    data=error_payload.get("data"),
                )
            raise RpcCallError("RPC error")
        return response.get("result")

    def batch(self, requests: list[dict[str, object]]) -> list[dict[str, object]]:
        response = self.request(requests)
        if response is None:
            return []
        if not isinstance(response, list):
            raise RpcTransportError("RPC endpoint returned a non-batch response")
        for item in response:
            if not isinstance(item, dict):
                raise RpcTransportError("RPC endpoint returned an invalid batch item")
        return response


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    sender: str
    nonce: int
    transaction_hash: str
    predicted_contract_address: str
    contract_address: str
    raw_transaction: str
    tx_type: str
    receipt: dict[str, object] | None
    transaction: dict[str, object] | None


class ContractDeployer:
    def __init__(self, client: RpcClient) -> None:
        self.client = client

    @classmethod
    def for_rpc_url(cls, rpc_url: str, *, timeout: float = 10.0) -> "ContractDeployer":
        return cls(RpcClient(HttpRpcTransport(rpc_url, timeout=timeout)))

    def _batch_indexed(self, requests: list[dict[str, object]]) -> dict[int, object]:
        responses = self.client.batch(requests)
        indexed: dict[int, object] = {}
        for response in responses:
            if "error" in response:
                error_payload = response["error"]
                if isinstance(error_payload, dict):
                    raise RpcCallError(
                        str(error_payload.get("message", "RPC batch call failed")),
                        code=error_payload.get("code"),
                        data=error_payload.get("data"),
                    )
                raise RpcCallError("RPC batch call failed")
            indexed[int(response["id"])] = response.get("result")
        return indexed

    def _deployment_context(self, sender_address: str, *, tx_type: str, chain_id: int | None) -> dict[str, int]:
        if tx_type not in {"eip1559", "legacy"}:
            raise ValueError("tx_type must be 'eip1559' or 'legacy'")
        request_batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [sender_address, "pending"]},
            {"jsonrpc": "2.0", "id": 2, "method": "eth_chainId", "params": []},
            {"jsonrpc": "2.0", "id": 3, "method": "eth_gasPrice", "params": []},
            {"jsonrpc": "2.0", "id": 4, "method": "eth_maxPriorityFeePerGas", "params": []},
            {"jsonrpc": "2.0", "id": 5, "method": "eth_getBlockByNumber", "params": ["latest", False]},
        ]
        indexed = self._batch_indexed(request_batch)
        latest_block = indexed.get(5) if isinstance(indexed.get(5), dict) else {}
        resolved_chain_id = int(indexed.get(2), 16) if isinstance(indexed.get(2), str) else int(indexed.get(2))
        context = {
            "nonce": int(indexed.get(1), 16) if isinstance(indexed.get(1), str) else int(indexed.get(1)),
            "chain_id": resolved_chain_id if chain_id is None else int(chain_id),
            "gas_price": int(indexed.get(3), 16) if isinstance(indexed.get(3), str) else int(indexed.get(3)),
            "max_priority_fee_per_gas": int(indexed.get(4), 16) if isinstance(indexed.get(4), str) else int(indexed.get(4)),
            "base_fee_per_gas": int(latest_block.get("baseFeePerGas", "0x0"), 16) if isinstance(latest_block, dict) else 0,
        }
        return context

    def build_deployment_transaction(
        self,
        artifact: ContractArtifact,
        *,
        private_key: int,
        constructor_args: list[object] | tuple[object, ...] = (),
        tx_type: str = "eip1559",
        chain_id: int | None = None,
        gas_limit: int = 800_000,
        value: int = 0,
        gas_price: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        max_fee_per_gas: int | None = None,
    ) -> tuple[object, str, str, int]:
        sender = address_from_private_key(private_key)
        sender_hex = sender.to_hex()
        context = self._deployment_context(sender_hex, tx_type=tx_type, chain_id=chain_id)
        nonce = context["nonce"]
        deployment_data = artifact.bytecode + encode_constructor_args(artifact.abi, constructor_args)
        if tx_type == "legacy":
            resolved_gas_price = context["gas_price"] if gas_price is None else int(gas_price)
            transaction = LegacyTransaction(
                nonce=nonce,
                gas_price=resolved_gas_price,
                gas_limit=int(gas_limit),
                to=None,
                value=int(value),
                data=deployment_data,
                chain_id=context["chain_id"],
            ).sign(private_key)
        else:
            resolved_priority_fee = context["max_priority_fee_per_gas"] if max_priority_fee_per_gas is None else int(max_priority_fee_per_gas)
            resolved_max_fee = (
                max(context["base_fee_per_gas"] + (resolved_priority_fee * 2), resolved_priority_fee)
                if max_fee_per_gas is None
                else int(max_fee_per_gas)
            )
            transaction = EIP1559Transaction(
                chain_id=context["chain_id"],
                nonce=nonce,
                max_priority_fee_per_gas=resolved_priority_fee,
                max_fee_per_gas=resolved_max_fee,
                gas_limit=int(gas_limit),
                to=None,
                value=int(value),
                data=deployment_data,
            ).sign(private_key)
        raw_transaction = "0x" + transaction.encode().hex()
        predicted_address = compute_create_address(sender, nonce).to_hex()
        return transaction, raw_transaction, predicted_address, nonce

    def wait_for_receipt(self, transaction_hash: str, *, timeout_seconds: float = 15.0, poll_interval_seconds: float = 0.25) -> dict[str, object] | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            receipt = self.client.call("eth_getTransactionReceipt", [transaction_hash])
            if isinstance(receipt, dict):
                return receipt
            time.sleep(poll_interval_seconds)
        return None

    def deploy_contract(
        self,
        artifact: ContractArtifact,
        *,
        private_key: int,
        constructor_args: list[object] | tuple[object, ...] = (),
        tx_type: str = "eip1559",
        chain_id: int | None = None,
        gas_limit: int = 800_000,
        value: int = 0,
        gas_price: int | None = None,
        max_priority_fee_per_gas: int | None = None,
        max_fee_per_gas: int | None = None,
        wait_for_receipt: bool = True,
        receipt_timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 0.25,
    ) -> DeploymentResult:
        transaction, raw_transaction, predicted_address, nonce = self.build_deployment_transaction(
            artifact,
            private_key=private_key,
            constructor_args=constructor_args,
            tx_type=tx_type,
            chain_id=chain_id,
            gas_limit=gas_limit,
            value=value,
            gas_price=gas_price,
            max_priority_fee_per_gas=max_priority_fee_per_gas,
            max_fee_per_gas=max_fee_per_gas,
        )
        sender = address_from_private_key(private_key).to_hex()
        transaction_hash = str(self.client.call("eth_sendRawTransaction", [raw_transaction]))
        receipt = None
        if wait_for_receipt:
            receipt = self.wait_for_receipt(
                transaction_hash,
                timeout_seconds=receipt_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        transaction_record = self.client.call("eth_getTransactionByHash", [transaction_hash])
        contract_address = predicted_address
        if isinstance(receipt, dict) and isinstance(receipt.get("contractAddress"), str):
            contract_address = str(receipt["contractAddress"])
        return DeploymentResult(
            sender=sender,
            nonce=nonce,
            transaction_hash=transaction_hash,
            predicted_contract_address=predicted_address,
            contract_address=contract_address,
            raw_transaction=raw_transaction,
            tx_type=tx_type,
            receipt=receipt if isinstance(receipt, dict) else None,
            transaction=transaction_record if isinstance(transaction_record, dict) else None,
        )


__all__ = [
    "ContractDeployer",
    "DeploymentResult",
    "HttpRpcTransport",
    "InProcessRpcTransport",
    "RpcClient",
]
