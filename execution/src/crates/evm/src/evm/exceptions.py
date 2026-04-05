from __future__ import annotations


class EVMError(Exception):
    """Base class for interpreter failures."""


class StackUnderflowError(EVMError):
    pass


class StackOverflowError(EVMError):
    pass


class InvalidOpcodeError(EVMError):
    pass


class InvalidJumpDestinationError(EVMError):
    pass


class OutOfGasError(EVMError):
    pass


class StaticModeViolationError(EVMError):
    pass


class WriteProtectionError(StaticModeViolationError):
    pass


class CallDepthExceededError(EVMError):
    pass


class ReturnDataBoundsError(EVMError):
    pass


class ExecutionReturn(EVMError):
    def __init__(self, data: bytes) -> None:
        super().__init__("execution returned")
        self.data = bytes(data)


class ExecutionRevert(EVMError):
    def __init__(self, data: bytes) -> None:
        super().__init__("execution reverted")
        self.data = bytes(data)
