from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from evm import StateDB
from primitives import Address, U256

from ...block import Block, BlockHeader, ChainConfig
from ...block_executor import apply_block as execute_block
from ...result import BlockExecutionResult
from ...trie import compute_state_root
from ..exceptions import StateReconstructionError
from ..models import AccountState, MerkleProof
from ..persistence.metadata_db import MetadataDB


class StateStore:
    """Persistent execution-state wrapper with current-state and historical snapshots."""

    def __init__(self, base_directory: Path | str, metadata_db: MetadataDB) -> None:
        self._base_directory = Path(base_directory)
        self._metadata_db = metadata_db
        self._state_directory = self._base_directory / "state"
        self._history_directory = self._state_directory / "history"
        self._current_path = self._state_directory / "current.json"
        self._fragments_path = self._state_directory / "light_fragments.json"
        self._state_directory.mkdir(parents=True, exist_ok=True)
        self._history_directory.mkdir(parents=True, exist_ok=True)
        self._state = StateDB()
        if self._current_path.exists():
            self.load_current()

    @property
    def state(self) -> StateDB:
        return self._state

    def clone_state(self) -> StateDB:
        return self._state.clone()

    def current_root(self) -> str:
        return compute_state_root(self._state).to_hex()

    def export_accounts(self, state: StateDB | None = None) -> tuple[AccountState, ...]:
        source = self._state if state is None else state
        accounts: list[AccountState] = []
        for address, account in source.accounts():
            accounts.append(
                AccountState(
                    address=address,
                    nonce=account.nonce,
                    balance=account.balance,
                    code=account.code,
                    storage=tuple((U256(slot), U256(value)) for slot, value in account.storage.items()),
                )
            )
        return tuple(sorted(accounts, key=lambda item: item.address.to_bytes()))

    def import_accounts(self, accounts: Iterable[AccountState]) -> None:
        state = StateDB()
        for account in accounts:
            target = state.get_or_create_account(account.address)
            target.nonce = account.nonce
            target.balance = account.balance
            target.code = account.code
            for slot, value in account.storage:
                target.storage.set(int(slot), int(value))
            target.storage.commit()
        self._state = state
        self.save_current()

    def save_current(self) -> None:
        payload = {
            "state_root": self.current_root(),
            "accounts": [account.to_dict() for account in self.export_accounts()],
        }
        self._current_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def load_current(self) -> None:
        payload = json.loads(self._current_path.read_text(encoding="utf-8"))
        self.import_accounts(AccountState.from_dict(item) for item in payload.get("accounts", ()))

    def ensure_genesis(self, accounts: Iterable[AccountState], *, block_hash: str) -> None:
        if self._current_path.exists():
            return
        self.import_accounts(accounts)
        self.record_history(0, block_hash=block_hash, pruning_enabled=False, retention=1_000_000)

    def get_account(self, address: Address) -> AccountState | None:
        account = self._state.get_account(address)
        if account is None:
            return None
        return AccountState(
            address=address,
            nonce=account.nonce,
            balance=account.balance,
            code=account.code,
            storage=tuple((U256(slot), U256(value)) for slot, value in account.storage.items()),
        )

    def get_storage(self, address: Address, slot: U256 | int) -> U256:
        return U256(self._state.get_storage(address, int(slot)))

    def set_balance(self, address: Address, value: int) -> None:
        self._state.set_balance(address, value)
        self.save_current()

    def set_storage(self, address: Address, slot: U256 | int, value: U256 | int) -> None:
        self._state.set_storage(address, int(slot), int(value))
        self._state.get_or_create_account(address).storage.commit()
        self.save_current()

    def apply_block(
        self,
        block: Block,
        *,
        chain_config: ChainConfig,
        parent_header: BlockHeader | None,
    ) -> BlockExecutionResult:
        result = execute_block(self._state, block, chain_config, parent_header=parent_header)
        self._state = result.state
        self.save_current()
        return result

    def record_history(self, height: int, *, block_hash: str, pruning_enabled: bool, retention: int) -> None:
        payload = {
            "height": int(height),
            "block_hash": block_hash,
            "state_root": self.current_root(),
            "accounts": [account.to_dict() for account in self.export_accounts()],
        }
        path = self._history_directory / f"{int(height):08d}_{block_hash[2:10]}.json"
        path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        self._metadata_db.put_json(
            "state_history",
            str(int(height)),
            {"block_hash": block_hash, "path": str(path), "state_root": payload["state_root"]},
        )
        if pruning_enabled:
            self.prune_history(keep_last=retention)

    def prune_history(self, *, keep_last: int) -> None:
        retained_from = max(0, self.latest_history_height() - int(keep_last) + 1)
        history_entries = self._metadata_db.list_namespace("state_history")
        for key, payload in history_entries.items():
            height = int(key)
            if height >= retained_from or height == 0:
                continue
            path = Path(str(payload["path"]))
            if path.exists():
                path.unlink()
            self._metadata_db.delete("state_history", key)

    def latest_history_height(self) -> int:
        entries = self._metadata_db.list_namespace("state_history")
        return max((int(key) for key in entries), default=0)

    def load_history_accounts(self, height: int) -> tuple[AccountState, ...]:
        metadata = self._metadata_db.get_json("state_history", str(int(height)))
        if metadata is None:
            raise StateReconstructionError(f"no stored state history for height {height}")
        payload = json.loads(Path(str(metadata["path"])).read_text(encoding="utf-8"))
        return tuple(AccountState.from_dict(item) for item in payload.get("accounts", ()))

    def restore_height(self, height: int) -> None:
        accounts = self.load_history_accounts(height)
        self.import_accounts(accounts)

    def cache_account_fragment(self, block_number: int, proof: MerkleProof) -> None:
        payload = self._load_fragment_payload()
        payload.setdefault("accounts", {})[f"{block_number}:{proof.key.lower()}"] = proof.to_dict()
        self._fragments_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def cache_storage_fragment(self, block_number: int, proof: MerkleProof) -> None:
        payload = self._load_fragment_payload()
        payload.setdefault("storage", {})[f"{block_number}:{proof.key.lower()}"] = proof.to_dict()
        self._fragments_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def get_cached_account_fragment(self, block_number: int, address: Address) -> MerkleProof | None:
        payload = self._load_fragment_payload()
        proof_payload = payload.get("accounts", {}).get(f"{block_number}:{address.to_hex().lower()}")
        return None if proof_payload is None else MerkleProof.from_dict(proof_payload)

    def _load_fragment_payload(self) -> dict[str, object]:
        if not self._fragments_path.exists():
            return {"accounts": {}, "storage": {}}
        return json.loads(self._fragments_path.read_text(encoding="utf-8"))
