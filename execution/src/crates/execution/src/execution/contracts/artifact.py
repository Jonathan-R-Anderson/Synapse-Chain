from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def _normalize_hex_bytecode(value: str, *, label: str) -> bytes:
    normalized = value[2:] if value.startswith(("0x", "0X")) else value
    if not normalized:
        return b""
    if any(character == "_" for character in normalized):
        raise ValueError(f"{label} contains unresolved link placeholders")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} is not valid hex bytecode") from exc


def _extract_bytecode(payload: dict[str, Any]) -> bytes | None:
    direct = payload.get("bytecode")
    if isinstance(direct, str):
        return _normalize_hex_bytecode(direct, label="artifact.bytecode")
    if isinstance(direct, dict) and isinstance(direct.get("object"), str):
        return _normalize_hex_bytecode(str(direct["object"]), label="artifact.bytecode.object")

    evm = payload.get("evm")
    if isinstance(evm, dict):
        evm_bytecode = evm.get("bytecode")
        if isinstance(evm_bytecode, dict) and isinstance(evm_bytecode.get("object"), str):
            return _normalize_hex_bytecode(str(evm_bytecode["object"]), label="artifact.evm.bytecode.object")
    return None


@dataclass(frozen=True, slots=True)
class ContractArtifact:
    contract_name: str
    abi: tuple[dict[str, Any], ...]
    bytecode: bytes
    source_path: Path | None = None

    def with_abi(self, abi: Sequence[dict[str, Any]]) -> "ContractArtifact":
        return ContractArtifact(
            contract_name=self.contract_name,
            abi=_normalize_abi_entries(abi, label="contract ABI"),
            bytecode=self.bytecode,
            source_path=self.source_path,
        )

    def constructor_inputs(self) -> tuple[dict[str, Any], ...]:
        for entry in self.abi:
            if entry.get("type") == "constructor":
                inputs = entry.get("inputs", [])
                if not isinstance(inputs, list):
                    raise ValueError("constructor inputs must be a list")
                return tuple(dict(item) for item in inputs if isinstance(item, dict))
        return ()


def _iter_standard_json_contracts(payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    contracts = payload.get("contracts")
    if not isinstance(contracts, dict):
        return ()
    for source_name, source_contracts in contracts.items():
        if not isinstance(source_contracts, dict):
            continue
        for contract_name, contract_payload in source_contracts.items():
            if not isinstance(contract_payload, dict):
                continue
            yield f"{source_name}:{contract_name}", contract_payload


def _normalize_abi_entries(payload: object, *, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be a JSON array")
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{index}] must be a JSON object")
        entries.append(dict(item))
    return tuple(entries)


def _select_contract_candidate(
    artifact_path: Path,
    candidates: list[tuple[str, dict[str, Any]]],
    *,
    contract_name: str | None,
    expected: str,
) -> tuple[str, dict[str, Any]]:
    if contract_name is not None:
        matching = [(name, item) for name, item in candidates if name == contract_name or name.endswith(f":{contract_name}")]
        if not matching:
            raise ValueError(f"contract {contract_name!r} was not found in {artifact_path}")
        return matching[0]

    if not candidates:
        raise ValueError(f"{artifact_path} does not contain {expected}")
    if len(candidates) > 1:
        names = ", ".join(name for name, _ in candidates)
        raise ValueError(f"{artifact_path} contains multiple contracts; choose one with --contract-name ({names})")
    return candidates[0]


def load_contract_artifact(path: str | Path, *, contract_name: str | None = None) -> ContractArtifact:
    artifact_path = Path(path)
    suffix = artifact_path.suffix.lower()

    if suffix in {".bin", ".hex"}:
        bytecode = _normalize_hex_bytecode(artifact_path.read_text(encoding="utf-8").strip(), label=str(artifact_path))
        return ContractArtifact(
            contract_name=contract_name or artifact_path.stem,
            abi=(),
            bytecode=bytecode,
            source_path=artifact_path,
        )

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("contract artifact must decode to a JSON object")

    candidates: list[tuple[str, dict[str, Any]]] = []

    direct_bytecode = _extract_bytecode(payload)
    if direct_bytecode is not None:
        direct_name = str(payload.get("contractName") or payload.get("contract_name") or artifact_path.stem)
        candidates.append((direct_name, payload))

    candidates.extend(_iter_standard_json_contracts(payload))
    chosen_name, chosen_payload = _select_contract_candidate(
        artifact_path,
        candidates,
        contract_name=contract_name,
        expected="deployable contract bytecode",
    )

    abi_payload = _normalize_abi_entries(chosen_payload.get("abi", []), label="contract ABI")
    bytecode = _extract_bytecode(chosen_payload)
    if bytecode is None or not bytecode:
        raise ValueError("chosen contract does not contain deployable bytecode")

    return ContractArtifact(
        contract_name=chosen_name.split(":")[-1],
        abi=abi_payload,
        bytecode=bytecode,
        source_path=artifact_path,
    )


def load_contract_abi(path: str | Path, *, contract_name: str | None = None) -> tuple[dict[str, Any], ...]:
    artifact_path = Path(path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    if isinstance(payload, list):
        return _normalize_abi_entries(payload, label="contract ABI")
    if not isinstance(payload, dict):
        raise ValueError("contract ABI must decode to a JSON object or JSON array")

    direct_abi = payload.get("abi")
    if direct_abi is not None:
        direct_name = str(payload.get("contractName") or payload.get("contract_name") or artifact_path.stem)
        if contract_name is None or direct_name == contract_name or direct_name.endswith(f":{contract_name}"):
            return _normalize_abi_entries(direct_abi, label="contract ABI")

    candidates = [(name, item) for name, item in _iter_standard_json_contracts(payload) if item.get("abi") is not None]
    chosen_name, chosen_payload = _select_contract_candidate(
        artifact_path,
        candidates,
        contract_name=contract_name,
        expected="contract ABI metadata",
    )
    return _normalize_abi_entries(chosen_payload.get("abi"), label=f"{chosen_name} ABI")


__all__ = ["ContractArtifact", "load_contract_abi", "load_contract_artifact"]
