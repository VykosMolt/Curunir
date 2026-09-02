"""Records for actor keys and signed actions."""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import ACTOR_KINDS, Marking
from curunir_operational.canonical import require_aware
from curunir_operational.contracts import Record, _member


KEY_STATUSES = ("ACTIVE", "REVOKED", "RETIRED")

IDENTITY_EVENT_TYPES = {
    "ACTOR_KEY_RECORDED": "actor_key",
    "SIGNED_ACTION_RECORDED": "signed_action",
}


@dataclass(frozen=True)
class ActorKeyRecord(Record):
    """One version of an actor's public key."""

    RECORD_TYPE = "actor_key"
    key_id: str
    version: int
    actor_id: str
    actor_kind: str
    public_key: str
    status: str
    enrolled_time: str
    status_time: str
    supersedes_key_id: str
    reason: str
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.actor_kind, ACTOR_KINDS, "actor kind")
        _member(self.status, KEY_STATUSES, "key status")
        if self.version < 1:
            raise ValueError("key versions start at 1")
        if not self.key_id or not self.actor_id:
            raise ValueError("an actor key requires a key id and an actor id")
        try:
            raw = bytes.fromhex(self.public_key)
        except ValueError as exc:
            raise ValueError("actor public key must be hexadecimal") from exc
        if len(raw) != 32:
            raise ValueError("actor public key must be 32 bytes")
        require_aware(self.enrolled_time)
        require_aware(self.status_time)
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class SignedActionRecord(Record):
    """A signature bound to one actor, action, target and mission."""

    RECORD_TYPE = "signed_action"
    action_id: str
    actor_id: str
    actor_kind: str
    key_id: str
    action_type: str
    target_kind: str
    target_id: str
    target_version_token: str
    mission_id: str
    nonce: str
    timestamp: str
    payload_digest: str
    signature: str
    signed_payload: dict
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.actor_kind, ACTOR_KINDS, "actor kind")
        required = (
            self.action_id, self.actor_id, self.key_id, self.action_type,
            self.target_kind, self.target_id, self.target_version_token,
            self.mission_id, self.nonce, self.payload_digest, self.signature,
        )
        if not all(isinstance(value, str) and value for value in required):
            raise ValueError("a signed action requires a complete binding")
        if not isinstance(self.signed_payload, dict):
            raise ValueError("signed_payload must be an object")
        require_aware(self.timestamp)
        require_aware(self.recorded_time)
