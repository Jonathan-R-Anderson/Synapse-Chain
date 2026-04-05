from __future__ import annotations

import argparse
import asyncio
import hashlib
import random

from .config import NetworkConfig
from .node import InMemoryNetwork, Node


def make_node_id(index: int) -> int:
    return int(hashlib.sha256(f"node:{index}".encode("utf-8")).hexdigest(), 16)


async def build_network(
    node_count: int,
    *,
    byzantine_count: int = 0,
    degree: int = 4,
    config: NetworkConfig | None = None,
) -> tuple[InMemoryNetwork, list[Node]]:
    network = InMemoryNetwork(config=config, seed=7)
    operator_count = max(network.config.committee.committee_size + 1, 4, node_count // 3)
    regions = ("us", "eu", "apac", "latam")
    nodes = [
        Node(
            make_node_id(index),
            network=network,
            region=regions[index % len(regions)],
            operator_id=f"operator-{index % operator_count}",
            storage_capacity=(1 << 29) * (1 + (index % 4)),
            malicious=index < byzantine_count,
        )
        for index in range(node_count)
    ]

    rng = random.Random(11)
    for index, node in enumerate(nodes):
        for offset in range(1, min(degree, node_count - 1) + 1):
            network.connect(node.node_id, nodes[(index + offset) % node_count].node_id)
        extra = rng.sample([candidate for candidate in nodes if candidate.node_id != node.node_id], k=min(2, max(0, node_count - 1)))
        for candidate in extra:
            network.connect(node.node_id, candidate.node_id)

    await asyncio.gather(*(node.start() for node in nodes))
    return network, nodes


async def run_network_simulation(
    *,
    node_count: int = 16,
    rounds: int = 6,
    byzantine_count: int = 2,
    degree: int = 4,
) -> list[Node]:
    network, nodes = await build_network(node_count, byzantine_count=byzantine_count, degree=degree)
    try:
        for round_index in range(1, rounds + 1):
            origin = nodes[round_index % len(nodes)]
            await origin.submit_transaction({"round": round_index, "value": f"tx-{round_index}"})
            await asyncio.sleep(0.05)
            coordinator = nodes[(round_index * 3) % len(nodes)]
            result = await coordinator.finalize_pending_block()
            await asyncio.sleep(0.1)
            sync_target = nodes[-1]
            await sync_target.sync_from_peer(nodes[0].node_id, from_height=max(0, sync_target.head_height - 1))
            committee = "none" if result is None else result.committee_id[:8]
            honest_heads = len({node.head_hash for node in nodes if not node.malicious})
            print(
                f"round={round_index:02d} committed={result is not None} committee={committee} "
                f"head_height={max(node.head_height for node in nodes)} honest_heads={honest_heads}"
            )
        return nodes
    finally:
        await asyncio.gather(*(node.stop() for node in nodes), return_exceptions=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the async P2P networking simulation.")
    parser.add_argument("--nodes", type=int, default=16)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--byzantine", type=int, default=2)
    parser.add_argument("--degree", type=int, default=4)
    args = parser.parse_args(argv)
    asyncio.run(
        run_network_simulation(
            node_count=args.nodes,
            rounds=args.rounds,
            byzantine_count=args.byzantine,
            degree=args.degree,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
