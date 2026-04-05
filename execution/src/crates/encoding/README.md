# Encoding Crate

This crate contains the canonical Ethereum-style encoding helpers used across the execution workspace.

## What It Contains

- `rlp.py`: Recursive Length Prefix encoding and decoding primitives

## Why It Exists

RLP is the serialization format used by:

- block headers
- receipts
- transactions
- trie nodes
- account and storage values at various commitment boundaries

Keeping the encoder isolated makes the rest of the code easier to test and lets the higher-level crates depend on one deterministic implementation.

## Design Notes

- the encoder is strict about shape and byte handling
- callers decide the meaning of the encoded objects; this crate only handles the transport format
- the implementation is intentionally dependency-light so it works cleanly inside the rest of the Python workspace

## Running Tests

```bash
python3 -m unittest discover execution/src/crates/encoding/tests
```
