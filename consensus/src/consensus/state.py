from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .config import ConsensusConfig
from .types import Attestation, BeaconBlock, Checkpoint, Validator
from .utils import sha256_hex


@dataclass(slots=True)
class BeaconState:
    slot: int
    epoch: int
    validators: list[Validator]
    balances: list[int]
    randao_mix: bytes
    justified_checkpoint: Checkpoint
    finalized_checkpoint: Checkpoint
    previous_justified_checkpoint: Checkpoint
    latest_block_root: str
    block_roots: dict[int, str] = field(default_factory=dict)
    state_roots: dict[int, str] = field(default_factory=dict)
    pending_attestations: list[Attestation] = field(default_factory=list)
    slashings: list[int] = field(default_factory=list)
    inactivity_scores: list[float] = field(default_factory=list)
    config: ConsensusConfig = field(default_factory=ConsensusConfig)
    blocks_by_root: dict[str, BeaconBlock] = field(default_factory=dict)
    checkpoint_votes: dict[int, list[Attestation]] = field(default_factory=dict)
    epoch_participation: dict[int, set[int]] = field(default_factory=dict)
    justified_history: list[Checkpoint] = field(default_factory=list)
    finalized_history: list[Checkpoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.validators) != len(self.balances):
            raise ValueError("validators and balances must align")
        if self.inactivity_scores and len(self.inactivity_scores) != len(self.validators):
            raise ValueError("inactivity_scores must align with validators")
        if not self.inactivity_scores:
            self.inactivity_scores = [0.0 for _ in self.validators]
        if len(self.randao_mix) != self.config.randao_mix_length:
            raise ValueError("randao_mix length must match config.randao_mix_length")

    def copy(self) -> "BeaconState":
        return BeaconState(
            slot=self.slot,
            epoch=self.epoch,
            validators=list(self.validators),
            balances=list(self.balances),
            randao_mix=bytes(self.randao_mix),
            justified_checkpoint=self.justified_checkpoint,
            finalized_checkpoint=self.finalized_checkpoint,
            previous_justified_checkpoint=self.previous_justified_checkpoint,
            latest_block_root=self.latest_block_root,
            block_roots=dict(self.block_roots),
            state_roots=dict(self.state_roots),
            pending_attestations=list(self.pending_attestations),
            slashings=list(self.slashings),
            inactivity_scores=list(self.inactivity_scores),
            config=self.config,
            blocks_by_root=dict(self.blocks_by_root),
            checkpoint_votes={epoch: list(attestations) for epoch, attestations in self.checkpoint_votes.items()},
            epoch_participation={epoch: set(indices) for epoch, indices in self.epoch_participation.items()},
            justified_history=list(self.justified_history),
            finalized_history=list(self.finalized_history),
        )

    def current_epoch(self) -> int:
        return self.slot // self.config.slots_per_epoch

    def epoch_start_slot(self, epoch: int) -> int:
        return epoch * self.config.slots_per_epoch

    def current_checkpoint(self) -> Checkpoint:
        start_slot = self.epoch_start_slot(self.current_epoch())
        return Checkpoint(epoch=self.current_epoch(), root=self.block_roots.get(start_slot, self.latest_block_root))

    def checkpoint_for_epoch(self, epoch: int) -> Checkpoint:
        start_slot = self.epoch_start_slot(epoch)
        return Checkpoint(epoch=epoch, root=self.block_roots.get(start_slot, self.latest_block_root))

    def state_root(self) -> str:
        return sha256_hex(
            {
                "slot": self.slot,
                "epoch": self.epoch,
                "validators": self.validators,
                "balances": self.balances,
                "randao_mix": self.randao_mix.hex(),
                "justified_checkpoint": self.justified_checkpoint,
                "finalized_checkpoint": self.finalized_checkpoint,
                "latest_block_root": self.latest_block_root,
                "block_roots": self.block_roots,
                "slashings": self.slashings,
                "inactivity_scores": [round(score, 12) for score in self.inactivity_scores],
            }
        )

    def record_state_root(self) -> str:
        root = self.state_root()
        self.state_roots[self.slot] = root
        while len(self.state_roots) > self.config.state_roots_limit:
            oldest = min(self.state_roots)
            del self.state_roots[oldest]
        return root

    def record_block(self, block: BeaconBlock) -> str:
        root = block.root()
        self.blocks_by_root[root] = block
        self.block_roots[block.slot] = root
        self.latest_block_root = root
        while len(self.block_roots) > self.config.block_roots_limit:
            oldest = min(self.block_roots)
            del self.block_roots[oldest]
        return root

    def update_validator(self, index: int, validator: Validator) -> None:
        self.validators[index] = validator

    def increase_balance(self, index: int, amount: int) -> None:
        self.balances[index] += max(0, int(amount))

    def decrease_balance(self, index: int, amount: int) -> None:
        self.balances[index] = max(0, self.balances[index] - max(0, int(amount)))


def create_genesis_state(validators: list[Validator], config: ConsensusConfig | None = None) -> BeaconState:
    resolved_config = ConsensusConfig() if config is None else config
    balances = [validator.effective_stake for validator in validators]
    genesis_root = sha256_hex({"genesis_validators": validators, "config": resolved_config})
    genesis_checkpoint = Checkpoint(epoch=0, root=genesis_root)
    state = BeaconState(
        slot=0,
        epoch=0,
        validators=list(validators),
        balances=balances,
        randao_mix=bytes(resolved_config.randao_mix_length),
        justified_checkpoint=genesis_checkpoint,
        finalized_checkpoint=genesis_checkpoint,
        previous_justified_checkpoint=genesis_checkpoint,
        latest_block_root=genesis_root,
        config=resolved_config,
    )
    state.block_roots[0] = genesis_root
    state.state_roots[0] = state.state_root()
    state.justified_history.append(genesis_checkpoint)
    state.finalized_history.append(genesis_checkpoint)
    return state
