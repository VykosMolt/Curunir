"""Signed load-bearing actions — the non-repudiation layer.

A load-bearing act (approve a report, resolve a forecast, dispose a review) is
signed by the actor over a canonical binding of *exactly* what is being
authorized: actor + kind, action type, the target and its exact version token,
the mission, a single-use nonce, and the material command. Verification refuses
anything that does not match live state: an expired/foreign session, a wrong
actor or mission, a replayed nonce, a stale target version (so a signature
captured against one version cannot be replayed against another), a key that
was not valid when it signed, or a signature that does not verify.

A verified action is recorded immutably, so replay can re-verify it against the
key that was valid at signing time — the signature proves authenticity, the
hash chain proves the log was not altered; neither substitutes for the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time, sha256

from .contracts import SignedActionRecord
from .crypto import canonical_bytes, sign, verify
from .registry import VALID, KeyRegistry
from .sessions import AuthError, Session, SessionManager

# outcomes
ACCEPTED = "ACCEPTED"
EXPIRED_SESSION = "EXPIRED_SESSION"
WRONG_ACTOR = "WRONG_ACTOR"
WRONG_MISSION = "WRONG_MISSION"
WRONG_TARGET = "WRONG_TARGET"
REPLAYED_NONCE = "REPLAYED_NONCE"
STALE_VERSION = "STALE_VERSION"
CLOCK_SKEW = "CLOCK_SKEW"
INVALID_SIGNATURE = "INVALID_SIGNATURE"
KEY_NOT_VALID = "KEY_NOT_VALID"

# a signed timestamp must be within this of server time, so a live actor cannot
# backdate an act before a deadline or postdate it past a later key revocation
MAX_CLOCK_SKEW_SECONDS = 300


class SignatureRejected(PermissionError):
    def __init__(self, status: str, detail: str = ""):
        self.status = status
        super().__init__(detail or status)


@dataclass
class VerifiedAction:
    """A fully-verified signed action, NOT yet recorded. The command it
    authorizes runs first; only if the act commits is the attribution recorded
    (`commit_action`), so the immutable signed-action log never contains a
    GENUINE record for an act the system refused."""
    session: Session
    record: SignedActionRecord


def action_payload(*, actor_id: str, actor_kind: str, action_type: str,
                   target_kind: str, target_id: str, target_version_token: str,
                   mission_id: str, nonce: str, timestamp: str,
                   command: Mapping[str, Any]) -> dict[str, Any]:
    """The exact object signed and verified. `command` is the material payload
    of the act — signing 'approve' alone is useless, so the whole command is
    bound."""
    return {"actor_id": actor_id, "actor_kind": actor_kind,
            "action_type": action_type, "target_kind": target_kind,
            "target_id": target_id, "target_version_token": target_version_token,
            "mission_id": mission_id, "nonce": nonce, "timestamp": timestamp,
            "command": dict(command)}


def sign_action(private_pem: str, **fields: Any) -> dict[str, Any]:
    """Sign an action from an actor's private key; returns the payload, its
    digest and the signature. Used by the reference signing client / any actor
    agent — never by the server (the server holds no private keys)."""
    payload = action_payload(**fields)
    return {"payload": payload, "signature": sign(private_pem, payload),
            "payload_digest": sha256(payload)}


def _nonce_seen(store, nonce: str) -> bool:
    return any(r["nonce"] == nonce for r in store.records_of("signed_action"))


def verify_action(store, registry: KeyRegistry, sessions: SessionManager, *,
                  session_id: str, payload: Mapping[str, Any], signature_hex: str,
                  expected_action_type: str, expected_target_kind: str,
                  expected_target_id: str, current_version_token: str,
                  mission_id: str, marking: Marking) -> VerifiedAction:
    """Verify a signed load-bearing action against live state and the ACTUAL
    operation being performed. Raises SignatureRejected(status) on any failure;
    returns a VerifiedAction (not yet recorded) the caller commits only after
    the act succeeds.

    The signature must bind the real target and action, not merely self-describe
    them: `expected_*` are the operation the caller is about to perform, and a
    signed payload naming a different target/action/version is refused —
    closing signature transplant across targets. `current_version_token` is the
    target-scoped current token."""
    # 1. session must be live and its key still active
    try:
        session = sessions.resolve(registry, session_id)
    except AuthError as error:
        raise SignatureRejected(EXPIRED_SESSION, str(error))
    # 2. the signed actor/kind must be the authenticated actor
    if payload.get("actor_id") != session.actor_id \
            or payload.get("actor_kind") != session.actor_kind:
        raise SignatureRejected(WRONG_ACTOR,
                                "signed actor is not the authenticated actor")
    # 3. mission binding
    if payload.get("mission_id") != mission_id:
        raise SignatureRejected(WRONG_MISSION, "action is for a different mission")
    # 4. the signature must name the ACTUAL target and action — a signature for
    #    one target/action cannot be transplanted onto another
    if payload.get("action_type") != expected_action_type \
            or payload.get("target_kind") != expected_target_kind \
            or payload.get("target_id") != expected_target_id:
        raise SignatureRejected(WRONG_TARGET,
                                "signed target/action is not the operation performed")
    # 5. single-use nonce (replay of the whole signed command)
    nonce = payload.get("nonce", "")
    if not nonce or _nonce_seen(store, nonce):
        raise SignatureRejected(REPLAYED_NONCE, "nonce missing or already used")
    # 6. exact (target-scoped) version — a signature captured against one
    #    version of one target cannot be replayed against another
    if payload.get("target_version_token") != current_version_token:
        raise SignatureRejected(STALE_VERSION,
                                "target has moved since the action was signed")
    # 7. the timestamp must be honest — bounded to server time so it cannot be
    #    back/post-dated to move an act across a deadline or a key revocation
    timestamp = payload.get("timestamp", "")
    try:
        skew = abs((parse_time(timestamp) - parse_time(sessions.now_fn())).total_seconds())
    except (ValueError, TypeError):
        raise SignatureRejected(CLOCK_SKEW, "action timestamp is unparseable")
    if skew > MAX_CLOCK_SKEW_SECONDS:
        raise SignatureRejected(CLOCK_SKEW,
                                "action timestamp deviates from server time")
    # 8. the signature must verify under the session's key
    public_key = registry.public_key_of(session.key_id)
    if not public_key or not verify(public_key, signature_hex, payload):
        raise SignatureRejected(INVALID_SIGNATURE, "signature does not verify")
    # 9. the key must have been trustworthy at signing time
    status = registry.verification_status_at(session.key_id, timestamp)
    if status != VALID:
        raise SignatureRejected(KEY_NOT_VALID, f"key status at signing: {status}")

    record = SignedActionRecord(
        action_id="act-" + sha256({"nonce": nonce, "key": session.key_id})[:24],
        actor_id=session.actor_id, actor_kind=session.actor_kind,
        key_id=session.key_id, action_type=payload["action_type"],
        target_kind=payload["target_kind"], target_id=payload["target_id"],
        target_version_token=payload["target_version_token"], mission_id=mission_id,
        nonce=nonce, timestamp=timestamp,
        payload_digest=sha256(dict(payload)), signature=signature_hex,
        signed_payload=dict(payload),
        recorded_time=sessions.now_fn(), marking=marking)
    return VerifiedAction(session=session, record=record)


def commit_action(store, verified: VerifiedAction, *, record_actor: str) -> dict[str, Any]:
    """Record the attribution AFTER the act it authorizes has committed. Called
    by the bridge only on the command's success, so a refused act never leaves a
    GENUINE signed-action record behind."""
    store.append("SIGNED_ACTION_RECORDED", verified.record,
                 recorded_time=verified.record.recorded_time, actor=record_actor)
    return verified.record.to_record()
