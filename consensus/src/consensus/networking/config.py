from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GossipConfig:
    fanout: int = 3
    pull_fanout: int = 2
    max_seen_messages: int = 4_096
    recent_cache_size: int = 256
    peer_discovery_interval: float = 1.0


@dataclass(frozen=True, slots=True)
class DHTConfig:
    id_bits: int = 256
    k_bucket_size: int = 8
    alpha: int = 3
    replication_factor: int = 3
    value_ttl: float = 300.0


@dataclass(frozen=True, slots=True)
class PoNConfig:
    alpha: float = 0.30
    beta: float = 0.25
    gamma: float = 0.20
    delta: float = 0.20
    epsilon: float = 0.15
    relay_reference: float = 100.0
    uptime_reference: float = 60.0
    storage_reference: float = float(1 << 30)
    max_self_report_multiplier: float = 1.25
    report_interval: float = 1.0


@dataclass(frozen=True, slots=True)
class CommitteeConfig:
    candidate_count: int = 8
    committee_size: int = 4
    weighted_selection: bool = True
    max_correlated_fraction: float = 1 / 3
    prefer_unique_regions: bool = True
    diversity_penalty: float = 0.10


@dataclass(frozen=True, slots=True)
class BFTConfig:
    request_timeout: float = 1.0
    vote_timeout: float = 2.0
    commit_timeout: float = 5.0
    proposer_rotation: bool = True


@dataclass(frozen=True, slots=True)
class SyncConfig:
    batch_size: int = 32
    request_timeout: float = 2.0


@dataclass(frozen=True, slots=True)
class NetworkConfig:
    gossip: GossipConfig = field(default_factory=GossipConfig)
    dht: DHTConfig = field(default_factory=DHTConfig)
    pon: PoNConfig = field(default_factory=PoNConfig)
    committee: CommitteeConfig = field(default_factory=CommitteeConfig)
    bft: BFTConfig = field(default_factory=BFTConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    base_latency_ms: float = 5.0
    latency_jitter_ms: float = 3.0
    drop_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.base_latency_ms < 0:
            raise ValueError("base_latency_ms must be non-negative")
        if self.latency_jitter_ms < 0:
            raise ValueError("latency_jitter_ms must be non-negative")
        if not 0.0 <= self.drop_rate < 1.0:
            raise ValueError("drop_rate must be in [0, 1)")
