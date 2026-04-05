from __future__ import annotations

import asyncio
import hashlib
import heapq
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..utils import stable_json_bytes
from .bft import PBFTService
from .committee import CommitteeSelector
from .config import NetworkConfig
from .dht import DHTService
from .gossip import GossipService
from .pon import ProofOfNetworkService
from .sync import SyncService
from .types import Block, Message, MessageType, PeerRecord, Transaction, payload_hash


@dataclass(slots=True)
class LinkState:
    latency_ms: float
    drop_rate: float


class InMemoryNetwork:
    def __init__(self, config: NetworkConfig | None = None, *, seed: int = 1) -> None:
        self.config = NetworkConfig() if config is None else config
        self._rng = random.Random(seed)
        self._nodes: dict[int, Node] = {}
        self._links: dict[tuple[int, int], LinkState] = {}

    def register(self, node: "Node") -> None:
        self._nodes[node.node_id] = node

    def get_node(self, node_id: int) -> "Node | None":
        return self._nodes.get(node_id)

    def get_secret(self, node_id: int) -> bytes:
        node = self._nodes[node_id]
        return node.secret

    def connect(self, left_id: int, right_id: int, *, latency_ms: float | None = None, drop_rate: float | None = None) -> None:
        if latency_ms is None:
            latency_ms = max(0.1, self.config.base_latency_ms + self._rng.uniform(-self.config.latency_jitter_ms, self.config.latency_jitter_ms))
        resolved_drop_rate = self.config.drop_rate if drop_rate is None else drop_rate
        self._links[(left_id, right_id)] = LinkState(latency_ms=latency_ms, drop_rate=resolved_drop_rate)
        self._links[(right_id, left_id)] = LinkState(latency_ms=latency_ms, drop_rate=resolved_drop_rate)
        left = self._nodes[left_id]
        right = self._nodes[right_id]
        left.add_peer(right)
        right.add_peer(left)

    def peers_of(self, node_id: int) -> list[int]:
        return [peer_id for (left_id, peer_id), _ in self._links.items() if left_id == node_id]

    def _route(self, sender_id: int, recipient_id: int) -> tuple[list[int], float] | None:
        if sender_id == recipient_id:
            return [sender_id], 0.0
        frontier: list[tuple[float, int, list[int]]] = [(0.0, sender_id, [sender_id])]
        best_cost: dict[int, float] = {sender_id: 0.0}
        while frontier:
            cost, current, path = heapq.heappop(frontier)
            if current == recipient_id:
                return path, cost
            for neighbor in self.peers_of(current):
                link = self._links[(current, neighbor)]
                next_cost = cost + link.latency_ms
                if next_cost >= best_cost.get(neighbor, float("inf")):
                    continue
                best_cost[neighbor] = next_cost
                heapq.heappush(frontier, (next_cost, neighbor, [*path, neighbor]))
        return None

    async def deliver(self, sender_id: int, recipient_id: int, message: Message) -> bool:
        recipient = self._nodes.get(recipient_id)
        sender = self._nodes.get(sender_id)
        if recipient is None or sender is None:
            return False
        routed = self._route(sender_id, recipient_id)
        if routed is None:
            return False
        path, total_latency = routed
        first_hop = self._links[(path[0], path[1])] if len(path) > 1 else LinkState(latency_ms=0.0, drop_rate=0.0)
        if self._rng.random() < first_hop.drop_rate:
            sender.mark_delivery(recipient_id, total_latency, success=False)
            return False
        await asyncio.sleep(total_latency / 1000.0)
        for current, nxt in zip(path, path[1:]):
            hop = self._links[(current, nxt)]
            self._nodes[current].mark_delivery(nxt, hop.latency_ms, success=True)
            self._nodes[nxt].mark_delivery(current, hop.latency_ms, success=True)
        for relay_id in path[1:-1]:
            self._nodes[relay_id].pon.record_relay(origin_id=sender_id)
        await recipient.enqueue_message(message)
        return True

    def metric_snapshot(self) -> list[dict[str, float]]:
        return [
            {
                "packets": float(node.pon.relayed_packets),
                "uptime": node.uptime_seconds(),
                "storage": float(node.pon.storage_usage),
            }
            for node in self._nodes.values()
        ]


class Node:
    def __init__(
        self,
        node_id: int,
        *,
        network: InMemoryNetwork,
        region: str,
        operator_id: str,
        endpoint: str | None = None,
        config: NetworkConfig | None = None,
        storage_capacity: int = 1 << 30,
        malicious: bool = False,
    ) -> None:
        self.node_id = node_id
        self.network = network
        self.config = network.config if config is None else config
        self.region = region
        self.operator_id = operator_id
        self.endpoint = endpoint or f"inmemory://{node_id}"
        self.storage_capacity = storage_capacity
        self.malicious = malicious
        self.secret = hashlib.sha256(f"node-secret:{node_id}".encode("utf-8")).digest()
        self.peer_records: dict[int, PeerRecord] = {}
        self.incoming: asyncio.Queue[Message] = asyncio.Queue()
        self.seen_message_ids: set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=self.config.gossip.max_seen_messages)
        self.running = False
        self._tasks: list[asyncio.Task[None]] = []
        self.started_at = time.monotonic()

        self.gossip = GossipService(self)
        self.dht = DHTService(self, self.config.dht)
        self.pon = ProofOfNetworkService(self, self.config.pon)
        self.committee_selector = CommitteeSelector(self)
        self.bft = PBFTService(self)
        self.sync = SyncService(self)
        self.score_reports: dict[int, Any] = {}

        self.pending_transactions: dict[str, Transaction] = {}
        self.blocks_by_hash: dict[str, Block] = {}
        self.blocks_by_height: dict[int, Block] = {}
        self.head_hash = "genesis"
        self.head_height = 0

        self.sync_waiters: dict[str, asyncio.Future[Any]] = {}
        self.network.register(self)

    def uptime_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def add_peer(self, peer: "Node") -> None:
        self.peer_records[peer.node_id] = PeerRecord(
            node_id=peer.node_id,
            region=peer.region,
            operator_id=peer.operator_id,
            endpoint=peer.endpoint,
        )
        self.dht.observe_peer(peer.node_id)

    def mark_delivery(self, peer_id: int, latency_ms: float, *, success: bool) -> None:
        record = self.peer_records.get(peer_id)
        if record is None:
            peer = self.network.get_node(peer_id)
            if peer is None:
                return
            self.add_peer(peer)
            record = self.peer_records[peer_id]
        health = record.health
        health.last_seen = time.monotonic()
        if success:
            health.successful_deliveries += 1
            if health.latency_ms == 0.0:
                health.latency_ms = latency_ms
            else:
                health.latency_ms = (health.latency_ms * 0.7) + (latency_ms * 0.3)
        else:
            health.failed_deliveries += 1

    def random_peer_subset(self, limit: int, *, exclude: set[int] | None = None) -> list[int]:
        exclude = set() if exclude is None else set(exclude)
        candidates = [peer_id for peer_id in self.peer_records if peer_id not in exclude]
        candidates.sort(key=lambda peer_id: (self.peer_records[peer_id].health.latency_ms, peer_id))
        if len(candidates) <= limit:
            return candidates
        rng = random.Random(self.node_id + len(self.seen_message_ids))
        return rng.sample(candidates, limit)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._tasks = [
            asyncio.create_task(self._message_loop()),
            asyncio.create_task(self._periodic_peer_discovery()),
            asyncio.create_task(self._periodic_score_reports()),
        ]

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def enqueue_message(self, message: Message) -> None:
        await self.incoming.put(message)

    async def send_message(self, peer_id: int, message: Message) -> bool:
        return await self.network.deliver(self.node_id, peer_id, message)

    async def submit_transaction(self, payload: dict[str, Any]) -> Transaction:
        transaction = Transaction(sender=str(self.node_id), payload=payload)
        self.pending_transactions[transaction.hash] = transaction
        await self.gossip.gossip(MessageType.TX_GOSSIP, transaction)
        return transaction

    async def finalize_pending_block(self, *, max_transactions: int = 32) -> Any | None:
        transactions = tuple(self.pending_transactions[tx_hash] for tx_hash in sorted(self.pending_transactions)[:max_transactions])
        if not transactions:
            return None
        block = Block(
            parent_hash=self.head_hash,
            transactions=transactions,
            signatures=(),
            committee_id=None,
            proposer_id=self.node_id,
            height=self.head_height + 1,
        )
        key = payload_hash({"parent": self.head_hash, "transactions": [transaction.hash for transaction in transactions]})
        result = await self.bft.finalize_block(block, key=key)
        for transaction in transactions:
            self.pending_transactions.pop(transaction.hash, None)
        return result

    async def sync_from_peer(self, peer_id: int, *, from_height: int = 0):
        return await self.sync.request_sync(peer_id, from_height=from_height)

    async def _message_loop(self) -> None:
        while self.running:
            message = await self.incoming.get()
            await self._handle_incoming_message(message)

    async def _periodic_peer_discovery(self) -> None:
        while self.running:
            await asyncio.sleep(self.config.gossip.peer_discovery_interval)
            await self.gossip.push_pull()

    async def _periodic_score_reports(self) -> None:
        while self.running:
            await asyncio.sleep(self.config.pon.report_interval)
            report = self.pon.compute_score()
            self.score_reports[self.node_id] = report
            for peer_id in self.random_peer_subset(self.config.gossip.fanout):
                await self.send_message(peer_id, Message(type=MessageType.SCORE_REPORT, payload=report, sender=self.node_id))

    async def _handle_incoming_message(self, message: Message) -> None:
        if message.message_id in self.seen_message_ids and message.type not in {MessageType.SYNC_RESPONSE, MessageType.BFT_PREVOTE, MessageType.BFT_PRECOMMIT, MessageType.BFT_COMMIT}:
            return
        if message.message_id not in self.seen_message_ids:
            self.seen_message_ids.add(message.message_id)
            self._seen_order.append(message.message_id)
            while len(self._seen_order) > self.config.gossip.max_seen_messages:
                expired = self._seen_order.popleft()
                self.seen_message_ids.discard(expired)
        self.pon.record_peer_contact(message.sender)

        handler_name = {
            MessageType.TX_GOSSIP: self._handle_tx_gossip,
            MessageType.BLOCK_GOSSIP: self._handle_block_gossip,
            MessageType.ATTESTATION: self._handle_attestation,
            MessageType.PEER_DISCOVERY: self._handle_peer_discovery,
            MessageType.SYNC_REQUEST: self._handle_sync_request,
            MessageType.SYNC_RESPONSE: self._handle_sync_response,
            MessageType.SCORE_REPORT: self._handle_score_report,
        }.get(message.type)
        if handler_name is not None:
            await handler_name(message)
            return
        if message.type in {
            MessageType.BFT_PROPOSE,
            MessageType.BFT_PREVOTE,
            MessageType.BFT_PRECOMMIT,
            MessageType.BFT_COMMIT,
        }:
            await self.bft.handle_message(message)

    async def _handle_tx_gossip(self, message: Message) -> None:
        tx = message.payload
        assert isinstance(tx, Transaction)
        if tx.hash not in self.pending_transactions:
            self.pending_transactions[tx.hash] = tx
            self.pon.record_relay(origin_id=message.sender)
            await self.gossip.forward(message, exclude={message.sender})

    async def _handle_block_gossip(self, message: Message) -> None:
        block = message.payload
        assert isinstance(block, Block)
        if block.hash not in self.blocks_by_hash:
            if block.committee_members and len(block.signatures) < self.bft.quorum_size(len(block.committee_members)):
                return
            self.apply_finalized_block(block)
            self.pon.record_relay(origin_id=message.sender)
            await self.gossip.forward(message, exclude={message.sender})

    async def _handle_attestation(self, message: Message) -> None:
        self.pon.record_relay(origin_id=message.sender)

    async def _handle_peer_discovery(self, message: Message) -> None:
        payload = message.payload
        peer_ids = payload.get("peer_ids", ()) if isinstance(payload, dict) else ()
        for peer_id in peer_ids:
            peer = self.network.get_node(peer_id)
            if peer is not None and peer_id != self.node_id:
                self.add_peer(peer)

    async def _handle_sync_request(self, message: Message) -> None:
        payload = message.payload
        if isinstance(payload, dict) and payload.get("kind") == "recent":
            from_height = int(payload.get("from_height", 0))
            request = {"request_id": payload_hash({"sender": message.sender, "from_height": from_height, "head": self.head_height})}
        else:
            from_height = int(payload.from_height)
            request = {"request_id": payload.request_id}
        blocks = tuple(
            self.blocks_by_height[height]
            for height in sorted(self.blocks_by_height)
            if height >= from_height
        )[: self.config.sync.batch_size]
        response = {
            "request_id": request["request_id"],
            "blocks": blocks,
            "peer_ids": tuple(sorted(self.peer_records)),
        }
        await self.send_message(message.sender, Message(type=MessageType.SYNC_RESPONSE, payload=response, sender=self.node_id))

    async def _handle_sync_response(self, message: Message) -> None:
        payload = message.payload
        request_id = payload["request_id"]
        blocks = payload["blocks"]
        peer_ids = payload["peer_ids"]
        for peer_id in peer_ids:
            peer = self.network.get_node(peer_id)
            if peer is not None and peer_id != self.node_id:
                self.add_peer(peer)
        future = self.sync_waiters.get(request_id)
        if future is not None and not future.done():
            future.set_result(blocks)

    async def _handle_score_report(self, message: Message) -> None:
        report = message.payload
        if self.pon.verify_report(report):
            self.score_reports[report.node_id] = report

    def resolve_score(self, node_id: int):
        if node_id not in self.score_reports:
            peer = self.network.get_node(node_id)
            if peer is not None:
                self.score_reports[node_id] = peer.pon.compute_score()
        return self.pon.accepted_score(node_id)

    def apply_finalized_block(self, block: Block) -> None:
        if block.height <= self.head_height and block.hash in self.blocks_by_hash:
            return
        if block.hash != hashlib.sha256(
            stable_json_bytes(
                {
                    "parent_hash": block.parent_hash,
                    "transactions": block.transactions,
                    "committee_id": block.committee_id,
                    "proposer_id": block.proposer_id,
                    "height": block.height,
                    "committee_members": block.committee_members,
                }
            )
        ).hexdigest():
            raise ValueError("invalid block hash")
        if block.committee_members and len(block.signatures) < self.bft.quorum_size(len(block.committee_members)):
            raise ValueError("block commit certificate does not reach quorum")
        self.blocks_by_hash[block.hash] = block
        self.blocks_by_height[block.height] = block
        for transaction in block.transactions:
            self.pending_transactions.pop(transaction.hash, None)
        if block.height >= self.head_height:
            self.head_height = block.height
            self.head_hash = block.hash
