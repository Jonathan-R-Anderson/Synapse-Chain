from __future__ import annotations

from primitives import Address


class ExecutionLayerError(Exception):
    """Base class for execution-layer validation and transition failures."""


class InvalidTransactionError(ExecutionLayerError):
    """Raised when a transaction is invalid before EVM execution begins."""


class InvalidSignatureError(InvalidTransactionError):
    pass


class UnsupportedTransactionTypeError(InvalidTransactionError):
    pass


class FeeRuleViolationError(InvalidTransactionError):
    pass


class IntrinsicGasTooLowError(InvalidTransactionError):
    pass


class InvalidZKProofError(InvalidTransactionError):
    pass


class NonceMismatchError(InvalidTransactionError):
    def __init__(self, sender: Address, expected: int, actual: int) -> None:
        super().__init__(
            f"invalid nonce for {sender.to_hex()}: expected {expected}, got {actual}"
        )
        self.sender = sender
        self.expected = expected
        self.actual = actual


class NonceTooLowError(NonceMismatchError):
    pass


class NonceTooHighError(NonceMismatchError):
    pass


class InsufficientBalanceError(InvalidTransactionError):
    def __init__(self, sender: Address, balance: int, required: int) -> None:
        super().__init__(
            f"insufficient balance for {sender.to_hex()}: balance {balance}, required {required}"
        )
        self.sender = sender
        self.balance = balance
        self.required = required


class BlockValidationError(ExecutionLayerError):
    pass


class BlockGasExceededError(BlockValidationError):
    pass
