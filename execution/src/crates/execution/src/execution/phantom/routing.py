from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field

from primitives import Address

from .errors import RouteNotFoundError
from .models import Channel, ChannelEdge, ChannelStatus, PaymentRoute, RouteHop


LOGGER = logging.getLogger("execution.phantom.routing")


@dataclass(order=True, slots=True)
class _Candidate:
    score: int
    hop_count: int
    required_amount: int
    sort_key: tuple[str, ...]
    node: Address = field(compare=False)
    path: tuple[RouteHop, ...] = field(compare=False)


class ChannelGraph:
    """Liquidity-aware view of open channels for multi-hop routing."""

    def __init__(self) -> None:
        self.nodes: set[Address] = set()
        self._channels: dict[str, Channel] = {}

    @property
    def edges(self) -> tuple[ChannelEdge, ...]:
        return tuple(
            ChannelEdge(
                channel_id=channel.channel_id,
                participants=channel.participants,  # type: ignore[arg-type]
                capacity=channel.total_capacity,
                available_balances={participant: channel.available_balance(participant) for participant in channel.participants},
                fee_base=channel.fee_base,
                fee_ppm=channel.fee_ppm,
                status=channel.status,
            )
            for channel in sorted(self._channels.values(), key=lambda item: item.channel_id)
        )

    def sync_channel(self, channel: Channel) -> None:
        self._channels[channel.channel_id] = channel
        self.nodes.update(channel.participants)

    def remove_channel(self, channel_id: str) -> None:
        channel = self._channels.pop(channel_id, None)
        if channel is None:
            return
        if not any(participant in active.participants for active in self._channels.values() for participant in channel.participants):
            for participant in channel.participants:
                self.nodes.discard(participant)

    def channels_for(self, participant: Address) -> tuple[Channel, ...]:
        return tuple(
            channel
            for channel in sorted(self._channels.values(), key=lambda item: item.channel_id)
            if participant in channel.participants and channel.status is ChannelStatus.OPEN
        )

    def find_route(self, sender: Address, receiver: Address, amount: int, *, max_hops: int = 6) -> PaymentRoute:
        if amount <= 0:
            raise RouteNotFoundError("payment amount must be positive")
        if sender == receiver:
            raise RouteNotFoundError("sender and receiver must be distinct")

        best: dict[Address, tuple[int, int, int]] = {}
        heap: list[_Candidate] = [
            _Candidate(score=0, hop_count=0, required_amount=int(amount), sort_key=(receiver.to_hex(),), node=receiver, path=())
        ]

        while heap:
            candidate = heapq.heappop(heap)
            best_tuple = (candidate.score, candidate.hop_count, candidate.required_amount)
            if best.get(candidate.node, best_tuple) < best_tuple:
                continue
            if candidate.node == sender:
                LOGGER.info("selected phantom route %s -> %s across %s hops", sender.to_hex(), receiver.to_hex(), len(candidate.path))
                return PaymentRoute(
                    sender=sender,
                    receiver=receiver,
                    amount=int(amount),
                    total_amount=candidate.required_amount,
                    hops=candidate.path,
                )

            if candidate.hop_count >= max_hops:
                continue

            visited_nodes = {candidate.node}
            for hop in candidate.path:
                visited_nodes.add(hop.sender)
                visited_nodes.add(hop.receiver)

            for channel in self.channels_for(candidate.node):
                neighbor = channel.participants[0] if channel.participants[1] == candidate.node else channel.participants[1]
                if neighbor in visited_nodes and neighbor != sender:
                    continue
                fee = channel.routing_fee(candidate.required_amount)
                required_amount = candidate.required_amount + fee
                available = channel.available_balance(neighbor)
                if available < required_amount:
                    continue
                spare_liquidity = max(0, available - required_amount)
                liquidity_penalty = 1_000_000 // (spare_liquidity + 1)
                score = candidate.score + (fee * 1_000) + 100 + liquidity_penalty
                path = (
                    RouteHop(
                        channel_id=channel.channel_id,
                        sender=neighbor,
                        receiver=candidate.node,
                        amount=required_amount,
                        fee=fee,
                    ),
                    *candidate.path,
                )
                sort_key = tuple(hop.channel_id for hop in path)
                next_tuple = (score, candidate.hop_count + 1, required_amount)
                if neighbor in best and best[neighbor] <= next_tuple:
                    continue
                best[neighbor] = next_tuple
                heapq.heappush(
                    heap,
                    _Candidate(
                        score=score,
                        hop_count=candidate.hop_count + 1,
                        required_amount=required_amount,
                        sort_key=sort_key,
                        node=neighbor,
                        path=path,
                    ),
                )

        raise RouteNotFoundError(
            f"no phantom route from {sender.to_hex()} to {receiver.to_hex()} for amount {amount}"
        )

