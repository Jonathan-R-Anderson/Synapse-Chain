# Execution Crate

This crate now covers three layers:

- execution-state transitions on top of the `transactions` and `evm` crates
- Ethereum-style block packaging, hashing, validation, and extension sidecars
- role-aware chain/state syncing with checkpoints, snapshots, and proof hooks
- JSON-RPC execution APIs for wallet/tool compatibility
- correctness tooling for fixture execution, block replay, and opcode tracing

## Module Responsibilities

- `state_transition.py` / `tx_validation.py` / `fee_market.py`: transaction validation and execution-layer accounting
- `block_header.py` / `receipt.py` / `block.py`: canonical header, receipt, block, and extended block models
- `trie.py`: transaction-root, receipt-root, ommers-hash, logs-bloom, and state-root helpers
- `base_fee.py`: EIP-1559 base-fee update logic
- `block_builder.py`: deterministic block construction from parent context plus execution artifacts
- `block_validator.py`: explicit structural, parent-link, gas, root, and base-fee validation
- `zk_hooks.py`: optional proof sidecars and deterministic proof-verification stubs
- `dht_hooks.py`: optional DHT publication/retrieval hooks and in-memory test store
- `block_executor.py`: sequential transaction execution against `evm.StateDB`
- `contracts/`: compiled artifact loading, limited Solidity ABI encoding, and deployment tooling for contract-creation transactions
- `sync/`: role-aware sync runtime, peer selection, checkpoint persistence, canonical-chain tracking, snapshots, and proof verification hooks
- `execution_tests/`: strict JSON fixture loader, fork-rule selection, result comparison, and fixture CLI
- `replay/`: offline block-bundle loader plus block replay executor/reporting
- `debug/`: opcode-trace capture and trace-diff utilities for mismatch debugging
- `rpc/`: JSON-RPC transport, Ethereum-compatible serializers, txpool access, gas estimation, and tracing adapters

## Sync Subsystem

The sync package supports multiple runtime roles instead of assuming one generic node. The same codebase can be configured as:

- full node
- light node
- archive node
- validator
- builder
- bootnode
- DHT node
- state provider
- RPC-backed full/archive node
- indexer
- zk prover / zk verifier
- watchtower
- snapshot generator

Implemented sync strategies include:

- full sync with sequential block execution and persisted checkpoints
- snap sync with header sync, verified snapshot restoration, and replay to head
- light sync with header tracking and proof-verified account/storage fragment retrieval

The runtime stores chain metadata, sync checkpoints, snapshots, and reconstructed state under the configured node state directory so the node can resume after restart without discarding progress.

## How Transaction Execution Works

1. Recover the sender and validate signature, nonce, fee fields, and intrinsic gas.
2. Deduct upfront gas, increment the sender nonce, and execute the call/create in the EVM.
3. Refund unused gas, apply capped storage refunds, pay the beneficiary tip, and burn the base fee when enabled.
4. Emit an Ethereum-style receipt and commit the transaction state so later transactions see the new storage baseline.

Execution failures such as `REVERT`, out-of-gas, or invalid opcodes still produce receipts and still consume gas. Pre-execution invalid transactions raise and do not mutate state.

## How Block Construction Works

`BlockBuilder` consumes a parent block/header, a transaction list, and an `ExecutionPayload` from the execution engine. It computes:

- `state_root`
- `transactions_root`
- `receipts_root`
- `logs_bloom`
- `gas_used`
- `base_fee_per_gas`

It then builds a canonical `BlockHeader`, assembles the `Block`, and validates the final structure before returning it.

## ZK and DHT Hooks

The canonical header remains Ethereum-like. Optional proof and distribution data live in `ExtendedBlock` sidecars:

- `BlockProofBundle` attaches execution-proof metadata without changing the block hash
- `BlockDistributionRecord` tracks optional DHT publication metadata without changing canonical serialization

## Correctness Harness

Phase 7 adds three offline-first tools on top of the execution engine:

1. `execution_tests.runner`
   Loads JSON execution fixtures, normalizes strict hex/state inputs, runs transactions or message calls, and compares post-state, receipts, logs, gas, and roots.
2. `replay.block_executor`
   Loads block bundles or exported RPC-style block JSON, replays the block through `apply_block`, and reports root/receipt/log mismatches.
3. `debug.trace`
   Replays a fixture case or block transaction with an opcode-level trace sink that captures gas, stack transitions, optional memory snapshots, and storage reads/writes.

Fork selection is centralized in `execution_tests/fork_rules.py` so receipt semantics, fee-model expectations, and future fork-specific validation do not leak through the rest of the harness.

## JSON-RPC Server

Phase 8 adds an Ethereum-style execution RPC surface on top of the existing execution engine.

Run the server with:

```bash
python3 -m rpc.server --host 127.0.0.1 --port 8545 --chain-id 1 --mode instant
```

Modes:

- `instant`: accepted raw transactions are mined immediately into a new block
- `mempool`: accepted raw transactions remain pending until mined through the in-process `ExecutionNode.append_pending_block()` helper

The HTTP transport uses JSON-RPC 2.0 over `POST`, supports batch requests, and includes permissive CORS headers for local wallet/tool development.

### Supported RPC Methods

- `web3_clientVersion`
- `net_version`
- `eth_accounts`
- `eth_blockNumber`
- `eth_call`
- `eth_chainId`
- `eth_estimateGas`
- `eth_feeHistory`
- `eth_gasPrice`
- `eth_getBalance`
- `eth_getBlockByNumber`
- `eth_getCode`
- `eth_getStorageAt`
- `eth_getTransactionByHash`
- `eth_getTransactionCount`
- `eth_getTransactionReceipt`
- `eth_maxPriorityFeePerGas`
- `eth_sendRawTransaction`
- `debug_traceCall`
- `debug_traceTransaction`
- `dev_getConfig`
- `dev_setCoinbase`
- `dev_mine`

### Tooling Notes

MetaMask:

- set the RPC URL to `http://127.0.0.1:8545`
- use the configured chain ID passed to `rpc.server`
- `eth_chainId`, `eth_blockNumber`, `eth_getBalance`, `eth_getCode`, `eth_call`, `eth_estimateGas`, `eth_sendRawTransaction`, and receipt lookup are implemented

Hardhat / Foundry:

- raw signed transaction submission works for legacy and EIP-1559 transactions already supported by the execution engine
- receipts, transaction lookups, block lookup, storage reads, gas estimation, and debug traces are available
- revert payloads from `eth_call` and `eth_estimateGas` are surfaced as JSON-RPC errors with revert data

Developer extensions:

- `dev_setCoinbase` changes the beneficiary used for later dev mining calls
- `dev_mine` can force empty-block production and optionally credit a configurable block reward to the chosen beneficiary
- these methods are intended for local-dev workflows and the graphical client, not Ethereum RPC compatibility

I2P overlay for long-running demo nodes:

- the `examples/container_node.py` roles can run with `EXECUTION_PRIVACY_NETWORK=i2p`
- when enabled, the execution demo roles expose a JSON-over-stream sync overlay through an I2P SAM bridge instead of relying only on in-process fixture peers
- the shared bootstrap destination file is controlled by `EXECUTION_I2P_BOOTSTRAP_FILE`
- this currently applies to the execution demo roles, not the separate consensus simulator

## Contract Deployment Tooling

The execution crate now includes `execution.contracts`, which turns contract deployment into a supported execution feature instead of requiring ad hoc raw-transaction assembly.

What it provides:

- contract artifact loading from `.bin`, `.hex`, and common JSON artifact formats
- ABI loading from standalone `.abi` files or JSON artifacts
- constructor argument encoding for common Solidity scalar types
- local signing of legacy and EIP-1559 contract-creation transactions
- deployment submission and receipt polling over the existing JSON-RPC API

From the repository root, the simplest entrypoint is:

```bash
python3 deploy_contract.py --help
```

If you want to invoke the crate module directly, use the same `PYTHONPATH` setup as the RPC server:

```bash
PYTHONPATH=execution/src/crates/primitives/src:execution/src/crates/crypto/src:execution/src/crates/encoding/src:execution/src/crates/state/src:execution/src/crates/zk/src:execution/src/crates/transactions/src:execution/src/crates/evm/src:execution/src/crates/execution/src \
python3 -m execution.contracts --help
```

Example deployment from `solc --bin --abi` outputs:

```bash
python3 deploy_contract.py \
  --rpc-url http://127.0.0.1:8545 \
  --artifact build/Counter.bin \
  --abi-path build/Counter.abi \
  --private-key 0x1
```

Example deployment from a JSON artifact with multiple contracts:

```bash
python3 deploy_contract.py \
  --rpc-url http://127.0.0.1:8545 \
  --artifact build/Combined.json \
  --contract-name Counter \
  --private-key 0x1
```

The command signs locally, submits with `eth_sendRawTransaction`, waits for `eth_getTransactionReceipt` by default, and reports the deployed address plus receipt status as JSON.

### Known Limitations

- the server is built on the Python standard library HTTP server in this workspace because `fastapi` is not installed here
- `safe` and `finalized` block tags are not implemented yet
- no filter/log subscription methods are exposed yet
- `eth_sendTransaction` is intentionally unsupported because the node does not manage unlocked private keys
- mempool replacement is implemented for same-sender same-nonce transactions, but the pool is intentionally simple and optimized for local-dev compatibility rather than adversarial network conditions

## Known Limitations

- The trie helper uses the existing trie/account abstractions, but the execution engine itself still runs on `evm.StateDB`.
- Contract creation still does not charge code-deposit gas.
- Access lists are accounted for in intrinsic gas only; warm/cold access tracking is not implemented.
- The built-in contract deployment CLI currently ABI-encodes common scalar constructor arguments only; arrays and tuples are not implemented yet.
- `Block.serialize()` is a deterministic internal container that includes receipts; canonical Ethereum header hashing still comes only from `BlockHeader.rlp_encode()`.
- The correctness runner is strict about hex normalization and will reject ambiguous encodings such as `0x00`.
- The block replay path can execute exported blocks only when full transaction bodies and a compatible pre-state snapshot are present. Hash-only RPC block responses are treated as reference data, not executable inputs.
- This workspace uses the existing `unittest` discovery harness in `run_tests.py`. `pytest`-style entrypoints were requested conceptually, but `pytest` is not installed in this environment.

## Running Tests

```bash
python3 execution/src/crates/run_tests.py
```

## Fixture CLI

```bash
python3 -m execution_tests.runner --fixture execution/src/crates/execution/tests/fixtures/simple_execution_fixture.json
```

## Block Replay CLI

```bash
python3 -m replay.block_executor --block path/to/block_bundle.json
```

## Trace CLI

```bash
python3 -m debug.trace --fixture execution/src/crates/execution/tests/fixtures/simple_execution_fixture.json --case legacy_zero_fee_transfer
```
