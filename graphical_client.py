#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import tkinter as tk


ROOT = Path(__file__).resolve().parent
CRATES = ROOT / "execution" / "src" / "crates"
CONSENSUS_SRC = ROOT / "consensus" / "src"

for crate_name in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    source_path = CRATES / crate_name / "src"
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))
if str(CONSENSUS_SRC) not in sys.path:
    sys.path.insert(0, str(CONSENSUS_SRC))

from crypto import SECP256K1_N, address_from_private_key  # noqa: E402
from consensus.networking.simulation import run_network_simulation  # noqa: E402
from consensus.simulation import run_simulation as run_beacon_simulation  # noqa: E402
from evm import StateDB  # noqa: E402
from execution.contracts import ContractDeployer, load_contract_abi, load_contract_artifact  # noqa: E402
from execution import ChainConfig, EIP1559Transaction, LegacyTransaction  # noqa: E402
from primitives import Address  # noqa: E402
from rpc.block_access import ExecutionNode  # noqa: E402
from rpc.compat import CompatibilityConfig  # noqa: E402
from rpc.server import JsonRpcServer  # noqa: E402


APP_TITLE = "Python Ethereum Desktop Client"
DEV_ACCOUNT_BALANCE = 10**24
CONSENSUS_ALGORITHMS = (
    ("manual", "Manual Reward Block"),
    ("hybrid-beacon", "Hybrid Beacon Simulation"),
    ("pbft-committee", "PBFT Committee Simulation"),
)
STACK_SERVICE_BLUEPRINTS = (
    {
        "service": "i2p-router",
        "role": "I2P Router",
        "description": "SAM bridge and router used by privacy-mode execution nodes.",
        "host_endpoint": lambda env: f"http://127.0.0.1:{env.get('I2P_CONSOLE_PORT', '7657')}",
    },
    {
        "service": "execution-rpc",
        "role": "Execution RPC",
        "description": "Ethereum-style JSON-RPC endpoint exposed to host clients.",
        "host_endpoint": lambda env: f"http://127.0.0.1:{env.get('EXECUTION_RPC_PORT', '8545')}",
    },
    {
        "service": "execution-full",
        "role": "Execution Full",
        "description": "Full-sync execution demo node.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "execution-light",
        "role": "Execution Light",
        "description": "Light-sync execution demo node.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "execution-archive",
        "role": "Execution Archive",
        "description": "Archive execution demo node.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "execution-bootnode",
        "role": "Execution Bootnode",
        "description": "Peer-discovery oriented execution bootnode.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "execution-state-provider",
        "role": "Execution State Provider",
        "description": "Execution node serving state and snapshot data.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "execution-validator",
        "role": "Execution Validator",
        "description": "Execution validator-role demo node.",
        "host_endpoint": lambda env: None,
    },
    {
        "service": "consensus-sim",
        "role": "Consensus Simulation",
        "description": "One-shot hybrid consensus simulator profile.",
        "host_endpoint": lambda env: None,
    },
)
PALETTE = {
    "bg": "#f2efe7",
    "panel": "#fffaf2",
    "surface": "#f7f1e4",
    "accent": "#114b5f",
    "accent_alt": "#d96c06",
    "text": "#1f2a30",
    "muted": "#58636a",
    "border": "#d5ccbc",
    "success": "#1c7c54",
    "danger": "#a43f32",
}


class RpcTransportError(RuntimeError):
    pass


class RpcCallError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True, slots=True)
class WalletProfile:
    label: str
    private_key: int
    address: str

    @property
    def private_key_hex(self) -> str:
        return f"0x{self.private_key:064x}"

    @property
    def combo_label(self) -> str:
        return f"{self.label} | {self.address}"


@dataclass(frozen=True, slots=True)
class StackServiceStatus:
    service: str
    role: str
    description: str
    status: str
    host_endpoint: str | None = None
    container_name: str | None = None
    container_ips: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True, slots=True)
class StackSnapshot:
    project_name: str
    host_ips: tuple[str, ...]
    services: tuple[StackServiceStatus, ...]
    discovery_error: str | None = None


def parse_json_array(value: str, *, label: str) -> list[object]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, list):
        raise ValueError(f"{label} must decode to a JSON array")
    return payload


def read_env_settings(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    if not path.exists():
        return payload
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def host_ip_addresses() -> tuple[str, ...]:
    addresses = {"127.0.0.1"}
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM):
            addresses.add(str(result[4][0]))
    except OSError:
        pass
    return tuple(sorted(addresses))


def _parse_compose_ps_output(raw: str) -> list[dict[str, object]]:
    text = raw.strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        items: list[dict[str, object]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                items.append(parsed)
        return items
    if isinstance(decoded, list):
        return [item for item in decoded if isinstance(item, dict)]
    if isinstance(decoded, dict):
        return [decoded]
    return []


def inspect_container_networks(container_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container_name],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout.strip() or "{}")
    if not isinstance(payload, dict):
        return (), ()
    ips: list[str] = []
    network_names: list[str] = []
    for network_name, data in payload.items():
        network_names.append(str(network_name))
        if isinstance(data, dict):
            ip_address = str(data.get("IPAddress", "")).strip()
            if ip_address:
                ips.append(ip_address)
    return tuple(ips), tuple(network_names)


def inspect_stack_snapshot(*, dev_server: "EmbeddedRpcServer | None" = None) -> StackSnapshot:
    env = read_env_settings(ROOT / ".env")
    running_by_service: dict[str, dict[str, object]] = {}
    discovery_error: str | None = None

    try:
        completed = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        for entry in _parse_compose_ps_output(completed.stdout):
            service_name = str(entry.get("Service") or entry.get("Name") or "").strip()
            if service_name:
                running_by_service[service_name] = entry
    except Exception as exc:  # noqa: BLE001
        discovery_error = str(exc)

    services: list[StackServiceStatus] = []
    for blueprint in STACK_SERVICE_BLUEPRINTS:
        service_name = str(blueprint["service"])
        runtime_entry = running_by_service.get(service_name)
        host_endpoint = blueprint["host_endpoint"](env)
        status = "configured"
        container_name = None
        container_ips: tuple[str, ...] = ()
        networks: tuple[str, ...] = ()
        note = ""
        if runtime_entry is not None:
            status = str(runtime_entry.get("State") or runtime_entry.get("Status") or "running")
            container_name = str(runtime_entry.get("Name") or "")
            if container_name:
                try:
                    container_ips, networks = inspect_container_networks(container_name)
                except Exception as exc:  # noqa: BLE001
                    note = str(exc)
        services.append(
            StackServiceStatus(
                service=service_name,
                role=str(blueprint["role"]),
                description=str(blueprint["description"]),
                status=status,
                host_endpoint=host_endpoint,
                container_name=container_name,
                container_ips=container_ips,
                networks=networks,
                note=note,
            )
        )

    if dev_server is not None and dev_server.running:
        services.append(
            StackServiceStatus(
                service="embedded-devnet",
                role="Embedded RPC",
                description="Tkinter client embedded prefunded execution devnet.",
                status="running",
                host_endpoint=dev_server.rpc_url,
                container_name=None,
                container_ips=(dev_server.host,),
                networks=(),
                note=f"chainId={dev_server.chain_id} mode={dev_server.mining_mode}",
            )
        )

    return StackSnapshot(
        project_name=env.get("COMPOSE_PROJECT_NAME", "python-ethereum-client"),
        host_ips=host_ip_addresses(),
        services=tuple(services),
        discovery_error=discovery_error,
    )


def compile_solidity_source(source_path: str, *, contract_name: str | None = None) -> dict[str, str]:
    solc_path = shutil.which("solc")
    if solc_path is None:
        raise RuntimeError("solc is not installed or not on PATH")

    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".sol":
        raise ValueError("source path must point to a .sol Solidity file")

    output_dir = Path(tempfile.mkdtemp(prefix="solc-artifacts-"))
    completed = subprocess.run(
        [solc_path, "--bin", "--abi", "-o", str(output_dir), str(source)],
        cwd=source.parent,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "solc failed"
        raise RuntimeError(detail)

    if contract_name is not None:
        artifact_path = output_dir / f"{contract_name}.bin"
        abi_path = output_dir / f"{contract_name}.abi"
        if not artifact_path.exists() or not abi_path.exists():
            available = ", ".join(path.stem for path in sorted(output_dir.glob("*.bin")))
            raise RuntimeError(f"contract {contract_name!r} was not produced by solc. Available artifacts: {available or 'none'}")
    else:
        artifacts = sorted(output_dir.glob("*.bin"))
        if not artifacts:
            raise RuntimeError("solc completed but did not emit any .bin artifacts")
        if len(artifacts) > 1:
            names = ", ".join(path.stem for path in artifacts)
            raise RuntimeError(f"multiple contracts were produced; set Contract Name first ({names})")
        artifact_path = artifacts[0]
        abi_path = artifact_path.with_suffix(".abi")

    return {
        "sourcePath": str(source),
        "outputDir": str(output_dir),
        "artifactPath": str(artifact_path),
        "abiPath": str(abi_path),
        "contractName": artifact_path.stem,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


class RpcHttpClient:
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

    def call(self, method: str, params: list[object] | None = None, *, request_id: int = 1) -> object | None:
        envelope = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": [] if params is None else params,
        }
        response = self.request(envelope)
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


class EmbeddedRpcServer:
    class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8546,
        chain_id: int = 1337,
        mining_mode: str = "instant",
    ) -> None:
        self.host = host
        self.port = port
        self.chain_id = chain_id
        self.mining_mode = mining_mode
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.node: ExecutionNode | None = None
        self.rpc_server: JsonRpcServer | None = None
        self.wallets: list[WalletProfile] = []

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def rpc_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> list[WalletProfile]:
        if self.running:
            return list(self.wallets)

        state = StateDB()
        wallets: list[WalletProfile] = []
        local_accounts: list[Address] = []
        for index, private_key in enumerate((1, 2, 3), start=1):
            address = address_from_private_key(private_key)
            state.set_balance(address, DEV_ACCOUNT_BALANCE)
            local_accounts.append(address)
            wallets.append(
                WalletProfile(
                    label=f"Dev Wallet {index}",
                    private_key=private_key,
                    address=address.to_hex(),
                )
            )

        node = ExecutionNode(
            chain_config=ChainConfig(chain_id=self.chain_id),
            compat_config=CompatibilityConfig(
                mining_mode=self.mining_mode,
                local_accounts=tuple(local_accounts),
                default_coinbase=local_accounts[0],
            ),
            state=state,
        )
        rpc_server = JsonRpcServer(node)
        self.node = node
        self.rpc_server = rpc_server

        started = threading.Event()
        errors: list[BaseException] = []

        def serve() -> None:
            cors_origin = rpc_server.context.compat.cors_allow_origin
            json_rpc = rpc_server

            class Handler(BaseHTTPRequestHandler):
                def _write(self, status: HTTPStatus, body: bytes | None) -> None:
                    self.send_response(int(status))
                    self.send_header("Access-Control-Allow-Origin", cors_origin)
                    self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type")
                    if body is None:
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_OPTIONS(self) -> None:  # noqa: N802
                    self._write(HTTPStatus.NO_CONTENT, None)

                def do_POST(self) -> None:  # noqa: N802
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    response = json_rpc.handle_json_bytes(body)
                    if response is None:
                        self._write(HTTPStatus.NO_CONTENT, None)
                        return
                    self._write(HTTPStatus.OK, response)

                def log_message(self, format: str, *args: object) -> None:
                    return

            try:
                httpd = self._ReusableThreadingHTTPServer((self.host, self.port), Handler)
                self._httpd = httpd
                started.set()
                httpd.serve_forever()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                started.set()

        self._thread = threading.Thread(target=serve, name="embedded-rpc-server", daemon=True)
        self._thread.start()
        started.wait(timeout=5)
        if errors:
            self._thread = None
            self._httpd = None
            raise RuntimeError(str(errors[0]))
        if self._httpd is None:
            self._thread = None
            raise RuntimeError("embedded RPC server failed to start")
        self.wallets = wallets
        return list(self.wallets)

    def stop(self) -> None:
        httpd = self._httpd
        thread = self._thread
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if thread is not None:
            thread.join(timeout=3)
        self._httpd = None
        self._thread = None
        self.node = None
        self.rpc_server = None

    def mine_blocks(
        self,
        *,
        beneficiary: str,
        reward: int,
        count: int,
        algorithm: str,
    ) -> list[dict[str, object]]:
        if not self.running or self.node is None:
            raise RuntimeError("embedded devnet is not running")
        miner = Address.from_hex(beneficiary)
        results: list[dict[str, object]] = []
        extra_data = f"mine:{algorithm}".encode("ascii")[:32]
        for _ in range(count):
            record = self.node.mine_block(
                beneficiary=miner,
                block_reward=max(0, int(reward)),
                allow_empty=True,
                extra_data=extra_data,
            )
            if record is None:
                break
            results.append(
                {
                    "number": hex(record.block.header.number),
                    "hash": record.block.hash().to_hex(),
                    "miner": record.block.header.coinbase.to_hex(),
                    "reward": hex(max(0, int(reward))),
                    "algorithm": algorithm,
                    "transactionCount": hex(len(record.transaction_records)),
                    "gasUsed": hex(record.block.header.gas_used),
                    "baseFeePerGas": hex(record.block.header.base_fee or 0),
                    "extraData": "0x" + record.block.header.extra_data.hex(),
                    "totalDifficulty": hex(record.total_difficulty),
                }
            )
        return results


def parse_int_value(value: str, *, label: str) -> int:
    cleaned = value.strip().replace("_", "")
    if not cleaned:
        raise ValueError(f"{label} is required")
    base = 16 if cleaned.startswith(("0x", "0X")) else 10
    try:
        return int(cleaned, base)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer or 0x-prefixed hex value") from exc


def parse_private_key(value: str) -> int:
    cleaned = value.strip().replace("_", "")
    if not cleaned:
        raise ValueError("private key is required")
    if cleaned.startswith(("0x", "0X")):
        normalized = cleaned
        radix = 16
    elif any(character in "abcdefABCDEF" for character in cleaned):
        normalized = cleaned
        radix = 16
    else:
        normalized = cleaned
        radix = 10
    try:
        private_key = int(normalized, radix)
    except ValueError as exc:
        raise ValueError("private key must be a decimal integer or hex string") from exc
    if not 1 <= private_key < SECP256K1_N:
        raise ValueError("private key is outside the secp256k1 range")
    return private_key


def pretty_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def hex_quantity_to_int(value: object, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith(("0x", "0X")) else int(value, 10)
    raise TypeError(f"cannot interpret {value!r} as a quantity")


class BlockchainDesktopClient(tk.Tk):
    def __init__(
        self,
        *,
        rpc_url: str,
        embedded_host: str,
        embedded_port: int,
        embedded_chain_id: int,
        start_devnet: bool,
    ) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1500x960")
        self.configure(bg=PALETTE["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.rpc_url_var = tk.StringVar(value=rpc_url)
        self.status_var = tk.StringVar(value="Ready.")
        self.client_version_var = tk.StringVar(value="Not connected")
        self.network_id_var = tk.StringVar(value="-")
        self.chain_id_var = tk.StringVar(value="-")
        self.block_number_var = tk.StringVar(value="-")
        self.gas_price_var = tk.StringVar(value="-")
        self.priority_fee_var = tk.StringVar(value="-")

        self.dev_host_var = tk.StringVar(value=embedded_host)
        self.dev_port_var = tk.StringVar(value=str(embedded_port))
        self.dev_chain_id_var = tk.StringVar(value=str(embedded_chain_id))
        self.dev_mode_var = tk.StringVar(value="instant")

        self.wallet_label_var = tk.StringVar()
        self.wallet_private_key_var = tk.StringVar()
        self.wallet_details_var = tk.StringVar(value="Select a wallet to load balance and nonce details.")

        self.transfer_type_var = tk.StringVar(value="EIP-1559")
        self.transfer_sender_var = tk.StringVar()
        self.transfer_to_var = tk.StringVar()
        self.transfer_amount_var = tk.StringVar(value="0")
        self.transfer_nonce_var = tk.StringVar()
        self.transfer_chain_id_var = tk.StringVar(value=str(embedded_chain_id))
        self.transfer_gas_limit_var = tk.StringVar(value="21000")
        self.transfer_gas_price_var = tk.StringVar()
        self.transfer_priority_fee_var = tk.StringVar()
        self.transfer_max_fee_var = tk.StringVar()

        self.contract_source_var = tk.StringVar()
        self.contract_artifact_var = tk.StringVar()
        self.contract_abi_var = tk.StringVar()
        self.contract_name_var = tk.StringVar()
        self.contract_sender_var = tk.StringVar()
        self.contract_constructor_args_var = tk.StringVar(value="[]")
        self.contract_tx_type_var = tk.StringVar(value="EIP-1559")
        self.contract_chain_id_var = tk.StringVar(value=str(embedded_chain_id))
        self.contract_gas_limit_var = tk.StringVar(value="800000")
        self.contract_value_var = tk.StringVar(value="0")
        self.contract_gas_price_var = tk.StringVar()
        self.contract_priority_fee_var = tk.StringVar()
        self.contract_max_fee_var = tk.StringVar()

        self.mining_algorithm_var = tk.StringVar(value="manual")
        self.mining_wallet_var = tk.StringVar()
        self.mining_reward_var = tk.StringVar(value="1000")
        self.mining_count_var = tk.StringVar(value="1")
        self.mining_allow_empty_var = tk.BooleanVar(value=True)
        self.mining_validators_var = tk.StringVar(value="24")
        self.mining_epochs_var = tk.StringVar(value="2")
        self.mining_nodes_var = tk.StringVar(value="10")
        self.mining_rounds_var = tk.StringVar(value="3")
        self.mining_byzantine_var = tk.StringVar(value="1")
        self.mining_degree_var = tk.StringVar(value="3")

        self.block_selector_var = tk.StringVar(value="latest")
        self.tx_hash_var = tk.StringVar()

        self.call_from_var = tk.StringVar()
        self.call_to_var = tk.StringVar()
        self.call_data_var = tk.StringVar(value="0x")
        self.call_value_var = tk.StringVar(value="0")
        self.call_gas_var = tk.StringVar()
        self.call_gas_price_var = tk.StringVar()
        self.call_max_fee_var = tk.StringVar()
        self.call_priority_fee_var = tk.StringVar()
        self.call_access_list_var = tk.StringVar()
        self.call_block_var = tk.StringVar(value="latest")
        self.trace_disable_memory_var = tk.BooleanVar(value=False)
        self.trace_disable_stack_var = tk.BooleanVar(value=False)
        self.trace_disable_storage_var = tk.BooleanVar(value=False)

        self.console_method_var = tk.StringVar(value="eth_blockNumber")

        self.wallets: dict[str, WalletProfile] = {}
        self.dev_server = EmbeddedRpcServer(
            host=embedded_host,
            port=embedded_port,
            chain_id=embedded_chain_id,
            mining_mode="instant",
        )

        self._build_style()
        self._build_layout()
        self.after(350, self.refresh_network_map)

        if start_devnet:
            self.after(200, self.start_devnet)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=PALETTE["bg"], foreground=PALETTE["text"])
        style.configure("TNotebook", background=PALETTE["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=PALETTE["surface"], padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", PALETTE["panel"])])
        style.configure("TFrame", background=PALETTE["bg"])
        style.configure("Card.TFrame", background=PALETTE["panel"], relief="solid", borderwidth=1)
        style.configure("TLabelframe", background=PALETTE["panel"], borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=PALETTE["panel"], foreground=PALETTE["accent"], font=("TkDefaultFont", 10, "bold"))
        style.configure("Header.TLabel", background=PALETTE["bg"], foreground=PALETTE["accent"], font=("TkDefaultFont", 18, "bold"))
        style.configure("Subheader.TLabel", background=PALETTE["bg"], foreground=PALETTE["muted"], font=("TkDefaultFont", 10))
        style.configure("MetricName.TLabel", background=PALETTE["panel"], foreground=PALETTE["muted"], font=("TkDefaultFont", 9, "bold"))
        style.configure("MetricValue.TLabel", background=PALETTE["panel"], foreground=PALETTE["text"], font=("TkDefaultFont", 12, "bold"))
        style.configure("Primary.TButton", background=PALETTE["accent"], foreground="#ffffff", padding=(12, 8))
        style.map("Primary.TButton", background=[("active", "#0b3947")])
        style.configure("Accent.TButton", background=PALETTE["accent_alt"], foreground="#ffffff", padding=(12, 8))
        style.map("Accent.TButton", background=[("active", "#bb5d05")])
        style.configure("Treeview", rowheight=28, fieldbackground="#ffffff", background="#ffffff", foreground=PALETTE["text"])
        style.configure("Treeview.Heading", background=PALETTE["surface"], foreground=PALETTE["text"], font=("TkDefaultFont", 9, "bold"))

    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text=APP_TITLE, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Desktop wallet, contract deployer, consensus mining lab, explorer, and network map for the Python Ethereum-like stack.",
            style="Subheader.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        self.connection_tab = ttk.Frame(notebook, padding=10)
        self.wallet_tab = ttk.Frame(notebook, padding=10)
        self.transfer_tab = ttk.Frame(notebook, padding=10)
        self.contracts_tab = ttk.Frame(notebook, padding=10)
        self.mining_tab = ttk.Frame(notebook, padding=10)
        self.explorer_tab = ttk.Frame(notebook, padding=10)
        self.call_tab = ttk.Frame(notebook, padding=10)
        self.console_tab = ttk.Frame(notebook, padding=10)
        self.network_tab = ttk.Frame(notebook, padding=10)

        notebook.add(self.connection_tab, text="Connection")
        notebook.add(self.wallet_tab, text="Wallets")
        notebook.add(self.transfer_tab, text="Transfer / Trade")
        notebook.add(self.contracts_tab, text="Contracts")
        notebook.add(self.mining_tab, text="Consensus / Mining")
        notebook.add(self.explorer_tab, text="Explorer")
        notebook.add(self.call_tab, text="Call / Trace")
        notebook.add(self.console_tab, text="RPC Console")
        notebook.add(self.network_tab, text="Network Map")

        self._build_connection_tab()
        self._build_wallet_tab()
        self._build_transfer_tab()
        self._build_contracts_tab()
        self._build_mining_tab()
        self._build_explorer_tab()
        self._build_call_tab()
        self._build_console_tab()
        self._build_network_tab()

        status_bar = ttk.Frame(outer, style="Card.TFrame", padding=(12, 8))
        status_bar.pack(fill="x", pady=(14, 0))
        ttk.Label(status_bar, textvariable=self.status_var, background=PALETTE["panel"], foreground=PALETTE["muted"]).pack(anchor="w")

    def _build_connection_tab(self) -> None:
        top = ttk.Frame(self.connection_tab)
        top.pack(fill="x")

        rpc_frame = ttk.LabelFrame(top, text="RPC Endpoint", padding=14)
        rpc_frame.pack(fill="x")

        ttk.Label(rpc_frame, text="RPC URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(rpc_frame, textvariable=self.rpc_url_var, width=70).grid(row=0, column=1, sticky="ew", padx=(10, 10))
        ttk.Button(rpc_frame, text="Connect / Refresh", style="Primary.TButton", command=self.refresh_dashboard).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(rpc_frame, text="Load Suggested Fees", command=self.load_transaction_defaults).grid(row=0, column=3)
        rpc_frame.columnconfigure(1, weight=1)

        metrics = ttk.Frame(self.connection_tab)
        metrics.pack(fill="x", pady=(14, 14))
        for index, (name, variable) in enumerate(
            (
                ("Client", self.client_version_var),
                ("Network ID", self.network_id_var),
                ("Chain ID", self.chain_id_var),
                ("Block", self.block_number_var),
                ("Gas Price", self.gas_price_var),
                ("Priority Fee", self.priority_fee_var),
            )
        ):
            card = ttk.Frame(metrics, style="Card.TFrame", padding=14)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            ttk.Label(card, text=name, style="MetricName.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="MetricValue.TLabel").pack(anchor="w", pady=(6, 0))
            metrics.columnconfigure(index, weight=1)

        lower = ttk.Frame(self.connection_tab)
        lower.pack(fill="both", expand=True)

        dev_frame = ttk.LabelFrame(lower, text="Embedded Devnet", padding=14)
        dev_frame.pack(side="left", fill="both", expand=False, padx=(0, 12))

        ttk.Label(dev_frame, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Entry(dev_frame, textvariable=self.dev_host_var, width=16).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(dev_frame, text="Port").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(dev_frame, textvariable=self.dev_port_var, width=16).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(dev_frame, text="Chain ID").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(dev_frame, textvariable=self.dev_chain_id_var, width=16).grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Label(dev_frame, text="Mode").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(dev_frame, textvariable=self.dev_mode_var, values=("instant", "mempool"), state="readonly", width=14).grid(
            row=3, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        ttk.Button(dev_frame, text="Start Embedded Devnet", style="Accent.TButton", command=self.start_devnet).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(14, 8)
        )
        ttk.Button(dev_frame, text="Stop Embedded Devnet", command=self.stop_devnet).grid(row=5, column=0, columnspan=2, sticky="ew")

        ttk.Label(
            dev_frame,
            text="This starts a local RPC node with three prefunded dev wallets so you can send native-coin transfers immediately.",
            wraplength=320,
            justify="left",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 8))

        self.dev_accounts_text = ScrolledText(dev_frame, width=44, height=18, wrap="word", font=("TkFixedFont", 9))
        self.dev_accounts_text.grid(row=7, column=0, columnspan=2, sticky="nsew")
        self.dev_accounts_text.insert("1.0", "Start the embedded devnet to expose prefunded wallets here.")
        self.dev_accounts_text.configure(state="disabled")
        dev_frame.columnconfigure(1, weight=1)
        dev_frame.rowconfigure(7, weight=1)

        latest_frame = ttk.LabelFrame(lower, text="Latest Block Snapshot", padding=14)
        latest_frame.pack(side="left", fill="both", expand=True)
        self.latest_block_text = ScrolledText(latest_frame, wrap="none", font=("TkFixedFont", 9))
        self.latest_block_text.pack(fill="both", expand=True)
        self.latest_block_text.insert("1.0", "Connect to an RPC endpoint to load chain state.")
        self.latest_block_text.configure(state="disabled")

    def _build_wallet_tab(self) -> None:
        top = ttk.Frame(self.wallet_tab)
        top.pack(fill="x")

        import_frame = ttk.LabelFrame(top, text="Import Wallet", padding=14)
        import_frame.pack(fill="x")
        ttk.Label(import_frame, text="Label").grid(row=0, column=0, sticky="w")
        ttk.Entry(import_frame, textvariable=self.wallet_label_var, width=24).grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(import_frame, text="Private Key").grid(row=0, column=2, sticky="w")
        ttk.Entry(import_frame, textvariable=self.wallet_private_key_var, width=56, show="*").grid(row=0, column=3, sticky="ew", padx=(8, 12))
        ttk.Button(import_frame, text="Import", style="Primary.TButton", command=self.import_wallet_from_form).grid(row=0, column=4)
        ttk.Label(
            import_frame,
            text="Accepts decimal dev keys such as 1, 2, 3 or full hex keys such as 0xabc123...",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(10, 0))
        import_frame.columnconfigure(1, weight=1)
        import_frame.columnconfigure(3, weight=1)

        content = ttk.Frame(self.wallet_tab)
        content.pack(fill="both", expand=True, pady=(14, 0))

        left = ttk.Frame(content)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(content)
        right.pack(side="left", fill="both", expand=False, padx=(14, 0))

        tree_frame = ttk.LabelFrame(left, text="Loaded Wallets", padding=14)
        tree_frame.pack(fill="both", expand=True)
        self.wallet_tree = ttk.Treeview(tree_frame, columns=("label", "address"), show="headings", selectmode="browse")
        self.wallet_tree.heading("label", text="Label")
        self.wallet_tree.heading("address", text="Address")
        self.wallet_tree.column("label", width=180, anchor="w")
        self.wallet_tree.column("address", width=460, anchor="w")
        self.wallet_tree.pack(fill="both", expand=True)
        self.wallet_tree.bind("<<TreeviewSelect>>", self._handle_wallet_selection)

        wallet_actions = ttk.Frame(tree_frame)
        wallet_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(wallet_actions, text="Refresh Selected", command=self.refresh_selected_wallet).pack(side="left")
        ttk.Button(wallet_actions, text="Use As Sender", command=self.use_selected_wallet_as_sender).pack(side="left", padx=(8, 0))
        ttk.Button(wallet_actions, text="Use As Recipient", command=self.use_selected_wallet_as_recipient).pack(side="left", padx=(8, 0))
        ttk.Button(wallet_actions, text="Remove", command=self.remove_selected_wallet).pack(side="left", padx=(8, 0))

        detail_frame = ttk.LabelFrame(right, text="Wallet Detail", padding=14)
        detail_frame.pack(fill="both", expand=True)
        ttk.Label(
            detail_frame,
            textvariable=self.wallet_details_var,
            background=PALETTE["panel"],
            foreground=PALETTE["text"],
            justify="left",
            wraplength=340,
        ).pack(anchor="w")

    def _build_transfer_tab(self) -> None:
        top = ttk.LabelFrame(self.transfer_tab, text="Native Transfer Builder", padding=14)
        top.pack(fill="x")

        ttk.Label(top, text="Transfer Type").grid(row=0, column=0, sticky="w")
        ttk.Combobox(top, textvariable=self.transfer_type_var, values=("EIP-1559", "Legacy"), state="readonly", width=18).grid(
            row=0, column=1, sticky="ew", padx=(8, 14)
        )
        ttk.Label(top, text="Sender").grid(row=0, column=2, sticky="w")
        self.transfer_sender_combo = ttk.Combobox(top, textvariable=self.transfer_sender_var, values=(), width=48)
        self.transfer_sender_combo.grid(row=0, column=3, sticky="ew", padx=(8, 14))
        ttk.Button(top, text="Suggest Fees / Nonce", command=self.load_transaction_defaults).grid(row=0, column=4)

        ttk.Label(top, text="Recipient").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.transfer_recipient_combo = ttk.Combobox(top, textvariable=self.transfer_to_var, values=(), width=48)
        self.transfer_recipient_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Amount").grid(row=1, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_amount_var, width=18).grid(row=1, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(top, text="Nonce").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_nonce_var, width=18).grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Chain ID").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_chain_id_var, width=18).grid(row=2, column=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Gas Limit").grid(row=2, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_gas_limit_var, width=18).grid(row=2, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(top, text="Gas Price").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_gas_price_var, width=18).grid(row=3, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Max Priority Fee").grid(row=3, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_priority_fee_var, width=18).grid(row=3, column=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Max Fee").grid(row=3, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.transfer_max_fee_var, width=18).grid(row=3, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        button_row = ttk.Frame(top)
        button_row.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(16, 0))
        ttk.Button(button_row, text="Preview Signed Transaction", style="Primary.TButton", command=self.preview_transfer).pack(side="left")
        ttk.Button(button_row, text="Send Signed Transaction", style="Accent.TButton", command=self.send_transfer).pack(side="left", padx=(8, 0))
        ttk.Label(
            button_row,
            text="This tab handles native-coin transfers. The current chain does not expose an exchange or order book.",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).pack(side="left", padx=(14, 0))

        for column in range(6):
            top.columnconfigure(column, weight=1)

        output_frame = ttk.LabelFrame(self.transfer_tab, text="Transfer Output", padding=14)
        output_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.transfer_output_text = ScrolledText(output_frame, wrap="word", font=("TkFixedFont", 9))
        self.transfer_output_text.pack(fill="both", expand=True)
        self.transfer_output_text.insert(
            "1.0",
            "Choose a sender wallet, set recipient and amount, then preview or send a signed native transfer.",
        )
        self.transfer_output_text.configure(state="disabled")

    def _build_contracts_tab(self) -> None:
        source_frame = ttk.LabelFrame(self.contracts_tab, text="Solidity / Artifact Inputs", padding=14)
        source_frame.pack(fill="x")

        ttk.Label(source_frame, text="Solidity Source").grid(row=0, column=0, sticky="w")
        ttk.Entry(source_frame, textvariable=self.contract_source_var, width=72).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(source_frame, text="Browse", command=self.browse_contract_source).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(source_frame, text="Compile With solc", style="Primary.TButton", command=self.compile_contract_source).grid(row=0, column=3)

        ttk.Label(source_frame, text="Artifact (.bin/.json)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(source_frame, textvariable=self.contract_artifact_var, width=72).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(10, 0))
        ttk.Button(source_frame, text="Browse", command=self.browse_contract_artifact).grid(row=1, column=2, padx=(0, 8), pady=(10, 0))
        ttk.Label(source_frame, text="ABI (.abi/.json)").grid(row=1, column=3, sticky="w", pady=(10, 0))
        ttk.Entry(source_frame, textvariable=self.contract_abi_var, width=44).grid(row=1, column=4, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Button(source_frame, text="Browse", command=self.browse_contract_abi).grid(row=1, column=5, padx=(8, 0), pady=(10, 0))

        ttk.Label(source_frame, text="Contract Name").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(source_frame, textvariable=self.contract_name_var, width=24).grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(10, 0))
        ttk.Label(
            source_frame,
            text="Use Contract Name when a JSON artifact or Solidity source emits multiple contracts.",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).grid(row=2, column=3, columnspan=3, sticky="w", pady=(10, 0))

        source_frame.columnconfigure(1, weight=1)
        source_frame.columnconfigure(4, weight=1)

        deploy_frame = ttk.LabelFrame(self.contracts_tab, text="Deploy Contract", padding=14)
        deploy_frame.pack(fill="x", pady=(14, 0))

        ttk.Label(deploy_frame, text="Signer").grid(row=0, column=0, sticky="w")
        self.contract_sender_combo = ttk.Combobox(deploy_frame, textvariable=self.contract_sender_var, values=(), width=54)
        self.contract_sender_combo.grid(row=0, column=1, sticky="ew", padx=(8, 14))
        ttk.Label(deploy_frame, text="Tx Type").grid(row=0, column=2, sticky="w")
        ttk.Combobox(deploy_frame, textvariable=self.contract_tx_type_var, values=("EIP-1559", "Legacy"), state="readonly", width=18).grid(
            row=0, column=3, sticky="ew", padx=(8, 14)
        )
        ttk.Button(deploy_frame, text="Load Fee Defaults", command=self.load_contract_defaults).grid(row=0, column=4)

        ttk.Label(deploy_frame, text="Constructor Args JSON").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_constructor_args_var, width=54).grid(
            row=1, column=1, sticky="ew", padx=(8, 14), pady=(10, 0)
        )
        ttk.Label(deploy_frame, text="Chain ID").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_chain_id_var, width=18).grid(row=1, column=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(deploy_frame, text="Value").grid(row=1, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_value_var, width=18).grid(row=1, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(deploy_frame, text="Gas Limit").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_gas_limit_var, width=18).grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(deploy_frame, text="Gas Price").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_gas_price_var, width=18).grid(row=2, column=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(deploy_frame, text="Max Priority Fee").grid(row=2, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_priority_fee_var, width=18).grid(row=2, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(deploy_frame, text="Max Fee").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(deploy_frame, textvariable=self.contract_max_fee_var, width=18).grid(row=3, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))

        contract_buttons = ttk.Frame(deploy_frame)
        contract_buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(16, 0))
        ttk.Button(contract_buttons, text="Preview Deployment", style="Primary.TButton", command=self.preview_contract_deploy).pack(side="left")
        ttk.Button(contract_buttons, text="Deploy Contract", style="Accent.TButton", command=self.deploy_contract).pack(side="left", padx=(8, 0))
        ttk.Label(
            contract_buttons,
            text="Solidity compilation requires a local solc install. Deployment itself uses the execution JSON-RPC API.",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).pack(side="left", padx=(14, 0))

        for column in range(6):
            deploy_frame.columnconfigure(column, weight=1)

        output_frame = ttk.LabelFrame(self.contracts_tab, text="Contract Output", padding=14)
        output_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.contract_output_text = ScrolledText(output_frame, wrap="none", font=("TkFixedFont", 9))
        self.contract_output_text.pack(fill="both", expand=True)
        self.contract_output_text.insert(
            "1.0",
            "Compile a Solidity file with solc or point at a compiled artifact, then preview or deploy the contract.",
        )
        self.contract_output_text.configure(state="disabled")

    def _build_mining_tab(self) -> None:
        top = ttk.LabelFrame(self.mining_tab, text="Consensus Mining Lab", padding=14)
        top.pack(fill="x")

        ttk.Label(top, text="Algorithm").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            top,
            textvariable=self.mining_algorithm_var,
            values=[name for name, _ in CONSENSUS_ALGORITHMS],
            state="readonly",
            width=24,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 14))
        ttk.Label(top, text="Miner Wallet / Address").grid(row=0, column=2, sticky="w")
        self.mining_wallet_combo = ttk.Combobox(top, textvariable=self.mining_wallet_var, values=(), width=54)
        self.mining_wallet_combo.grid(row=0, column=3, sticky="ew", padx=(8, 14))
        ttk.Label(top, text="Reward").grid(row=0, column=4, sticky="w")
        ttk.Entry(top, textvariable=self.mining_reward_var, width=18).grid(row=0, column=5, sticky="ew", padx=(8, 0))

        ttk.Label(top, text="Block Count").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_count_var, width=18).grid(row=1, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Checkbutton(top, text="Allow Empty Blocks", variable=self.mining_allow_empty_var).grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Label(
            top,
            text="Hybrid Beacon",
            background=PALETTE["panel"],
            foreground=PALETTE["accent"],
        ).grid(row=1, column=3, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_validators_var, width=10).grid(row=1, column=4, sticky="ew", padx=(8, 4), pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_epochs_var, width=10).grid(row=1, column=5, sticky="ew", padx=(4, 0), pady=(10, 0))

        ttk.Label(top, text="PBFT Nodes").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_nodes_var, width=18).grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Rounds").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_rounds_var, width=18).grid(row=2, column=3, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(top, text="Byzantine").grid(row=2, column=4, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_byzantine_var, width=18).grid(row=2, column=5, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(top, text="PBFT Degree").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(top, textvariable=self.mining_degree_var, width=18).grid(row=3, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))

        mining_buttons = ttk.Frame(top)
        mining_buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(16, 0))
        ttk.Button(mining_buttons, text="Run Consensus Only", command=self.run_consensus_only).pack(side="left")
        ttk.Button(mining_buttons, text="Mine Reward Block", style="Accent.TButton", command=self.mine_reward_block).pack(side="left", padx=(8, 0))
        ttk.Label(
            mining_buttons,
            text="Consensus simulations are local research workloads. Reward blocks use the execution dev_mine extension and are not canonical chain consensus.",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).pack(side="left", padx=(14, 0))

        for column in range(6):
            top.columnconfigure(column, weight=1)

        output_frame = ttk.LabelFrame(self.mining_tab, text="Consensus / Mining Output", padding=14)
        output_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.mining_output_text = ScrolledText(output_frame, wrap="none", font=("TkFixedFont", 9))
        self.mining_output_text.pack(fill="both", expand=True)
        self.mining_output_text.insert(
            "1.0",
            "Choose a consensus simulation mode, run it locally, and optionally mint a reward block through the execution dev_mine API.",
        )
        self.mining_output_text.configure(state="disabled")

    def _build_network_tab(self) -> None:
        controls = ttk.LabelFrame(self.network_tab, text="Stack Discovery", padding=14)
        controls.pack(fill="x")
        ttk.Button(controls, text="Refresh Network Map", style="Primary.TButton", command=self.refresh_network_map).pack(side="left")
        ttk.Label(
            controls,
            text="This inspects docker compose when possible and falls back to the configured topology from docker-compose.yml and .env.",
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
        ).pack(side="left", padx=(12, 0))

        content = ttk.Frame(self.network_tab)
        content.pack(fill="both", expand=True, pady=(14, 0))

        left = ttk.Frame(content)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(content)
        right.pack(side="left", fill="both", expand=False, padx=(14, 0))

        table_frame = ttk.LabelFrame(left, text="Service Status", padding=14)
        table_frame.pack(fill="both", expand=True)
        self.network_tree = ttk.Treeview(
            table_frame,
            columns=("service", "status", "role", "endpoint", "ips"),
            show="headings",
            selectmode="browse",
        )
        for column, heading, width in (
            ("service", "Service", 180),
            ("status", "Status", 120),
            ("role", "Role", 170),
            ("endpoint", "Host Endpoint", 240),
            ("ips", "Container IPs", 220),
        ):
            self.network_tree.heading(column, text=heading)
            self.network_tree.column(column, width=width, anchor="w")
        self.network_tree.pack(fill="both", expand=True)

        canvas_frame = ttk.LabelFrame(right, text="Topology Diagram", padding=14)
        canvas_frame.pack(fill="both", expand=False)
        self.network_canvas = tk.Canvas(canvas_frame, width=420, height=640, bg=PALETTE["panel"], highlightthickness=0)
        self.network_canvas.pack(fill="both", expand=True)

        detail_frame = ttk.LabelFrame(right, text="Discovery Detail", padding=14)
        detail_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.network_output_text = ScrolledText(detail_frame, width=50, height=20, wrap="word", font=("TkFixedFont", 9))
        self.network_output_text.pack(fill="both", expand=True)
        self.network_output_text.insert("1.0", "Refresh the network map to inspect the configured and currently running stack.")
        self.network_output_text.configure(state="disabled")

    def _build_explorer_tab(self) -> None:
        controls = ttk.LabelFrame(self.explorer_tab, text="Lookup", padding=14)
        controls.pack(fill="x")

        ttk.Label(controls, text="Block Selector").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.block_selector_var, width=18).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        ttk.Button(controls, text="Fetch Block", style="Primary.TButton", command=self.fetch_block).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(controls, text="Latest", command=lambda: self.block_selector_var.set("latest")).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(controls, text="Pending", command=lambda: self.block_selector_var.set("pending")).grid(row=0, column=4)

        ttk.Label(controls, text="Transaction Hash").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(controls, textvariable=self.tx_hash_var, width=78).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 12), pady=(10, 0))
        ttk.Button(controls, text="Fetch Tx", command=self.fetch_transaction).grid(row=1, column=3, pady=(10, 0), padx=(0, 8))
        ttk.Button(controls, text="Receipt", command=self.fetch_receipt).grid(row=1, column=4, pady=(10, 0), padx=(0, 8))
        ttk.Button(controls, text="Trace", command=self.trace_transaction).grid(row=1, column=5, pady=(10, 0))
        controls.columnconfigure(1, weight=1)

        output = ttk.LabelFrame(self.explorer_tab, text="Explorer Output", padding=14)
        output.pack(fill="both", expand=True, pady=(14, 0))
        self.explorer_output_text = ScrolledText(output, wrap="none", font=("TkFixedFont", 9))
        self.explorer_output_text.pack(fill="both", expand=True)
        self.explorer_output_text.insert("1.0", "Block, transaction, receipt, and trace results will appear here.")
        self.explorer_output_text.configure(state="disabled")

    def _build_call_tab(self) -> None:
        form = ttk.LabelFrame(self.call_tab, text="Contract Call / Trace Builder", padding=14)
        form.pack(fill="x")

        ttk.Label(form, text="From").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.call_from_var, width=42).grid(row=0, column=1, sticky="ew", padx=(8, 14))
        ttk.Label(form, text="To").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.call_to_var, width=42).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="Data").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_data_var, width=42).grid(row=1, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(form, text="Value").grid(row=1, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_value_var, width=42).grid(row=1, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(form, text="Gas").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_gas_var, width=42).grid(row=2, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(form, text="Gas Price").grid(row=2, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_gas_price_var, width=42).grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(form, text="Max Fee").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_max_fee_var, width=42).grid(row=3, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(form, text="Max Priority Fee").grid(row=3, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_priority_fee_var, width=42).grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(form, text="Access List JSON").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_access_list_var, width=42).grid(row=4, column=1, sticky="ew", padx=(8, 14), pady=(10, 0))
        ttk.Label(form, text="Block Selector").grid(row=4, column=2, sticky="w", pady=(10, 0))
        ttk.Entry(form, textvariable=self.call_block_var, width=42).grid(row=4, column=3, sticky="ew", padx=(8, 0), pady=(10, 0))

        options = ttk.Frame(form)
        options.grid(row=5, column=0, columnspan=4, sticky="w", pady=(12, 0))
        ttk.Checkbutton(options, text="Disable Memory", variable=self.trace_disable_memory_var).pack(side="left")
        ttk.Checkbutton(options, text="Disable Stack", variable=self.trace_disable_stack_var).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(options, text="Disable Storage", variable=self.trace_disable_storage_var).pack(side="left", padx=(10, 0))

        buttons = ttk.Frame(form)
        buttons.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="eth_call", style="Primary.TButton", command=self.run_eth_call).pack(side="left")
        ttk.Button(buttons, text="eth_estimateGas", command=self.run_estimate_gas).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="debug_traceCall", command=self.run_trace_call).pack(side="left", padx=(8, 0))

        for column in range(4):
            form.columnconfigure(column, weight=1)

        output = ttk.LabelFrame(self.call_tab, text="Call / Trace Output", padding=14)
        output.pack(fill="both", expand=True, pady=(14, 0))
        self.call_output_text = ScrolledText(output, wrap="none", font=("TkFixedFont", 9))
        self.call_output_text.pack(fill="both", expand=True)
        self.call_output_text.insert("1.0", "Use this tab to run eth_call, estimate gas, or capture opcode traces.")
        self.call_output_text.configure(state="disabled")

    def _build_console_tab(self) -> None:
        controls = ttk.LabelFrame(self.console_tab, text="Raw JSON-RPC", padding=14)
        controls.pack(fill="x")

        ttk.Label(controls, text="Method").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.console_method_var, width=36).grid(row=0, column=1, sticky="ew", padx=(8, 14))
        ttk.Button(controls, text="Execute", style="Primary.TButton", command=self.run_console_request).grid(row=0, column=2)
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Params JSON Array").grid(row=1, column=0, sticky="nw", pady=(10, 0))
        self.console_params_text = ScrolledText(controls, width=90, height=8, font=("TkFixedFont", 9))
        self.console_params_text.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(10, 0))
        self.console_params_text.insert("1.0", "[]")

        output = ttk.LabelFrame(self.console_tab, text="RPC Response", padding=14)
        output.pack(fill="both", expand=True, pady=(14, 0))
        self.console_output_text = ScrolledText(output, wrap="none", font=("TkFixedFont", 9))
        self.console_output_text.pack(fill="both", expand=True)
        self.console_output_text.insert("1.0", "Run any supported JSON-RPC method directly from this tab.")
        self.console_output_text.configure(state="disabled")

    def _on_close(self) -> None:
        try:
            self.dev_server.stop()
        finally:
            self.destroy()

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def require_client(self) -> RpcHttpClient:
        rpc_url = self.rpc_url_var.get().strip()
        if not rpc_url:
            raise ValueError("RPC URL is required")
        return RpcHttpClient(rpc_url)

    def run_background(
        self,
        description: str,
        task: callable,
        *,
        on_success: callable | None = None,
    ) -> None:
        self.set_status(description)

        def worker() -> None:
            try:
                result = task()
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._report_background_error(exc))
                return

            def finish() -> None:
                if on_success is not None:
                    on_success(result)
                self.set_status("Ready.")

            self.after(0, finish)

        threading.Thread(target=worker, name="desktop-client-worker", daemon=True).start()

    def _report_background_error(self, exc: Exception) -> None:
        self.set_status(f"Error: {exc}")
        messagebox.showerror(APP_TITLE, str(exc))

    def set_text(self, widget: ScrolledText, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def refresh_dashboard(self) -> None:
        def task() -> dict[str, object]:
            client = self.require_client()
            responses = client.batch(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "web3_clientVersion", "params": []},
                    {"jsonrpc": "2.0", "id": 2, "method": "net_version", "params": []},
                    {"jsonrpc": "2.0", "id": 3, "method": "eth_chainId", "params": []},
                    {"jsonrpc": "2.0", "id": 4, "method": "eth_blockNumber", "params": []},
                    {"jsonrpc": "2.0", "id": 5, "method": "eth_gasPrice", "params": []},
                    {"jsonrpc": "2.0", "id": 6, "method": "eth_maxPriorityFeePerGas", "params": []},
                    {"jsonrpc": "2.0", "id": 7, "method": "eth_getBlockByNumber", "params": ["latest", True]},
                ]
            )
            indexed: dict[int, object] = {}
            for response in responses:
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, dict):
                        raise RpcCallError(str(error.get("message", "RPC batch call failed")), code=error.get("code"), data=error.get("data"))
                    raise RpcCallError("RPC batch call failed")
                indexed[int(response["id"])] = response.get("result")
            return indexed

        def on_success(indexed: dict[int, object]) -> None:
            self.client_version_var.set(str(indexed.get(1, "-")))
            self.network_id_var.set(str(indexed.get(2, "-")))
            self.chain_id_var.set(str(indexed.get(3, "-")))
            self.block_number_var.set(str(indexed.get(4, "-")))
            self.gas_price_var.set(str(indexed.get(5, "-")))
            self.priority_fee_var.set(str(indexed.get(6, "-")))
            latest_block = indexed.get(7, {})
            self.set_text(self.latest_block_text, pretty_json(latest_block))

        self.run_background("Refreshing dashboard...", task, on_success=on_success)

    def start_devnet(self) -> None:
        def task() -> list[WalletProfile]:
            host = self.dev_host_var.get().strip() or "127.0.0.1"
            port = parse_int_value(self.dev_port_var.get(), label="devnet port")
            chain_id = parse_int_value(self.dev_chain_id_var.get(), label="devnet chain ID")
            mining_mode = self.dev_mode_var.get().strip() or "instant"
            if mining_mode not in {"instant", "mempool"}:
                raise ValueError("devnet mode must be instant or mempool")
            self.dev_server.stop()
            self.dev_server = EmbeddedRpcServer(host=host, port=port, chain_id=chain_id, mining_mode=mining_mode)
            return self.dev_server.start()

        def on_success(wallets: list[WalletProfile]) -> None:
            self.rpc_url_var.set(self.dev_server.rpc_url)
            lines = [
                f"Embedded RPC: {self.dev_server.rpc_url}",
                f"Chain ID: {self.dev_server.chain_id}",
                f"Mode: {self.dev_server.mining_mode}",
                "",
            ]
            for wallet in wallets:
                self.upsert_wallet(wallet, announce=False)
                lines.extend(
                    [
                        wallet.combo_label,
                        f"  Address: {wallet.address}",
                        f"  Private Key (decimal): {wallet.private_key}",
                        f"  Private Key (hex): {wallet.private_key_hex}",
                        f"  Starting Balance: {DEV_ACCOUNT_BALANCE}",
                        "",
                    ]
                )
            self.set_text(self.dev_accounts_text, "\n".join(lines).strip())
            if wallets:
                self.transfer_sender_var.set(wallets[0].combo_label)
                self.contract_sender_var.set(wallets[0].combo_label)
                self.mining_wallet_var.set(wallets[0].combo_label)
                self.call_from_var.set(wallets[0].address)
            if len(wallets) > 1:
                self.transfer_to_var.set(wallets[1].address)
                self.call_to_var.set(wallets[1].address)
            self.transfer_chain_id_var.set(str(self.dev_server.chain_id))
            self.refresh_dashboard()
            self.refresh_network_map()

        self.run_background("Starting embedded devnet...", task, on_success=on_success)

    def stop_devnet(self) -> None:
        def task() -> None:
            self.dev_server.stop()
            return None

        def on_success(_: None) -> None:
            self.set_text(self.dev_accounts_text, "Embedded devnet stopped.")
            self.refresh_network_map()

        self.run_background("Stopping embedded devnet...", task, on_success=on_success)

    def import_wallet_from_form(self) -> None:
        try:
            private_key = parse_private_key(self.wallet_private_key_var.get())
            label = self.wallet_label_var.get().strip() or f"Wallet {len(self.wallets) + 1}"
            address = address_from_private_key(private_key).to_hex()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.upsert_wallet(WalletProfile(label=label, private_key=private_key, address=address))
        self.wallet_private_key_var.set("")
        self.wallet_label_var.set("")

    def upsert_wallet(self, profile: WalletProfile, *, announce: bool = True) -> None:
        self.wallets[profile.address] = profile
        existing_item = None
        for item_id in self.wallet_tree.get_children():
            address = self.wallet_tree.set(item_id, "address")
            if address == profile.address:
                existing_item = item_id
                break
        if existing_item is None:
            self.wallet_tree.insert("", "end", iid=profile.address, values=(profile.label, profile.address))
        else:
            self.wallet_tree.item(existing_item, values=(profile.label, profile.address))
        self._refresh_wallet_choices()
        if announce:
            self.set_status(f"Loaded wallet {profile.label}.")

    def _refresh_wallet_choices(self) -> None:
        values = [profile.combo_label for profile in self.wallets.values()]
        self.transfer_sender_combo.configure(values=values)
        self.transfer_recipient_combo.configure(values=[profile.address for profile in self.wallets.values()])
        self.contract_sender_combo.configure(values=values)
        self.mining_wallet_combo.configure(values=[*values, *[profile.address for profile in self.wallets.values()]])

    def selected_wallet(self) -> WalletProfile | None:
        selection = self.wallet_tree.selection()
        if not selection:
            return None
        address = selection[0]
        return self.wallets.get(address)

    def _handle_wallet_selection(self, _: object) -> None:
        profile = self.selected_wallet()
        if profile is None:
            return
        self.wallet_details_var.set(
            "\n".join(
                [
                    f"Label: {profile.label}",
                    f"Address: {profile.address}",
                    f"Private Key (hex): {profile.private_key_hex}",
                    "",
                    "Use Refresh Selected to pull current balance and nonce values from the active RPC endpoint.",
                ]
            )
        )

    def refresh_selected_wallet(self) -> None:
        profile = self.selected_wallet()
        if profile is None:
            messagebox.showinfo(APP_TITLE, "Select a wallet first.")
            return

        def task() -> dict[str, object]:
            client = self.require_client()
            responses = client.batch(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [profile.address, "latest"]},
                    {"jsonrpc": "2.0", "id": 2, "method": "eth_getBalance", "params": [profile.address, "pending"]},
                    {"jsonrpc": "2.0", "id": 3, "method": "eth_getTransactionCount", "params": [profile.address, "latest"]},
                    {"jsonrpc": "2.0", "id": 4, "method": "eth_getTransactionCount", "params": [profile.address, "pending"]},
                ]
            )
            indexed: dict[int, object] = {}
            for response in responses:
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, dict):
                        raise RpcCallError(str(error.get("message", "RPC wallet lookup failed")), code=error.get("code"), data=error.get("data"))
                    raise RpcCallError("RPC wallet lookup failed")
                indexed[int(response["id"])] = response.get("result")
            return indexed

        def on_success(indexed: dict[int, object]) -> None:
            latest_balance = hex_quantity_to_int(indexed.get(1))
            pending_balance = hex_quantity_to_int(indexed.get(2))
            latest_nonce = hex_quantity_to_int(indexed.get(3))
            pending_nonce = hex_quantity_to_int(indexed.get(4))
            self.wallet_details_var.set(
                "\n".join(
                    [
                        f"Label: {profile.label}",
                        f"Address: {profile.address}",
                        f"Private Key (hex): {profile.private_key_hex}",
                        "",
                        f"Latest Balance: {latest_balance}",
                        f"Pending Balance: {pending_balance}",
                        f"Latest Nonce: {latest_nonce}",
                        f"Pending Nonce: {pending_nonce}",
                    ]
                )
            )

        self.run_background(f"Refreshing wallet {profile.label}...", task, on_success=on_success)

    def use_selected_wallet_as_sender(self) -> None:
        profile = self.selected_wallet()
        if profile is None:
            messagebox.showinfo(APP_TITLE, "Select a wallet first.")
            return
        self.transfer_sender_var.set(profile.combo_label)
        self.contract_sender_var.set(profile.combo_label)
        self.mining_wallet_var.set(profile.combo_label)
        self.call_from_var.set(profile.address)
        self.set_status(f"Using {profile.label} as the current sender.")

    def use_selected_wallet_as_recipient(self) -> None:
        profile = self.selected_wallet()
        if profile is None:
            messagebox.showinfo(APP_TITLE, "Select a wallet first.")
            return
        self.transfer_to_var.set(profile.address)
        self.call_to_var.set(profile.address)
        self.set_status(f"Using {profile.label} as the current recipient.")

    def remove_selected_wallet(self) -> None:
        profile = self.selected_wallet()
        if profile is None:
            messagebox.showinfo(APP_TITLE, "Select a wallet first.")
            return
        self.wallets.pop(profile.address, None)
        self.wallet_tree.delete(profile.address)
        self._refresh_wallet_choices()
        self.wallet_details_var.set("Wallet removed.")

    def resolve_loaded_wallet(self, selected: str) -> WalletProfile | None:
        normalized = selected.strip()
        if not normalized:
            return None
        for profile in self.wallets.values():
            if normalized == profile.combo_label or normalized == profile.address:
                return profile
        return None

    def resolve_sender_wallet(self) -> WalletProfile:
        selected = self.transfer_sender_var.get().strip()
        if not selected:
            raise ValueError("select a sender wallet")
        profile = self.resolve_loaded_wallet(selected)
        if profile is not None:
            return profile
        raise ValueError("sender wallet is not loaded in the client")

    def resolve_contract_wallet(self) -> WalletProfile:
        selected = self.contract_sender_var.get().strip()
        if not selected:
            raise ValueError("select a contract signer wallet")
        profile = self.resolve_loaded_wallet(selected)
        if profile is not None:
            return profile
        raise ValueError("contract signer wallet is not loaded in the client")

    def resolve_miner_address(self) -> str:
        selected = self.mining_wallet_var.get().strip()
        if not selected:
            raise ValueError("select a miner wallet or enter an address")
        profile = self.resolve_loaded_wallet(selected)
        if profile is not None:
            return profile.address
        return Address.from_hex(selected).to_hex()

    def browse_contract_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Solidity Source",
            initialdir=str(ROOT),
            filetypes=(("Solidity", "*.sol"), ("All files", "*.*")),
        )
        if selected:
            self.contract_source_var.set(selected)

    def browse_contract_artifact(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Compiled Contract Artifact",
            initialdir=str(ROOT),
            filetypes=(("Contract artifacts", "*.bin *.hex *.json"), ("All files", "*.*")),
        )
        if selected:
            self.contract_artifact_var.set(selected)

    def browse_contract_abi(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select Contract ABI",
            initialdir=str(ROOT),
            filetypes=(("ABI files", "*.abi *.json"), ("All files", "*.*")),
        )
        if selected:
            self.contract_abi_var.set(selected)

    def compile_contract_source(self) -> None:
        source_path = self.contract_source_var.get().strip()
        contract_name = self.contract_name_var.get().strip() or None
        if not source_path:
            messagebox.showinfo(APP_TITLE, "Select a Solidity source file first.")
            return

        def task() -> dict[str, str]:
            return compile_solidity_source(source_path, contract_name=contract_name)

        def on_success(payload: dict[str, str]) -> None:
            self.contract_artifact_var.set(payload["artifactPath"])
            self.contract_abi_var.set(payload["abiPath"])
            if not self.contract_name_var.get().strip():
                self.contract_name_var.set(payload["contractName"])
            self.set_text(self.contract_output_text, pretty_json(payload))

        self.run_background("Compiling Solidity source with solc...", task, on_success=on_success)

    def load_contract_defaults(self) -> None:
        try:
            sender = self.resolve_contract_wallet()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[int, object]:
            client = self.require_client()
            responses = client.batch(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                    {"jsonrpc": "2.0", "id": 2, "method": "eth_gasPrice", "params": []},
                    {"jsonrpc": "2.0", "id": 3, "method": "eth_maxPriorityFeePerGas", "params": []},
                    {"jsonrpc": "2.0", "id": 4, "method": "eth_getBlockByNumber", "params": ["latest", False]},
                    {"jsonrpc": "2.0", "id": 5, "method": "eth_getTransactionCount", "params": [sender.address, "pending"]},
                ]
            )
            indexed: dict[int, object] = {}
            for response in responses:
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, dict):
                        raise RpcCallError(str(error.get("message", "RPC default lookup failed")), code=error.get("code"), data=error.get("data"))
                    raise RpcCallError("RPC default lookup failed")
                indexed[int(response["id"])] = response.get("result")
            return indexed

        def on_success(indexed: dict[int, object]) -> None:
            chain_id = hex_quantity_to_int(indexed.get(1), default=1337)
            gas_price = hex_quantity_to_int(indexed.get(2))
            priority_fee = hex_quantity_to_int(indexed.get(3), default=max(gas_price, 1))
            latest_block = indexed.get(4) if isinstance(indexed.get(4), dict) else {}
            base_fee = hex_quantity_to_int(latest_block.get("baseFeePerGas"), default=0) if isinstance(latest_block, dict) else 0
            max_fee = max(base_fee + (priority_fee * 2), priority_fee)

            self.contract_chain_id_var.set(str(chain_id))
            self.contract_gas_price_var.set(str(gas_price))
            self.contract_priority_fee_var.set(str(priority_fee))
            self.contract_max_fee_var.set(str(max_fee))
            self.set_status(f"Loaded deploy defaults for nonce {hex_quantity_to_int(indexed.get(5))}.")

        self.run_background("Loading contract deployment defaults...", task, on_success=on_success)

    def _load_contract_artifact_from_form(self):
        artifact_path = self.contract_artifact_var.get().strip()
        if not artifact_path:
            raise ValueError("contract artifact path is required")
        contract_name = self.contract_name_var.get().strip() or None
        artifact = load_contract_artifact(artifact_path, contract_name=contract_name)
        abi_path = self.contract_abi_var.get().strip()
        if abi_path:
            artifact = artifact.with_abi(load_contract_abi(abi_path, contract_name=contract_name))
        return artifact

    def _contract_deployer_kwargs(self) -> dict[str, object]:
        tx_type = "legacy" if self.contract_tx_type_var.get().strip() == "Legacy" else "eip1559"
        kwargs: dict[str, object] = {
            "tx_type": tx_type,
            "gas_limit": parse_int_value(self.contract_gas_limit_var.get(), label="contract gas limit"),
            "value": parse_int_value(self.contract_value_var.get(), label="contract value"),
        }
        if self.contract_chain_id_var.get().strip():
            kwargs["chain_id"] = parse_int_value(self.contract_chain_id_var.get(), label="contract chain ID")
        if self.contract_gas_price_var.get().strip():
            kwargs["gas_price"] = parse_int_value(self.contract_gas_price_var.get(), label="contract gas price")
        if self.contract_priority_fee_var.get().strip():
            kwargs["max_priority_fee_per_gas"] = parse_int_value(self.contract_priority_fee_var.get(), label="contract max priority fee")
        if self.contract_max_fee_var.get().strip():
            kwargs["max_fee_per_gas"] = parse_int_value(self.contract_max_fee_var.get(), label="contract max fee")
        return kwargs

    def preview_contract_deploy(self) -> None:
        try:
            wallet = self.resolve_contract_wallet()
            artifact = self._load_contract_artifact_from_form()
            constructor_args = parse_json_array(self.contract_constructor_args_var.get().strip() or "[]", label="constructor args")
            deploy_kwargs = self._contract_deployer_kwargs()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[str, object]:
            deployer = ContractDeployer.for_rpc_url(self.rpc_url_var.get().strip())
            transaction, raw_transaction, predicted_address, nonce = deployer.build_deployment_transaction(
                artifact,
                private_key=wallet.private_key,
                constructor_args=constructor_args,
                **deploy_kwargs,
            )
            return {
                "artifact": self.contract_artifact_var.get().strip(),
                "contractName": artifact.contract_name,
                "signer": wallet.address,
                "constructorArgs": constructor_args,
                "nonce": nonce,
                "predictedContractAddress": predicted_address,
                "transactionHash": transaction.tx_hash().to_hex(),
                "rawTransaction": raw_transaction,
                "txType": deploy_kwargs["tx_type"],
            }

        self.run_background(
            "Building contract deployment transaction...",
            task,
            on_success=lambda payload: self.set_text(self.contract_output_text, pretty_json(payload)),
        )

    def deploy_contract(self) -> None:
        try:
            wallet = self.resolve_contract_wallet()
            artifact = self._load_contract_artifact_from_form()
            constructor_args = parse_json_array(self.contract_constructor_args_var.get().strip() or "[]", label="constructor args")
            deploy_kwargs = self._contract_deployer_kwargs()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[str, object]:
            deployer = ContractDeployer.for_rpc_url(self.rpc_url_var.get().strip())
            result = deployer.deploy_contract(
                artifact,
                private_key=wallet.private_key,
                constructor_args=constructor_args,
                **deploy_kwargs,
            )
            deployed_code = None
            if result.receipt is not None and result.receipt.get("status") == "0x1":
                deployed_code = deployer.client.call("eth_getCode", [result.contract_address, "latest"])
            return {
                "artifact": self.contract_artifact_var.get().strip(),
                "contractName": artifact.contract_name,
                "signer": wallet.address,
                "constructorArgs": constructor_args,
                "transactionHash": result.transaction_hash,
                "contractAddress": result.contract_address,
                "predictedContractAddress": result.predicted_contract_address,
                "receipt": result.receipt,
                "transaction": result.transaction,
                "deployedCode": deployed_code,
            }

        def on_success(payload: dict[str, object]) -> None:
            self.set_text(self.contract_output_text, pretty_json(payload))
            self.tx_hash_var.set(str(payload["transactionHash"]))
            self.call_to_var.set(str(payload["contractAddress"]))
            self.call_from_var.set(wallet.address)
            self.block_selector_var.set("latest")
            self.refresh_dashboard()

        self.run_background("Deploying smart contract...", task, on_success=on_success)

    def _run_consensus_workload(self, algorithm: str) -> dict[str, object]:
        if algorithm == "manual":
            return {
                "algorithm": algorithm,
                "summary": "No local consensus simulation was requested before mining.",
            }

        if algorithm == "hybrid-beacon":
            validators = parse_int_value(self.mining_validators_var.get(), label="validator count")
            epochs = parse_int_value(self.mining_epochs_var.get(), label="epoch count")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                state = run_beacon_simulation(num_validators=validators, epochs=epochs)
            return {
                "algorithm": algorithm,
                "validators": validators,
                "epochs": epochs,
                "finalEpoch": state.epoch,
                "finalSlot": state.slot,
                "latestBlockRoot": state.latest_block_root,
                "justifiedCheckpoint": {
                    "epoch": state.justified_checkpoint.epoch,
                    "root": state.justified_checkpoint.root,
                },
                "finalizedCheckpoint": {
                    "epoch": state.finalized_checkpoint.epoch,
                    "root": state.finalized_checkpoint.root,
                },
                "stdout": buffer.getvalue().strip(),
            }

        nodes = parse_int_value(self.mining_nodes_var.get(), label="PBFT node count")
        rounds = parse_int_value(self.mining_rounds_var.get(), label="PBFT rounds")
        byzantine = parse_int_value(self.mining_byzantine_var.get(), label="PBFT byzantine count")
        degree = parse_int_value(self.mining_degree_var.get(), label="PBFT degree")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            simulated_nodes = asyncio.run(
                run_network_simulation(
                    node_count=nodes,
                    rounds=rounds,
                    byzantine_count=byzantine,
                    degree=degree,
                )
            )
        honest_heads = len({node.head_hash for node in simulated_nodes if not node.malicious})
        return {
            "algorithm": algorithm,
            "nodes": nodes,
            "rounds": rounds,
            "byzantine": byzantine,
            "degree": degree,
            "maxHeadHeight": max(node.head_height for node in simulated_nodes),
            "honestHeadCount": honest_heads,
            "samplePeers": {
                str(node.node_id): {
                    "region": node.region,
                    "operator": node.operator_id,
                    "endpoint": node.endpoint,
                    "peerCount": len(node.peer_records),
                }
                for node in simulated_nodes[: min(4, len(simulated_nodes))]
            },
            "stdout": buffer.getvalue().strip(),
        }

    def run_consensus_only(self) -> None:
        algorithm = self.mining_algorithm_var.get().strip() or "manual"

        self.run_background(
            f"Running {algorithm} consensus workload...",
            lambda: self._run_consensus_workload(algorithm),
            on_success=lambda payload: self.set_text(self.mining_output_text, pretty_json(payload)),
        )

    def mine_reward_block(self) -> None:
        try:
            miner_address = self.resolve_miner_address()
            reward = parse_int_value(self.mining_reward_var.get(), label="reward amount")
            count = parse_int_value(self.mining_count_var.get(), label="block count")
            algorithm = self.mining_algorithm_var.get().strip() or "manual"
            allow_empty = bool(self.mining_allow_empty_var.get())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[str, object]:
            consensus_summary = self._run_consensus_workload(algorithm)
            client = self.require_client()
            options = {
                "count": hex(count),
                "reward": hex(reward),
                "beneficiary": miner_address,
                "allowEmpty": allow_empty,
                "algorithm": algorithm,
            }
            try:
                mined_blocks = client.call("dev_mine", [options])
            except RpcCallError as exc:
                if exc.code == -32601 and self.dev_server.running and self.rpc_url_var.get().strip() == self.dev_server.rpc_url:
                    mined_blocks = self.dev_server.mine_blocks(
                        beneficiary=miner_address,
                        reward=reward,
                        count=count,
                        algorithm=algorithm,
                    )
                else:
                    raise
            miner_balance = client.call("eth_getBalance", [miner_address, "latest"])
            latest_block = client.call("eth_getBlockByNumber", ["latest", False])
            return {
                "consensus": consensus_summary,
                "minedBlocks": mined_blocks,
                "miner": miner_address,
                "latestBalance": miner_balance,
                "latestBlock": latest_block,
                "warning": "Consensus simulations are local research workloads. Reward issuance comes from the execution dev_mine extension.",
            }

        def on_success(payload: dict[str, object]) -> None:
            self.set_text(self.mining_output_text, pretty_json(payload))
            self.refresh_dashboard()

        self.run_background("Running consensus workload and mining reward block...", task, on_success=on_success)

    def refresh_network_map(self) -> None:
        def task() -> StackSnapshot:
            return inspect_stack_snapshot(dev_server=self.dev_server)

        def on_success(snapshot: StackSnapshot) -> None:
            for item_id in self.network_tree.get_children():
                self.network_tree.delete(item_id)
            for service in snapshot.services:
                self.network_tree.insert(
                    "",
                    "end",
                    values=(
                        service.service,
                        service.status,
                        service.role,
                        service.host_endpoint or "-",
                        ", ".join(service.container_ips) if service.container_ips else "-",
                    ),
                )
            lines = [
                f"Compose Project: {snapshot.project_name}",
                f"Host IPs: {', '.join(snapshot.host_ips)}",
                "",
            ]
            if snapshot.discovery_error:
                lines.extend(
                    [
                        "Docker discovery fallback:",
                        snapshot.discovery_error,
                        "",
                    ]
                )
            for service in snapshot.services:
                lines.extend(
                    [
                        f"{service.service} [{service.status}]",
                        f"  Role: {service.role}",
                        f"  Description: {service.description}",
                        f"  Host Endpoint: {service.host_endpoint or '-'}",
                        f"  Container: {service.container_name or '-'}",
                        f"  Container IPs: {', '.join(service.container_ips) if service.container_ips else '-'}",
                        f"  Networks: {', '.join(service.networks) if service.networks else '-'}",
                        f"  Note: {service.note or '-'}",
                        "",
                    ]
                )
            self.set_text(self.network_output_text, "\n".join(lines).strip())
            self._draw_network_map(snapshot)

        self.run_background("Inspecting stack topology...", task, on_success=on_success)

    def _draw_network_map(self, snapshot: StackSnapshot) -> None:
        canvas = self.network_canvas
        canvas.delete("all")

        def box(x1: int, y1: int, x2: int, y2: int, title: str, detail: str, *, fill: str) -> tuple[float, float]:
            canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=PALETTE["border"], width=2)
            canvas.create_text((x1 + x2) / 2, y1 + 16, text=title, fill=PALETTE["text"], font=("TkDefaultFont", 10, "bold"))
            canvas.create_text((x1 + x2) / 2, y1 + 40, text=detail, fill=PALETTE["muted"], font=("TkDefaultFont", 8), width=(x2 - x1 - 12))
            return ((x1 + x2) / 2, (y1 + y2) / 2)

        host_center = box(130, 20, 290, 78, "Host / IDE", "\n".join(snapshot.host_ips[:3]), fill=PALETTE["surface"])
        launcher_center = box(130, 108, 290, 166, "start.py / Compose", snapshot.project_name, fill=PALETTE["panel"])
        rpc_center = box(130, 196, 290, 254, "Current RPC", self.rpc_url_var.get().strip() or "-", fill=PALETTE["surface"])
        canvas.create_line(host_center[0], 78, launcher_center[0], 108, fill=PALETTE["accent"], width=2)
        canvas.create_line(launcher_center[0], 166, rpc_center[0], 196, fill=PALETTE["accent"], width=2)

        ordered = list(snapshot.services)
        start_y = 300
        left_x = 18
        right_x = 218
        for index, service in enumerate(ordered):
            column_x = left_x if index % 2 == 0 else right_x
            row_y = start_y + (index // 2) * 84
            fill = PALETTE["surface"]
            status_lower = service.status.lower()
            if "running" in status_lower:
                fill = "#e5f7ef"
            elif any(token in status_lower for token in ("exit", "dead", "failed")):
                fill = "#f8e6e1"
            detail = "\n".join(
                [
                    service.role,
                    service.host_endpoint or (service.container_ips[0] if service.container_ips else "container-only"),
                    service.status,
                ]
            )
            center = box(column_x, row_y, column_x + 184, row_y + 62, service.service, detail, fill=fill)
            canvas.create_line(launcher_center[0], 166, center[0], row_y, fill=PALETTE["border"], width=1)

    def load_transaction_defaults(self) -> None:
        try:
            sender = self.resolve_sender_wallet()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[str, object]:
            client = self.require_client()
            responses = client.batch(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                    {"jsonrpc": "2.0", "id": 2, "method": "eth_getTransactionCount", "params": [sender.address, "pending"]},
                    {"jsonrpc": "2.0", "id": 3, "method": "eth_gasPrice", "params": []},
                    {"jsonrpc": "2.0", "id": 4, "method": "eth_maxPriorityFeePerGas", "params": []},
                    {"jsonrpc": "2.0", "id": 5, "method": "eth_getBlockByNumber", "params": ["latest", False]},
                ]
            )
            indexed: dict[int, object] = {}
            for response in responses:
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, dict):
                        raise RpcCallError(str(error.get("message", "RPC default lookup failed")), code=error.get("code"), data=error.get("data"))
                    raise RpcCallError("RPC default lookup failed")
                indexed[int(response["id"])] = response.get("result")
            return indexed

        def on_success(indexed: dict[int, object]) -> None:
            chain_id = hex_quantity_to_int(indexed.get(1), default=1337)
            nonce = hex_quantity_to_int(indexed.get(2))
            gas_price = hex_quantity_to_int(indexed.get(3))
            priority_fee = hex_quantity_to_int(indexed.get(4), default=max(gas_price, 1))
            latest_block = indexed.get(5) if isinstance(indexed.get(5), dict) else {}
            base_fee = hex_quantity_to_int(latest_block.get("baseFeePerGas"), default=0) if isinstance(latest_block, dict) else 0
            max_fee = max(base_fee + (priority_fee * 2), priority_fee)

            self.transfer_chain_id_var.set(str(chain_id))
            self.transfer_nonce_var.set(str(nonce))
            self.transfer_gas_limit_var.set(self.transfer_gas_limit_var.get().strip() or "21000")
            self.transfer_gas_price_var.set(str(gas_price))
            self.transfer_priority_fee_var.set(str(priority_fee))
            self.transfer_max_fee_var.set(str(max_fee))
            self.chain_id_var.set(hex(chain_id))

        self.run_background("Loading nonce and fee suggestions...", task, on_success=on_success)

    def build_signed_transaction(self) -> tuple[WalletProfile, object, str]:
        sender = self.resolve_sender_wallet()
        recipient = Address.from_hex(self.transfer_to_var.get().strip())
        amount = parse_int_value(self.transfer_amount_var.get(), label="transfer amount")
        nonce = parse_int_value(self.transfer_nonce_var.get(), label="nonce")
        chain_id = parse_int_value(self.transfer_chain_id_var.get(), label="chain ID")
        gas_limit = parse_int_value(self.transfer_gas_limit_var.get(), label="gas limit")
        tx_type = self.transfer_type_var.get().strip()

        if tx_type == "Legacy":
            gas_price = parse_int_value(self.transfer_gas_price_var.get(), label="gas price")
            signed = LegacyTransaction(
                nonce=nonce,
                gas_price=gas_price,
                gas_limit=gas_limit,
                to=recipient,
                value=amount,
                data=b"",
                chain_id=chain_id,
            ).sign(sender.private_key)
        else:
            max_priority_fee = parse_int_value(self.transfer_priority_fee_var.get(), label="max priority fee")
            max_fee = parse_int_value(self.transfer_max_fee_var.get(), label="max fee")
            signed = EIP1559Transaction(
                chain_id=chain_id,
                nonce=nonce,
                max_priority_fee_per_gas=max_priority_fee,
                max_fee_per_gas=max_fee,
                gas_limit=gas_limit,
                to=recipient,
                value=amount,
                data=b"",
            ).sign(sender.private_key)
        raw_hex = "0x" + signed.encode().hex()
        return sender, signed, raw_hex

    def preview_transfer(self) -> None:
        try:
            sender, signed, raw_hex = self.build_signed_transaction()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        preview = {
            "sender": sender.address,
            "recipient": self.transfer_to_var.get().strip(),
            "amount": self.transfer_amount_var.get().strip(),
            "transactionType": self.transfer_type_var.get().strip(),
            "transactionHash": signed.tx_hash().to_hex(),
            "rawTransaction": raw_hex,
        }
        self.set_text(self.transfer_output_text, pretty_json(preview))
        self.set_status("Signed transaction preview generated.")

    def send_transfer(self) -> None:
        try:
            sender, signed, raw_hex = self.build_signed_transaction()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return

        def task() -> dict[str, object]:
            client = self.require_client()
            tx_hash = client.call("eth_sendRawTransaction", [raw_hex])
            tx_object = client.call("eth_getTransactionByHash", [tx_hash])
            receipt = client.call("eth_getTransactionReceipt", [tx_hash])
            return {
                "sender": sender.address,
                "transactionHash": tx_hash,
                "rawTransaction": raw_hex,
                "transaction": tx_object,
                "receipt": receipt,
                "signedHash": signed.tx_hash().to_hex(),
            }

        def on_success(payload: dict[str, object]) -> None:
            self.set_text(self.transfer_output_text, pretty_json(payload))
            self.tx_hash_var.set(str(payload["transactionHash"]))
            self.block_selector_var.set("latest")
            self.refresh_dashboard()

        self.run_background("Submitting signed transaction...", task, on_success=on_success)

    def fetch_block(self) -> None:
        selector = self.block_selector_var.get().strip() or "latest"

        def task() -> object | None:
            client = self.require_client()
            return client.call("eth_getBlockByNumber", [selector, True])

        self.run_background(
            f"Fetching block {selector}...",
            task,
            on_success=lambda result: self.set_text(self.explorer_output_text, pretty_json(result)),
        )

    def fetch_transaction(self) -> None:
        tx_hash = self.tx_hash_var.get().strip()
        if not tx_hash:
            messagebox.showinfo(APP_TITLE, "Transaction hash is required.")
            return

        def task() -> object | None:
            client = self.require_client()
            return client.call("eth_getTransactionByHash", [tx_hash])

        self.run_background(
            f"Fetching transaction {tx_hash[:18]}...",
            task,
            on_success=lambda result: self.set_text(self.explorer_output_text, pretty_json(result)),
        )

    def fetch_receipt(self) -> None:
        tx_hash = self.tx_hash_var.get().strip()
        if not tx_hash:
            messagebox.showinfo(APP_TITLE, "Transaction hash is required.")
            return

        def task() -> object | None:
            client = self.require_client()
            return client.call("eth_getTransactionReceipt", [tx_hash])

        self.run_background(
            f"Fetching receipt {tx_hash[:18]}...",
            task,
            on_success=lambda result: self.set_text(self.explorer_output_text, pretty_json(result)),
        )

    def trace_transaction(self) -> None:
        tx_hash = self.tx_hash_var.get().strip()
        if not tx_hash:
            messagebox.showinfo(APP_TITLE, "Transaction hash is required.")
            return

        def task() -> object | None:
            client = self.require_client()
            return client.call("debug_traceTransaction", [tx_hash, self._trace_options_payload()])

        self.run_background(
            f"Tracing transaction {tx_hash[:18]}...",
            task,
            on_success=lambda result: self.set_text(self.explorer_output_text, pretty_json(result)),
        )

    def _call_object_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.call_from_var.get().strip():
            payload["from"] = self.call_from_var.get().strip()
        if self.call_to_var.get().strip():
            payload["to"] = self.call_to_var.get().strip()
        if self.call_data_var.get().strip():
            payload["data"] = self.call_data_var.get().strip()
        if self.call_value_var.get().strip():
            payload["value"] = hex(parse_int_value(self.call_value_var.get(), label="call value"))
        if self.call_gas_var.get().strip():
            payload["gas"] = hex(parse_int_value(self.call_gas_var.get(), label="call gas"))
        if self.call_gas_price_var.get().strip():
            payload["gasPrice"] = hex(parse_int_value(self.call_gas_price_var.get(), label="call gas price"))
        if self.call_max_fee_var.get().strip():
            payload["maxFeePerGas"] = hex(parse_int_value(self.call_max_fee_var.get(), label="call max fee"))
        if self.call_priority_fee_var.get().strip():
            payload["maxPriorityFeePerGas"] = hex(parse_int_value(self.call_priority_fee_var.get(), label="call max priority fee"))
        if self.call_access_list_var.get().strip():
            payload["accessList"] = json.loads(self.call_access_list_var.get().strip())
        return payload

    def _trace_options_payload(self) -> dict[str, bool]:
        return {
            "disableMemory": self.trace_disable_memory_var.get(),
            "disableStack": self.trace_disable_stack_var.get(),
            "disableStorage": self.trace_disable_storage_var.get(),
        }

    def run_eth_call(self) -> None:
        try:
            call_object = self._call_object_payload()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        selector = self.call_block_var.get().strip() or "latest"

        def task() -> object | None:
            client = self.require_client()
            return client.call("eth_call", [call_object, selector])

        self.run_background(
            "Running eth_call...",
            task,
            on_success=lambda result: self.set_text(self.call_output_text, pretty_json({"result": result, "callObject": call_object})),
        )

    def run_estimate_gas(self) -> None:
        try:
            call_object = self._call_object_payload()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        selector = self.call_block_var.get().strip() or "latest"

        def task() -> object | None:
            client = self.require_client()
            return client.call("eth_estimateGas", [call_object, selector])

        self.run_background(
            "Estimating gas...",
            task,
            on_success=lambda result: self.set_text(self.call_output_text, pretty_json({"gasEstimate": result, "callObject": call_object})),
        )

    def run_trace_call(self) -> None:
        try:
            call_object = self._call_object_payload()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, str(exc))
            return
        selector = self.call_block_var.get().strip() or "latest"
        options = self._trace_options_payload()

        def task() -> object | None:
            client = self.require_client()
            return client.call("debug_traceCall", [call_object, selector, options])

        self.run_background(
            "Tracing call...",
            task,
            on_success=lambda result: self.set_text(
                self.call_output_text,
                pretty_json({"trace": result, "callObject": call_object, "traceOptions": options}),
            ),
        )

    def run_console_request(self) -> None:
        method = self.console_method_var.get().strip()
        if not method:
            messagebox.showinfo(APP_TITLE, "RPC method is required.")
            return
        try:
            params = json.loads(self.console_params_text.get("1.0", "end").strip() or "[]")
        except json.JSONDecodeError as exc:
            messagebox.showerror(APP_TITLE, f"Params JSON is invalid: {exc}")
            return
        if not isinstance(params, list):
            messagebox.showerror(APP_TITLE, "Params must decode to a JSON array.")
            return

        def task() -> object | None:
            client = self.require_client()
            return client.request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                }
            )

        self.run_background(
            f"Calling {method}...",
            task,
            on_success=lambda result: self.set_text(self.console_output_text, pretty_json(result)),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Graphical desktop client for the Python Ethereum-like chain.")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--embedded-host", default="127.0.0.1")
    parser.add_argument("--embedded-port", type=int, default=8546)
    parser.add_argument("--embedded-chain-id", type=int, default=1337)
    parser.add_argument("--start-devnet", action="store_true", help="Start the embedded prefunded RPC devnet on launch.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = BlockchainDesktopClient(
        rpc_url=args.rpc_url,
        embedded_host=args.embedded_host,
        embedded_port=args.embedded_port,
        embedded_chain_id=args.embedded_chain_id,
        start_devnet=bool(args.start_devnet),
    )
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
