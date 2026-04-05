from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .config import PoNConfig
from .types import NodeScore


@dataclass(slots=True)
class ObservedMetrics:
    relayed_packets: int = 0
    successful_messages: int = 0


class ProofOfNetworkService:
    def __init__(self, node: "Node", config: PoNConfig) -> None:
        self.node = node
        self.config = config
        self.storage_usage: int = 0
        self.relayed_packets: int = 0
        self.observed_metrics: dict[int, ObservedMetrics] = {}
        self._last_report: NodeScore | None = None

    def record_relay(self, origin_id: int | None = None) -> None:
        self.relayed_packets += 1
        if origin_id is not None:
            observed = self.observed_metrics.setdefault(origin_id, ObservedMetrics())
            observed.relayed_packets += 1

    def record_peer_contact(self, peer_id: int) -> None:
        observed = self.observed_metrics.setdefault(peer_id, ObservedMetrics())
        observed.successful_messages += 1

    def set_storage_usage(self, value: int) -> None:
        self.storage_usage = max(0, int(value))

    def _network_maxima(self) -> tuple[float, float, float]:
        scores = self.node.network.metric_snapshot()
        max_packets = max((item["packets"] for item in scores), default=1.0)
        max_uptime = max((item["uptime"] for item in scores), default=1.0)
        max_storage = max((item["storage"] for item in scores), default=1.0)
        return max_packets, max_uptime, max_storage

    def _sign(self, payload: bytes) -> str:
        return hashlib.sha256(self.node.secret + payload).hexdigest()

    def verify_report(self, report: NodeScore) -> bool:
        payload = f"{report.node_id}:{report.coverage_radius:.12f}:{report.packets_relayed}:{report.uptime:.12f}:{report.storage_provided}:{report.density:.12f}:{report.computed_score:.12f}".encode("utf-8")
        expected = hashlib.sha256(self.node.network.get_secret(report.node_id) + payload).hexdigest()
        return expected == report.signature

    def compute_score(self) -> NodeScore:
        max_packets, max_uptime, max_storage = self._network_maxima()
        coverage = self.node.dht.coverage_radius()
        density = self.node.dht.local_density()
        uptime = self.node.uptime_seconds()
        packets = self.relayed_packets
        storage = self.storage_usage

        normalized_coverage = coverage
        normalized_packets = min(1.0, packets / max(max_packets, self.config.relay_reference))
        normalized_uptime = min(1.0, uptime / max(max_uptime, self.config.uptime_reference))
        normalized_storage = min(1.0, storage / max(max_storage, self.config.storage_reference))
        normalized_density = min(1.0, density)

        computed = (
            (self.config.alpha * normalized_coverage)
            + (self.config.beta * normalized_packets)
            + (self.config.gamma * normalized_uptime)
            + (self.config.delta * normalized_storage)
            - (self.config.epsilon * normalized_density)
        )
        payload = f"{self.node.node_id}:{coverage:.12f}:{packets}:{uptime:.12f}:{storage}:{density:.12f}:{computed:.12f}".encode("utf-8")
        score = NodeScore(
            node_id=self.node.node_id,
            coverage_radius=coverage,
            packets_relayed=packets,
            uptime=uptime,
            storage_provided=storage,
            density=density,
            computed_score=max(0.0, computed),
            signature=self._sign(payload),
            observed_by=tuple(sorted(self.observed_metrics)),
        )
        self._last_report = score
        return score

    def accepted_score(self, node_id: int) -> NodeScore:
        if node_id == self.node.node_id:
            return self.compute_score()
        report = self.node.score_reports[node_id]
        observed = self.observed_metrics.get(node_id)
        if observed is None:
            return report
        validated_packets = min(report.packets_relayed, int(observed.relayed_packets * self.config.max_self_report_multiplier) + 1)
        confidence = min(1.0, observed.successful_messages / max(1.0, report.uptime))
        adjusted_score = report.computed_score * (0.5 + 0.5 * confidence)
        return NodeScore(
            node_id=report.node_id,
            coverage_radius=report.coverage_radius,
            packets_relayed=validated_packets,
            uptime=report.uptime,
            storage_provided=report.storage_provided,
            density=report.density,
            computed_score=max(0.0, adjusted_score),
            signature=report.signature,
            observed_by=report.observed_by,
        )
