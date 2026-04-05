from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from primitives import Address, Hash

from .hashing import phantom_hash


def _normalize_address(value: Address | bytes | bytearray | memoryview | str) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, str):
        return Address.from_hex(value)
    return Address(bytes(value))


def _normalize_hash(value: Hash | bytes | bytearray | memoryview | str) -> Hash:
    if isinstance(value, Hash):
        return value
    if isinstance(value, str):
        return Hash.from_hex(value)
    return Hash(bytes(value))


def _normalize_balances(balances: dict[Address | str, int]) -> dict[Address, int]:
    normalized: dict[Address, int] = {}
    for participant, balance in balances.items():
        amount = int(balance)
        if amount < 0:
            raise ValueError("balances cannot be negative")
        normalized[_normalize_address(participant)] = amount
    return dict(sorted(normalized.items(), key=lambda item: item[0].to_hex()))


class ChannelStatus(str, Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class HTLCStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    REFUNDED = "REFUNDED"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class OperationType(str, Enum):
    OPEN_CHANNEL = "OPEN_CHANNEL"
    COOPERATIVE_CLOSE = "COOPERATIVE_CLOSE"
    CHALLENGE_CLOSE = "CHALLENGE_CLOSE"
    FINALIZE_CHANNEL = "FINALIZE_CHANNEL"
    SUBMIT_STATE_WITH_PROOF = "SUBMIT_STATE_WITH_PROOF"


@dataclass(frozen=True, slots=True)
class HTLC:
    htlc_id: str
    hashlock: Hash | str
    timelock: int
    amount: int
    sender: Address | str
    receiver: Address | str
    fee: int = 0
    status: HTLCStatus = HTLCStatus.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(self, "hashlock", _normalize_hash(self.hashlock))
        object.__setattr__(self, "sender", _normalize_address(self.sender))
        object.__setattr__(self, "receiver", _normalize_address(self.receiver))
        object.__setattr__(self, "timelock", int(self.timelock))
        object.__setattr__(self, "amount", int(self.amount))
        object.__setattr__(self, "fee", int(self.fee))
        if self.amount <= 0:
            raise ValueError("htlc amount must be positive")
        if self.fee < 0:
            raise ValueError("htlc fee cannot be negative")
        if self.timelock <= 0:
            raise ValueError("htlc timelock must be positive")

    def to_payload(self) -> dict[str, object]:
        return {
            "htlc_id": self.htlc_id,
            "hashlock": self.hashlock.to_hex(),
            "timelock": self.timelock,
            "amount": self.amount,
            "sender": self.sender.to_hex(),
            "receiver": self.receiver.to_hex(),
            "fee": self.fee,
            "status": self.status.value,
        }


@dataclass(slots=True)
class ChannelState:
    channel_id: str
    nonce: int
    balances: dict[Address | str, int]
    active_htlcs: tuple[HTLC, ...] = ()
    state_root: Hash | str | None = None
    signatures: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.nonce = int(self.nonce)
        if self.nonce < 0:
            raise ValueError("state nonce cannot be negative")
        self.balances = _normalize_balances(self.balances)
        self.active_htlcs = tuple(sorted(self.active_htlcs, key=lambda item: item.htlc_id))
        self.signatures = {str(address): str(signature) for address, signature in sorted(self.signatures.items())}
        computed = phantom_hash(self.state_payload())
        self.state_root = computed if self.state_root is None else _normalize_hash(self.state_root)
        if self.state_root != computed:
            raise ValueError("channel state root does not match the state payload")

    @property
    def participants(self) -> tuple[Address, ...]:
        return tuple(self.balances)

    @property
    def total_balance(self) -> int:
        return sum(self.balances.values())

    def state_payload(self) -> dict[str, object]:
        return {
            "channel_id": self.channel_id,
            "nonce": self.nonce,
            "balances": {participant.to_hex(): self.balances[participant] for participant in self.balances},
            "active_htlcs": [htlc.to_payload() for htlc in self.active_htlcs],
        }

    def clone(
        self,
        *,
        nonce: int | None = None,
        balances: dict[Address | str, int] | None = None,
        active_htlcs: tuple[HTLC, ...] | None = None,
        signatures: dict[str, str] | None = None,
    ) -> "ChannelState":
        return ChannelState(
            channel_id=self.channel_id,
            nonce=self.nonce if nonce is None else nonce,
            balances=self.balances if balances is None else balances,
            active_htlcs=self.active_htlcs if active_htlcs is None else active_htlcs,
            signatures=dict(self.signatures if signatures is None else signatures),
        )


@dataclass(slots=True)
class Channel:
    channel_id: str
    participants: tuple[Address | str, Address | str]
    balances: dict[Address | str, int]
    nonce: int
    state_root: Hash | str
    open_block: int
    dispute_window: int
    status: ChannelStatus = ChannelStatus.OPEN
    escrow_address: Address | str | None = None
    latest_state: ChannelState | None = None
    closing_state: ChannelState | None = None
    closing_started_block: int | None = None
    max_htlcs: int = 16
    fee_base: int = 0
    fee_ppm: int = 0

    def __post_init__(self) -> None:
        first = _normalize_address(self.participants[0])
        second = _normalize_address(self.participants[1])
        if first == second:
            raise ValueError("channels require distinct participants")
        self.participants = tuple(sorted((first, second), key=lambda item: item.to_hex()))  # type: ignore[assignment]
        self.balances = _normalize_balances(self.balances)
        if tuple(self.balances) != self.participants:
            raise ValueError("channel balances must match the participant set")
        self.nonce = int(self.nonce)
        self.open_block = int(self.open_block)
        self.dispute_window = int(self.dispute_window)
        self.max_htlcs = int(self.max_htlcs)
        self.fee_base = int(self.fee_base)
        self.fee_ppm = int(self.fee_ppm)
        self.state_root = _normalize_hash(self.state_root)
        self.escrow_address = None if self.escrow_address is None else _normalize_address(self.escrow_address)
        if self.latest_state is None:
            self.latest_state = ChannelState(
                channel_id=self.channel_id,
                nonce=self.nonce,
                balances=self.balances,
            )
        if self.latest_state.state_root != self.state_root:
            raise ValueError("channel state_root must match latest_state")

    @property
    def total_capacity(self) -> int:
        return sum(self.balances.values())

    @property
    def active_htlcs(self) -> tuple[HTLC, ...]:
        assert self.latest_state is not None
        return self.latest_state.active_htlcs

    def locked_balance(self, participant: Address) -> int:
        return sum(
            htlc.amount
            for htlc in self.active_htlcs
            if htlc.sender == participant and htlc.status is HTLCStatus.PENDING
        )

    def available_balance(self, participant: Address) -> int:
        return self.balances[participant] - self.locked_balance(participant)

    def routing_fee(self, amount: int) -> int:
        proportional = (int(amount) * self.fee_ppm + 999_999) // 1_000_000 if self.fee_ppm else 0
        return self.fee_base + proportional

    def apply_state(self, state: ChannelState) -> None:
        self.latest_state = state
        self.balances = dict(state.balances)
        self.nonce = state.nonce
        self.state_root = state.state_root


@dataclass(frozen=True, slots=True)
class ChannelEdge:
    channel_id: str
    participants: tuple[Address, Address]
    capacity: int
    available_balances: dict[Address, int]
    fee_base: int
    fee_ppm: int
    status: ChannelStatus


@dataclass(frozen=True, slots=True)
class RouteHop:
    channel_id: str
    sender: Address
    receiver: Address
    amount: int
    fee: int
    timelock: int = 0


@dataclass(frozen=True, slots=True)
class PaymentRoute:
    sender: Address
    receiver: Address
    amount: int
    total_amount: int
    hops: tuple[RouteHop, ...]


@dataclass(slots=True)
class PaymentRecord:
    payment_id: str
    route: PaymentRoute
    hashlock: Hash | str
    created_block: int
    htlc_ids: tuple[str, ...]
    status: PaymentStatus = PaymentStatus.PENDING
    secret: bytes | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        self.hashlock = _normalize_hash(self.hashlock)


@dataclass(frozen=True, slots=True)
class OpenChannelOperation:
    participants: tuple[Address | str, Address | str]
    deposits: dict[Address | str, int]
    dispute_window: int
    fee_base: int = 0
    fee_ppm: int = 0
    channel_id_hint: str | None = None

    op_type: OperationType = OperationType.OPEN_CHANNEL

    def to_payload(self) -> dict[str, object]:
        return {
            "op_type": self.op_type.value,
            "participants": [_normalize_address(item).to_hex() for item in self.participants],
            "deposits": {key.to_hex(): value for key, value in _normalize_balances(self.deposits).items()},
            "dispute_window": int(self.dispute_window),
            "fee_base": int(self.fee_base),
            "fee_ppm": int(self.fee_ppm),
            "channel_id_hint": self.channel_id_hint,
        }


@dataclass(frozen=True, slots=True)
class CloseChannelOperation:
    channel_id: str
    final_state: ChannelState

    op_type: OperationType = OperationType.COOPERATIVE_CLOSE

    def to_payload(self) -> dict[str, object]:
        return {
            "op_type": self.op_type.value,
            "channel_id": self.channel_id,
            "final_state": self.final_state.state_payload() | {"signatures": dict(self.final_state.signatures)},
        }


@dataclass(frozen=True, slots=True)
class ChallengeChannelOperation:
    channel_id: str
    state: ChannelState

    op_type: OperationType = OperationType.CHALLENGE_CLOSE

    def to_payload(self) -> dict[str, object]:
        return {
            "op_type": self.op_type.value,
            "channel_id": self.channel_id,
            "state": self.state.state_payload() | {"signatures": dict(self.state.signatures)},
        }


@dataclass(frozen=True, slots=True)
class FinalizeChannelOperation:
    channel_id: str

    op_type: OperationType = OperationType.FINALIZE_CHANNEL

    def to_payload(self) -> dict[str, object]:
        return {
            "op_type": self.op_type.value,
            "channel_id": self.channel_id,
        }


@dataclass(frozen=True, slots=True)
class SubmitStateWithProofOperation:
    channel_id: str
    state: ChannelState
    zk_proof: dict[str, object]

    op_type: OperationType = OperationType.SUBMIT_STATE_WITH_PROOF

    def to_payload(self) -> dict[str, object]:
        return {
            "op_type": self.op_type.value,
            "channel_id": self.channel_id,
            "state": self.state.state_payload() | {"signatures": dict(self.state.signatures)},
            "zk_proof": dict(self.zk_proof),
        }


SettlementOperation: TypeAlias = (
    OpenChannelOperation
    | CloseChannelOperation
    | ChallengeChannelOperation
    | FinalizeChannelOperation
    | SubmitStateWithProofOperation
)


@dataclass(frozen=True, slots=True)
class SettlementTransaction:
    sender: Address | str
    nonce: int
    operation: SettlementOperation
    signature: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sender", _normalize_address(self.sender))
        object.__setattr__(self, "nonce", int(self.nonce))
        object.__setattr__(self, "signature", None if self.signature is None else str(self.signature))
        if self.nonce < 0:
            raise ValueError("settlement transaction nonce cannot be negative")

    def signing_payload(self) -> dict[str, object]:
        return {
            "sender": self.sender.to_hex(),
            "nonce": self.nonce,
            "operation": self.operation.to_payload(),
        }

    def signing_hash(self) -> Hash:
        return phantom_hash(self.signing_payload())

    def tx_hash(self) -> Hash:
        return phantom_hash(self.signing_payload() | {"signature": self.signature})

    def with_signature(self, signature: str) -> "SettlementTransaction":
        return SettlementTransaction(sender=self.sender, nonce=self.nonce, operation=self.operation, signature=signature)


@dataclass(frozen=True, slots=True)
class PhantomReceipt:
    tx_hash: Hash
    block_number: int
    sender: Address
    success: bool
    result: str
    channel_id: str | None = None

