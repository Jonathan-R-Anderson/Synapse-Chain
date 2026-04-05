from __future__ import annotations

from rpc.tracing import TraceOptions
from rpc.types import parse_call_request

from . import RpcContext, RpcHandler, require_params


def register(methods: dict[str, RpcHandler]) -> None:
    methods["debug_traceTransaction"] = debug_trace_transaction
    methods["debug_traceCall"] = debug_trace_call


def debug_trace_transaction(context: RpcContext, params: list[object]) -> dict[str, object]:
    require_params(params, min_count=1, max_count=2)
    options = TraceOptions.from_json(params[1] if len(params) > 1 else None)
    return context.tracer.trace_transaction(params[0], options=options)


def debug_trace_call(context: RpcContext, params: list[object]) -> dict[str, object]:
    require_params(params, min_count=2, max_count=3)
    options = TraceOptions.from_json(params[2] if len(params) > 2 else None)
    return context.tracer.trace_call(parse_call_request(params[0]), selector=params[1], options=options)
