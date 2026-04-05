from __future__ import annotations

from .block import Block, BlockHeader, ChainConfig
from .block_validator import BlockValidator
from .exceptions import BlockValidationError


def validate_block_for_execution(
    block: Block,
    parent_header: BlockHeader | None,
    chain_config: ChainConfig,
) -> None:
    """Pre-execution structural validation for an incoming block.

    This intentionally validates only the parts that are independent of the
    post-state execution result. Full root/state validation belongs to the
    dedicated block validator once execution artifacts are available.
    """

    validator = BlockValidator(chain_config)
    validator.validate_header(block.header)
    validator.validate_against_parent(block, parent_header)
    validator.validate_base_fee(block, parent_header)
    if any(int(transaction.gas_limit) > block.header.gas_limit for transaction in block.transactions):
        raise BlockValidationError("transaction gas limit exceeds the block gas limit")
