from __future__ import annotations

from . import RpcContext, RpcHandler, require_params


def register(methods: dict[str, RpcHandler]) -> None:
    methods["net_version"] = net_version


def net_version(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return context.compat.resolved_network_version(context.node.chain_config)
