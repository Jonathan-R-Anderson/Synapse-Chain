from __future__ import annotations

from typing import Protocol

from .models import Channel, ChannelState


class ChannelProofVerifier(Protocol):
    def verify_state_update(self, channel: Channel, state: ChannelState, zk_proof: dict[str, object]) -> bool: ...

    def verify_state_transition_proof(
        self,
        channel: Channel,
        previous_state: ChannelState,
        next_state: ChannelState,
        zk_proof: dict[str, object],
    ) -> bool: ...


class NullChannelProofVerifier:
    """Default zk hook that rejects proof-submitted state updates until a verifier is installed."""

    def verify_state_update(self, channel: Channel, state: ChannelState, zk_proof: dict[str, object]) -> bool:
        return False

    def verify_state_transition_proof(
        self,
        channel: Channel,
        previous_state: ChannelState,
        next_state: ChannelState,
        zk_proof: dict[str, object],
    ) -> bool:
        return False
