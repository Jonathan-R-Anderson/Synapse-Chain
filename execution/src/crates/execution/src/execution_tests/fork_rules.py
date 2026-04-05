from __future__ import annotations

from dataclasses import dataclass

from execution import ChainConfig, FeeModel


MAINNET_EIP155_BLOCK = 2_675_000
MAINNET_BYZANTIUM_BLOCK = 4_370_000
MAINNET_CONSTANTINOPLE_BLOCK = 7_280_000
MAINNET_ISTANBUL_BLOCK = 9_069_000
MAINNET_BERLIN_BLOCK = 12_244_000
MAINNET_LONDON_BLOCK = 12_965_000
MAINNET_PARIS_TRANSITION_BLOCK = 15_537_394
MAINNET_SHANGHAI_TIMESTAMP = 1_681_338_455
MAINNET_CANCUN_TIMESTAMP = 1_710_338_135


@dataclass(frozen=True, slots=True)
class ForkRules:
    name: str
    eip155: bool
    eip2929: bool
    eip2930: bool
    eip1559: bool
    byzantium_receipts: bool
    create2_enabled: bool
    revert_enabled: bool
    staticcall_enabled: bool
    prev_randao_enabled: bool
    access_lists_enabled: bool


def rules_for_name(name: str) -> ForkRules:
    normalized = name.strip().lower()
    if normalized in {"frontier", "homestead"}:
        return ForkRules(
            name="homestead",
            eip155=False,
            eip2929=False,
            eip2930=False,
            eip1559=False,
            byzantium_receipts=False,
            create2_enabled=False,
            revert_enabled=False,
            staticcall_enabled=False,
            prev_randao_enabled=False,
            access_lists_enabled=False,
        )
    if normalized in {"spurious-dragon", "eip155"}:
        return ForkRules(
            name="spurious-dragon",
            eip155=True,
            eip2929=False,
            eip2930=False,
            eip1559=False,
            byzantium_receipts=False,
            create2_enabled=False,
            revert_enabled=False,
            staticcall_enabled=False,
            prev_randao_enabled=False,
            access_lists_enabled=False,
        )
    if normalized in {"byzantium"}:
        return ForkRules(
            name="byzantium",
            eip155=True,
            eip2929=False,
            eip2930=False,
            eip1559=False,
            byzantium_receipts=True,
            create2_enabled=False,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=False,
            access_lists_enabled=False,
        )
    if normalized in {"constantinople", "petersburg"}:
        return ForkRules(
            name="petersburg",
            eip155=True,
            eip2929=False,
            eip2930=False,
            eip1559=False,
            byzantium_receipts=True,
            create2_enabled=True,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=False,
            access_lists_enabled=False,
        )
    if normalized in {"istanbul"}:
        return ForkRules(
            name="istanbul",
            eip155=True,
            eip2929=False,
            eip2930=False,
            eip1559=False,
            byzantium_receipts=True,
            create2_enabled=True,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=False,
            access_lists_enabled=False,
        )
    if normalized in {"berlin"}:
        return ForkRules(
            name="berlin",
            eip155=True,
            eip2929=True,
            eip2930=True,
            eip1559=False,
            byzantium_receipts=True,
            create2_enabled=True,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=False,
            access_lists_enabled=True,
        )
    if normalized in {"london", "gray-glacier", "arrow-glacier"}:
        return ForkRules(
            name="london",
            eip155=True,
            eip2929=True,
            eip2930=True,
            eip1559=True,
            byzantium_receipts=True,
            create2_enabled=True,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=False,
            access_lists_enabled=True,
        )
    if normalized in {"paris", "merge", "shanghai", "cancun"}:
        return ForkRules(
            name=normalized,
            eip155=True,
            eip2929=True,
            eip2930=True,
            eip1559=True,
            byzantium_receipts=True,
            create2_enabled=True,
            revert_enabled=True,
            staticcall_enabled=True,
            prev_randao_enabled=True,
            access_lists_enabled=True,
        )
    raise ValueError(f"unsupported fork name: {name}")


def rules_for_block(
    number: int,
    timestamp: int | None = None,
    *,
    base_fee: int | None = None,
    difficulty: int | None = None,
    prev_randao_present: bool = False,
) -> ForkRules:
    if number >= MAINNET_LONDON_BLOCK or base_fee is not None:
        rules = rules_for_name("london")
    elif number >= MAINNET_BERLIN_BLOCK:
        rules = rules_for_name("berlin")
    elif number >= MAINNET_ISTANBUL_BLOCK:
        rules = rules_for_name("istanbul")
    elif number >= MAINNET_CONSTANTINOPLE_BLOCK:
        rules = rules_for_name("petersburg")
    elif number >= MAINNET_BYZANTIUM_BLOCK:
        rules = rules_for_name("byzantium")
    elif number >= MAINNET_EIP155_BLOCK:
        rules = rules_for_name("spurious-dragon")
    else:
        rules = rules_for_name("homestead")

    prev_randao_enabled = prev_randao_present or (
        number >= MAINNET_PARIS_TRANSITION_BLOCK and difficulty == 0
    )
    name = rules.name
    if timestamp is not None and timestamp >= MAINNET_CANCUN_TIMESTAMP:
        name = "cancun"
    elif timestamp is not None and timestamp >= MAINNET_SHANGHAI_TIMESTAMP:
        name = "shanghai"
    elif prev_randao_enabled:
        name = "paris"

    return ForkRules(
        name=name,
        eip155=rules.eip155,
        eip2929=rules.eip2929,
        eip2930=rules.eip2930,
        eip1559=rules.eip1559,
        byzantium_receipts=rules.byzantium_receipts,
        create2_enabled=rules.create2_enabled,
        revert_enabled=rules.revert_enabled,
        staticcall_enabled=rules.staticcall_enabled,
        prev_randao_enabled=prev_randao_enabled,
        access_lists_enabled=rules.access_lists_enabled,
    )


def chain_config_for_rules(rules: ForkRules, *, chain_id: int) -> ChainConfig:
    return ChainConfig(
        chain_id=chain_id,
        fee_model=FeeModel.EIP1559 if rules.eip1559 else FeeModel.LEGACY,
        support_legacy_transactions=True,
        support_eip1559_transactions=rules.eip1559,
        support_zk_transactions=rules.eip1559,
        allow_unprotected_legacy_transactions=not rules.eip155,
    )
