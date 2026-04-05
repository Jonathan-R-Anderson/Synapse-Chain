from __future__ import annotations

import json
import unittest

from evm.utils import compute_create_address
from execution_test_helpers import (
    PRIVATE_KEY_ONE,
    SENDER_ONE,
    addr,
    build_counter_runtime,
    build_init_code,
    build_reverter_runtime,
    encode_call,
    make_eip1559_tx,
)

from rpc.block_access import ExecutionNode
from rpc.compat import CompatibilityConfig
from rpc.server import JsonRpcServer


class RpcServerTests(unittest.TestCase):
    def _rpc(self, server: JsonRpcServer, method: str, params: list[object], request_id: int = 1) -> dict[str, object]:
        response = server.handle_json_bytes(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": request_id,
                }
            ).encode("utf-8")
        )
        self.assertIsNotNone(response)
        return json.loads(response.decode("utf-8"))

    def _funded_server(self, *, mode: str = "instant") -> JsonRpcServer:
        node = ExecutionNode(compat_config=CompatibilityConfig(mining_mode=mode))
        node.head.post_state.set_balance(SENDER_ONE, 10**20)
        return JsonRpcServer(node)

    def _eip1559_fee(self, server: JsonRpcServer) -> int:
        return server.context.node.build_pending_preview().block_env.base_fee or 0

    def test_transfer_flow_exposes_balance_receipt_block_and_batch(self) -> None:
        server = self._funded_server()
        recipient = addr("0x1000000000000000000000000000000000000001")
        base_fee = self._eip1559_fee(server)
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            value=7,
            gas_limit=21_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=base_fee + 2,
        )

        send_response = self._rpc(server, "eth_sendRawTransaction", ["0x" + transaction.encode().hex()])
        self.assertEqual(send_response["result"], transaction.tx_hash().to_hex())

        balance_response = self._rpc(server, "eth_getBalance", [recipient.to_hex(), "latest"], request_id=2)
        self.assertEqual(balance_response["result"], "0x7")

        receipt_response = self._rpc(server, "eth_getTransactionReceipt", [transaction.tx_hash().to_hex()], request_id=3)
        self.assertEqual(receipt_response["result"]["status"], "0x1")
        self.assertEqual(receipt_response["result"]["transactionHash"], transaction.tx_hash().to_hex())

        block_response = self._rpc(server, "eth_getBlockByNumber", ["latest", True], request_id=4)
        self.assertEqual(block_response["result"]["number"], "0x1")
        self.assertEqual(block_response["result"]["transactions"][0]["hash"], transaction.tx_hash().to_hex())

        batch = server.handle_json_bytes(
            json.dumps(
                [
                    {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 5},
                    {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 6},
                ]
            ).encode("utf-8")
        )
        self.assertIsNotNone(batch)
        batch_payload = json.loads(batch.decode("utf-8"))
        self.assertEqual(batch_payload[0]["result"], "0x1")
        self.assertEqual(batch_payload[1]["result"], "0x1")

        unknown = self._rpc(server, "eth_notReal", [], request_id=7)
        self.assertEqual(unknown["error"]["code"], -32601)

    def test_contract_call_estimation_trace_and_revert_data(self) -> None:
        server = self._funded_server()
        base_fee = self._eip1559_fee(server)
        create_tx = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            None,
            gas_limit=300_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=base_fee + 2,
            data=build_init_code(build_counter_runtime()),
        )
        self._rpc(server, "eth_sendRawTransaction", ["0x" + create_tx.encode().hex()])
        contract = compute_create_address(SENDER_ONE, 0)

        call_payload = {
            "from": SENDER_ONE.to_hex(),
            "to": contract.to_hex(),
            "data": "0x" + encode_call("get()").hex(),
        }
        get_response = self._rpc(server, "eth_call", [call_payload, "latest"], request_id=2)
        self.assertEqual(get_response["result"], "0x" + ("00" * 32))

        set_payload = {
            "from": SENDER_ONE.to_hex(),
            "to": contract.to_hex(),
            "data": "0x" + encode_call("set(uint256)", 11).hex(),
        }
        estimate_response = self._rpc(server, "eth_estimateGas", [set_payload, "latest"], request_id=3)
        self.assertGreater(int(estimate_response["result"], 16), 21_000)

        next_base_fee = self._eip1559_fee(server)
        set_tx = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            1,
            contract,
            gas_limit=100_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=next_base_fee + 2,
            data=encode_call("set(uint256)", 11),
        )
        self._rpc(server, "eth_sendRawTransaction", ["0x" + set_tx.encode().hex()], request_id=4)

        updated_response = self._rpc(server, "eth_call", [call_payload, "latest"], request_id=5)
        self.assertEqual(int(updated_response["result"], 16), 11)

        trace_response = self._rpc(server, "debug_traceTransaction", [set_tx.tx_hash().to_hex(), {}], request_id=6)
        self.assertTrue(any(step["op"] == "SSTORE" for step in trace_response["result"]["structLogs"]))

        reverter = addr("0x5000000000000000000000000000000000000005")
        server.context.node.head.post_state.set_code(reverter, build_reverter_runtime())
        revert_payload = {
            "from": SENDER_ONE.to_hex(),
            "to": reverter.to_hex(),
            "data": "0x",
        }
        revert_response = self._rpc(server, "eth_call", [revert_payload, "latest"], request_id=7)
        self.assertEqual(revert_response["error"]["code"], 3)
        self.assertEqual(revert_response["error"]["data"], "0xdeadbeef")

    def test_mempool_mode_exposes_pending_state_and_receipts_after_manual_mine(self) -> None:
        server = self._funded_server(mode="mempool")
        recipient = addr("0x3000000000000000000000000000000000000003")
        base_fee = self._eip1559_fee(server)
        transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            value=5,
            gas_limit=21_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=base_fee + 2,
        )

        send_response = self._rpc(server, "eth_sendRawTransaction", ["0x" + transaction.encode().hex()])
        self.assertEqual(send_response["result"], transaction.tx_hash().to_hex())

        pending_tx = self._rpc(server, "eth_getTransactionByHash", [transaction.tx_hash().to_hex()], request_id=2)
        self.assertIsNone(pending_tx["result"]["blockHash"])

        pending_nonce = self._rpc(server, "eth_getTransactionCount", [SENDER_ONE.to_hex(), "pending"], request_id=3)
        self.assertEqual(pending_nonce["result"], "0x1")

        pending_balance = self._rpc(server, "eth_getBalance", [recipient.to_hex(), "pending"], request_id=4)
        self.assertEqual(pending_balance["result"], "0x5")

        receipt_before = self._rpc(server, "eth_getTransactionReceipt", [transaction.tx_hash().to_hex()], request_id=5)
        self.assertIsNone(receipt_before["result"])

        mined = server.context.node.append_pending_block()
        self.assertIsNotNone(mined)

        receipt_after = self._rpc(server, "eth_getTransactionReceipt", [transaction.tx_hash().to_hex()], request_id=6)
        self.assertEqual(receipt_after["result"]["status"], "0x1")
        confirmed_balance = self._rpc(server, "eth_getBalance", [recipient.to_hex(), "latest"], request_id=7)
        self.assertEqual(confirmed_balance["result"], "0x5")


if __name__ == "__main__":
    unittest.main()
