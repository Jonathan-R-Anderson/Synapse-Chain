from __future__ import annotations

import unittest

from execution_test_helpers import addr, make_legacy_tx

from execution import EIP1559Transaction, calculate_intrinsic_gas, calculate_zk_verification_gas
from zk import ProofType, ZKGasModel, ZKProof
from execution import ZKTransaction


class IntrinsicGasTests(unittest.TestCase):
    def test_legacy_transfer_intrinsic_gas(self) -> None:
        transaction = make_legacy_tx(1, 0, addr("0x1000000000000000000000000000000000000001"))
        self.assertEqual(calculate_intrinsic_gas(transaction), 21_000)

    def test_contract_creation_intrinsic_gas_counts_data(self) -> None:
        transaction = make_legacy_tx(1, 0, None, data=b"\x00\x01")
        self.assertEqual(calculate_intrinsic_gas(transaction), 21_000 + 32_000 + 4 + 16)

    def test_zk_verification_gas_is_separate_from_intrinsic_gas(self) -> None:
        base = EIP1559Transaction(
            chain_id=1,
            nonce=0,
            max_priority_fee_per_gas=1,
            max_fee_per_gas=2,
            gas_limit=500_000,
            to=addr("0x2000000000000000000000000000000000000002"),
            value=0,
            data=b"\x01\x02",
        )
        transaction = ZKTransaction(base_tx=base, proof=ZKProof(ProofType.PLONK, b"proof-bytes"))
        self.assertEqual(calculate_intrinsic_gas(transaction), base.intrinsic_gas())
        self.assertEqual(
            calculate_zk_verification_gas(transaction, ZKGasModel()),
            ZKGasModel().verification_gas(transaction.proof),
        )


if __name__ == "__main__":
    unittest.main()
