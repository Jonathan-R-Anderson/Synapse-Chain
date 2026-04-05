from __future__ import annotations


STOP = 0x00
ADD = 0x01
MUL = 0x02
SUB = 0x03
DIV = 0x04
MOD = 0x06
LT = 0x10
GT = 0x11
EQ = 0x14
ISZERO = 0x15
AND = 0x16
OR = 0x17
XOR = 0x18
NOT = 0x19
BYTE = 0x1A
SHL = 0x1B
SHR = 0x1C
SAR = 0x1D
KECCAK256 = 0x20
ADDRESS = 0x30
BALANCE = 0x31
ORIGIN = 0x32
CALLER = 0x33
CALLVALUE = 0x34
CALLDATALOAD = 0x35
CALLDATASIZE = 0x36
CALLDATACOPY = 0x37
CODESIZE = 0x38
CODECOPY = 0x39
GASPRICE = 0x3A
RETURNDATASIZE = 0x3D
RETURNDATACOPY = 0x3E
CHAINID = 0x46
POP = 0x50
MLOAD = 0x51
MSTORE = 0x52
MSTORE8 = 0x53
SLOAD = 0x54
SSTORE = 0x55
JUMP = 0x56
JUMPI = 0x57
PC = 0x58
MSIZE = 0x59
GAS = 0x5A
JUMPDEST = 0x5B
PUSH0 = 0x5F
LOG0 = 0xA0
LOG4 = 0xA4
CREATE = 0xF0
CALL = 0xF1
RETURN = 0xF3
DELEGATECALL = 0xF4
CREATE2 = 0xF5
STATICCALL = 0xFA
REVERT = 0xFD
INVALID = 0xFE


OPCODE_NAMES = {
    STOP: "STOP",
    ADD: "ADD",
    MUL: "MUL",
    SUB: "SUB",
    DIV: "DIV",
    MOD: "MOD",
    LT: "LT",
    GT: "GT",
    EQ: "EQ",
    ISZERO: "ISZERO",
    AND: "AND",
    OR: "OR",
    XOR: "XOR",
    NOT: "NOT",
    BYTE: "BYTE",
    SHL: "SHL",
    SHR: "SHR",
    SAR: "SAR",
    KECCAK256: "KECCAK256",
    ADDRESS: "ADDRESS",
    BALANCE: "BALANCE",
    ORIGIN: "ORIGIN",
    CALLER: "CALLER",
    CALLVALUE: "CALLVALUE",
    CALLDATALOAD: "CALLDATALOAD",
    CALLDATASIZE: "CALLDATASIZE",
    CALLDATACOPY: "CALLDATACOPY",
    CODESIZE: "CODESIZE",
    CODECOPY: "CODECOPY",
    GASPRICE: "GASPRICE",
    RETURNDATASIZE: "RETURNDATASIZE",
    RETURNDATACOPY: "RETURNDATACOPY",
    CHAINID: "CHAINID",
    POP: "POP",
    MLOAD: "MLOAD",
    MSTORE: "MSTORE",
    MSTORE8: "MSTORE8",
    SLOAD: "SLOAD",
    SSTORE: "SSTORE",
    JUMP: "JUMP",
    JUMPI: "JUMPI",
    PC: "PC",
    MSIZE: "MSIZE",
    GAS: "GAS",
    JUMPDEST: "JUMPDEST",
    PUSH0: "PUSH0",
    CREATE: "CREATE",
    CALL: "CALL",
    RETURN: "RETURN",
    DELEGATECALL: "DELEGATECALL",
    CREATE2: "CREATE2",
    STATICCALL: "STATICCALL",
    REVERT: "REVERT",
    INVALID: "INVALID",
}


def opcode_name(opcode: int) -> str:
    if 0x60 <= opcode <= 0x7F:
        return f"PUSH{opcode - 0x5F}"
    if 0x80 <= opcode <= 0x8F:
        return f"DUP{opcode - 0x7F}"
    if 0x90 <= opcode <= 0x9F:
        return f"SWAP{opcode - 0x8F}"
    if LOG0 <= opcode <= LOG4:
        return f"LOG{opcode - LOG0}"
    return OPCODE_NAMES.get(opcode, f"UNKNOWN_0x{opcode:02x}")


def is_push(opcode: int) -> bool:
    return opcode == PUSH0 or 0x60 <= opcode <= 0x7F


def push_size(opcode: int) -> int:
    if opcode == PUSH0:
        return 0
    if 0x60 <= opcode <= 0x7F:
        return opcode - 0x5F
    raise ValueError("opcode is not a PUSH variant")


def is_dup(opcode: int) -> bool:
    return 0x80 <= opcode <= 0x8F


def dup_index(opcode: int) -> int:
    return opcode - 0x7F


def is_swap(opcode: int) -> bool:
    return 0x90 <= opcode <= 0x9F


def swap_index(opcode: int) -> int:
    return opcode - 0x8F


def is_log(opcode: int) -> bool:
    return LOG0 <= opcode <= LOG4


def log_topic_count(opcode: int) -> int:
    return opcode - LOG0
