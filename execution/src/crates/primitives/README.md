# Primitives Crate

This crate defines the fixed-width scalar and byte-array types used across the rest of the execution client.

## What It Contains

- `U256`: unsigned 256-bit integer with checked and wrapping arithmetic helpers
- `Address`: fixed-width 20-byte account address
- `Hash`: fixed-width 32-byte hash value

These types give the higher-level crates deterministic widths, canonical hex rendering, and explicit conversions instead of passing around raw Python integers and `bytes`.

## Why It Exists

The rest of the workspace relies on a common type system for:

- state and trie keys
- block and transaction hashes
- address handling
- serialization boundaries
- gas and balance math

Keeping those definitions in one crate avoids subtle width and encoding mismatches between execution, transactions, crypto, and state code.

## Source Layout

- `src/primitives/types.py`: `U256`, `Address`, `Hash`

## Running Tests

```bash
python3 -m unittest discover execution/src/crates/primitives/tests
```
