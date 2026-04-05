from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
for crate in ("transactions", "zk", "state", "encoding", "crypto", "primitives"):
    sys.path.insert(0, str(CRATES / crate / "src"))

from crypto import address_from_private_key, keccak256
from primitives import Address, U256
from state import State
from transactions import (
    AccessListEntry,
    EIP1559Transaction,
    LegacyTransaction,
    TransactionDecodeError,
    TransactionValidationError,
    TransactionValidator,
    ZKProofValidationError,
    ZKTransaction,
    ZKVerificationTiming,
    decode_transaction,
)
from zk import ProofType, ZKProof, ZKVerifierRegistry


PRIVATE_KEY_ONE = 1
SENDER_ONE = address_from_private_key(PRIVATE_KEY_ONE)
EIP155_EXAMPLE_RAW = bytes.fromhex(
    "f86c098504a817c800825208943535353535353535353535353535353535353535880de0b6b3a76400008025"
    "a028ef61340bd939bc2195fe537567866003e1a15d3c71ff63e1590620aa636276"
    "a067cbe9d8997f761aecb703304b3800ccf555c9f3dc64214b297fb1966a3b6d83"
)


class _AcceptVerifier:
    def verify(self, proof: ZKProof, public_inputs: list[U256]) -> bool:
        return proof.data == b"valid-proof" and public_inputs == [U256(7), U256(11)]


class _RejectVerifier:
    def verify(self, proof: ZKProof, public_inputs: list[U256]) -> bool:
        return False


class TransactionTests(unittest.TestCase):
    def test_decode_known_eip155_legacy_transaction(self) -> None:
        transaction = decode_transaction(EIP155_EXAMPLE_RAW)
        self.assertIsInstance(transaction, LegacyTransaction)
        self.assertEqual(transaction.chain_id, 1)
        self.assertEqual(transaction.sender().to_hex(), "0x9d8a62f656a8d1615c1294fd71e9cfb3e4855a4f")
        self.assertEqual(transaction.signing_hash().to_hex(), "0xdaf5a779ae972f972197303d7b574746c7ef83eadac0f2791ad23db92e4c8e53")
        self.assertEqual(transaction.encode(), EIP155_EXAMPLE_RAW)

    def test_legacy_intrinsic_gas_contract_creation(self) -> None:
        transaction = LegacyTransaction(
            nonce=0,
            gas_price=1,
            gas_limit=60_000,
            to=None,
            value=0,
            data=b"\x00\x01",
        )
        self.assertEqual(transaction.intrinsic_gas(), 21_000 + 32_000 + 4 + 16)

    def test_eip1559_roundtrip_and_sender_recovery(self) -> None:
        transaction = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1_000_000_000,
            max_fee_per_gas=2_000_000_000,
            gas_limit=30_000,
            to=Address.from_hex("0x3535353535353535353535353535353535353535"),
            value=1,
            data=b"\x01\x02",
            access_list=(
                AccessListEntry(
                    address=Address.from_hex("0x1111111111111111111111111111111111111111"),
                    storage_keys=(U256(1), U256(2)),
                ),
            ),
        ).sign(PRIVATE_KEY_ONE)
        raw = transaction.encode()
        decoded = decode_transaction(raw)
        self.assertIsInstance(decoded, EIP1559Transaction)
        self.assertEqual(decoded, transaction)
        self.assertEqual(decoded.sender(), SENDER_ONE)

    def test_zk_transaction_roundtrip(self) -> None:
        base = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=500_000,
            to=Address.from_hex("0x2222222222222222222222222222222222222222"),
            value=0,
            data=b"\xaa\xbb",
            access_list=(),
        )
        transaction = ZKTransaction(
            base_tx=base,
            proof=ZKProof(ProofType.PLONK, b"proof-bytes"),
            public_inputs=(U256(5), U256(6)),
        ).sign(PRIVATE_KEY_ONE)
        decoded = decode_transaction(transaction.encode())
        self.assertIsInstance(decoded, ZKTransaction)
        self.assertEqual(decoded, transaction)
        self.assertEqual(decoded.sender(), SENDER_ONE)

    def test_invalid_type_rejected(self) -> None:
        with self.assertRaises(TransactionDecodeError):
            decode_transaction(b"\x01\x80")


class ValidationTests(unittest.TestCase):
    def test_standard_transaction_validates_without_zk(self) -> None:
        state = State()
        validator = TransactionValidator(chain_id=1)
        transaction = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=21_000,
            to=Address.from_hex("0x3333333333333333333333333333333333333333"),
            value=0,
            data=b"",
        ).sign(PRIVATE_KEY_ONE)
        result = validator.validate(transaction, state)
        self.assertEqual(result.sender, SENDER_ONE)
        self.assertTrue(result.zk_verified)
        self.assertFalse(result.zk_verification_deferred)
        self.assertEqual(result.intrinsic_gas, 21_000)

    def test_nonce_validation_rejects_mismatch(self) -> None:
        state = State()
        state.increment_nonce(SENDER_ONE)
        validator = TransactionValidator(chain_id=1)
        transaction = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=21_000,
            to=Address.from_hex("0x4444444444444444444444444444444444444444"),
            value=0,
            data=b"",
        ).sign(PRIVATE_KEY_ONE)
        with self.assertRaises(TransactionValidationError):
            validator.validate(transaction, state)

    def test_valid_zk_proof_passes_pre_execution_validation(self) -> None:
        state = State()
        registry = ZKVerifierRegistry()
        registry.register(ProofType.GROTH16, _AcceptVerifier())
        transaction = ZKTransaction(
            base_tx=EIP1559Transaction(
                chain_id=1,
                nonce=0,
                max_priority_fee_per_gas=1,
                max_fee_per_gas=2,
                gas_limit=400_000,
                to=Address.from_hex("0x5555555555555555555555555555555555555555"),
                value=0,
                data=b"",
            ),
            proof=ZKProof(ProofType.GROTH16, b"valid-proof"),
            public_inputs=(U256(7), U256(11)),
        ).sign(PRIVATE_KEY_ONE)
        result = TransactionValidator(chain_id=1).validate(transaction, state, registry)
        self.assertTrue(result.zk_verified)
        self.assertGreater(result.intrinsic_gas, transaction.base_tx.intrinsic_gas())

    def test_invalid_zk_proof_rejects_transaction(self) -> None:
        state = State()
        registry = ZKVerifierRegistry()
        registry.register(ProofType.STARK, _RejectVerifier())
        transaction = ZKTransaction(
            base_tx=EIP1559Transaction(
                chain_id=1,
                nonce=0,
                max_priority_fee_per_gas=1,
                max_fee_per_gas=2,
                gas_limit=500_000,
                to=Address.from_hex("0x6666666666666666666666666666666666666666"),
                value=0,
                data=b"",
            ),
            proof=ZKProof(ProofType.STARK, b"bad-proof"),
            public_inputs=(),
        ).sign(PRIVATE_KEY_ONE)
        with self.assertRaises(ZKProofValidationError):
            TransactionValidator(chain_id=1).validate(transaction, state, registry)

    def test_deferred_zk_verification_is_supported(self) -> None:
        state = State()
        registry = ZKVerifierRegistry()
        registry.register(ProofType.GROTH16, _AcceptVerifier())
        transaction = ZKTransaction(
            base_tx=EIP1559Transaction(
                chain_id=1,
                nonce=0,
                max_priority_fee_per_gas=1,
                max_fee_per_gas=2,
                gas_limit=400_000,
                to=Address.from_hex("0x7777777777777777777777777777777777777777"),
                value=0,
                data=b"\x99",
            ),
            proof=ZKProof(ProofType.GROTH16, b"valid-proof"),
            public_inputs=(U256(7), U256(11)),
        ).sign(PRIVATE_KEY_ONE)
        validator = TransactionValidator(chain_id=1, zk_verification_timing=ZKVerificationTiming.DURING_EXECUTION)
        result = validator.validate(transaction, state)
        self.assertFalse(result.zk_verified)
        self.assertTrue(result.zk_verification_deferred)
        verified = result.verify_zk(registry)
        self.assertTrue(verified.zk_verified)

    def test_eip1559_intrinsic_gas_includes_access_list(self) -> None:
        transaction = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=100_000,
            to=Address.from_hex("0x8888888888888888888888888888888888888888"),
            value=0,
            data=b"\x00\xff",
            access_list=(
                AccessListEntry(
                    address=Address.from_hex("0x9999999999999999999999999999999999999999"),
                    storage_keys=(U256(1), U256(2), U256(3)),
                ),
            ),
        )
        self.assertEqual(transaction.intrinsic_gas(), 21_000 + 4 + 16 + 2_400 + (3 * 1_900))

    def test_transaction_hash_is_keccak_of_raw_payload(self) -> None:
        transaction = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=21_000,
            to=Address.from_hex("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
            value=0,
            data=b"",
        ).sign(PRIVATE_KEY_ONE)
        self.assertEqual(transaction.tx_hash(), keccak256(transaction.encode()))


if __name__ == "__main__":
    unittest.main()
