from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import socket
import socketserver
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from ...block import Block, BlockHeader
from ..config import NodeConfig
from ..models import MerkleProof, PeerInfo, SnapshotManifest
from ..services.state_provider_service import StateProviderService
from .protocols import PeerClient

if TYPE_CHECKING:
    from ..runtime import NodeRuntime


LOG = logging.getLogger(__name__)


class I2PTransportError(RuntimeError):
    pass


def i2p_privacy_enabled() -> bool:
    return os.environ.get("EXECUTION_PRIVACY_NETWORK", "plain").strip().lower() == "i2p"


def parse_env_list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return ()
    values: list[str] = []
    for item in raw.replace("\n", ",").split(","):
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def _parse_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _parse_float(name: str, *, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _parse_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw, 0)


def normalize_i2p_destination(reference: str) -> str:
    cleaned = reference.strip()
    if cleaned.startswith("i2p://"):
        cleaned = cleaned[len("i2p://") :]
    return cleaned.strip().rstrip("/")


def is_i2p_destination(reference: str) -> bool:
    cleaned = normalize_i2p_destination(reference)
    if not cleaned:
        return False
    return cleaned.endswith(".i2p") or len(cleaned) >= 128


def advertised_i2p_endpoint(destination: str) -> str:
    return f"i2p://{normalize_i2p_destination(destination)}"


@dataclass(frozen=True, slots=True)
class I2POverlayConfig:
    sam_host: str
    sam_port: int
    timeout_seconds: float
    signature_type: int
    inbound_quantity: int
    outbound_quantity: int
    bootstrap_file: Path | None
    publish_destination: bool
    bootstrap_wait_seconds: float

    @classmethod
    def from_env(cls) -> "I2POverlayConfig":
        bootstrap_file_raw = os.environ.get("EXECUTION_I2P_BOOTSTRAP_FILE", "").strip()
        bootstrap_file = Path(bootstrap_file_raw) if bootstrap_file_raw else None
        return cls(
            sam_host=os.environ.get("EXECUTION_I2P_SAM_HOST", "127.0.0.1").strip() or "127.0.0.1",
            sam_port=_parse_int("EXECUTION_I2P_SAM_PORT", default=7656),
            timeout_seconds=_parse_float("EXECUTION_I2P_TIMEOUT_SECONDS", default=15.0),
            signature_type=_parse_int("EXECUTION_I2P_SIGNATURE_TYPE", default=7),
            inbound_quantity=_parse_int("EXECUTION_I2P_INBOUND_QUANTITY", default=2),
            outbound_quantity=_parse_int("EXECUTION_I2P_OUTBOUND_QUANTITY", default=2),
            bootstrap_file=bootstrap_file,
            publish_destination=_parse_bool("EXECUTION_I2P_PUBLISH_DESTINATION", default=False),
            bootstrap_wait_seconds=_parse_float("EXECUTION_I2P_BOOTSTRAP_WAIT_SECONDS", default=20.0),
        )


class _LineConnection:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.reader = sock.makefile("rb")
        self.writer = sock.makefile("wb")

    @classmethod
    def connect(cls, host: str, port: int, *, timeout: float) -> "_LineConnection":
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        return cls(sock)

    def write_line(self, line: str) -> None:
        self.writer.write(line.encode("utf-8") + b"\n")
        self.writer.flush()

    def read_line(self) -> str:
        payload = self.reader.readline()
        if not payload:
            raise I2PTransportError("unexpected EOF from SAM bridge")
        return payload.decode("utf-8", errors="replace").strip()

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            try:
                self.writer.close()
            finally:
                self.sock.close()


def _parse_sam_status(line: str) -> tuple[str, dict[str, str]]:
    tokens = shlex.split(line)
    if len(tokens) < 2:
        raise I2PTransportError(f"unexpected SAM response: {line}")
    command = " ".join(tokens[:2])
    payload: dict[str, str] = {}
    for token in tokens[2:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        payload[key] = value
    return command, payload


def _hello(connection: _LineConnection) -> None:
    connection.write_line("HELLO VERSION MIN=3.1 MAX=3.1")
    command, payload = _parse_sam_status(connection.read_line())
    if command != "HELLO REPLY" or payload.get("RESULT") != "OK":
        raise I2PTransportError(f"SAM HELLO failed: {command} {payload}")


class _OverlayTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler, overlay: "I2POverlayServer") -> None:
        self.overlay = overlay
        super().__init__(address, handler)


class _OverlayRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        payload = self.rfile.readline()
        if not payload:
            return
        try:
            request = json.loads(payload.decode("utf-8"))
            response = self.server.overlay.handle_request(request)  # type: ignore[attr-defined]
            body = {"ok": True, "result": response}
        except Exception as exc:  # noqa: BLE001
            body = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n")
        self.wfile.flush()


class I2PSamSession:
    def __init__(self, config: I2POverlayConfig, *, state_directory: Path, session_id: str | None = None) -> None:
        self.config = config
        self.state_directory = Path(state_directory)
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or f"execution-{uuid.uuid4().hex[:12]}"
        self.private_key_path = self.state_directory / "i2p-destination.key"
        self.control: _LineConnection | None = None
        self.forward: _LineConnection | None = None
        self.public_destination: str | None = None

    def _generate_destination(self) -> tuple[str, str]:
        connection = _LineConnection.connect(self.config.sam_host, self.config.sam_port, timeout=self.config.timeout_seconds)
        try:
            _hello(connection)
            connection.write_line(f"DEST GENERATE SIGNATURE_TYPE={self.config.signature_type}")
            command, payload = _parse_sam_status(connection.read_line())
            if command != "DEST REPLY" or "PUB" not in payload or "PRIV" not in payload:
                raise I2PTransportError(f"SAM DEST GENERATE failed: {command} {payload}")
            return payload["PUB"], payload["PRIV"]
        finally:
            connection.close()

    def _private_key(self) -> str:
        if self.private_key_path.exists():
            return self.private_key_path.read_text(encoding="utf-8").strip()
        public, private = self._generate_destination()
        self.private_key_path.write_text(private, encoding="utf-8")
        published = self.state_directory / "i2p-destination.pub"
        published.write_text(public, encoding="utf-8")
        return private

    def start(self, *, local_port: int) -> str:
        if self.control is not None and self.public_destination is not None:
            return self.public_destination
        private_key = self._private_key()
        control = _LineConnection.connect(self.config.sam_host, self.config.sam_port, timeout=self.config.timeout_seconds)
        try:
            _hello(control)
            command_line = (
                f"SESSION CREATE STYLE=STREAM ID={self.session_id} DESTINATION={private_key} "
                f"SIGNATURE_TYPE={self.config.signature_type} "
                f"inbound.quantity={self.config.inbound_quantity} "
                f"outbound.quantity={self.config.outbound_quantity} "
                "i2cp.leaseSetEncType=4,0"
            )
            control.write_line(command_line)
            command, payload = _parse_sam_status(control.read_line())
            if command != "SESSION STATUS" or payload.get("RESULT") != "OK":
                raise I2PTransportError(f"SAM SESSION CREATE failed: {command} {payload}")
            control.write_line("NAMING LOOKUP NAME=ME")
            command, payload = _parse_sam_status(control.read_line())
            if command != "NAMING REPLY" or payload.get("RESULT") != "OK" or "VALUE" not in payload:
                raise I2PTransportError(f"SAM NAMING LOOKUP failed: {command} {payload}")
            self.control = control
            self.public_destination = payload["VALUE"]
        except Exception:
            control.close()
            raise

        forward = _LineConnection.connect(self.config.sam_host, self.config.sam_port, timeout=self.config.timeout_seconds)
        try:
            _hello(forward)
            forward.write_line(f"STREAM FORWARD ID={self.session_id} PORT={local_port} HOST=127.0.0.1 SILENT=true")
            command, payload = _parse_sam_status(forward.read_line())
            if command != "STREAM STATUS" or payload.get("RESULT") != "OK":
                raise I2PTransportError(f"SAM STREAM FORWARD failed: {command} {payload}")
            self.forward = forward
        except Exception:
            forward.close()
            self.stop()
            raise
        assert self.public_destination is not None
        published = self.state_directory / "i2p-destination.pub"
        published.write_text(self.public_destination, encoding="utf-8")
        return self.public_destination

    def stop(self) -> None:
        if self.forward is not None:
            self.forward.close()
            self.forward = None
        if self.control is not None:
            self.control.close()
            self.control = None

    def request(self, destination: str, method: str, params: dict[str, Any] | None = None) -> Any:
        if self.control is None:
            raise I2PTransportError("I2P session is not active")
        resolved = normalize_i2p_destination(destination)
        connection = _LineConnection.connect(self.config.sam_host, self.config.sam_port, timeout=self.config.timeout_seconds)
        try:
            _hello(connection)
            connection.write_line(f"STREAM CONNECT ID={self.session_id} DESTINATION={resolved} SILENT=false")
            command, payload = _parse_sam_status(connection.read_line())
            if command != "STREAM STATUS" or payload.get("RESULT") != "OK":
                raise I2PTransportError(f"SAM STREAM CONNECT failed: {command} {payload}")
            connection.write_line(json.dumps({"method": method, "params": params or {}}, separators=(",", ":"), sort_keys=True))
            body = json.loads(connection.read_line())
            if not isinstance(body, dict) or not body.get("ok", False):
                error = "remote overlay request failed"
                if isinstance(body, dict) and "error" in body:
                    error = str(body["error"])
                raise I2PTransportError(error)
            return body.get("result")
        finally:
            connection.close()


class I2POverlayServer:
    def __init__(self, runtime: "NodeRuntime", config: NodeConfig, *, overlay_config: I2POverlayConfig | None = None) -> None:
        self.runtime = runtime
        self.config = config
        self.overlay_config = overlay_config or I2POverlayConfig.from_env()
        self.sam_session = I2PSamSession(self.overlay_config, state_directory=self.runtime.config.state_directory)
        self._server: _OverlayTcpServer | None = None
        self._thread: threading.Thread | None = None
        self._public_destination: str | None = None

    @property
    def public_destination(self) -> str:
        if self._public_destination is None:
            raise I2PTransportError("I2P overlay is not running")
        return self._public_destination

    @property
    def advertised_endpoint(self) -> str:
        return advertised_i2p_endpoint(self.public_destination)

    def start(self) -> str:
        if self._server is not None and self._public_destination is not None:
            return self._public_destination
        server = _OverlayTcpServer(("127.0.0.1", 0), _OverlayRequestHandler, self)
        thread = threading.Thread(target=server.serve_forever, name=f"i2p-overlay-{self.config.node_name}", daemon=True)
        thread.start()
        try:
            self._public_destination = self.sam_session.start(local_port=int(server.server_address[1]))
        except Exception:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            raise
        self._server = server
        self._thread = thread
        if self.overlay_config.publish_destination:
            self.publish_bootstrap_destination()
        return self.public_destination

    def stop(self) -> None:
        self.sam_session.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def publish_bootstrap_destination(self) -> None:
        if self.overlay_config.bootstrap_file is None:
            return
        self.overlay_config.bootstrap_file.parent.mkdir(parents=True, exist_ok=True)
        line = self.advertised_endpoint
        existing = load_bootstrap_destinations(self.overlay_config.bootstrap_file)
        if line in existing:
            return
        with self.overlay_config.bootstrap_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def peer_info(self) -> PeerInfo:
        head = self.runtime.chain_store.get_canonical_head()
        return PeerInfo(
            peer_id=self.config.node_name,
            endpoint=self.advertised_endpoint,
            capabilities=self.config.capabilities,
            head_number=None if head is None else head.number,
            head_hash=None if head is None else head.hash().to_hex(),
            metadata={
                "chain_id": self.config.chain_config.chain_id,
                "privacy_network": "i2p",
            },
        )

    def _known_peers(self) -> tuple[PeerInfo, ...]:
        peers: dict[str, PeerInfo] = {self.peer_info().peer_id: self.peer_info()}
        for peer in self.runtime.peer_manager.routing_table.all_peers():
            peers[peer.peer_id] = peer
        for peer in self.runtime.peer_manager.all_peer_info():
            peers[peer.peer_id] = peer
        return tuple(peers.values())

    def handle_request(self, request: dict[str, Any]) -> Any:
        method = str(request.get("method", "")).strip()
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("overlay params must be an object")
        if method == "ping":
            return {"ok": True}
        if method == "get_peer_info":
            return self.peer_info().to_dict()
        if method == "announce_peer":
            payload = params.get("peer")
            if not isinstance(payload, dict):
                raise ValueError("announce_peer requires params.peer")
            peer = PeerInfo.from_dict(payload)
            if peer.peer_id != self.config.node_name:
                self.runtime.peer_manager.routing_table.add_peer(peer)
            return {"accepted": True}
        if method == "list_known_peers":
            return [peer.to_dict() for peer in self._known_peers()]
        if method == "get_headers":
            start_height = int(params.get("start_height", 0))
            limit = int(params.get("limit", 0))
            headers: list[dict[str, Any]] = []
            for height in range(start_height, start_height + max(0, limit)):
                header = self.runtime.chain_store.get_canonical_header(height)
                if header is None:
                    break
                headers.append(header.to_dict())
            return headers
        if method == "get_block":
            block_hash = str(params.get("block_hash", "")).strip()
            block = self.runtime.chain_store.get_block(block_hash)
            return None if block is None else block.to_dict()
        provider = StateProviderService(self.runtime.state_store, self.runtime.snapshot_store)
        if method == "get_snapshot_manifest":
            manifest = provider.get_snapshot_manifest()
            return None if manifest is None else manifest.to_dict()
        if method == "get_snapshot_chunk":
            return provider.get_snapshot_chunk(str(params["snapshot_id"]), str(params["chunk_id"])).hex()
        if method == "get_account_proof":
            proof = provider.get_account_proof(int(params["block_number"]), str(params["address"]))
            return None if proof is None else proof.to_dict()
        if method == "get_storage_proof":
            proof = provider.get_storage_proof(int(params["block_number"]), str(params["address"]), str(params["slot"]))
            return None if proof is None else proof.to_dict()
        raise ValueError(f"unsupported overlay method: {method}")


class I2PNodePeerClient(PeerClient):
    def __init__(self, session: I2PSamSession, peer_info: PeerInfo) -> None:
        self._session = session
        self._peer_info = peer_info

    @property
    def peer_info(self) -> PeerInfo:
        return self._peer_info

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._session.request, self._peer_info.endpoint, method, params or {})

    async def ping(self) -> float:
        await self._request("ping")
        return 1.0

    async def get_peer_info(self) -> PeerInfo:
        payload = await self._request("get_peer_info")
        if not isinstance(payload, dict):
            raise I2PTransportError("peer info response must be an object")
        info = PeerInfo.from_dict(payload)
        self._peer_info = info
        return info

    async def announce_peer(self, peer: PeerInfo) -> None:
        await self._request("announce_peer", {"peer": peer.to_dict()})

    async def list_known_peers(self) -> tuple[PeerInfo, ...]:
        payload = await self._request("list_known_peers")
        if not isinstance(payload, list):
            raise I2PTransportError("known peers response must be a list")
        return tuple(PeerInfo.from_dict(item) for item in payload if isinstance(item, dict))

    async def get_headers(self, start_height: int, limit: int) -> tuple[BlockHeader, ...]:
        payload = await self._request("get_headers", {"start_height": start_height, "limit": limit})
        if not isinstance(payload, list):
            raise I2PTransportError("headers response must be a list")
        return tuple(BlockHeader.from_dict(item) for item in payload if isinstance(item, dict))

    async def get_block(self, block_hash: str) -> Block | None:
        payload = await self._request("get_block", {"block_hash": block_hash})
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise I2PTransportError("block response must be an object")
        return Block.from_dict(payload)

    async def get_snapshot_manifest(self) -> SnapshotManifest | None:
        payload = await self._request("get_snapshot_manifest")
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise I2PTransportError("snapshot manifest response must be an object")
        return SnapshotManifest.from_dict(payload)

    async def get_snapshot_chunk(self, snapshot_id: str, chunk_id: str) -> bytes:
        payload = await self._request("get_snapshot_chunk", {"snapshot_id": snapshot_id, "chunk_id": chunk_id})
        if not isinstance(payload, str):
            raise I2PTransportError("snapshot chunk response must be a hex string")
        return bytes.fromhex(payload)

    async def get_account_proof(self, block_number: int, address: str) -> MerkleProof | None:
        payload = await self._request("get_account_proof", {"block_number": block_number, "address": address})
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise I2PTransportError("account proof response must be an object")
        return MerkleProof.from_dict(payload)

    async def get_storage_proof(self, block_number: int, address: str, slot: str) -> MerkleProof | None:
        payload = await self._request("get_storage_proof", {"block_number": block_number, "address": address, "slot": slot})
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise I2PTransportError("storage proof response must be an object")
        return MerkleProof.from_dict(payload)


def load_bootstrap_destinations(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.exists():
        return ()
    values: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        cleaned = raw_line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        values.append(cleaned)
    return tuple(values)


def wait_for_bootstrap_destinations(path: Path | None, *, timeout_seconds: float) -> tuple[str, ...]:
    if path is None:
        return ()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        values = load_bootstrap_destinations(path)
        if values:
            return values
        if time.monotonic() >= deadline:
            return ()
        time.sleep(0.5)


def configured_bootstrap_references(config: NodeConfig, overlay_config: I2POverlayConfig) -> tuple[str, ...]:
    values: list[str] = []
    for candidate in (*config.bootnodes, *config.static_peers, *load_bootstrap_destinations(overlay_config.bootstrap_file)):
        if is_i2p_destination(candidate) and candidate not in values:
            values.append(candidate)
    return tuple(values)


def wait_for_configured_bootstrap_references(config: NodeConfig, overlay_config: I2POverlayConfig) -> tuple[str, ...]:
    current = configured_bootstrap_references(config, overlay_config)
    if current:
        return current
    loaded = wait_for_bootstrap_destinations(overlay_config.bootstrap_file, timeout_seconds=overlay_config.bootstrap_wait_seconds)
    values: list[str] = []
    for candidate in (*config.bootnodes, *config.static_peers, *loaded):
        if is_i2p_destination(candidate) and candidate not in values:
            values.append(candidate)
    return tuple(values)


def unique_peers(peers: Iterable[PeerInfo]) -> tuple[PeerInfo, ...]:
    indexed: dict[str, PeerInfo] = {}
    for peer in peers:
        indexed[peer.peer_id] = peer
    return tuple(indexed.values())


__all__ = [
    "I2PNodePeerClient",
    "I2POverlayConfig",
    "I2POverlayServer",
    "I2PSamSession",
    "I2PTransportError",
    "advertised_i2p_endpoint",
    "configured_bootstrap_references",
    "i2p_privacy_enabled",
    "is_i2p_destination",
    "load_bootstrap_destinations",
    "normalize_i2p_destination",
    "unique_peers",
    "wait_for_configured_bootstrap_references",
]
