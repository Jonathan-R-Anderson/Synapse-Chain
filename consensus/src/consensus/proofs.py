from __future__ import annotations

from typing import Any

from .types import Validator


def _claim_validity(claim: Any, *, default: bool = True) -> bool:
    if claim is None:
        return default
    if isinstance(claim, bool):
        return claim
    if isinstance(claim, dict) and "valid" in claim:
        return bool(claim["valid"])
    return default


def verify_pow_claim(validator: Validator, claim: Any) -> bool:
    return _claim_validity(claim, default=validator.pow_score > 0)


def verify_storage_proof(validator: Validator, proof: Any) -> bool:
    return _claim_validity(proof, default=validator.storage_committed_bytes > 0)


def verify_retrievability_challenge(validator: Validator, challenge_response: Any) -> bool:
    if isinstance(challenge_response, dict) and "success_rate" in challenge_response:
        return float(challenge_response["success_rate"]) >= 0.5
    return _claim_validity(challenge_response, default=validator.storage_challenge_success_rate >= 0.5)


def verify_network_relay_proof(validator: Validator, proof: Any) -> bool:
    return _claim_validity(proof, default=validator.network_relay_score > 0 and validator.network_uptime_score >= 0.5)


def verify_coverage_proof(validator: Validator, proof: Any) -> bool:
    return _claim_validity(proof, default=validator.network_coverage_score > 0 and validator.network_density_penalty < 0.9)


def verify_useful_compute_claim(validator: Validator, claim: Any) -> bool:
    return _claim_validity(claim, default=validator.useful_compute_score > 0)


def verify_identity_claim(validator: Validator, claim: Any) -> bool:
    return _claim_validity(claim, default=validator.identity_verified)
