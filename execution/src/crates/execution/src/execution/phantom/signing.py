from __future__ import annotations

from crypto import address_from_private_key, address_from_public_key, recover_public_key, sign_message_hash
from primitives import Address, Hash

from .errors import SignatureValidationError
from .models import ChannelState, SettlementTransaction


def sign_hash(message_hash: Hash, private_key: bytes | bytearray | memoryview | int) -> str:
    return "0x" + sign_message_hash(message_hash.to_bytes(), private_key).to_bytes().hex()


def recover_signer(message_hash: Hash, signature: str) -> Address:
    raw = bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)
    public_key = recover_public_key(message_hash.to_bytes(), raw)
    return address_from_public_key(public_key)


def verify_signature(message_hash: Hash, signature: str, expected_signer: Address) -> None:
    signer = recover_signer(message_hash, signature)
    if signer != expected_signer:
        raise SignatureValidationError(
            f"signature signer mismatch: expected {expected_signer.to_hex()}, got {signer.to_hex()}"
        )


def sign_channel_state(state: ChannelState, private_key: bytes | bytearray | memoryview | int) -> ChannelState:
    signer = address_from_private_key(private_key).to_hex()
    signatures = dict(state.signatures)
    signatures[signer] = sign_hash(state.state_root, private_key)
    return state.clone(signatures=signatures)


def sign_settlement_transaction(
    transaction: SettlementTransaction,
    private_key: bytes | bytearray | memoryview | int,
) -> SettlementTransaction:
    sender = address_from_private_key(private_key)
    if sender != transaction.sender:
        raise SignatureValidationError("settlement private key does not match the transaction sender")
    return transaction.with_signature(sign_hash(transaction.signing_hash(), private_key))

