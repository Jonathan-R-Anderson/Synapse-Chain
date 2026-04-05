# Transactions Crate

This crate defines Ethereum-compatible transaction models plus transaction-level validation rules.

## Supported Transaction Families

- legacy transactions
- EIP-1559 transactions
- ZK-extended transactions layered on top of dynamic-fee transactions

## Module Responsibilities

- `models.py`: canonical transaction dataclasses, signing, hashing, and encode/decode support
- `validation.py`: sender recovery, intrinsic gas checks, fee validation, and chain-id checks
- `constants.py`: transaction-type constants and shared validation values

## Why It Exists

The transaction layer sits between crypto and execution:

- it converts signed payloads into strongly typed objects
- it enforces transaction-shape rules before execution starts
- it provides a stable API for block building, RPC submission, replay, and testing

The execution crate then builds on top of this crate instead of re-encoding fee and signature rules itself.

## Running Tests

```bash
python3 -m unittest discover execution/src/crates/transactions/tests
```
