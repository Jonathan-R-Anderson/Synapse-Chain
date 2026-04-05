# Execution Workspace

This directory is a multi-package Python workspace for the execution side of the client.

## Crates

- [`primitives`](./primitives/README.md): fixed-width types such as `U256`, `Address`, and `Hash`
- [`crypto`](./crypto/README.md): Keccak-256, secp256k1, and address derivation
- [`encoding`](./encoding/README.md): RLP encoding and decoding
- [`state`](./state/README.md): account/state storage plus trie commitments
- [`zk`](./zk/README.md): proof abstractions and verifier hooks
- [`transactions`](./transactions/README.md): signed transaction models and validation
- [`evm`](./evm/README.md): interpreter, storage, calls, logs, and precompiles
- [`execution`](./execution/README.md): block processing, RPC, sync, replay, and tracing

## How The Packages Fit Together

1. `primitives` defines the shared fixed-width values.
2. `crypto` and `encoding` provide the low-level hashing, signing, and RLP utilities.
3. `state` provides account/state storage and commitment structures.
4. `transactions` models signed payloads and transaction-level rules.
5. `evm` executes bytecode against an in-memory state database.
6. `execution` ties all of that together into block processing, JSON-RPC, sync, and tooling.

## Tests

Run the whole execution workspace suite with:

```bash
python3 execution/src/crates/run_tests.py
```

Each crate also has its own `README.md` and test directory.
