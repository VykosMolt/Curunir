"""Replay verification of signed actions (§15, §83).

Historical attribution is not trusted merely because it is in the log. Replay
re-verifies each recorded signed action's Ed25519 signature against the key that
was valid when it was signed, and classifies the outcome — so a tampered
payload, a signature by a key that was already revoked, or an unknown key are
all distinguished from a genuine authenticated act. Key rotation never
invalidates a legitimate historical signature: verification uses the key's
validity window, not its current status.
"""
from __future__ import annotations

from typing import Any

from curunir_operational.canonical import sha256

from .crypto import verify
from .registry import (COMPROMISED, NOT_YET_ENROLLED, REVOKED, RETIRED, UNKNOWN,
                       VALID, KeyRegistry)

# per-action outcomes
GENUINE = "GENUINE"                       # valid signature by a key valid at signing
DIGEST_MISMATCH = "DIGEST_MISMATCH"       # stored payload does not match its digest
SIGNATURE_INVALID = "SIGNATURE_INVALID"   # signature does not verify (tampered)
KEY_UNKNOWN = "KEY_UNKNOWN"               # no such key in the registry
KEY_NOT_VALID_AT_TIME = "KEY_NOT_VALID_AT_TIME"  # revoked/retired/compromised then


def verify_signed_action(registry: KeyRegistry, record: dict[str, Any]) -> str:
    """Classify one recorded signed action. GENUINE only when the stored
    payload matches its digest, the signature verifies under the key's public
    key, and the key was VALID at the action's timestamp."""
    payload = record.get("signed_payload") or {}
    if sha256(dict(payload)) != record.get("payload_digest"):
        return DIGEST_MISMATCH
    public_key = registry.public_key_of(record["key_id"])
    if public_key is None:
        return KEY_UNKNOWN
    if not verify(public_key, record["signature"], payload):
        return SIGNATURE_INVALID
    status = registry.verification_status_at(record["key_id"], record["timestamp"])
    if status == VALID:
        return GENUINE
    return KEY_NOT_VALID_AT_TIME


def verify_all(store) -> dict[str, Any]:
    """Re-verify every signed action in the log. Returns the per-action
    verdicts and whether all are GENUINE."""
    registry = KeyRegistry(store)
    verdicts = []
    for record in store.records_of("signed_action"):
        verdicts.append({"action_id": record["action_id"],
                         "actor_id": record["actor_id"],
                         "action_type": record["action_type"],
                         "verdict": verify_signed_action(registry, record)})
    return {"all_genuine": all(v["verdict"] == GENUINE for v in verdicts),
            "count": len(verdicts), "verdicts": verdicts}
