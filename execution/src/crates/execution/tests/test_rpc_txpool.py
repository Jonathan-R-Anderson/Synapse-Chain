from __future__ import annotations

import unittest

from execution_test_helpers import PRIVATE_KEY_ONE, addr, make_legacy_tx

from crypto import address_from_private_key
from rpc.errors import ReplacementUnderpricedError
from rpc.txpool_access import TxPool


class RpcTxPoolTests(unittest.TestCase):
    def test_same_nonce_replacement_requires_fee_bump(self) -> None:
        sender = address_from_private_key(PRIVATE_KEY_ONE)
        recipient = addr("0x2000000000000000000000000000000000000002")
        pool = TxPool(replacement_bump_percent=10)

        original = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            gas_limit=21_000,
            gas_price=100,
            value=1,
            chain_id=None,
        )
        pool.add(original, sender=sender, base_fee=None)

        underpriced = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            gas_limit=21_000,
            gas_price=109,
            value=2,
            chain_id=None,
        )
        with self.assertRaises(ReplacementUnderpricedError):
            pool.add(underpriced, sender=sender, base_fee=None)

        replacement = make_legacy_tx(
            PRIVATE_KEY_ONE,
            0,
            recipient,
            gas_limit=21_000,
            gas_price=110,
            value=3,
            chain_id=None,
        )
        accepted = pool.add(replacement, sender=sender, base_fee=None)
        self.assertEqual(accepted.transaction.tx_hash(), replacement.tx_hash())
        self.assertEqual(len(pool.ordered()), 1)


if __name__ == "__main__":
    unittest.main()
