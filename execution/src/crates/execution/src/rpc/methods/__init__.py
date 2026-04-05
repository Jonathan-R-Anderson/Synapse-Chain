from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rpc.block_access import ExecutionNode
from rpc.compat import CompatibilityConfig
from rpc.gas import GasEstimator
from rpc.state_access import StateAccessor
from rpc.tracing import TransactionTracer


RpcHandler = Callable[["RpcContext", list[object]], object]


@dataclass(slots=True)
class RpcContext:
    node: ExecutionNode
    compat: CompatibilityConfig
    state: StateAccessor
    gas: GasEstimator
    tracer: TransactionTracer


def require_params(params: list[object], *, min_count: int, max_count: int | None = None) -> None:
    maximum = min_count if max_count is None else max_count
    if len(params) < min_count or len(params) > maximum:
        if min_count == maximum:
            raise ValueError(f"expected exactly {min_count} params")
        raise ValueError(f"expected between {min_count} and {maximum} params")
