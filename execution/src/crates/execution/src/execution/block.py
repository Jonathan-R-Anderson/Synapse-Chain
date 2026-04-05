from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from primitives import Address, Hash
from transactions.validation import ZKVerificationTiming
from zk import ZKGasModel

from .base_fee import (
    DEFAULT_BASE_FEE_MAX_CHANGE_DENOMINATOR,
    DEFAULT_ELASTICITY_MULTIPLIER,
    DEFAULT_GAS_LIMIT_BOUND_DIVISOR,
    DEFAULT_INITIAL_BASE_FEE,
)
from .block_header import BlockHeader
from .hashing import bytes_to_hex, hex_to_bytes
from .receipt import Receipt
from .rlp_codec import RlpDecodingError, rlp_decode, rlp_encode
from .transaction import Transaction, decode_transaction_payload, encode_transaction, transaction_from_dict, transaction_to_dict

if TYPE_CHECKING:
    from .dht_hooks import BlockDistributionRecord
    from .result import ExecutionPayload
    from .zk_hooks import BlockProofBundle


class FeeModel(str, Enum):
    LEGACY = "legacy"
    EIP1559 = "eip1559"


@dataclass(frozen=True, slots=True)
class ChainConfig:
    chain_id: int = 1
    fee_model: FeeModel = FeeModel.EIP1559
    support_legacy_transactions: bool = True
    support_eip1559_transactions: bool = True
    support_zk_transactions: bool = True
    allow_unprotected_legacy_transactions: bool = True
    enforce_low_s: bool = True
    gas_refund_quotient: int = 5
    burn_base_fee: bool = True
    zk_verification_timing: ZKVerificationTiming = ZKVerificationTiming.PRE_EXECUTION
    zk_gas_model: ZKGasModel = field(default_factory=ZKGasModel)
    elasticity_multiplier: int = DEFAULT_ELASTICITY_MULTIPLIER
    base_fee_max_change_denominator: int = DEFAULT_BASE_FEE_MAX_CHANGE_DENOMINATOR
    gas_limit_bound_divisor: int = DEFAULT_GAS_LIMIT_BOUND_DIVISOR
    initial_base_fee_per_gas: int = DEFAULT_INITIAL_BASE_FEE
    max_extra_data_bytes: int = 32

    def __post_init__(self) -> None:
        if self.chain_id < 1:
            raise ValueError("chain_id must be positive")
        if self.gas_refund_quotient < 1:
            raise ValueError("gas_refund_quotient must be positive")
        if self.elasticity_multiplier < 1:
            raise ValueError("elasticity_multiplier must be positive")
        if self.base_fee_max_change_denominator < 1:
            raise ValueError("base_fee_max_change_denominator must be positive")
        if self.gas_limit_bound_divisor < 1:
            raise ValueError("gas_limit_bound_divisor must be positive")
        if self.initial_base_fee_per_gas < 0:
            raise ValueError("initial_base_fee_per_gas must be non-negative")
        if self.max_extra_data_bytes < 0:
            raise ValueError("max_extra_data_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class Block:
    header: BlockHeader
    transactions: tuple[Transaction, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    ommers: tuple[BlockHeader, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "transactions", tuple(self.transactions))
        object.__setattr__(self, "receipts", tuple(self.receipts))
        object.__setattr__(self, "ommers", tuple(self.ommers))

    def hash(self) -> Hash:
        return self.header.hash()

    def validate_structure(
        self,
        parent_block: Block | BlockHeader | None = None,
        *,
        execution_payload: "ExecutionPayload | None" = None,
        chain_config: ChainConfig | None = None,
    ) -> None:
        from .block_validator import BlockValidator

        BlockValidator(chain_config=chain_config or ChainConfig()).validate_block_structure(
            self,
            parent_block=parent_block,
            execution_payload=execution_payload,
        )

    def serialize(self) -> bytes:
        """Serialize the block into a deterministic internal container.

        This is not the canonical Ethereum block-body wire format because it
        intentionally includes receipts to support local block construction and
        validation workflows.
        """

        return rlp_encode(
            [
                self.header.rlp_encode(),
                [encode_transaction(transaction) for transaction in self.transactions],
                [receipt.serialize() for receipt in self.receipts],
                [ommer.rlp_encode() for ommer in self.ommers],
            ]
        )

    @classmethod
    def deserialize(cls, payload: bytes | bytearray | memoryview) -> "Block":
        try:
            decoded = rlp_decode(bytes(payload))
        except RlpDecodingError as exc:
            raise ValueError("invalid block serialization") from exc
        if not isinstance(decoded, list) or len(decoded) != 4:
            raise ValueError("block payload must decode to [header, txs, receipts, ommers]")
        header_raw, transactions_raw, receipts_raw, ommers_raw = decoded
        if not isinstance(header_raw, bytes):
            raise ValueError("serialized block header must be raw bytes")
        if not isinstance(transactions_raw, list) or not isinstance(receipts_raw, list) or not isinstance(ommers_raw, list):
            raise ValueError("serialized block lists are malformed")
        return cls(
            header=BlockHeader.rlp_decode(header_raw),
            transactions=tuple(decode_transaction_payload(raw) for raw in transactions_raw),
            receipts=tuple(Receipt.deserialize(raw) for raw in receipts_raw),
            ommers=tuple(BlockHeader.rlp_decode(raw) for raw in ommers_raw),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "header": self.header.to_dict(),
            "hash": self.hash().to_hex(),
            "transactions": [transaction_to_dict(transaction) for transaction in self.transactions],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "ommers": [ommer.to_dict() for ommer in self.ommers],
            "serialized": bytes_to_hex(self.serialize()),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Block":
        header_data = data.get("header")
        if not isinstance(header_data, dict):
            raise ValueError("block.header must be a dictionary")
        return cls(
            header=BlockHeader.from_dict(header_data),
            transactions=tuple(transaction_from_dict(item) for item in data.get("transactions", [])),
            receipts=tuple(Receipt.from_dict(item) for item in data.get("receipts", [])),
            ommers=tuple(BlockHeader.from_dict(item) for item in data.get("ommers", [])),
        )


@dataclass(frozen=True, slots=True)
class BlockEnvironment:
    block_number: int
    timestamp: int
    gas_limit: int
    coinbase: Address
    base_fee: int | None
    chain_id: int
    prev_randao: Hash | None = None

    @classmethod
    def from_block(cls, block: Block, chain_config: ChainConfig) -> "BlockEnvironment":
        return cls(
            block_number=block.header.number,
            timestamp=block.header.timestamp,
            gas_limit=block.header.gas_limit,
            coinbase=block.header.coinbase,
            base_fee=block.header.base_fee,
            chain_id=chain_config.chain_id,
            prev_randao=block.header.mix_hash,
        )


@dataclass(frozen=True, slots=True)
class ExtendedBlock:
    block: Block
    zk_proof_bundle: "BlockProofBundle | None" = None
    dht_metadata: "BlockDistributionRecord | None" = None
    execution_witness: bytes | None = None

    def __post_init__(self) -> None:
        if self.execution_witness is not None:
            object.__setattr__(self, "execution_witness", bytes(self.execution_witness))

    def hash(self) -> Hash:
        return self.block.hash()

    def to_dict(self) -> dict[str, object]:
        return {
            "block": self.block.to_dict(),
            "zk_proof_bundle": None if self.zk_proof_bundle is None else self.zk_proof_bundle.to_dict(),
            "dht_metadata": None if self.dht_metadata is None else self.dht_metadata.to_dict(),
            "execution_witness": None if self.execution_witness is None else bytes_to_hex(self.execution_witness),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ExtendedBlock":
        from .dht_hooks import BlockDistributionRecord
        from .zk_hooks import BlockProofBundle

        block_data = data.get("block")
        if not isinstance(block_data, dict):
            raise ValueError("extended block requires a block payload")
        zk_payload = data.get("zk_proof_bundle")
        dht_payload = data.get("dht_metadata")
        witness = data.get("execution_witness")
        return cls(
            block=Block.from_dict(block_data),
            zk_proof_bundle=None if zk_payload is None else BlockProofBundle.from_dict(zk_payload),
            dht_metadata=None if dht_payload is None else BlockDistributionRecord.from_dict(dht_payload),
            execution_witness=None if witness is None else hex_to_bytes(str(witness), label="execution_witness"),
        )
