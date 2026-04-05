# EVM Crate

This package implements a modular Python EVM interpreter intended for Ethereum-compatible execution testing.

## Architecture

- `stack.py`: 1024-item uint256 stack with `PUSH`, `DUP`, and `SWAP` support.
- `memory.py`: linear byte-addressable memory with explicit expansion cost calculation.
- `gas.py`: gas meter plus opcode and dynamic gas helpers.
- `storage.py`, `account.py`, `state.py`: in-memory account and storage state with snapshot/restore.
- `context.py`, `callframe.py`: immutable execution context plus mutable per-frame runtime state.
- `interpreter.py`: opcode dispatch loop, nested calls, creation, revert/return handling, and log propagation.
- `precompiles.py`: precompile registry for `ecrecover`, `sha256`, `ripemd160`, and `identity`.

## Running Tests

Run the EVM suite directly with:

```bash
python3 -m unittest discover execution/src/crates/evm/tests
```

Run the whole monorepo suite with:

```bash
python3 execution/src/crates/run_tests.py
```

## Solidity Fixtures

There is no local `solc` binary available in this environment, so the integration tests use included ABI-compatible bytecode fixtures with Solidity-style selector dispatch, revert behavior, proxy forwarding, and constructor patterns.

## Known Limitations

- Gas accounting is explicit and close to Ethereum, but not fork-perfect. Warm/cold access accounting and code-deposit gas are not yet modeled.
- The execution state is an in-memory snapshotting state DB for interpreter work, not the trie-backed state crate.
- The classic precompiles are implemented; the larger arithmetic precompiles are left as extension points.
