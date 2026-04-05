# State Crate

This package implements the Phase 2 execution-layer state model for the Python client.

## Architecture

- `state.core.State` is the stable public API used by future execution logic.
- `state.account.Account` is the canonical account object with `nonce`, `balance`, `code_hash`, and `storage_root`.
- `state.backends.hashmap.HashMapStateBackend` provides the Stage A in-memory representation.
- `state.backends.mpt.MptStateBackend` provides the Stage B trie-backed commitment layer.
- `state.trie.MerklePatriciaTrie` is a reusable hexary Merkle Patricia Trie builder with secure key transforms.

## How Accounts Are Stored

Each logical account is represented by:

- the `Account` record itself
- raw bytecode kept outside the account under its `code_hash`
- per-account storage managed separately from the account header

Account serialization follows the Ethereum account tuple:

```text
rlp([nonce, balance, storage_root, code_hash])
```

The public `set_account()` API keeps those derived fields coherent by requiring code changes to go through `set_code()` and storage changes to go through `set_storage()`.

## How Storage Works

Storage is modeled as `U256 -> U256`.

- Hashmap backend: storage is a `StorageMap` backed by a Python dictionary.
- MPT backend: storage is a per-account `MerklePatriciaTrie`.
- Zero-valued writes delete the slot, matching Ethereum storage semantics.
- Storage values are committed as RLP-encoded integers.

## How Trie Roots Are Computed

- Account trie logical key: `Address`
- Storage trie logical key: `U256` slot
- Trie path derivation: `keccak256(address)` for accounts and `keccak256(slot_bytes32)` for storage
- Node encoding: Ethereum hex-prefix encoding plus RLP
- External trie root: `keccak256(rlp(root_node))`

The hashmap backend rebuilds tries during `commit()` so it produces the same committed roots as the MPT backend. The trie backend maintains trie objects directly and also recomputes their roots on `commit()`.

## Swapping Backends

Use the same `State` facade with different backend instances:

```python
from state import HashMapStateBackend, MptStateBackend, State

state_a = State(HashMapStateBackend())
state_b = State(MptStateBackend())
```

All public read/write methods stay the same. This keeps higher-level execution logic decoupled from the underlying commitment storage.

## Snapshots

`State.snapshot()` captures an in-memory clone of the active backend and `State.revert()` restores it. This is intentionally simple for now, but the backend boundary leaves room for future journaling, pruning, and persistent database implementations.
