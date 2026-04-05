from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from execution import BlockEnvironment, apply_transaction

from .block_access import ExecutionNode
from .errors import server_error
from .state_access import StateAccessor
from .types import CallRequest, parse_hash


@dataclass(frozen=True, slots=True)
class TraceOptions:
    disable_memory: bool = False
    disable_stack: bool = False
    disable_storage: bool = False

    @classmethod
    def from_json(cls, value: object | None) -> "TraceOptions":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise TypeError("trace options must be an object")
        return cls(
            disable_memory=bool(value.get("disableMemory", False)),
            disable_stack=bool(value.get("disableStack", False)),
            disable_storage=bool(value.get("disableStorage", False)),
        )


class TransactionTracer:
    def __init__(self, node: ExecutionNode, state_accessor: StateAccessor) -> None:
        self.node = node
        self.state_accessor = state_accessor

    def trace_transaction(self, tx_hash: object, *, options: TraceOptions | None = None) -> dict[str, object]:
        options = TraceOptions() if options is None else options
        record = self.node.transaction_by_hash(parse_hash(tx_hash).to_hex())
        if record is None:
            raise server_error("transaction not found")
        if record.block_number == 0:
            raise server_error("genesis transactions are not traceable")
        pre_block = self.node.block_by_number(record.block_number - 1)
        if pre_block is None:
            raise server_error("pre-state for transaction tracing is unavailable")
        state = pre_block.post_state.clone()
        block_record = self.node.block_by_number(record.block_number)
        assert block_record is not None
        block_env = BlockEnvironment.from_block(block_record.block, self.node.chain_config)
        for prior in block_record.transaction_records[: record.transaction_index]:
            apply_transaction(
                state=state,
                transaction=prior.transaction,
                block_env=block_env,
                chain_config=self.node.chain_config,
                verifier_registry=self.node.verifier_registry,
            )
        call = CallRequest(
            from_address=record.sender,
            to=record.transaction.to,
            gas=int(record.transaction.gas_limit),
            gas_price=getattr(record.transaction, "gas_price", None),
            max_fee_per_gas=getattr(record.transaction, "max_fee_per_gas", None),
            max_priority_fee_per_gas=getattr(record.transaction, "max_priority_fee_per_gas", None),
            value=int(record.transaction.value),
            data=record.transaction.data,
            access_list=getattr(record.transaction, "access_list", ()),
        )
        result = self.state_accessor.simulate_call_on_state(
            state,
            block_env,
            call,
            trace=True,
            gas_limit=int(record.transaction.gas_limit),
        )
        if result.trace is None:
            raise server_error("trace capture failed")
        return format_trace_result(result, options=options)

    def trace_call(
        self,
        call: CallRequest,
        *,
        selector: object | None,
        options: TraceOptions | None = None,
    ) -> dict[str, object]:
        options = TraceOptions() if options is None else options
        result = self.state_accessor.simulate_call(call, selector=selector, trace=True)
        if result.trace is None:
            raise server_error("trace capture failed")
        return format_trace_result(result, options=options)


def _memory_words(raw: bytes | None) -> list[str] | None:
    if raw is None:
        return None
    if not raw:
        return []
    padded = raw + (b"\x00" * ((32 - (len(raw) % 32)) % 32))
    return ["0x" + padded[index : index + 32].hex() for index in range(0, len(padded), 32)]


def format_trace_result(result: Any, *, options: TraceOptions) -> dict[str, object]:
    sink = result.trace
    assert sink is not None
    storage_state: dict[str, str] = {}
    struct_logs: list[dict[str, object]] = []
    for row in sink.rows:
        if not options.disable_storage:
            for key, value in row.storage_writes:
                storage_state[f"0x{key:064x}"] = f"0x{value:064x}"
        gas_cost = max(row.gas_before - row.gas_after, 0)
        entry: dict[str, object] = {
            "pc": row.pc,
            "op": row.opcode_name,
            "gas": row.gas_before,
            "gasCost": gas_cost,
            "depth": row.depth + 1,
        }
        if not options.disable_stack:
            entry["stack"] = [f"0x{value:064x}" for value in row.stack_before]
        if not options.disable_memory:
            entry["memory"] = _memory_words(row.memory_before)
        if not options.disable_storage:
            entry["storage"] = dict(storage_state)
        if row.error is not None:
            entry["error"] = row.error
        struct_logs.append(entry)
    response: dict[str, object] = {
        "gas": result.gas_used,
        "failed": not result.success,
        "returnValue": result.output.hex(),
        "structLogs": struct_logs,
    }
    if result.reverted:
        response["error"] = "execution reverted"
    elif result.error is not None:
        response["error"] = str(result.error)
    return response
