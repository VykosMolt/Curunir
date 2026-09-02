"""Re-verify stored action signatures from the log alone."""
from __future__ import annotations

from typing import Any

from curunir_operational.canonical import sha256

from .crypto import verify
from .registry import KeyRegistry, VALID


GENUINE = "GENUINE"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
SIGNATURE_INVALID = "SIGNATURE_INVALID"
KEY_UNKNOWN = "KEY_UNKNOWN"
KEY_NOT_VALID_AT_TIME = "KEY_NOT_VALID_AT_TIME"


def verify_signed_action(registry: KeyRegistry, record: dict[str, Any]) -> str:
    payload = record.get("signed_payload")
    if not isinstance(payload, dict) \
            or sha256(payload) != record.get("payload_digest"):
        return DIGEST_MISMATCH
    public_key = registry.public_key_of(record.get("key_id", ""))
    if public_key is None:
        return KEY_UNKNOWN
    if not verify(public_key, record.get("signature", ""), payload):
        return SIGNATURE_INVALID
    try:
        status = registry.verification_status_at(
            record["key_id"], record["timestamp"])
    except (KeyError, AttributeError, TypeError, ValueError):
        return KEY_NOT_VALID_AT_TIME
    return GENUINE if status == VALID else KEY_NOT_VALID_AT_TIME


def verify_all(store) -> dict[str, Any]:
    registry = KeyRegistry(store)
    verdicts = []
    for record in store.records_of("signed_action"):
        verdicts.append({
            "action_id": record["action_id"],
            "actor_id": record["actor_id"],
            "action_type": record["action_type"],
            "verdict": verify_signed_action(registry, record),
        })
    return {
        "all_genuine": all(item["verdict"] == GENUINE for item in verdicts),
        "count": len(verdicts),
        "verdicts": verdicts,
    }
