from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRATES = ROOT.parent
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    sys.path.insert(0, str(CRATES / crate / "src"))

from crypto import address_from_private_key
from evm import StateDB
from execution import (
    ChannelManager,
    ChannelState,
    ChannelValidationError,
    ChallengeChannelOperation,
    CloseChannelOperation,
    FinalizeChannelOperation,
    HTLCError,
    InMemoryChannelDHT,
    MempoolError,
    OpenChannelOperation,
    PaymentStatus,
    PhantomSettlementChain,
    ReplayAttackError,
    RouteNotFoundError,
    SettlementTransaction,
    SignatureValidationError,
    sign_channel_state,
    sign_settlement_transaction,
)


PRIVATE_KEYS = {
    "alice": 11,
    "bob": 12,
    "carol": 13,
    "dave": 14,
    "erin": 15,
}


class PhantomChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addresses = {name: address_from_private_key(key) for name, key in PRIVATE_KEYS.items()}
        self.state = StateDB()
        for address in self.addresses.values():
            self.state.set_balance(address, 1_000)
        self.dht = InMemoryChannelDHT()
        self.manager = ChannelManager(dht=self.dht, max_route_length=5, base_timelock=8, timelock_delta=3)
        for key in PRIVATE_KEYS.values():
            self.manager.register_signing_key(key)

    def _open_channel(
        self,
        left: str,
        right: str,
        *,
        left_amount: int,
        right_amount: int,
        current_block: int = 1,
        dispute_window: int = 3,
        fee_base: int = 0,
        fee_ppm: int = 0,
    ):
        return self.manager.open_channel(
            participants=(self.addresses[left], self.addresses[right]),
            deposits={self.addresses[left]: left_amount, self.addresses[right]: right_amount},
            dispute_window=dispute_window,
            chain_state=self.state,
            current_block=current_block,
            fee_base=fee_base,
            fee_ppm=fee_ppm,
        )

    def test_open_update_and_cooperative_close(self) -> None:
        channel = self._open_channel("alice", "bob", left_amount=50, right_amount=50)
        updated = self.manager.build_signed_state(
            channel.channel_id,
            balances={self.addresses["alice"]: 35, self.addresses["bob"]: 65},
        )
        self.manager.update_state(channel.channel_id, updated)
        closed = self.manager.close_channel(channel.channel_id, updated, chain_state=self.state, current_block=4)
        self.assertEqual(closed.status.value, "CLOSED")
        self.assertEqual(self.state.get_balance(self.addresses["alice"]), 985)
        self.assertEqual(self.state.get_balance(self.addresses["bob"]), 1_015)

    def test_invalid_signatures_and_invalid_balances_are_rejected(self) -> None:
        channel = self._open_channel("alice", "bob", left_amount=40, right_amount=40)
        bad_state = ChannelState(
            channel_id=channel.channel_id,
            nonce=1,
            balances={self.addresses["alice"]: 20, self.addresses["bob"]: 60},
        )
        bad_state = sign_channel_state(bad_state, PRIVATE_KEYS["alice"])
        with self.assertRaises(SignatureValidationError):
            self.manager.update_state(channel.channel_id, bad_state)

        bad_totals = ChannelState(
            channel_id=channel.channel_id,
            nonce=1,
            balances={self.addresses["alice"]: 30, self.addresses["bob"]: 60},
        )
        bad_totals = sign_channel_state(sign_channel_state(bad_totals, PRIVATE_KEYS["alice"]), PRIVATE_KEYS["bob"])
        with self.assertRaises(ChannelValidationError):
            self.manager.update_state(channel.channel_id, bad_totals)

    def test_routing_selects_valid_path_and_rejects_no_route(self) -> None:
        self._open_channel("alice", "bob", left_amount=30, right_amount=30, fee_base=8)
        self._open_channel("bob", "dave", left_amount=30, right_amount=30, fee_base=8)
        cheap_ab = self._open_channel("alice", "carol", left_amount=40, right_amount=40, fee_base=1)
        cheap_cd = self._open_channel("carol", "dave", left_amount=40, right_amount=40, fee_base=1)

        route = self.manager.find_route(self.addresses["alice"], self.addresses["dave"], 10)
        self.assertEqual(tuple(hop.channel_id for hop in route.hops), (cheap_ab.channel_id, cheap_cd.channel_id))

        for channel in self.manager.list_channels():
            channel.status = channel.status.CLOSED
        with self.assertRaises(RouteNotFoundError):
            self.manager.find_route(self.addresses["alice"], self.addresses["dave"], 10)

    def test_insufficient_liquidity_rejects_route(self) -> None:
        self._open_channel("alice", "bob", left_amount=4, right_amount=4)
        self._open_channel("bob", "carol", left_amount=4, right_amount=4)
        with self.assertRaises(RouteNotFoundError):
            self.manager.find_route(self.addresses["alice"], self.addresses["carol"], 10)

    def test_successful_multi_hop_payment_redeems_atomically(self) -> None:
        channel_ab = self._open_channel("alice", "bob", left_amount=40, right_amount=10, fee_base=2)
        channel_bc = self._open_channel("bob", "carol", left_amount=30, right_amount=10, fee_base=1)
        channel_cd = self._open_channel("carol", "dave", left_amount=20, right_amount=10, fee_base=0)

        payment, secret = self.manager.initiate_payment(
            self.addresses["alice"],
            self.addresses["dave"],
            10,
            current_block=5,
        )
        self.assertEqual(payment.status, PaymentStatus.PENDING)
        self.assertTrue(channel_ab.active_htlcs)
        self.assertTrue(channel_bc.active_htlcs)
        self.assertTrue(channel_cd.active_htlcs)

        redeemed = self.manager.redeem_payment(secret, current_block=6)
        self.assertEqual(redeemed.status, PaymentStatus.COMPLETED)
        self.assertEqual(channel_cd.balances[self.addresses["dave"]], 20)
        self.assertEqual(channel_ab.balances[self.addresses["alice"]], 27)
        self.assertEqual(channel_ab.balances[self.addresses["bob"]], 23)
        self.assertEqual(channel_bc.balances[self.addresses["bob"]], 19)
        self.assertEqual(channel_bc.balances[self.addresses["carol"]], 21)

    def test_timeout_refunds_without_partial_execution(self) -> None:
        channel_ab = self._open_channel("alice", "bob", left_amount=30, right_amount=10)
        channel_bc = self._open_channel("bob", "carol", left_amount=20, right_amount=10)
        payment, _ = self.manager.initiate_payment(
            self.addresses["alice"],
            self.addresses["carol"],
            10,
            current_block=3,
        )
        refund_block = payment.route.hops[-1].timelock + 1
        refunded = self.manager.refund_payment(payment.payment_id, current_block=refund_block)
        self.assertEqual(refunded.status, PaymentStatus.REFUNDED)
        self.assertEqual(channel_ab.balances[self.addresses["alice"]], 30)
        self.assertEqual(channel_bc.balances[self.addresses["bob"]], 20)
        self.assertFalse(channel_ab.active_htlcs)
        self.assertFalse(channel_bc.active_htlcs)

    def test_replay_attacks_and_old_state_submission_are_rejected(self) -> None:
        channel = self._open_channel("alice", "bob", left_amount=50, right_amount=50)
        state_one = self.manager.build_signed_state(
            channel.channel_id,
            balances={self.addresses["alice"]: 45, self.addresses["bob"]: 55},
        )
        self.manager.update_state(channel.channel_id, state_one)
        with self.assertRaises(ReplayAttackError):
            self.manager.update_state(channel.channel_id, state_one)

        newer = self.manager.build_signed_state(
            channel.channel_id,
            balances={self.addresses["alice"]: 40, self.addresses["bob"]: 60},
        )
        self.manager.challenge_close(channel.channel_id, newer, current_block=7)
        with self.assertRaises(ReplayAttackError):
            self.manager.submit_newer_state(channel.channel_id, state_one, current_block=8)

    def test_partial_route_failure_rolls_back_all_channel_locks(self) -> None:
        channel_ab = self._open_channel("alice", "bob", left_amount=35, right_amount=5)
        channel_bc = self._open_channel("bob", "carol", left_amount=20, right_amount=5)
        del self.manager._signing_keys[self.addresses["bob"].to_hex()]
        with self.assertRaises(SignatureValidationError):
            self.manager.initiate_payment(
                self.addresses["alice"],
                self.addresses["carol"],
                10,
                current_block=4,
            )
        self.assertFalse(channel_ab.active_htlcs)
        self.assertFalse(channel_bc.active_htlcs)

    def test_malicious_intermediary_cannot_redeem_with_wrong_secret(self) -> None:
        self._open_channel("alice", "bob", left_amount=35, right_amount=5)
        self._open_channel("bob", "carol", left_amount=20, right_amount=5)
        payment, _ = self.manager.initiate_payment(
            self.addresses["alice"],
            self.addresses["carol"],
            10,
            current_block=4,
        )
        with self.assertRaises(HTLCError):
            self.manager.redeem_payment(b"wrong-secret".ljust(32, b"\x00"), current_block=5)
        self.assertEqual(self.manager.list_payments()[0].status, PaymentStatus.PENDING)
        refunded = self.manager.refund_payment(payment.payment_id, current_block=payment.route.hops[-1].timelock + 1)
        self.assertEqual(refunded.status, PaymentStatus.REFUNDED)

    def test_dht_tracks_open_channels(self) -> None:
        channel = self._open_channel("alice", "bob", left_amount=25, right_amount=25)
        announcement = self.dht.get_channel(channel.channel_id)
        assert announcement is not None
        self.assertEqual(announcement.capacity, 50)
        self.assertEqual(len(self.dht.peers()), len(PRIVATE_KEYS))

    def test_settlement_chain_mempool_and_dispute_flow(self) -> None:
        manager = ChannelManager(base_timelock=6, timelock_delta=2)
        for key in PRIVATE_KEYS.values():
            manager.register_signing_key(key)
        chain_state = StateDB()
        chain_state.set_balance(self.addresses["alice"], 100)
        chain_state.set_balance(self.addresses["bob"], 0)
        chain_state.set_balance(self.addresses["carol"], 100)
        chain = PhantomSettlementChain(manager, state=chain_state)

        open_tx = sign_settlement_transaction(
            SettlementTransaction(
                sender=self.addresses["alice"],
                nonce=0,
                operation=OpenChannelOperation(
                    participants=(self.addresses["alice"], self.addresses["bob"]),
                    deposits={self.addresses["alice"]: 40, self.addresses["bob"]: 0},
                    dispute_window=1,
                    channel_id_hint="channel-ab",
                ),
            ),
            PRIVATE_KEYS["alice"],
        )
        chain.submit(open_tx)
        block_one = chain.mine_pending_block()
        assert block_one is not None
        self.assertEqual(block_one.block.header.number, 1)
        channel = manager.get_channel("channel-ab")

        disputed_state = manager.build_signed_state(
            channel.channel_id,
            balances={self.addresses["alice"]: 30, self.addresses["bob"]: 10},
        )
        dispute_tx = sign_settlement_transaction(
            SettlementTransaction(
                sender=self.addresses["bob"],
                nonce=0,
                operation=ChallengeChannelOperation(channel_id=channel.channel_id, state=disputed_state),
            ),
            PRIVATE_KEYS["bob"],
        )
        chain.submit(dispute_tx)
        block_two = chain.mine_pending_block()
        assert block_two is not None
        self.assertEqual(manager.get_channel(channel.channel_id).status.value, "CLOSING")

        filler_tx = sign_settlement_transaction(
            SettlementTransaction(
                sender=self.addresses["carol"],
                nonce=0,
                operation=OpenChannelOperation(
                    participants=(self.addresses["carol"], self.addresses["erin"]),
                    deposits={self.addresses["carol"]: 10, self.addresses["erin"]: 0},
                    dispute_window=1,
                    channel_id_hint="channel-ce",
                ),
            ),
            PRIVATE_KEYS["carol"],
        )
        chain.submit(filler_tx)
        self.assertIsNotNone(chain.mine_pending_block())

        finalize_tx = sign_settlement_transaction(
            SettlementTransaction(
                sender=self.addresses["alice"],
                nonce=1,
                operation=CloseChannelOperation(channel_id=channel.channel_id, final_state=disputed_state),
            ),
            PRIVATE_KEYS["alice"],
        )
        with self.assertRaises(MempoolError):
            chain.submit(
                SettlementTransaction(
                    sender=self.addresses["alice"],
                    nonce=1,
                    operation=OpenChannelOperation(
                        participants=(self.addresses["alice"], self.addresses["erin"]),
                        deposits={self.addresses["alice"]: 1, self.addresses["erin"]: 0},
                        dispute_window=1,
                    ),
                )
            )

        finalize_real = sign_settlement_transaction(
            SettlementTransaction(
                sender=self.addresses["alice"],
                nonce=1,
                operation=FinalizeChannelOperation(channel_id=channel.channel_id),
            ),
            PRIVATE_KEYS["alice"],
        )
        chain.submit(finalize_real)
        block_four = chain.mine_pending_block()
        assert block_four is not None
        self.assertEqual(manager.get_channel(channel.channel_id).status.value, "CLOSED")
        self.assertEqual(chain.head.post_state.get_balance(self.addresses["bob"]), 10)


if __name__ == "__main__":
    unittest.main()
