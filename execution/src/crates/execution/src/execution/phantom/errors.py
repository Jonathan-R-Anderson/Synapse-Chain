from __future__ import annotations


class PhantomChannelError(ValueError):
    """Base error for Phantom Channel failures."""


class ChannelValidationError(PhantomChannelError):
    """Raised when a channel or channel state is invalid."""


class SignatureValidationError(ChannelValidationError):
    """Raised when a channel or settlement signature is invalid."""


class ReplayAttackError(ChannelValidationError):
    """Raised when a stale nonce or state is replayed."""


class RouteNotFoundError(PhantomChannelError):
    """Raised when routing cannot satisfy a payment request."""


class HTLCError(PhantomChannelError):
    """Raised for HTLC construction, redemption, or refund failures."""


class DisputeError(PhantomChannelError):
    """Raised for invalid channel close or dispute actions."""


class SettlementError(PhantomChannelError):
    """Raised for on-chain settlement failures."""


class MempoolError(PhantomChannelError):
    """Raised for phantom settlement mempool failures."""

