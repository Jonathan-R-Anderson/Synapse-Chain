from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from crypto import keccak256
from primitives import Address, U256

from .callframe import CallFrame
from .context import ExecutionContext, ExecutionResult
from .exceptions import (
    CallDepthExceededError,
    EVMError,
    ExecutionRevert,
    ExecutionReturn,
    InvalidJumpDestinationError,
    InvalidOpcodeError,
    OutOfGasError,
    ReturnDataBoundsError,
    WriteProtectionError,
)
from .gas import (
    BASE_OPCODE_GAS,
    GAS_CALL,
    GAS_CALL_STIPEND,
    GAS_CALL_VALUE,
    GAS_CREATE,
    GAS_CREATE2_WORD,
    GAS_KECCAK256_WORD,
    GAS_LOG,
    GAS_LOG_DATA,
    GAS_LOG_TOPIC,
    GAS_NEW_ACCOUNT,
    copy_cost,
)
from .logs import LogEntry
from .memory import Memory
from .opcodes import (
    ADDRESS,
    BALANCE,
    BYTE,
    CALL,
    CALLDATALOAD,
    CALLDATACOPY,
    CALLDATASIZE,
    CALLER,
    CALLVALUE,
    CHAINID,
    CODECOPY,
    CODESIZE,
    CREATE,
    CREATE2,
    DELEGATECALL,
    EQ,
    GAS,
    GASPRICE,
    INVALID,
    ISZERO,
    JUMP,
    JUMPI,
    JUMPDEST,
    KECCAK256,
    MLOAD,
    MSIZE,
    MSTORE,
    MSTORE8,
    NOT,
    ORIGIN,
    PC,
    POP,
    PUSH0,
    REVERT,
    RETURN,
    RETURNDATACOPY,
    RETURNDATASIZE,
    SAR,
    SLOAD,
    SSTORE,
    STATICCALL,
    STOP,
    opcode_name,
    is_dup,
    is_log,
    is_push,
    is_swap,
    dup_index,
    log_topic_count,
    push_size,
    swap_index,
)
from .precompiles import PrecompileRegistry
from .state import StateDB
from .tracing import ExecutionTraceRow, FrameTraceEvent, TraceSink
from .utils import (
    MAX_CALL_DEPTH,
    ZERO_ADDRESS,
    address_to_int,
    buffer_read,
    compute_create2_address,
    compute_create_address,
    from_signed,
    int_to_address,
    to_signed,
    words_for_size,
)


_Handler = Callable[["Interpreter", CallFrame, int], None]


@dataclass(slots=True)
class Interpreter:
    state: StateDB | None = None
    precompiles: PrecompileRegistry | None = None
    trace_sink: TraceSink | None = None
    _dispatch: dict[int, _Handler] = field(init=False, repr=False)
    _step_storage_reads: list[tuple[int, int]] = field(init=False, default_factory=list, repr=False)
    _step_storage_writes: list[tuple[int, int]] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = StateDB()
        if self.precompiles is None:
            self.precompiles = PrecompileRegistry()
        self._dispatch: dict[int, _Handler] = {
            STOP: Interpreter._op_stop,
            0x01: Interpreter._op_add,
            0x02: Interpreter._op_mul,
            0x03: Interpreter._op_sub,
            0x04: Interpreter._op_div,
            0x06: Interpreter._op_mod,
            0x10: Interpreter._op_lt,
            0x11: Interpreter._op_gt,
            EQ: Interpreter._op_eq,
            ISZERO: Interpreter._op_iszero,
            0x16: Interpreter._op_and,
            0x17: Interpreter._op_or,
            0x18: Interpreter._op_xor,
            NOT: Interpreter._op_not,
            BYTE: Interpreter._op_byte,
            0x1B: Interpreter._op_shl,
            0x1C: Interpreter._op_shr,
            SAR: Interpreter._op_sar,
            KECCAK256: Interpreter._op_keccak256,
            ADDRESS: Interpreter._op_address,
            BALANCE: Interpreter._op_balance,
            ORIGIN: Interpreter._op_origin,
            CALLER: Interpreter._op_caller,
            CALLVALUE: Interpreter._op_callvalue,
            CALLDATALOAD: Interpreter._op_calldataload,
            CALLDATASIZE: Interpreter._op_calldatasize,
            CALLDATACOPY: Interpreter._op_calldatacopy,
            CODESIZE: Interpreter._op_codesize,
            CODECOPY: Interpreter._op_codecopy,
            GASPRICE: Interpreter._op_gasprice,
            RETURNDATASIZE: Interpreter._op_returndatasize,
            RETURNDATACOPY: Interpreter._op_returndatacopy,
            CHAINID: Interpreter._op_chainid,
            POP: Interpreter._op_pop,
            MLOAD: Interpreter._op_mload,
            MSTORE: Interpreter._op_mstore,
            MSTORE8: Interpreter._op_mstore8,
            SLOAD: Interpreter._op_sload,
            SSTORE: Interpreter._op_sstore,
            JUMP: Interpreter._op_jump,
            JUMPI: Interpreter._op_jumpi,
            PC: Interpreter._op_pc,
            MSIZE: Interpreter._op_msize,
            GAS: Interpreter._op_gas,
            JUMPDEST: Interpreter._op_jumpdest,
            CREATE: Interpreter._op_create,
            CALL: Interpreter._op_call,
            RETURN: Interpreter._op_return,
            DELEGATECALL: Interpreter._op_delegatecall,
            CREATE2: Interpreter._op_create2,
            STATICCALL: Interpreter._op_staticcall,
            REVERT: Interpreter._op_revert,
            INVALID: Interpreter._op_invalid,
        }

    def execute(self, context: ExecutionContext) -> ExecutionResult:
        snapshot = self.state.snapshot()
        result = self._execute_frame(context)
        if not result.success:
            self.state.restore(snapshot)
        return result

    def call(
        self,
        address: Address,
        caller: Address = ZERO_ADDRESS,
        origin: Address | None = None,
        value: int = 0,
        calldata: bytes = b"",
        gas: int = 1_000_000,
        static: bool = False,
        gas_price: int = 0,
        chain_id: int = 1,
    ) -> ExecutionResult:
        snapshot = self.state.snapshot()
        if value != 0:
            if static:
                return ExecutionResult(success=False, gas_remaining=0, error=WriteProtectionError(), reverted=False)
            if not self.state.transfer(caller, address, value):
                self.state.restore(snapshot)
                return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        code = self.state.get_code(address)
        precompile = self.precompiles.get(address)
        if precompile is not None and not code:
            gas_cost = precompile.gas_cost(calldata)
            if gas_cost > gas:
                self.state.restore(snapshot)
                return ExecutionResult(success=False, gas_remaining=0, error=OutOfGasError(), reverted=False)
            return ExecutionResult(success=True, gas_remaining=gas - gas_cost, output=precompile.run(calldata))

        context = ExecutionContext(
            address=address,
            code_address=address,
            caller=caller,
            origin=caller if origin is None else origin,
            value=value,
            calldata=calldata,
            code=code,
            gas=gas,
            static=static,
            depth=0,
            gas_price=gas_price,
            chain_id=chain_id,
        )
        result = self.execute(context)
        if not result.success:
            self.state.restore(snapshot)
        return result

    def _execute_frame(self, context: ExecutionContext) -> ExecutionResult:
        frame = CallFrame(context)
        self._notify_enter_frame(frame)
        try:
            while frame.pc < len(frame.context.code):
                opcode_position = frame.pc
                opcode = frame.context.code[frame.pc]
                frame.pc += 1

                self._execute_opcode(frame, opcode, opcode_position)

            result = ExecutionResult(
                success=True,
                gas_remaining=frame.gas_meter.remaining,
                gas_refund=frame.gas_meter.refund,
                logs=tuple(frame.logs),
            )
            self._notify_exit_frame(frame, result)
            return result
        except ExecutionReturn as signal:
            result = ExecutionResult(
                success=True,
                gas_remaining=frame.gas_meter.remaining,
                gas_refund=frame.gas_meter.refund,
                output=signal.data,
                logs=tuple(frame.logs),
            )
            self._notify_exit_frame(frame, result)
            return result
        except ExecutionRevert as signal:
            result = ExecutionResult(
                success=False,
                gas_remaining=frame.gas_meter.remaining,
                gas_refund=0,
                output=signal.data,
                reverted=True,
            )
            self._notify_exit_frame(frame, result)
            return result
        except EVMError as exc:
            frame.gas_meter.consume_all()
            result = ExecutionResult(success=False, gas_remaining=0, gas_refund=0, error=exc, reverted=False)
            self._notify_exit_frame(frame, result)
            return result

    def _capture_memory_snapshot(self, frame: CallFrame) -> bytes | None:
        if self.trace_sink is None or not self.trace_sink.capture_memory_snapshots:
            return None
        return bytes(frame.memory._data)

    def _emit_trace_row(
        self,
        frame: CallFrame,
        opcode: int,
        pc: int,
        gas_before: int,
        stack_before: list[int],
        memory_before: bytes | None,
        *,
        error: Exception | None = None,
    ) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.on_step(
            ExecutionTraceRow(
                depth=frame.context.depth,
                pc=pc,
                opcode=opcode,
                opcode_name=opcode_name(opcode),
                gas_before=gas_before,
                gas_after=frame.gas_meter.remaining,
                stack_before=tuple(stack_before),
                stack_after=tuple(frame.stack.to_list()),
                memory_before=memory_before,
                memory_after=self._capture_memory_snapshot(frame),
                storage_reads=tuple(self._step_storage_reads),
                storage_writes=tuple(self._step_storage_writes),
                error=None if error is None else f"{type(error).__name__}: {error}",
            )
        )

    def _execute_opcode(self, frame: CallFrame, opcode: int, opcode_position: int) -> None:
        stack_before = frame.stack.to_list() if self.trace_sink is not None else []
        memory_before = self._capture_memory_snapshot(frame)
        gas_before = frame.gas_meter.remaining
        self._step_storage_reads = []
        self._step_storage_writes = []
        try:
            if is_push(opcode):
                self._charge_base(frame, opcode)
                self._op_push(frame, opcode)
                self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before)
                return
            if is_dup(opcode):
                self._charge_base(frame, opcode)
                frame.stack.dup(dup_index(opcode))
                self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before)
                return
            if is_swap(opcode):
                self._charge_base(frame, opcode)
                frame.stack.swap(swap_index(opcode))
                self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before)
                return
            if is_log(opcode):
                self._op_log(frame, opcode)
                self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before)
                return

            self._charge_base(frame, opcode)
            handler = self._dispatch.get(opcode)
            if handler is None:
                raise InvalidOpcodeError(f"invalid opcode 0x{opcode:02x} at pc={opcode_position}")
            handler(self, frame, opcode)
            self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before)
        except Exception as exc:
            self._emit_trace_row(frame, opcode, opcode_position, gas_before, stack_before, memory_before, error=exc)
            raise

    def _notify_enter_frame(self, frame: CallFrame) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.on_enter_frame(
            FrameTraceEvent(
                event="enter",
                depth=frame.context.depth,
                address=frame.context.address,
                caller=frame.context.caller,
                code_address=frame.context.code_address or frame.context.address,
                gas=frame.context.gas,
            )
        )

    def _notify_exit_frame(self, frame: CallFrame, result: ExecutionResult) -> None:
        if self.trace_sink is None:
            return
        self.trace_sink.on_exit_frame(
            FrameTraceEvent(
                event="exit",
                depth=frame.context.depth,
                address=frame.context.address,
                caller=frame.context.caller,
                code_address=frame.context.code_address or frame.context.address,
                gas=frame.context.gas,
                success=result.success,
                gas_remaining=result.gas_remaining,
                output=result.output,
            )
        )

    def _charge_base(self, frame: CallFrame, opcode: int) -> None:
        frame.gas_meter.charge(BASE_OPCODE_GAS.get(opcode, 0), opcode_name(opcode))

    def _memory_view(self, frame: CallFrame, offset: int, size: int) -> bytes:
        cost = frame.memory.expansion_cost(offset, size)
        frame.gas_meter.charge(cost, "memory expansion")
        frame.memory.expand_for_access(offset, size)
        return frame.memory.read(offset, size)

    def _charge_memory(self, frame: CallFrame, offset: int, size: int) -> None:
        cost = frame.memory.expansion_cost(offset, size)
        frame.gas_meter.charge(cost, "memory expansion")
        frame.memory.expand_for_access(offset, size)

    def _op_push(self, frame: CallFrame, opcode: int) -> None:
        size = push_size(opcode)
        value = 0 if size == 0 else int.from_bytes(frame.context.code[frame.pc : frame.pc + size], byteorder="big", signed=False)
        frame.pc += size
        frame.stack.push(value)

    def _binary_op(self, frame: CallFrame, operation: Callable[[int, int], int]) -> None:
        top = frame.stack.pop()
        next_value = frame.stack.pop()
        frame.stack.push(operation(next_value, top))

    def _op_stop(self, frame: CallFrame, opcode: int) -> None:
        raise ExecutionReturn(b"")

    def _op_add(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a + b)

    def _op_mul(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a * b)

    def _op_sub(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a - b)

    def _op_div(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: 0 if b == 0 else a // b)

    def _op_mod(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: 0 if b == 0 else a % b)

    def _op_lt(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: 1 if a < b else 0)

    def _op_gt(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: 1 if a > b else 0)

    def _op_eq(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: 1 if a == b else 0)

    def _op_iszero(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(1 if frame.stack.pop() == 0 else 0)

    def _op_and(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a & b)

    def _op_or(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a | b)

    def _op_xor(self, frame: CallFrame, opcode: int) -> None:
        self._binary_op(frame, lambda a, b: a ^ b)

    def _op_not(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(~frame.stack.pop())

    def _op_byte(self, frame: CallFrame, opcode: int) -> None:
        index = frame.stack.pop()
        value = frame.stack.pop()
        if index >= 32:
            frame.stack.push(0)
            return
        shift = (31 - index) * 8
        frame.stack.push((value >> shift) & 0xFF)

    def _op_shl(self, frame: CallFrame, opcode: int) -> None:
        shift = frame.stack.pop()
        value = frame.stack.pop()
        frame.stack.push(0 if shift >= 256 else (value << shift))

    def _op_shr(self, frame: CallFrame, opcode: int) -> None:
        shift = frame.stack.pop()
        value = frame.stack.pop()
        frame.stack.push(0 if shift >= 256 else (value >> shift))

    def _op_sar(self, frame: CallFrame, opcode: int) -> None:
        shift = frame.stack.pop()
        value = frame.stack.pop()
        if shift >= 256:
            frame.stack.push((1 << 256) - 1 if value & (1 << 255) else 0)
            return
        signed_value = to_signed(value)
        frame.stack.push(from_signed(signed_value >> shift))

    def _op_keccak256(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        size = frame.stack.pop()
        self._charge_memory(frame, offset, size)
        frame.gas_meter.charge(words_for_size(size) * GAS_KECCAK256_WORD, "keccak words")
        frame.stack.push(int.from_bytes(keccak256(frame.memory.read(offset, size)).to_bytes(), byteorder="big", signed=False))

    def _op_address(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(address_to_int(frame.context.address))

    def _op_balance(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(self.state.get_balance(int_to_address(frame.stack.pop())))

    def _op_origin(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(address_to_int(frame.context.origin))

    def _op_caller(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(address_to_int(frame.context.caller))

    def _op_callvalue(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.context.value)

    def _op_calldataload(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        frame.stack.push(int.from_bytes(buffer_read(frame.context.calldata, offset, 32), byteorder="big", signed=False))

    def _op_calldatasize(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(len(frame.context.calldata))

    def _op_calldatacopy(self, frame: CallFrame, opcode: int) -> None:
        memory_offset = frame.stack.pop()
        data_offset = frame.stack.pop()
        size = frame.stack.pop()
        self._charge_memory(frame, memory_offset, size)
        frame.gas_meter.charge(copy_cost(size), "calldata copy")
        frame.memory.write(memory_offset, buffer_read(frame.context.calldata, data_offset, size))

    def _op_codesize(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(len(frame.context.code))

    def _op_codecopy(self, frame: CallFrame, opcode: int) -> None:
        memory_offset = frame.stack.pop()
        code_offset = frame.stack.pop()
        size = frame.stack.pop()
        self._charge_memory(frame, memory_offset, size)
        frame.gas_meter.charge(copy_cost(size), "code copy")
        frame.memory.write(memory_offset, buffer_read(frame.context.code, code_offset, size))

    def _op_gasprice(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.context.gas_price)

    def _op_returndatasize(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(len(frame.last_return_data))

    def _op_returndatacopy(self, frame: CallFrame, opcode: int) -> None:
        memory_offset = frame.stack.pop()
        data_offset = frame.stack.pop()
        size = frame.stack.pop()
        if data_offset + size > len(frame.last_return_data):
            raise ReturnDataBoundsError("returndata access out of bounds")
        self._charge_memory(frame, memory_offset, size)
        frame.gas_meter.charge(copy_cost(size), "returndata copy")
        frame.memory.write(memory_offset, frame.last_return_data[data_offset : data_offset + size])

    def _op_chainid(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.context.chain_id)

    def _op_pop(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.pop()

    def _op_mload(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        self._charge_memory(frame, offset, 32)
        frame.stack.push(frame.memory.read_word(offset))

    def _op_mstore(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        value = frame.stack.pop()
        self._charge_memory(frame, offset, 32)
        frame.memory.write_word(offset, value)

    def _op_mstore8(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        value = frame.stack.pop()
        self._charge_memory(frame, offset, 1)
        frame.memory.write(offset, bytes([value & 0xFF]))

    def _op_sload(self, frame: CallFrame, opcode: int) -> None:
        key = frame.stack.pop()
        value = self.state.get_storage(frame.context.address, key)
        self._step_storage_reads.append((key, value))
        frame.stack.push(value)

    def _op_sstore(self, frame: CallFrame, opcode: int) -> None:
        if frame.context.static:
            raise WriteProtectionError("SSTORE is not allowed during STATICCALL")
        key = frame.stack.pop()
        value = frame.stack.pop()
        cost, refund = self.state.estimate_sstore(frame.context.address, key, value)
        frame.gas_meter.charge(cost, "SSTORE")
        self.state.set_storage(frame.context.address, key, value)
        self._step_storage_writes.append((key, value))
        if refund:
            frame.gas_meter.add_refund(refund)

    def _op_jump(self, frame: CallFrame, opcode: int) -> None:
        destination = frame.stack.pop()
        self._jump_to(frame, destination)

    def _op_jumpi(self, frame: CallFrame, opcode: int) -> None:
        destination = frame.stack.pop()
        condition = frame.stack.pop()
        if condition != 0:
            self._jump_to(frame, destination)

    def _op_pc(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.pc - 1)

    def _op_msize(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.memory.size)

    def _op_gas(self, frame: CallFrame, opcode: int) -> None:
        frame.stack.push(frame.gas_meter.remaining)

    def _op_jumpdest(self, frame: CallFrame, opcode: int) -> None:
        return None

    def _op_return(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        size = frame.stack.pop()
        self._charge_memory(frame, offset, size)
        raise ExecutionReturn(frame.memory.read(offset, size))

    def _op_revert(self, frame: CallFrame, opcode: int) -> None:
        offset = frame.stack.pop()
        size = frame.stack.pop()
        self._charge_memory(frame, offset, size)
        raise ExecutionRevert(frame.memory.read(offset, size))

    def _op_invalid(self, frame: CallFrame, opcode: int) -> None:
        raise InvalidOpcodeError("INVALID opcode encountered")

    def _op_log(self, frame: CallFrame, opcode: int) -> None:
        if frame.context.static:
            raise WriteProtectionError("LOG opcodes are not allowed during STATICCALL")
        offset = frame.stack.pop()
        size = frame.stack.pop()
        topic_count = log_topic_count(opcode)
        topics = tuple(U256(frame.stack.pop()) for _ in range(topic_count))
        self._charge_memory(frame, offset, size)
        frame.gas_meter.charge(
            GAS_LOG + (topic_count * GAS_LOG_TOPIC) + (size * GAS_LOG_DATA),
            opcode_name(opcode),
        )
        frame.logs.append(LogEntry(address=frame.context.address, topics=topics, data=frame.memory.read(offset, size)))

    def _jump_to(self, frame: CallFrame, destination: int) -> None:
        if destination not in frame.jumpdests:
            raise InvalidJumpDestinationError(f"invalid jump destination {destination}")
        frame.pc = destination

    def _call_common(
        self,
        frame: CallFrame,
        gas_requested: int,
        target: Address,
        calldata: bytes,
        out_offset: int,
        out_size: int,
        value: int,
        address: Address,
        caller: Address,
        static: bool,
        code_address: Address,
    ) -> ExecutionResult:
        if frame.context.depth + 1 >= MAX_CALL_DEPTH:
            return ExecutionResult(success=False, gas_remaining=0, error=CallDepthExceededError(), reverted=False)
        if static and value != 0:
            raise WriteProtectionError("non-zero value transfer is not allowed in STATICCALL")
        if value != 0 and frame.context.static:
            raise WriteProtectionError("CALL with value is not allowed during STATICCALL")

        self._charge_memory(frame, out_offset, out_size)

        call_cost = GAS_CALL
        if value != 0:
            call_cost += GAS_CALL_VALUE
            if not self.state.account_exists(target):
                call_cost += GAS_NEW_ACCOUNT
        frame.gas_meter.charge(call_cost, "CALL base")

        if value != 0 and self.state.get_balance(frame.context.address) < value:
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        available_for_child = frame.gas_meter.remaining - (frame.gas_meter.remaining // 64)
        forwarded_gas = min(gas_requested, max(available_for_child, 0))
        child_gas = forwarded_gas
        if value != 0:
            child_gas += GAS_CALL_STIPEND
        frame.gas_meter.charge(forwarded_gas, "CALL gas forwarded")

        snapshot = self.state.snapshot()
        transferred = True
        if value != 0:
            transferred = self.state.transfer(frame.context.address, target, value)
        if not transferred:
            self.state.restore(snapshot)
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        precompile = self.precompiles.get(code_address)
        if precompile is not None:
            gas_cost = precompile.gas_cost(calldata)
            if child_gas < gas_cost:
                self.state.restore(snapshot)
                return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)
            output = precompile.run(calldata)
            result = ExecutionResult(success=True, gas_remaining=child_gas - gas_cost, output=output)
        else:
            code = self.state.get_code(code_address)
            if not code:
                result = ExecutionResult(success=True, gas_remaining=child_gas, output=b"")
            else:
                child_context = ExecutionContext(
                    address=address,
                    code_address=code_address,
                    caller=caller,
                    origin=frame.context.origin,
                    value=value,
                    calldata=calldata,
                    code=code,
                    gas=child_gas,
                    static=static,
                    depth=frame.context.depth + 1,
                    gas_price=frame.context.gas_price,
                    chain_id=frame.context.chain_id,
                )
                result = self._execute_frame(child_context)

        if not result.success:
            self.state.restore(snapshot)
        elif result.gas_refund:
            frame.gas_meter.add_refund(result.gas_refund)
        frame.gas_meter.return_gas(min(result.gas_remaining, forwarded_gas))
        frame.last_return_data = result.output
        copied = result.output[:out_size]
        if copied:
            frame.memory.write(out_offset, copied)
        return result

    def _op_call(self, frame: CallFrame, opcode: int) -> None:
        gas_requested = frame.stack.pop()
        target = int_to_address(frame.stack.pop())
        value = frame.stack.pop()
        in_offset = frame.stack.pop()
        in_size = frame.stack.pop()
        out_offset = frame.stack.pop()
        out_size = frame.stack.pop()
        self._charge_memory(frame, in_offset, in_size)
        calldata = frame.memory.read(in_offset, in_size)
        result = self._call_common(
            frame,
            gas_requested=gas_requested,
            target=target,
            calldata=calldata,
            out_offset=out_offset,
            out_size=out_size,
            value=value,
            address=target,
            caller=frame.context.address,
            static=frame.context.static,
            code_address=target,
        )
        frame.stack.push(1 if result.success else 0)
        if result.success:
            frame.logs.extend(result.logs)

    def _op_delegatecall(self, frame: CallFrame, opcode: int) -> None:
        gas_requested = frame.stack.pop()
        target = int_to_address(frame.stack.pop())
        in_offset = frame.stack.pop()
        in_size = frame.stack.pop()
        out_offset = frame.stack.pop()
        out_size = frame.stack.pop()
        self._charge_memory(frame, in_offset, in_size)
        calldata = frame.memory.read(in_offset, in_size)
        result = self._call_common(
            frame,
            gas_requested=gas_requested,
            target=target,
            calldata=calldata,
            out_offset=out_offset,
            out_size=out_size,
            value=frame.context.value,
            address=frame.context.address,
            caller=frame.context.caller,
            static=frame.context.static,
            code_address=target,
        )
        frame.stack.push(1 if result.success else 0)
        if result.success:
            frame.logs.extend(result.logs)

    def _op_staticcall(self, frame: CallFrame, opcode: int) -> None:
        gas_requested = frame.stack.pop()
        target = int_to_address(frame.stack.pop())
        in_offset = frame.stack.pop()
        in_size = frame.stack.pop()
        out_offset = frame.stack.pop()
        out_size = frame.stack.pop()
        self._charge_memory(frame, in_offset, in_size)
        calldata = frame.memory.read(in_offset, in_size)
        result = self._call_common(
            frame,
            gas_requested=gas_requested,
            target=target,
            calldata=calldata,
            out_offset=out_offset,
            out_size=out_size,
            value=0,
            address=target,
            caller=frame.context.address,
            static=True,
            code_address=target,
        )
        frame.stack.push(1 if result.success else 0)
        if result.success:
            frame.logs.extend(result.logs)

    def _create_common(self, frame: CallFrame, value: int, offset: int, size: int, salt: int | None) -> ExecutionResult:
        if frame.context.static:
            raise WriteProtectionError("CREATE is not allowed during STATICCALL")
        if frame.context.depth + 1 >= MAX_CALL_DEPTH:
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        self._charge_memory(frame, offset, size)
        init_code = frame.memory.read(offset, size)
        extra_cost = 0 if salt is None else words_for_size(len(init_code)) * GAS_CREATE2_WORD
        frame.gas_meter.charge(GAS_CREATE + extra_cost, "CREATE")

        if self.state.get_balance(frame.context.address) < value:
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        creator_nonce = self.state.get_nonce(frame.context.address)
        new_address = (
            compute_create_address(frame.context.address, creator_nonce)
            if salt is None
            else compute_create2_address(frame.context.address, salt, init_code)
        )
        self.state.increment_nonce(frame.context.address)
        if not self.state.can_deploy_to(new_address):
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        snapshot = self.state.snapshot()
        self.state.get_or_create_account(new_address).nonce = 1
        if not self.state.transfer(frame.context.address, new_address, value):
            self.state.restore(snapshot)
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)

        available_for_child = frame.gas_meter.remaining - (frame.gas_meter.remaining // 64)
        child_gas = max(available_for_child, 0)
        frame.gas_meter.charge(child_gas, "CREATE gas forwarded")
        child_context = ExecutionContext(
            address=new_address,
            code_address=new_address,
            caller=frame.context.address,
            origin=frame.context.origin,
            value=value,
            calldata=b"",
            code=init_code,
            gas=child_gas,
            static=False,
            depth=frame.context.depth + 1,
            gas_price=frame.context.gas_price,
            chain_id=frame.context.chain_id,
        )
        result = self._execute_frame(child_context)
        if result.success:
            self.state.set_code(new_address, result.output)
            if result.gas_refund:
                frame.gas_meter.add_refund(result.gas_refund)
        else:
            self.state.restore(snapshot)
        frame.gas_meter.return_gas(result.gas_remaining)
        frame.last_return_data = result.output
        return ExecutionResult(
            success=result.success,
            gas_remaining=result.gas_remaining,
            output=result.output,
            logs=result.logs,
            reverted=result.reverted,
            error=result.error,
            created_address=new_address if result.success else None,
        )

    def _op_create(self, frame: CallFrame, opcode: int) -> None:
        value = frame.stack.pop()
        offset = frame.stack.pop()
        size = frame.stack.pop()
        result = self._create_common(frame, value=value, offset=offset, size=size, salt=None)
        frame.stack.push(0 if not result.success or result.created_address is None else address_to_int(result.created_address))
        if result.success:
            frame.logs.extend(result.logs)

    def _op_create2(self, frame: CallFrame, opcode: int) -> None:
        value = frame.stack.pop()
        offset = frame.stack.pop()
        size = frame.stack.pop()
        salt = frame.stack.pop()
        result = self._create_common(frame, value=value, offset=offset, size=size, salt=salt)
        frame.stack.push(0 if not result.success or result.created_address is None else address_to_int(result.created_address))
        if result.success:
            frame.logs.extend(result.logs)
