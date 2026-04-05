from __future__ import annotations

import copy
import logging
import secrets
from dataclasses import dataclass

from crypto import address_from_private_key
from evm import StateDB
from primitives import Address, Hash

from .dht import InMemoryChannelDHT
from .errors import ChannelValidationError, DisputeError, HTLCError, ReplayAttackError, SignatureValidationError
from .hashing import hash_secret, phantom_hash
from .models import (
    Channel,
    ChannelState,
    ChannelStatus,
    ChallengeChannelOperation,
    CloseChannelOperation,
    FinalizeChannelOperation,
    HTLC,
    HTLCStatus,
    OpenChannelOperation,
    PaymentRecord,
    PaymentRoute,
    PaymentStatus,
    SettlementOperation,
    SubmitStateWithProofOperation,
)
from .routing import ChannelGraph
from .signing import sign_channel_state, verify_signature
from .zk import ChannelProofVerifier, NullChannelProofVerifier


LOGGER = logging.getLogger("execution.phantom.manager")


@dataclass(frozen=True, slots=True)
class ManagerSnapshot:
    channels: dict[str, Channel]
    payments: dict[str, PaymentRecord]
    channel_sequence: int
    payment_sequence: int


class ChannelManager:
    """State-channel lifecycle manager with HTLC routing and dispute handling."""

    def __init__(
        self,
        *,
        max_htlcs_per_channel: int = 16,
        max_route_length: int = 6,
        base_timelock: int = 12,
        timelock_delta: int = 4,
        max_timelock_horizon: int = 96,
        dht: InMemoryChannelDHT | None = None,
        proof_verifier: ChannelProofVerifier | None = None,
    ) -> None:
        self.max_htlcs_per_channel = int(max_htlcs_per_channel)
        self.max_route_length = int(max_route_length)
        self.base_timelock = int(base_timelock)
        self.timelock_delta = int(timelock_delta)
        self.max_timelock_horizon = int(max_timelock_horizon)
        self._channels: dict[str, Channel] = {}
        self._payments: dict[str, PaymentRecord] = {}
        self._graph = ChannelGraph()
        self._signing_keys: dict[str, bytes | bytearray | memoryview | int] = {}
        self._channel_sequence = 0
        self._payment_sequence = 0
        self._dht = dht
        self._proof_verifier = NullChannelProofVerifier() if proof_verifier is None else proof_verifier

    def snapshot(self) -> ManagerSnapshot:
        return ManagerSnapshot(
            channels=copy.deepcopy(self._channels),
            payments=copy.deepcopy(self._payments),
            channel_sequence=self._channel_sequence,
            payment_sequence=self._payment_sequence,
        )

    def restore(self, snapshot: ManagerSnapshot) -> None:
        self._channels = copy.deepcopy(snapshot.channels)
        self._payments = copy.deepcopy(snapshot.payments)
        self._channel_sequence = snapshot.channel_sequence
        self._payment_sequence = snapshot.payment_sequence
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        self._graph = ChannelGraph()
        for channel in self._channels.values():
            self._graph.sync_channel(channel)
            if self._dht is not None:
                self._dht.publish_channel(channel)

    def register_signing_key(self, private_key: bytes | bytearray | memoryview | int) -> Address:
        address = address_from_private_key(private_key)
        self._signing_keys[address.to_hex()] = private_key
        if self._dht is not None:
            self._dht.publish_node(address)
        return address

    def get_channel(self, channel_id: str) -> Channel:
        channel = self._channels.get(channel_id)
        if channel is None:
            raise ChannelValidationError(f"unknown channel {channel_id}")
        return channel

    def list_channels(self) -> tuple[Channel, ...]:
        return tuple(sorted(self._channels.values(), key=lambda item: item.channel_id))

    def list_payments(self) -> tuple[PaymentRecord, ...]:
        return tuple(sorted(self._payments.values(), key=lambda item: item.payment_id))

    @property
    def graph(self) -> ChannelGraph:
        return self._graph

    def _next_channel_id(self, participants: tuple[Address, Address], current_block: int, channel_id_hint: str | None = None) -> str:
        if channel_id_hint is not None:
            return channel_id_hint
        self._channel_sequence += 1
        return phantom_hash(
            {
                "type": "phantom_channel",
                "participants": [participant.to_hex() for participant in participants],
                "sequence": self._channel_sequence,
                "open_block": int(current_block),
            }
        ).to_hex()

    def _next_payment_id(self, sender: Address, receiver: Address, amount: int, current_block: int) -> str:
        self._payment_sequence += 1
        return phantom_hash(
            {
                "type": "phantom_payment",
                "sender": sender.to_hex(),
                "receiver": receiver.to_hex(),
                "amount": int(amount),
                "sequence": self._payment_sequence,
                "block": int(current_block),
            }
        ).to_hex()

    def _escrow_address(self, channel_id: str) -> Address:
        return Address(phantom_hash({"phantom_escrow": channel_id}).to_bytes()[-20:])

    def _publish_channel(self, channel: Channel) -> None:
        self._graph.sync_channel(channel)
        if self._dht is not None:
            self._dht.publish_channel(channel)

    def _ensure_all_signatures(self, channel: Channel, state: ChannelState) -> None:
        participant_keys = {participant.to_hex() for participant in channel.participants}
        if set(state.signatures) != participant_keys:
            raise SignatureValidationError("channel state must contain exactly one signature per participant")
        for participant in channel.participants:
            verify_signature(state.state_root, state.signatures[participant.to_hex()], participant)

    def _validate_state(
        self,
        channel: Channel,
        state: ChannelState,
        *,
        min_nonce: int | None = None,
        strict_newer: bool,
    ) -> None:
        if state.channel_id != channel.channel_id:
            raise ChannelValidationError("state channel_id does not match the channel")
        if tuple(state.balances) != channel.participants:
            raise ChannelValidationError("state balances do not match the channel participants")
        if state.total_balance != channel.total_capacity:
            raise ChannelValidationError("channel state cannot create or destroy balance")
        if min_nonce is not None:
            if strict_newer and state.nonce <= min_nonce:
                raise ReplayAttackError("channel nonce must strictly increase")
            if not strict_newer and state.nonce < min_nonce:
                raise ReplayAttackError("submitted channel state is older than the current commitment")
        if len(state.active_htlcs) > min(channel.max_htlcs, self.max_htlcs_per_channel):
            raise HTLCError("too many active HTLCs in the channel state")

        seen_htlc_ids: set[str] = set()
        locked_by_sender: dict[Address, int] = {participant: 0 for participant in channel.participants}
        for htlc in state.active_htlcs:
            if htlc.status is not HTLCStatus.PENDING:
                raise HTLCError("active HTLC set can only contain pending HTLCs")
            if htlc.htlc_id in seen_htlc_ids:
                raise HTLCError("duplicate HTLC id in channel state")
            if htlc.sender not in channel.participants or htlc.receiver not in channel.participants:
                raise HTLCError("HTLC participants must belong to the channel")
            if htlc.sender == htlc.receiver:
                raise HTLCError("HTLC sender and receiver must differ")
            seen_htlc_ids.add(htlc.htlc_id)
            locked_by_sender[htlc.sender] += htlc.amount
        for participant, locked in locked_by_sender.items():
            if locked > state.balances[participant]:
                raise HTLCError(f"participant {participant.to_hex()} over-commits locked liquidity")
        self._ensure_all_signatures(channel, state)

    def _sign_state(self, channel: Channel, state: ChannelState) -> ChannelState:
        signed = state
        for participant in channel.participants:
            private_key = self._signing_keys.get(participant.to_hex())
            if private_key is None:
                raise SignatureValidationError(f"missing signing key for participant {participant.to_hex()}")
            signed = sign_channel_state(signed, private_key)
        return signed

    def build_signed_state(
        self,
        channel_id: str,
        *,
        balances: dict[Address | str, int] | None = None,
        active_htlcs: tuple[HTLC, ...] | None = None,
        nonce: int | None = None,
    ) -> ChannelState:
        channel = self.get_channel(channel_id)
        state = ChannelState(
            channel_id=channel.channel_id,
            nonce=channel.nonce + 1 if nonce is None else int(nonce),
            balances=channel.balances if balances is None else balances,
            active_htlcs=channel.active_htlcs if active_htlcs is None else active_htlcs,
        )
        return self._sign_state(channel, state)

    def open_channel(
        self,
        participants: tuple[Address | str, Address | str],
        deposits: dict[Address | str, int],
        dispute_window: int,
        *,
        chain_state: StateDB,
        current_block: int,
        fee_base: int = 0,
        fee_ppm: int = 0,
        channel_id_hint: str | None = None,
    ) -> Channel:
        normalized_participants = tuple(sorted((Address.from_hex(str(participants[0])) if isinstance(participants[0], str) else participants[0], Address.from_hex(str(participants[1])) if isinstance(participants[1], str) else participants[1]), key=lambda item: item.to_hex()))  # type: ignore[arg-type]
        if len(normalized_participants) != 2:
            raise ChannelValidationError("phantom routing channels must be pairwise")
        normalized_deposits = {participant: int(amount) for participant, amount in deposits.items()}
        balances = {participant: normalized_deposits.get(participant, normalized_deposits.get(participant.to_hex(), 0)) for participant in normalized_participants}
        if any(amount < 0 for amount in balances.values()):
            raise ChannelValidationError("channel deposits cannot be negative")
        if sum(balances.values()) <= 0:
            raise ChannelValidationError("channel must lock a positive amount of total liquidity")
        if int(dispute_window) <= 0:
            raise ChannelValidationError("dispute window must be positive")
        for participant, amount in balances.items():
            if chain_state.get_balance(participant) < amount:
                raise ChannelValidationError(f"participant {participant.to_hex()} has insufficient balance")

        channel_id = self._next_channel_id(normalized_participants, current_block, channel_id_hint=channel_id_hint)
        escrow = self._escrow_address(channel_id)
        for participant, amount in balances.items():
            if not chain_state.transfer(participant, escrow, amount):
                raise ChannelValidationError("failed to move deposits into channel escrow")
        initial_state = ChannelState(channel_id=channel_id, nonce=0, balances=balances)
        channel = Channel(
            channel_id=channel_id,
            participants=normalized_participants,
            balances=balances,
            nonce=0,
            state_root=initial_state.state_root,
            open_block=int(current_block),
            dispute_window=int(dispute_window),
            escrow_address=escrow,
            latest_state=initial_state,
            fee_base=int(fee_base),
            fee_ppm=int(fee_ppm),
            max_htlcs=self.max_htlcs_per_channel,
        )
        self._channels[channel_id] = channel
        self._publish_channel(channel)
        LOGGER.info("opened phantom channel %s", channel_id)
        return channel

    def update_state(self, channel_id: str, new_state: ChannelState) -> Channel:
        channel = self.get_channel(channel_id)
        if channel.status is not ChannelStatus.OPEN:
            raise ChannelValidationError("only open channels can accept off-chain updates")
        self._validate_state(channel, new_state, min_nonce=channel.nonce, strict_newer=True)
        channel.apply_state(new_state)
        self._publish_channel(channel)
        return channel

    def close_channel(self, channel_id: str, final_state: ChannelState, *, chain_state: StateDB, current_block: int) -> Channel:
        channel = self.get_channel(channel_id)
        if channel.status is ChannelStatus.CLOSED:
            raise DisputeError("channel is already closed")
        self._validate_state(channel, final_state, min_nonce=channel.nonce, strict_newer=False)
        if final_state.nonce > channel.nonce:
            channel.apply_state(final_state)
        channel.closing_state = final_state
        self._settle_channel(channel, final_state, chain_state=chain_state, current_block=current_block)
        LOGGER.info("cooperatively closed phantom channel %s", channel_id)
        return channel

    def challenge_close(self, channel_id: str, state: ChannelState, *, current_block: int) -> Channel:
        channel = self.get_channel(channel_id)
        if channel.status is ChannelStatus.CLOSED:
            raise DisputeError("closed channels cannot be challenged")
        minimum = channel.closing_state.nonce if channel.closing_state is not None else channel.nonce
        strict = channel.closing_state is not None
        self._validate_state(channel, state, min_nonce=minimum, strict_newer=strict)
        if state.nonce > channel.nonce:
            channel.apply_state(state)
        if channel.closing_started_block is None:
            channel.closing_started_block = int(current_block)
        channel.closing_state = state
        channel.status = ChannelStatus.CLOSING
        self._publish_channel(channel)
        LOGGER.info("challenge close submitted for phantom channel %s", channel_id)
        return channel

    def submit_newer_state(self, channel_id: str, state: ChannelState, *, current_block: int) -> Channel:
        channel = self.get_channel(channel_id)
        if channel.status is not ChannelStatus.CLOSING or channel.closing_state is None:
            raise DisputeError("channel is not in a dispute window")
        if channel.closing_started_block is None or current_block > channel.closing_started_block + channel.dispute_window:
            raise DisputeError("dispute window has expired")
        self._validate_state(channel, state, min_nonce=channel.closing_state.nonce, strict_newer=True)
        channel.closing_state = state
        if state.nonce > channel.nonce:
            channel.apply_state(state)
        self._publish_channel(channel)
        return channel

    def finalize_channel(self, channel_id: str, *, chain_state: StateDB, current_block: int) -> Channel:
        channel = self.get_channel(channel_id)
        if channel.status is ChannelStatus.CLOSED:
            raise DisputeError("channel is already finalized")
        if channel.status is not ChannelStatus.CLOSING or channel.closing_state is None:
            raise DisputeError("channel is not waiting for dispute finalization")
        assert channel.closing_started_block is not None
        if current_block <= channel.closing_started_block + channel.dispute_window:
            raise DisputeError("dispute window is still active")
        self._settle_channel(channel, channel.closing_state, chain_state=chain_state, current_block=current_block)
        LOGGER.info("finalized phantom channel %s", channel_id)
        return channel

    def submit_state_with_proof(self, channel_id: str, state: ChannelState, zk_proof: dict[str, object]) -> Channel:
        channel = self.get_channel(channel_id)
        if not self._proof_verifier.verify_state_update(channel, state, zk_proof):
            raise SignatureValidationError("zk proof rejected for phantom channel state submission")
        self._validate_state(channel, state, min_nonce=channel.nonce, strict_newer=True)
        channel.apply_state(state)
        self._publish_channel(channel)
        return channel

    def _settle_channel(self, channel: Channel, final_state: ChannelState, *, chain_state: StateDB, current_block: int) -> None:
        if channel.escrow_address is None:
            raise ChannelValidationError("channel escrow address is missing")
        escrow_balance = chain_state.get_balance(channel.escrow_address)
        expected = final_state.total_balance
        if escrow_balance != expected:
            raise ChannelValidationError("channel escrow balance does not match the committed channel state")
        for participant, amount in final_state.balances.items():
            if not chain_state.transfer(channel.escrow_address, participant, amount):
                raise ChannelValidationError("failed to settle channel escrow")
        channel.closing_state = final_state
        channel.closing_started_block = int(current_block)
        channel.status = ChannelStatus.CLOSED
        channel.apply_state(final_state)
        self._publish_channel(channel)

    def find_route(self, sender: Address, receiver: Address, amount: int) -> PaymentRoute:
        return self._graph.find_route(sender, receiver, amount, max_hops=self.max_route_length)

    def _find_pending_payment_by_hashlock(self, hashlock: Hash) -> PaymentRecord:
        for payment in self._payments.values():
            if payment.hashlock == hashlock and payment.status is PaymentStatus.PENDING:
                return payment
        raise HTLCError("no pending payment matches the provided secret")

    def _find_matching_htlc(self, channel: Channel, hashlock: Hash) -> HTLC:
        for htlc in channel.active_htlcs:
            if htlc.hashlock == hashlock and htlc.status is HTLCStatus.PENDING:
                return htlc
        raise HTLCError(f"channel {channel.channel_id} does not contain the requested HTLC")

    def _apply_signed_state(
        self,
        channel: Channel,
        *,
        balances: dict[Address, int],
        active_htlcs: tuple[HTLC, ...],
    ) -> None:
        state = ChannelState(
            channel_id=channel.channel_id,
            nonce=channel.nonce + 1,
            balances=balances,
            active_htlcs=active_htlcs,
        )
        state = self._sign_state(channel, state)
        self._validate_state(channel, state, min_nonce=channel.nonce, strict_newer=True)
        channel.apply_state(state)
        self._publish_channel(channel)

    def _apply_htlc_add(self, channel: Channel, htlc: HTLC) -> None:
        if channel.status is not ChannelStatus.OPEN:
            raise HTLCError("HTLCs can only be added to open channels")
        if channel.available_balance(htlc.sender) < htlc.amount:
            raise HTLCError("insufficient unlocked liquidity for HTLC")
        if len(channel.active_htlcs) >= min(channel.max_htlcs, self.max_htlcs_per_channel):
            raise HTLCError("channel reached the active HTLC limit")
        self._apply_signed_state(channel, balances=dict(channel.balances), active_htlcs=(*channel.active_htlcs, htlc))

    def _apply_htlc_claim(self, channel: Channel, hashlock: Hash, *, current_block: int) -> None:
        htlc = self._find_matching_htlc(channel, hashlock)
        if current_block > htlc.timelock:
            raise HTLCError("cannot redeem an expired HTLC")
        balances = dict(channel.balances)
        balances[htlc.sender] -= htlc.amount
        balances[htlc.receiver] += htlc.amount
        remaining = tuple(item for item in channel.active_htlcs if item.htlc_id != htlc.htlc_id)
        self._apply_signed_state(channel, balances=balances, active_htlcs=remaining)

    def _apply_htlc_refund(self, channel: Channel, hashlock: Hash, *, current_block: int, cascading: bool = False) -> None:
        htlc = self._find_matching_htlc(channel, hashlock)
        if not cascading and current_block <= htlc.timelock:
            raise HTLCError("HTLC refund timelock has not elapsed")
        remaining = tuple(item for item in channel.active_htlcs if item.htlc_id != htlc.htlc_id)
        self._apply_signed_state(channel, balances=dict(channel.balances), active_htlcs=remaining)

    def initiate_payment(
        self,
        sender: Address,
        receiver: Address,
        amount: int,
        *,
        current_block: int,
        secret: bytes | bytearray | memoryview | None = None,
    ) -> tuple[PaymentRecord, bytes]:
        route = self.find_route(sender, receiver, amount)
        if len(route.hops) > self.max_route_length:
            raise HTLCError("route exceeds the maximum hop count")
        secret_bytes = secrets.token_bytes(32) if secret is None else bytes(secret)
        hashlock = hash_secret(secret_bytes)
        payment_id = self._next_payment_id(sender, receiver, amount, current_block)
        snapshot = self.snapshot()
        try:
            timed_hops = []
            htlc_ids: list[str] = []
            for index, hop in enumerate(route.hops):
                timelock = current_block + self.base_timelock + ((len(route.hops) - index - 1) * self.timelock_delta)
                if timelock - current_block > self.max_timelock_horizon:
                    raise HTLCError("timelock horizon exceeds the configured safety bound")
                timed_hop = type(hop)(
                    channel_id=hop.channel_id,
                    sender=hop.sender,
                    receiver=hop.receiver,
                    amount=hop.amount,
                    fee=hop.fee,
                    timelock=timelock,
                )
                htlc_id = phantom_hash(
                    {
                        "payment_id": payment_id,
                        "channel_id": hop.channel_id,
                        "amount": hop.amount,
                        "hashlock": hashlock.to_hex(),
                    }
                ).to_hex()
                htlc = HTLC(
                    htlc_id=htlc_id,
                    hashlock=hashlock,
                    timelock=timelock,
                    amount=hop.amount,
                    sender=hop.sender,
                    receiver=hop.receiver,
                    fee=hop.fee,
                )
                self._apply_htlc_add(self.get_channel(hop.channel_id), htlc)
                timed_hops.append(timed_hop)
                htlc_ids.append(htlc_id)
            payment = PaymentRecord(
                payment_id=payment_id,
                route=PaymentRoute(
                    sender=route.sender,
                    receiver=route.receiver,
                    amount=route.amount,
                    total_amount=route.total_amount,
                    hops=tuple(timed_hops),
                ),
                hashlock=hashlock,
                created_block=current_block,
                htlc_ids=tuple(htlc_ids),
            )
            self._payments[payment_id] = payment
            LOGGER.info("initiated phantom payment %s across %s hops", payment_id, len(timed_hops))
            return payment, secret_bytes
        except Exception:
            self.restore(snapshot)
            raise

    def redeem_payment(self, secret: bytes | bytearray | memoryview, *, current_block: int) -> PaymentRecord:
        secret_bytes = bytes(secret)
        hashlock = hash_secret(secret_bytes)
        payment = self._find_pending_payment_by_hashlock(hashlock)
        if current_block > payment.route.hops[-1].timelock:
            raise HTLCError("receiver missed the HTLC redemption window")
        snapshot = self.snapshot()
        try:
            for hop in reversed(payment.route.hops):
                self._apply_htlc_claim(self.get_channel(hop.channel_id), hashlock, current_block=current_block)
            payment.status = PaymentStatus.COMPLETED
            payment.secret = secret_bytes
            return payment
        except Exception:
            self.restore(snapshot)
            raise

    def refund_payment(self, payment_id: str, *, current_block: int) -> PaymentRecord:
        payment = self._payments.get(payment_id)
        if payment is None:
            raise HTLCError(f"unknown payment {payment_id}")
        if payment.status is not PaymentStatus.PENDING:
            raise HTLCError("payment is not waiting for refund")
        if current_block <= payment.route.hops[-1].timelock:
            raise HTLCError("payment cannot be refunded before the receiver-side timelock expires")
        snapshot = self.snapshot()
        try:
            for hop in reversed(payment.route.hops):
                self._apply_htlc_refund(
                    self.get_channel(hop.channel_id),
                    payment.hashlock,
                    current_block=current_block,
                    cascading=True,
                )
            payment.status = PaymentStatus.REFUNDED
            return payment
        except Exception:
            self.restore(snapshot)
            raise

    def expire_payments(self, *, current_block: int) -> tuple[PaymentRecord, ...]:
        refunded: list[PaymentRecord] = []
        for payment in self.list_payments():
            if payment.status is PaymentStatus.PENDING and current_block > payment.route.hops[-1].timelock:
                refunded.append(self.refund_payment(payment.payment_id, current_block=current_block))
        return tuple(refunded)

    def apply_operation(
        self,
        operation: SettlementOperation,
        *,
        chain_state: StateDB,
        current_block: int,
        sender: Address,
    ) -> Channel:
        if isinstance(operation, OpenChannelOperation):
            participants = tuple(
                sorted(
                    (
                        Address.from_hex(str(operation.participants[0]))
                        if isinstance(operation.participants[0], str)
                        else operation.participants[0],
                        Address.from_hex(str(operation.participants[1]))
                        if isinstance(operation.participants[1], str)
                        else operation.participants[1],
                    ),
                    key=lambda item: item.to_hex(),
                )
            )
            if sender not in participants:
                raise SignatureValidationError("only a channel participant can submit an open-channel operation")
            for participant in participants:
                deposit = int(operation.deposits.get(participant, operation.deposits.get(participant.to_hex(), 0)))
                if participant != sender and deposit > 0 and participant.to_hex() not in self._signing_keys:
                    raise SignatureValidationError(
                        "open-channel operation needs local approval for every participant funding the channel"
                    )
            return self.open_channel(
                participants=operation.participants,
                deposits=operation.deposits,
                dispute_window=operation.dispute_window,
                chain_state=chain_state,
                current_block=current_block,
                fee_base=operation.fee_base,
                fee_ppm=operation.fee_ppm,
                channel_id_hint=operation.channel_id_hint,
            )
        if isinstance(operation, CloseChannelOperation):
            if sender not in self.get_channel(operation.channel_id).participants:
                raise SignatureValidationError("only a channel participant can cooperatively close the channel")
            return self.close_channel(operation.channel_id, operation.final_state, chain_state=chain_state, current_block=current_block)
        if isinstance(operation, ChallengeChannelOperation):
            if sender not in self.get_channel(operation.channel_id).participants:
                raise SignatureValidationError("only a channel participant can challenge a close")
            return self.challenge_close(operation.channel_id, operation.state, current_block=current_block)
        if isinstance(operation, FinalizeChannelOperation):
            if sender not in self.get_channel(operation.channel_id).participants:
                raise SignatureValidationError("only a channel participant can finalize a channel dispute")
            return self.finalize_channel(operation.channel_id, chain_state=chain_state, current_block=current_block)
        if isinstance(operation, SubmitStateWithProofOperation):
            if sender not in self.get_channel(operation.channel_id).participants:
                raise SignatureValidationError("only a channel participant can submit a proof-backed state")
            return self.submit_state_with_proof(operation.channel_id, operation.state, operation.zk_proof)
        raise TypeError(f"unsupported phantom operation: {type(operation)!r}")
