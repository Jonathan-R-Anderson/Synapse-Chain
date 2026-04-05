from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


CRATES = Path(__file__).resolve().parents[2]
for crate in ("execution", "evm", "transactions", "zk", "state", "encoding", "crypto", "primitives"):
    source = CRATES / crate / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from crypto import address_from_private_key
from evm import StateDB
from execution import (
    ChannelManager,
    ChallengeChannelOperation,
    FinalizeChannelOperation,
    OpenChannelOperation,
    PhantomSettlementChain,
    SettlementTransaction,
    sign_settlement_transaction,
)


PRIVATE_KEYS = {
    "alice": 21,
    "bob": 22,
    "carol": 23,
    "dave": 24,
}


def _dump_balances(state: StateDB, addresses: dict[str, object]) -> dict[str, int]:
    return {name: state.get_balance(address) for name, address in addresses.items()}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    addresses = {name: address_from_private_key(key) for name, key in PRIVATE_KEYS.items()}
    manager = ChannelManager()
    for key in PRIVATE_KEYS.values():
        manager.register_signing_key(key)

    state = StateDB()
    for address in addresses.values():
        state.set_balance(address, 500)

    channel_ab = manager.open_channel(
        participants=(addresses["alice"], addresses["bob"]),
        deposits={addresses["alice"]: 80, addresses["bob"]: 20},
        dispute_window=2,
        chain_state=state,
        current_block=1,
        fee_base=2,
    )
    channel_bc = manager.open_channel(
        participants=(addresses["bob"], addresses["carol"]),
        deposits={addresses["bob"]: 60, addresses["carol"]: 20},
        dispute_window=2,
        chain_state=state,
        current_block=1,
        fee_base=1,
    )
    channel_cd = manager.open_channel(
        participants=(addresses["carol"], addresses["dave"]),
        deposits={addresses["carol"]: 40, addresses["dave"]: 20},
        dispute_window=2,
        chain_state=state,
        current_block=1,
    )

    payment, secret = manager.initiate_payment(addresses["alice"], addresses["dave"], 15, current_block=5)
    manager.redeem_payment(secret, current_block=6)

    settlement_state = StateDB()
    settlement_state.set_balance(addresses["alice"], 100)
    settlement_state.set_balance(addresses["bob"], 0)
    chain = PhantomSettlementChain(ChannelManager(), state=settlement_state)
    for key in (PRIVATE_KEYS["alice"], PRIVATE_KEYS["bob"]):
        chain.manager.register_signing_key(key)

    open_tx = sign_settlement_transaction(
        SettlementTransaction(
            sender=addresses["alice"],
            nonce=0,
            operation=OpenChannelOperation(
                participants=(addresses["alice"], addresses["bob"]),
                deposits={addresses["alice"]: 50, addresses["bob"]: 0},
                dispute_window=1,
                channel_id_hint="settlement-demo",
            ),
        ),
        PRIVATE_KEYS["alice"],
    )
    chain.submit(open_tx)
    chain.mine_pending_block()

    disputed = chain.manager.build_signed_state(
        "settlement-demo",
        balances={addresses["alice"]: 35, addresses["bob"]: 15},
    )
    dispute_tx = sign_settlement_transaction(
        SettlementTransaction(
            sender=addresses["bob"],
            nonce=0,
            operation=ChallengeChannelOperation(channel_id="settlement-demo", state=disputed),
        ),
        PRIVATE_KEYS["bob"],
    )
    chain.submit(dispute_tx)
    chain.mine_pending_block()

    filler_tx = sign_settlement_transaction(
        SettlementTransaction(
            sender=addresses["alice"],
            nonce=1,
            operation=OpenChannelOperation(
                participants=(addresses["alice"], addresses["bob"]),
                deposits={addresses["alice"]: 1, addresses["bob"]: 0},
                dispute_window=1,
                channel_id_hint="settlement-filler",
            ),
        ),
        PRIVATE_KEYS["alice"],
    )
    chain.submit(filler_tx)
    chain.mine_pending_block()

    finalize_tx = sign_settlement_transaction(
        SettlementTransaction(
            sender=addresses["alice"],
            nonce=2,
            operation=FinalizeChannelOperation(channel_id="settlement-demo"),
        ),
        PRIVATE_KEYS["alice"],
    )
    chain.submit(finalize_tx)
    chain.mine_pending_block()

    print(
        json.dumps(
            {
                "offchain_channels": {
                    channel_ab.channel_id: channel_ab.balances[addresses["alice"]],
                    channel_bc.channel_id: channel_bc.balances[addresses["bob"]],
                    channel_cd.channel_id: channel_cd.balances[addresses["dave"]],
                },
                "payment_status": payment.status.value,
                "offchain_balances": _dump_balances(state, addresses),
                "settlement_balances": _dump_balances(chain.head.post_state, {"alice": addresses["alice"], "bob": addresses["bob"]}),
                "settlement_head": chain.head.block.header.number,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
