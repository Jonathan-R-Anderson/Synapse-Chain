#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import tkinter as tk


ROOT = Path(__file__).resolve().parent
CRATES = ROOT / "execution" / "src" / "crates"

for crate_name in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    source_path = CRATES / crate_name / "src"
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))

from crypto import SECP256K1_N, address_from_private_key  # noqa: E402
from evm import StateDB  # noqa: E402
from execution import ChainConfig, EIP1559Transaction, LegacyTransaction  # noqa: E402
from primitives import Address  # noqa: E402
from rpc.block_access import ExecutionNode  # noqa: E402
from rpc.compat import CompatibilityConfig  # noqa: E402
from rpc.server import JsonRpcServer  # noqa: E402


APP_TITLE = "Python Ethereum Desktop Client"
DEV_ACCOUNT_BALANCE = 10**24
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
            text="Desktop wallet, explorer, call/tracing console, and native transfer client for the Python Ethereum-like chain.",
            style="Subheader.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        self.connection_tab = ttk.Frame(notebook, padding=10)
        self.wallet_tab = ttk.Frame(notebook, padding=10)
        self.transfer_tab = ttk.Frame(notebook, padding=10)
        self.explorer_tab = ttk.Frame(notebook, padding=10)
        self.call_tab = ttk.Frame(notebook, padding=10)
        self.console_tab = ttk.Frame(notebook, padding=10)

        notebook.add(self.connection_tab, text="Connection")
        notebook.add(self.wallet_tab, text="Wallets")
        notebook.add(self.transfer_tab, text="Transfer / Trade")
        notebook.add(self.explorer_tab, text="Explorer")
        notebook.add(self.call_tab, text="Call / Trace")
        notebook.add(self.console_tab, text="RPC Console")

        self._build_connection_tab()
        self._build_wallet_tab()
        self._build_transfer_tab()
        self._build_explorer_tab()
        self._build_call_tab()
        self._build_console_tab()

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
                self.call_from_var.set(wallets[0].address)
            if len(wallets) > 1:
                self.transfer_to_var.set(wallets[1].address)
                self.call_to_var.set(wallets[1].address)
            self.transfer_chain_id_var.set(str(self.dev_server.chain_id))
            self.refresh_dashboard()

        self.run_background("Starting embedded devnet...", task, on_success=on_success)

    def stop_devnet(self) -> None:
        def task() -> None:
            self.dev_server.stop()
            return None

        def on_success(_: None) -> None:
            self.set_text(self.dev_accounts_text, "Embedded devnet stopped.")

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

    def resolve_sender_wallet(self) -> WalletProfile:
        selected = self.transfer_sender_var.get().strip()
        if not selected:
            raise ValueError("select a sender wallet")
        for profile in self.wallets.values():
            if profile.combo_label == selected or profile.address == selected:
                return profile
        raise ValueError("sender wallet is not loaded in the client")

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
