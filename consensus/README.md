# Hybrid Consensus Client

This package implements an Ethereum-style beacon-chain consensus client, but replaces pure stake dominance with a composite validator score.

## Model

The client keeps Ethereum-like consensus structure:

- beacon state
- slots and epochs
- validator registry
- committee attestations
- checkpoints
- justification and finality
- LMD-style fork choice

What changes is validator weight. Instead of stake alone, proposer selection, committee influence, and finality weight are driven by:

- identity and burn-backed registration
- capped stake
- proof-of-work / useful compute contribution
- storage and retrievability performance
- network relay / uptime / diversity / coverage
- recent protocol activity and reliability

The composite score is recomputed deterministically from config-driven formulas in [scoring.py](./src/consensus/scoring.py).

## How It Differs From Ethereum PoS

- proposer selection is a weighted lottery over composite score, not raw stake
- committee membership is weighted sampling, not stake-only shuffling
- attestation weight is the sum of validator composite scores
- justification/finality thresholds use total eligible composite weight
- validators must clear minimum identity, stake, storage, network, and activity thresholds before they can participate

## Proposers And Committees

Epoch randomness is derived deterministically from the beacon state RANDAO mix and checkpoints. Each eligible validator gets a slot-specific lottery ticket from:

- epoch randomness
- validator id
- slot number

Selection uses weighted sampling without replacement. Higher composite score improves probability, but no validator wins deterministically just by having the highest score.

## Finality

At each epoch transition the client:

- rewards attesters and proposers
- applies inactivity and resource penalties
- recomputes validator scores
- measures attesting composite weight for recent checkpoints
- justifies checkpoints once the configured threshold is met
- finalizes a justified parent when its child checkpoint is later justified

This preserves the structure of Ethereum PoS finality while changing the weight source from stake to composite contribution.

## Proof Interfaces

Placeholder verifier hooks live in [proofs.py](./src/consensus/proofs.py):

- `verify_pow_claim`
- `verify_storage_proof`
- `verify_retrievability_challenge`
- `verify_network_relay_proof`
- `verify_coverage_proof`
- `verify_useful_compute_claim`
- `verify_identity_claim`

They are deterministic mocks today, but the module boundaries are explicit so real cryptographic proof systems can be plugged in later.

## Networking

Phase 11 adds a modular asyncio networking stack under [networking](./src/consensus/networking):

- gossip propagation for transactions, blocks, attestations, peer discovery, and sync traffic
- a Kademlia-style DHT with 256-bit node ids, XOR distance, k-buckets, iterative lookup, and replicated storage
- Proof-of-Network scoring based on coverage, relayed packets, uptime, storage, and local-density penalty
- PBFT-style committee consensus with propose, prevote, precommit, and commit phases

Each node maintains peer health, an in-memory transport queue, a routing table, signed PoN score reports, pending transactions, finalized blocks, and sync waiters. The code is structured so the transport can later be replaced with real sockets or libp2p-style plumbing without rewriting the consensus rules.

## Committee Selection

Networking committees are built in two steps:

1. The DHT finds the closest candidate nodes for a transaction or block key.
2. The committee selector ranks those candidates by verified PoN score and performs weighted sampling.

The selector prefers operator and region diversity, but will relax those constraints just enough to keep a full committee when the candidate pool is too correlated. That preserves PBFT liveness without giving up the diversity bias when enough independent nodes exist.

## PBFT Finality

For each proposed block:

- the selected leader broadcasts a proposal to the committee
- members verify the parent, block shape, and committee metadata
- members exchange prevotes, precommits, and commits
- a block is finalized once at least `2/3 + 1` committee signatures are collected

The resulting commit certificate is attached to the block, gossiped through the network, and stored in the DHT alongside the transactions it finalizes.

## Running

Beacon-style hybrid-consensus simulation:

```bash
python3 consensus/run_simulation.py --validators 24 --epochs 6
```

Or:

```bash
PYTHONPATH=consensus/src python3 -m consensus.simulation --validators 24 --epochs 6
```

Networking simulation:

```bash
python3 consensus/run_network_simulation.py --nodes 10 --rounds 3 --byzantine 1 --degree 3
```

Or:

```bash
PYTHONPATH=consensus/src python3 -m consensus.networking.simulation --nodes 10 --rounds 3 --byzantine 1 --degree 3
```

Tests:

```bash
python3 consensus/run_tests.py
```

The tests are pytest-compatible, but this environment does not currently have `pytest` installed, so the local runner uses `unittest` discovery. The suite covers hybrid scoring, committee selection, finality, gossip propagation, DHT lookups, signed PoN reporting, PBFT commits, and late-joiner sync.

## Current Limits

- transport is an in-memory asyncio network, not a real TCP/QUIC/libp2p stack yet
- commit certificates are tuples of deterministic placeholder signatures, not real aggregated cryptographic signatures
- score reports and proof verifiers are deterministic mocks intended to be replaced with cryptographic attestation and proof systems later
