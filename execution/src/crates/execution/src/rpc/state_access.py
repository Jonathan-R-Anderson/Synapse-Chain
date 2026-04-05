from __future__ import annotations

from dataclasses import dataclass, replace

from evm import ExecutionContext, ExecutionResult, Interpreter, StateDB, TraceCaptureSink
from evm.utils import compute_create_address
from execution import BlockEnvironment, ChainConfig, calculate_intrinsic_gas
from primitives import Address

from .block_access import BlockRecord, ExecutionNode, PendingPreview
from .types import BlockSelector, CallRequest, parse_block_selector


@dataclass(frozen=True, slots=True)
class ResolvedState:
    state: StateDB
    block_env: BlockEnvironment
    block_number: int
    block: BlockRecord | PendingPreview
    pending: bool


@dataclass(frozen=True, slots=True)
class CallResult:
    success: bool
    gas_limit: int
    gas_used: int
    gas_remaining: int
    gas_refunded: int
    output: bytes
    error: Exception | None
    reverted: bool
    created_address: Address | None
    trace: TraceCaptureSink | None = None


def _compute_call_gas_usage(
    *,
    gas_limit: int,
    gas_remaining: int,
    gas_refund: int,
    success: bool,
    chain_config: ChainConfig,
) -> tuple[int, int]:
    consumed_before_refund = gas_limit - gas_remaining
    refund_cap = consumed_before_refund // chain_config.gas_refund_quotient if success else 0
    refunded_gas = min(gas_refund, refund_cap)
    return gas_limit - (gas_remaining + refunded_gas), refunded_gas


class StateAccessor:
    def __init__(self, node: ExecutionNode) -> None:
        self.node = node

    def resolve(self, selector: BlockSelector | object | None) -> ResolvedState | None:
        block = self.node.block_by_selector(parse_block_selector(selector))
        if block is None:
            return None
        if isinstance(block, PendingPreview):
            return ResolvedState(
                state=block.state.clone(),
                block_env=block.block_env,
                block_number=block.block.header.number,
                block=block,
                pending=True,
            )
        return ResolvedState(
            state=block.post_state.clone(),
            block_env=BlockEnvironment.from_block(block.block, self.node.chain_config),
            block_number=block.block.header.number,
            block=block,
            pending=False,
        )

    def simulate_call(
        self,
        call: CallRequest,
        *,
        selector: BlockSelector | object | None,
        trace: bool = False,
        gas_limit: int | None = None,
    ) -> CallResult:
        resolved = self.resolve(selector)
        if resolved is None:
            raise ValueError("block not found")
        return self.simulate_call_on_state(
            resolved.state,
            resolved.block_env,
            call,
            trace=trace,
            gas_limit=gas_limit,
        )

    def simulate_call_on_state(
        self,
        state: StateDB,
        block_env: BlockEnvironment,
        call: CallRequest,
        *,
        trace: bool = False,
        gas_limit: int | None = None,
    ) -> CallResult:
        selected_gas_limit = gas_limit if gas_limit is not None else call.gas
        if selected_gas_limit is None:
            selected_gas_limit = min(
                block_env.gas_limit,
                self.node.compat_config.default_call_gas,
                self.node.compat_config.gas_estimation_cap,
            )
        synthetic = call.synthetic_transaction(self.node.chain_config, selected_gas_limit)
        intrinsic_gas = calculate_intrinsic_gas(synthetic)
        if selected_gas_limit < intrinsic_gas:
            raise ValueError("intrinsic gas too low")
        working_state = state.clone()
        if call.value and working_state.get_balance(call.from_address) < call.value:
            raise ValueError("insufficient balance for value transfer")
        runtime_gas = selected_gas_limit - intrinsic_gas
        sink = TraceCaptureSink(capture_memory_snapshots=True) if trace else None
        interpreter = Interpreter(state=working_state, trace_sink=sink)
        gas_price = call.effective_gas_price(
            block_env,
            self.node.compat_config.default_gas_price,
            self.node.compat_config.default_max_priority_fee_per_gas,
        )
        if call.to is None:
            result = self._simulate_create(
                state=working_state,
                interpreter=interpreter,
                call=call,
                gas=runtime_gas,
                block_env=block_env,
                gas_price=gas_price,
            )
        else:
            result = interpreter.call(
                address=call.to,
                caller=call.from_address,
                origin=call.from_address,
                value=call.value,
                calldata=call.data,
                gas=runtime_gas,
                static=False,
                gas_price=gas_price,
                chain_id=block_env.chain_id,
            )
        gas_used, gas_refunded = _compute_call_gas_usage(
            gas_limit=selected_gas_limit,
            gas_remaining=result.gas_remaining,
            gas_refund=result.gas_refund,
            success=result.success,
            chain_config=self.node.chain_config,
        )
        return CallResult(
            success=result.success,
            gas_limit=selected_gas_limit,
            gas_used=gas_used,
            gas_remaining=result.gas_remaining,
            gas_refunded=gas_refunded,
            output=result.output,
            error=result.error,
            reverted=result.reverted,
            created_address=result.created_address,
            trace=sink,
        )

    def _simulate_create(
        self,
        *,
        state: StateDB,
        interpreter: Interpreter,
        call: CallRequest,
        gas: int,
        block_env: BlockEnvironment,
        gas_price: int,
    ) -> ExecutionResult:
        sender_nonce = state.get_nonce(call.from_address)
        created_address = compute_create_address(call.from_address, sender_nonce)
        if not state.can_deploy_to(created_address):
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)
        snapshot = state.snapshot()
        state.get_or_create_account(created_address).nonce = 1
        if call.value and not state.transfer(call.from_address, created_address, call.value):
            state.restore(snapshot)
            return ExecutionResult(success=False, gas_remaining=0, output=b"", reverted=False)
        context = ExecutionContext(
            address=created_address,
            code_address=created_address,
            caller=call.from_address,
            origin=call.from_address,
            value=call.value,
            calldata=b"",
            code=call.data,
            gas=gas,
            static=False,
            depth=0,
            gas_price=gas_price,
            chain_id=block_env.chain_id,
        )
        result = interpreter.execute(context)
        if not result.success:
            state.restore(snapshot)
            return result
        state.set_code(created_address, result.output)
        return replace(result, created_address=created_address)
