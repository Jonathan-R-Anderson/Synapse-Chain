from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    identity: float = 0.18
    stake: float = 0.26
    compute: float = 0.16
    storage: float = 0.16
    network: float = 0.14
    activity: float = 0.10

    def __post_init__(self) -> None:
        if min(self.identity, self.stake, self.compute, self.storage, self.network, self.activity) < 0:
            raise ValueError("score weights must be non-negative")
        total = self.identity + self.stake + self.compute + self.storage + self.network + self.activity
        if total <= 0:
            raise ValueError("score weights must sum to a positive value")


@dataclass(frozen=True, slots=True)
class EligibilityThresholds:
    min_effective_stake: int = 32
    min_identity_weight: float = 0.30
    min_storage_weight: float = 0.10
    min_network_weight: float = 0.10
    min_activity_weight: float = 0.05
    min_total_score: float = 0.15
    require_identity_verified: bool = True
    min_attestation_participation: float = 0.10


@dataclass(frozen=True, slots=True)
class SlashingConfig:
    stake_penalty_fraction: float = 0.25
    reputation_penalty_fraction: float = 0.40
    force_exit_epochs: int = 4
    minimum_slash_score: float = 0.0
    proposer_equivocation_penalty: float = 1.0
    attestation_equivocation_penalty: float = 1.0


@dataclass(frozen=True, slots=True)
class InactivityConfig:
    base_penalty: float = 0.02
    leak_threshold_epochs: int = 2
    leak_penalty_rate: float = 0.05
    attestation_reward: float = 0.01
    proposer_reward: float = 0.02
    challenge_failure_penalty: float = 0.04


@dataclass(frozen=True, slots=True)
class ForkChoiceConfig:
    proposer_boost: float = 0.10
    justified_weight_threshold: float = 2 / 3
    finality_weight_threshold: float = 2 / 3
    stalled_finality_epochs: int = 4


@dataclass(frozen=True, slots=True)
class DensityPenaltyConfig:
    saturation_threshold: float = 0.70
    maximum_penalty: float = 0.50


@dataclass(frozen=True, slots=True)
class ScoreWindowConfig:
    stake_cap: int = 256
    excess_stake_diminishing_factor: float = 0.35
    burn_reference: int = 64
    burn_cap_multiplier: float = 8.0
    reputation_reference: float = 100.0
    pow_reference: float = 100.0
    useful_compute_reference: float = 100.0
    storage_reference_bytes: int = 1 << 40
    relay_reference: float = 100.0
    uptime_reference: float = 1.0
    peer_diversity_reference: float = 100.0
    coverage_reference: float = 100.0
    activity_reference: float = 100.0


@dataclass(frozen=True, slots=True)
class ScoreDecayConfig:
    idle_epoch_decay: float = 0.95
    inactivity_score_bias: float = 0.05
    activity_floor: float = 0.01
    unverified_identity_multiplier: float = 0.20
    storage_failure_floor: float = 0.10
    network_failure_floor: float = 0.10


@dataclass(frozen=True, slots=True)
class ConsensusConfig:
    slots_per_epoch: int = 8
    seconds_per_slot: int = 12
    max_validators_per_committee: int = 16
    target_committee_count: int = 2
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    eligibility_thresholds: EligibilityThresholds = field(default_factory=EligibilityThresholds)
    slashing: SlashingConfig = field(default_factory=SlashingConfig)
    inactivity: InactivityConfig = field(default_factory=InactivityConfig)
    fork_choice: ForkChoiceConfig = field(default_factory=ForkChoiceConfig)
    score_windows: ScoreWindowConfig = field(default_factory=ScoreWindowConfig)
    score_decay: ScoreDecayConfig = field(default_factory=ScoreDecayConfig)
    density_penalty: DensityPenaltyConfig = field(default_factory=DensityPenaltyConfig)
    randao_mix_length: int = 32
    block_roots_limit: int = 256
    state_roots_limit: int = 256

    def __post_init__(self) -> None:
        if self.slots_per_epoch < 1:
            raise ValueError("slots_per_epoch must be positive")
        if self.seconds_per_slot < 1:
            raise ValueError("seconds_per_slot must be positive")
        if self.max_validators_per_committee < 1:
            raise ValueError("max_validators_per_committee must be positive")
        if self.target_committee_count < 1:
            raise ValueError("target_committee_count must be positive")
        if self.randao_mix_length < 16:
            raise ValueError("randao_mix_length must be at least 16 bytes")
        if self.block_roots_limit < self.slots_per_epoch:
            raise ValueError("block_roots_limit must be at least slots_per_epoch")
        if self.state_roots_limit < self.slots_per_epoch:
            raise ValueError("state_roots_limit must be at least slots_per_epoch")
