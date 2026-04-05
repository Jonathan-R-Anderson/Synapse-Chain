from __future__ import annotations

from . import RpcContext, RpcHandler, require_params


def register(methods: dict[str, RpcHandler]) -> None:
    methods["web3_clientVersion"] = web3_client_version


def web3_client_version(context: RpcContext, params: list[object]) -> str:
    require_params(params, min_count=0, max_count=0)
    return context.compat.client_version
