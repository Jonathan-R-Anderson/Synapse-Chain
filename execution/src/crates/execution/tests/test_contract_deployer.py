from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    crate_src = str(CRATES / crate / "src")
    if crate_src not in sys.path:
        sys.path.insert(0, crate_src)

from execution_test_helpers import (
    PRIVATE_KEY_ONE,
    SENDER_ONE,
    build_counter_runtime,
    build_init_code,
    make_eip1559_tx,
)
from evm.utils import selector
from execution.contracts import (
    ContractDeployer,
    InProcessRpcTransport,
    RpcClient,
    encode_abi_arguments,
    encode_constructor_args,
    encode_function_call,
    load_contract_abi,
    load_contract_artifact,
)
from primitives import Address
from rpc.block_access import ExecutionNode
from rpc.compat import CompatibilityConfig
from rpc.server import JsonRpcServer


COUNTER_ABI: list[dict[str, object]] = [
    {
        "type": "function",
        "name": "set",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "newValue", "type": "uint256"}],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "get",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


class ContractDeploymentToolsTests(unittest.TestCase):
    def _funded_deployer(self) -> ContractDeployer:
        node = ExecutionNode(compat_config=CompatibilityConfig(mining_mode="instant"))
        node.head.post_state.set_balance(SENDER_ONE, 10**20)
        return ContractDeployer(RpcClient(InProcessRpcTransport(JsonRpcServer(node))))

    def test_abi_encoding_supports_static_and_dynamic_values(self) -> None:
        encoded = encode_abi_arguments(["uint256", "string", "bool"], [7, "hi", True])
        self.assertEqual(int.from_bytes(encoded[0:32], byteorder="big"), 7)
        self.assertEqual(int.from_bytes(encoded[32:64], byteorder="big"), 96)
        self.assertEqual(int.from_bytes(encoded[64:96], byteorder="big"), 1)
        self.assertEqual(int.from_bytes(encoded[96:128], byteorder="big"), 2)
        self.assertEqual(encoded[128:130], b"hi")

        constructor_abi = [{"type": "constructor", "inputs": [{"name": "seed", "type": "uint256"}]}]
        self.assertEqual(encode_constructor_args(constructor_abi, [9]), (9).to_bytes(32, byteorder="big"))

        encoded_call = encode_function_call(COUNTER_ABI, "set", [11])
        self.assertEqual(encoded_call[:4], selector("set(uint256)"))
        self.assertEqual(int.from_bytes(encoded_call[4:], byteorder="big"), 11)

    def test_artifact_loader_supports_plain_abi_and_standard_json_contracts(self) -> None:
        init_code = build_init_code(build_counter_runtime())

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            abi_path = root / "Counter.abi"
            abi_path.write_text(json.dumps(COUNTER_ABI), encoding="utf-8")
            self.assertEqual(load_contract_abi(abi_path), tuple(COUNTER_ABI))

            artifact_path = root / "Counter.json"
            artifact_path.write_text(
                json.dumps(
                    {
                        "contracts": {
                            "Counter.sol": {
                                "Counter": {
                                    "abi": COUNTER_ABI,
                                    "evm": {"bytecode": {"object": "0x" + init_code.hex()}},
                                },
                                "Other": {
                                    "abi": [],
                                    "evm": {"bytecode": {"object": "0x00"}},
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_contract_artifact(artifact_path)

            artifact = load_contract_artifact(artifact_path, contract_name="Counter")
            self.assertEqual(artifact.contract_name, "Counter")
            self.assertEqual(artifact.bytecode, init_code)
            self.assertEqual(artifact.abi, tuple(COUNTER_ABI))

    def test_deployer_can_deploy_contract_from_bin_and_abi_files(self) -> None:
        init_code = build_init_code(build_counter_runtime())
        deployer = self._funded_deployer()

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            bin_path = root / "Counter.bin"
            abi_path = root / "Counter.abi"
            bin_path.write_text(init_code.hex(), encoding="utf-8")
            abi_path.write_text(json.dumps(COUNTER_ABI), encoding="utf-8")

            artifact = load_contract_artifact(bin_path).with_abi(load_contract_abi(abi_path))
            result = deployer.deploy_contract(
                artifact,
                private_key=PRIVATE_KEY_ONE,
                gas_limit=300_000,
                wait_for_receipt=True,
            )

        self.assertEqual(result.nonce, 0)
        self.assertEqual(result.contract_address, result.predicted_contract_address)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertEqual(result.receipt["status"], "0x1")

        code = deployer.client.call("eth_getCode", [result.contract_address, "latest"])
        self.assertIsInstance(code, str)
        self.assertNotEqual(code, "0x")

        get_call = {
            "from": SENDER_ONE.to_hex(),
            "to": result.contract_address,
            "data": "0x" + encode_function_call(artifact.abi, "get", []).hex(),
        }
        initial_value = deployer.client.call("eth_call", [get_call, "latest"])
        self.assertEqual(int(initial_value, 16), 0)

        latest_block = deployer.client.call("eth_getBlockByNumber", ["latest", False])
        assert isinstance(latest_block, dict)
        base_fee = int(latest_block.get("baseFeePerGas", "0x0"), 16)
        set_transaction = make_eip1559_tx(
            PRIVATE_KEY_ONE,
            1,
            Address.from_hex(result.contract_address),
            gas_limit=100_000,
            max_priority_fee_per_gas=2,
            max_fee_per_gas=base_fee + 2,
            data=encode_function_call(artifact.abi, "set", [11]),
        )
        set_hash = deployer.client.call("eth_sendRawTransaction", ["0x" + set_transaction.encode().hex()])
        set_receipt = deployer.client.call("eth_getTransactionReceipt", [set_hash])
        self.assertIsInstance(set_receipt, dict)
        assert isinstance(set_receipt, dict)
        self.assertEqual(set_receipt["status"], "0x1")

        updated_value = deployer.client.call("eth_call", [get_call, "latest"])
        self.assertEqual(int(updated_value, 16), 11)


if __name__ == "__main__":
    unittest.main()
