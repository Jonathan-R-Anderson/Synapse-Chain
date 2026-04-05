from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evm.exceptions import OutOfGasError
from execution.exceptions import (
    FeeRuleViolationError,
    InsufficientBalanceError,
    IntrinsicGasTooLowError,
    InvalidSignatureError,
    InvalidTransactionError,
    NonceTooHighError,
    NonceTooLowError,
    UnsupportedTransactionTypeError,
)
from transactions import TransactionDecodeError, TransactionSignatureError


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000
EXECUTION_ERROR = 3


class AlreadyKnownError(Exception):
    pass


class ReplacementUnderpricedError(Exception):
    pass


@dataclass(slots=True)
class JsonRpcError(Exception):
    code: int
    message: str
    data: Any = None

    def to_response(self, request_id: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": self.code,
                "message": self.message,
            },
        }
        if self.data is not None:
            payload["error"]["data"] = self.data
        return payload


class ExecutionReverted(JsonRpcError):
    def __init__(self, data: str | None = None) -> None:
        super().__init__(EXECUTION_ERROR, "execution reverted", data=data)


def parse_error(message: str = "parse error") -> JsonRpcError:
    return JsonRpcError(PARSE_ERROR, message)


def invalid_request(message: str = "invalid request") -> JsonRpcError:
    return JsonRpcError(INVALID_REQUEST, message)


def method_not_found(message: str = "method not found") -> JsonRpcError:
    return JsonRpcError(METHOD_NOT_FOUND, message)


def invalid_params(message: str = "invalid params") -> JsonRpcError:
    return JsonRpcError(INVALID_PARAMS, message)


def internal_error(message: str = "internal error") -> JsonRpcError:
    return JsonRpcError(INTERNAL_ERROR, message)


def server_error(message: str, data: Any = None) -> JsonRpcError:
    return JsonRpcError(SERVER_ERROR, message, data=data)


def map_exception(exc: Exception) -> JsonRpcError:
    if isinstance(exc, JsonRpcError):
        return exc
    if isinstance(exc, (TypeError, ValueError)):
        return invalid_params(str(exc))
    if isinstance(exc, AlreadyKnownError):
        return server_error("already known")
    if isinstance(exc, ReplacementUnderpricedError):
        return server_error("replacement transaction underpriced")
    if isinstance(exc, NonceTooLowError):
        return server_error("nonce too low")
    if isinstance(exc, NonceTooHighError):
        return server_error("nonce too high")
    if isinstance(exc, InvalidSignatureError | TransactionSignatureError):
        return server_error("invalid sender")
    if isinstance(exc, InsufficientBalanceError):
        return server_error("insufficient funds for gas * price + value")
    if isinstance(exc, IntrinsicGasTooLowError):
        return server_error("intrinsic gas too low")
    if isinstance(exc, UnsupportedTransactionTypeError):
        return server_error("unsupported transaction type")
    if isinstance(exc, TransactionDecodeError):
        return server_error("invalid transaction")
    if isinstance(exc, FeeRuleViolationError):
        return server_error(str(exc))
    if isinstance(exc, OutOfGasError):
        return server_error("out of gas")
    if isinstance(exc, InvalidTransactionError):
        return server_error(str(exc))
    return internal_error()
