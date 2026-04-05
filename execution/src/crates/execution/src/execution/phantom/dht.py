from __future__ import annotations

from dataclasses import dataclass

from primitives import Address

from .models import Channel, ChannelStatus


@dataclass(frozen=True, slots=True)
class NodeAnnouncement:
    node: Address
    available: bool = True


@dataclass(frozen=True, slots=True)
class ChannelAnnouncement:
    channel_id: str
    participants: tuple[Address, Address]
    capacity: int
    available_balances: dict[Address, int]
    fee_base: int
    fee_ppm: int
    status: ChannelStatus


class InMemoryChannelDHT:
    """In-memory DHT-style registry for route discovery extension points."""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeAnnouncement] = {}
        self._channels: dict[str, ChannelAnnouncement] = {}

    def publish_node(self, node: Address, *, available: bool = True) -> None:
        self._nodes[node.to_hex()] = NodeAnnouncement(node=node, available=available)

    def publish_channel(self, channel: Channel) -> None:
        self._channels[channel.channel_id] = ChannelAnnouncement(
            channel_id=channel.channel_id,
            participants=channel.participants,  # type: ignore[arg-type]
            capacity=channel.total_capacity,
            available_balances={participant: channel.available_balance(participant) for participant in channel.participants},
            fee_base=channel.fee_base,
            fee_ppm=channel.fee_ppm,
            status=channel.status,
        )
        for participant in channel.participants:
            self.publish_node(participant)

    def get_channel(self, channel_id: str) -> ChannelAnnouncement | None:
        return self._channels.get(channel_id)

    def channels(self) -> tuple[ChannelAnnouncement, ...]:
        return tuple(sorted(self._channels.values(), key=lambda item: item.channel_id))

    def peers(self) -> tuple[NodeAnnouncement, ...]:
        return tuple(sorted(self._nodes.values(), key=lambda item: item.node.to_hex()))

