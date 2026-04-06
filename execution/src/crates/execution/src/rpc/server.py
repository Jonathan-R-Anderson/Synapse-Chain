from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from execution import ChainConfig
from rpc.block_access import ExecutionNode
from rpc.compat import CompatibilityConfig
from rpc.errors import internal_error, invalid_request, map_exception, method_not_found, parse_error
from rpc.gas import GasEstimator
from rpc.methods import RpcContext, RpcHandler
from rpc.methods import dev as dev_methods
from rpc.methods import debug as debug_methods
from rpc.methods import eth as eth_methods
from rpc.methods import net as net_methods
from rpc.methods import web3 as web3_methods
from rpc.state_access import StateAccessor
from rpc.tracing import TransactionTracer


LOG = logging.getLogger(__name__)


def build_method_table(context: RpcContext) -> dict[str, RpcHandler]:
    methods: dict[str, RpcHandler] = {}
    web3_methods.register(methods)
    net_methods.register(methods)
    eth_methods.register(methods)
    debug_methods.register(methods)
    dev_methods.register(methods)
    return methods


class JsonRpcServer:
    def __init__(self, node: ExecutionNode) -> None:
        state_accessor = StateAccessor(node)
        self.context = RpcContext(
            node=node,
            compat=node.compat_config,
            state=state_accessor,
            gas=GasEstimator(state_accessor),
            tracer=TransactionTracer(node, state_accessor),
        )
        self.methods = build_method_table(self.context)

    def handle_json_bytes(self, body: bytes) -> bytes | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json.dumps(parse_error().to_response(None)).encode("utf-8")
        response = self._dispatch(payload)
        if response is None:
            return None
        return json.dumps(response, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _dispatch(self, payload: object) -> dict[str, object] | list[dict[str, object]] | None:
        if isinstance(payload, list):
            if not payload:
                return invalid_request().to_response(None)
            responses = [response for item in payload if (response := self._dispatch_one(item)) is not None]
            return None if not responses else responses
        return self._dispatch_one(payload)

    def _dispatch_one(self, payload: object) -> dict[str, object] | None:
        if not isinstance(payload, dict):
            return invalid_request().to_response(None)
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0":
            return invalid_request("jsonrpc must be '2.0'").to_response(request_id)
        method = payload.get("method")
        params = payload.get("params", [])
        if not isinstance(method, str):
            return invalid_request("method must be a string").to_response(request_id)
        if params is None:
            params = []
        if not isinstance(params, list):
            return invalid_request("params must be an array").to_response(request_id)
        handler = self.methods.get(method)
        if handler is None:
            return method_not_found().to_response(request_id)
        try:
            result = handler(self.context, params)
        except Exception as exc:
            error = map_exception(exc)
            if error.code == internal_error().code:
                LOG.exception("Unhandled JSON-RPC exception while serving %s", method)
            return error.to_response(request_id)
        if "id" not in payload:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result,
        }

    def serve(self, host: str = "127.0.0.1", port: int = 8545) -> None:
        cors_origin = self.context.compat.cors_allow_origin
        server = self

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
                response = server.handle_json_bytes(body)
                if response is None:
                    self._write(HTTPStatus.NO_CONTENT, None)
                    return
                self._write(HTTPStatus.OK, response)

            def log_message(self, format: str, *args: object) -> None:
                LOG.debug(format, *args)

        ThreadingHTTPServer((host, port), Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the execution JSON-RPC API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8545)
    parser.add_argument("--chain-id", type=int, default=1)
    parser.add_argument("--mode", choices=["instant", "mempool"], default="instant")
    args = parser.parse_args(argv)

    node = ExecutionNode(
        compat_config=CompatibilityConfig(mining_mode=args.mode),
        chain_config=ChainConfig(chain_id=args.chain_id),
    )
    logging.basicConfig(level=logging.INFO)
    JsonRpcServer(node).serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
