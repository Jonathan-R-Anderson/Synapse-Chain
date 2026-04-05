from __future__ import annotations

import asyncio
import hashlib
import unittest
from dataclasses import replace

from consensus.networking import NetworkConfig
from consensus.networking.config import CommitteeConfig
from consensus.networking.dht import xor_distance
from consensus.networking.node import Node
from consensus.networking.pon import ObservedMetrics
from consensus.networking.simulation import build_network, make_node_id


class NetworkingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        await asyncio.sleep(0)

    async def _stop_nodes(self, *nodes: Node) -> None:
        await asyncio.gather(*(node.stop() for node in nodes), return_exceptions=True)

    async def test_gossip_propagates_transactions_across_the_mesh(self) -> None:
        _, nodes = await build_network(6, byzantine_count=0, degree=3)
        try:
            transaction = await nodes[0].submit_transaction({"kind": "transfer", "nonce": 1})
            await asyncio.sleep(0.25)
            self.assertTrue(all(transaction.hash in node.pending_transactions for node in nodes))
        finally:
            await self._stop_nodes(*nodes)

    async def test_dht_store_find_value_and_sorted_lookup(self) -> None:
        _, nodes = await build_network(6, byzantine_count=0, degree=3)
        try:
            key = hashlib.sha256(b"networking-dht").hexdigest()
            value = {"height": 7, "payload": "hello"}
            await nodes[0].dht.store(key, value)
            found = await nodes[-1].dht.find_value(key)
            self.assertEqual(found, value)

            target_id = int(key, 16)
            closest = await nodes[-1].dht.find_node(target_id)
            distances = [xor_distance(peer_id, target_id) for peer_id in closest]
            self.assertEqual(distances, sorted(distances))
        finally:
            await self._stop_nodes(*nodes)

    async def test_pon_score_reports_are_signed_and_cross_validated(self) -> None:
        _, nodes = await build_network(4, byzantine_count=0, degree=2)
        try:
            observer = nodes[0]
            subject = nodes[1]
            subject.pon.relayed_packets = 100
            subject.pon.set_storage_usage(1 << 30)
            report = subject.pon.compute_score()

            self.assertTrue(observer.pon.verify_report(report))
            self.assertFalse(observer.pon.verify_report(replace(report, computed_score=report.computed_score + 0.1)))

            observer.score_reports[subject.node_id] = report
            observer.pon.observed_metrics[subject.node_id] = ObservedMetrics(relayed_packets=2, successful_messages=32)
            accepted = observer.resolve_score(subject.node_id)

            self.assertLessEqual(accepted.packets_relayed, 3)
            self.assertLessEqual(accepted.computed_score, report.computed_score)
        finally:
            await self._stop_nodes(*nodes)

    async def test_committee_selection_fills_target_size_and_picks_highest_score_leader(self) -> None:
        config = NetworkConfig(committee=CommitteeConfig(candidate_count=8, committee_size=4, weighted_selection=True))
        _, nodes = await build_network(8, byzantine_count=0, degree=3, config=config)
        try:
            selector_node = nodes[2]
            for index, node in enumerate(nodes, start=1):
                node.pon.relayed_packets = index * 25
                node.pon.set_storage_usage(index * (1 << 28))
                selector_node.score_reports[node.node_id] = node.pon.compute_score()

            selection = await selector_node.committee_selector.select_committee("01" * 32)
            self.assertEqual(len(selection.member_ids), config.committee.committee_size)
            self.assertEqual(len(selection.member_ids), len(set(selection.member_ids)))
            self.assertIn(selection.leader_id, selection.member_ids)
            self.assertEqual(selection.score_distribution[selection.leader_id], max(selection.score_distribution.values()))
        finally:
            await self._stop_nodes(*nodes)

    async def test_pbft_commits_with_one_byzantine_member(self) -> None:
        _, nodes = await build_network(8, byzantine_count=1, degree=3)
        try:
            transaction = await nodes[1].submit_transaction({"kind": "contract_call", "nonce": 1})
            await asyncio.sleep(0.25)
            result = await nodes[3].finalize_pending_block()
            await asyncio.sleep(0.25)

            self.assertTrue(result.committed)
            self.assertGreaterEqual(len(result.signatures), nodes[3].bft.quorum_size(len(result.member_ids)))
            self.assertTrue(all(node.head_height == 1 for node in nodes))
            self.assertTrue(all(transaction.hash not in node.pending_transactions for node in nodes))
        finally:
            await self._stop_nodes(*nodes)

    async def test_sync_protocol_reconstructs_state_for_late_joiner(self) -> None:
        _, nodes = await build_network(6, byzantine_count=0, degree=3)
        late_node: Node | None = None
        try:
            for nonce in (1, 2):
                await nodes[0].submit_transaction({"kind": "sync-test", "nonce": nonce})
                await asyncio.sleep(0.2)
                await nodes[1].finalize_pending_block()
                await asyncio.sleep(0.2)

            late_node = Node(
                make_node_id(99),
                network=nodes[0].network,
                region="us",
                operator_id="operator-late",
                storage_capacity=1 << 30,
            )
            nodes[0].network.connect(late_node.node_id, nodes[0].node_id)
            nodes[0].network.connect(late_node.node_id, nodes[1].node_id)
            await late_node.start()

            applied = await late_node.sync_from_peer(nodes[0].node_id, from_height=0)
            self.assertEqual(len(applied), 2)
            self.assertEqual(late_node.head_height, nodes[0].head_height)
            self.assertEqual(late_node.head_hash, nodes[0].head_hash)
            self.assertEqual(sorted(late_node.blocks_by_height), [1, 2])
        finally:
            cleanup = [*nodes]
            if late_node is not None:
                cleanup.append(late_node)
            await self._stop_nodes(*cleanup)


if __name__ == "__main__":
    unittest.main()
