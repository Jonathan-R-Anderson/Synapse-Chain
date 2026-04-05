from __future__ import annotations

from dataclasses import replace

from evm.exceptions import OutOfGasError
from execution import calculate_intrinsic_gas

from .errors import ExecutionReverted, server_error
from .state_access import CallResult, StateAccessor
from .types import BlockSelector, CallRequest


class GasEstimator:
    def __init__(self, state_accessor: StateAccessor) -> None:
        self.state_accessor = state_accessor

    def estimate(self, call: CallRequest, *, selector: BlockSelector | object | None) -> int:
        resolved = self.state_accessor.resolve(selector)
        if resolved is None:
            raise ValueError("block not found")
        upper_bound = call.gas
        if upper_bound is None:
            upper_bound = min(
                resolved.block_env.gas_limit,
                self.state_accessor.node.compat_config.gas_estimation_cap,
            )
        lower_bound = calculate_intrinsic_gas(
            call.synthetic_transaction(self.state_accessor.node.chain_config, upper_bound)
        )
        initial = self.state_accessor.simulate_call(call, selector=selector, gas_limit=upper_bound)
        if not initial.success:
            self._raise_execution_failure(initial)
        if upper_bound <= lower_bound:
            return upper_bound
        high = upper_bound
        low = lower_bound
        while low < high:
            mid = (low + high) // 2
            outcome = self.state_accessor.simulate_call(call, selector=selector, gas_limit=mid)
            if outcome.success:
                high = mid
                continue
            if isinstance(outcome.error, OutOfGasError):
                low = mid + 1
                continue
            self._raise_execution_failure(outcome)
        return high

    @staticmethod
    def _raise_execution_failure(result: CallResult) -> None:
        if result.reverted:
            raise ExecutionReverted("0x" + result.output.hex())
        if result.error is not None:
            raise server_error(str(result.error))
        raise server_error("execution failed")
