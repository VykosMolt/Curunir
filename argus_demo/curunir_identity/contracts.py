"""Identity records that live in the mission log — the replayable half of the
actor-identity substrate.

Public keys and their lifecycle (enroll / rotate / revoke / retire) are mission
history: a signed action must stay verifiable from the log alone, against the
key that was valid when it was signed. Private keys are NOT here — they are the
actor's secret, held in a keystore outside the mission store. Sessions are NOT
here either — they are ephemeral deployment state (see sessions.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import ACTOR_KINDS, Marking
from curunir_operational.canonical import require_aware, require_aware_or_none
from curunir_operational.contracts import Record, _member

KEY_STATUSES = ("ACTIVE", "REVOKED", "RETIRED")

IDENTITY_EVENT_TYPES = {
    "ACTOR_KEY_RECORDED": "actor_key",
    "SIGNED_ACTION_RECORDED": "signed_action",
}


@dataclass(frozen=True)
class ActorKeyRecord(Record):
    """One version of one enrolled public key. Enrollment is version 1 ACTIVE;
    revocation/retirement append later versions carrying the same key_id, so
    the full lifecycle — and the exact window a key was valid — replays from the
    log. `enrolled_time` never moves across versions; `status_time` is when this
    version's status took effect."""
    RECORD_TYPE = "actor_key"
    key_id: str
    version: int
    actor_id: str
    actor_kind: str
    public_key: str          # 32-byte Ed25519 raw public key, hex
    status: str              # KEY_STATUSES
    enrolled_time: str
    status_time: str
    supersedes_key_id: str   # the key this one rotated in for, or ""
    reason: str
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.actor_kind, ACTOR_KINDS, "actor kind")
        _member(self.status, KEY_STATUSES, "key status")
        if self.version < 1:
            raise ValueError("key versions start at 1")
        if not self.public_key:
            raise ValueError("an actor key requires its public key")
        require_aware(self.enrolled_time)
        require_aware(self.status_time)
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class SignedActionRecord(Record):
    """The immutable attribution of one load-bearing action: who (actor+key),
    what (action over an exact target VERSION in an exact mission), when (time +
    single-use nonce), and the Ed25519 signature over the canonical binding of
    all of it. Replay re-verifies the signature against the key valid at
    `timestamp`. The payload digest lets a verifier confirm the signed bytes
    without the record having to inline the full command."""
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
    payload_digest: str      # sha256 of the canonical signed payload, hex
    signature: str           # Ed25519 signature, hex
    signed_payload: dict     # the exact canonical payload the signature covers,
                             # so replay can re-verify from the log alone. The
                             # record's marking protects it (it inherits the
                             # target's marking), so this is not an unrestricted
                             # audit log of restricted command content.
    recorded_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.actor_kind, ACTOR_KINDS, "actor kind")
        if not (self.actor_id and self.key_id and self.action_type
                and self.nonce and self.signature):
            raise ValueError("a signed action binds actor, key, action, nonce and signature")
        require_aware(self.timestamp)
        require_aware(self.recorded_time)
