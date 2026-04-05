# ZK Crate

This crate contains the proof abstractions and verifier hooks used by the rest of the client for zero-knowledge aware workflows.

## What It Contains

- `proofs.py`: proof models, proof types, and serializable proof payloads
- `verifiers.py`: verifier protocol, verifier registry, and verification gas-cost model

## Current Scope

The crate does not implement production zk proving systems. It provides the interfaces that let the rest of the workspace:

- attach proof sidecars to blocks or transactions
- register proof verifiers by proof type
- estimate verification gas
- keep zk integration points separate from canonical execution logic

This keeps future prover/verifier upgrades localized instead of spreading proof-specific code across the execution engine.

## Downstream Consumers

- `transactions`: optional zk transaction extensions
- `execution`: block proof sidecars and execution hooks
- `sync`: future zk-ready sync verification interfaces

## Running Tests

```bash
python3 -m unittest discover execution/src/crates/zk/tests
```
