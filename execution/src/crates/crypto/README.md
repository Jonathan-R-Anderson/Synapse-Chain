# Crypto Crate

This crate provides the execution client's cryptographic building blocks.

## What It Contains

- `keccak.py`: Keccak-256 hashing helpers used for transaction hashes, trie roots, and address derivation
- `secp256k1.py`: key generation, signing, signature recovery, and low-level secp256k1 utilities
- `address.py`: address derivation helpers such as `address_from_private_key`

## How It Is Used

The crate is consumed by:

- `transactions` for signing and sender recovery
- `execution` for header/block hashing and proof sidecars
- `state` for secure trie key transforms
- tests and fixtures for deterministic demo accounts

## Design Notes

- APIs are intentionally small and Python-native
- hashing and address helpers stay separate from transaction logic so the transaction crate can remain focused on wire formats and validation
- secp256k1 support is geared toward Ethereum-style signing and recovery flows

## Running Tests

```bash
python3 -m unittest discover execution/src/crates/crypto/tests
```
