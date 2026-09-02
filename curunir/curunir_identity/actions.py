"""Verify a signed action, then record it once the action has succeeded."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping

from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time, sha256

from .contracts import SignedActionRecord
from .crypto import sign, verify
from .registry import KeyRegistry, VALID
from .sessions import AuthError, Session, SessionManager


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
MAX_CLOCK_SKEW_SECONDS = 300


class SignatureRejected(PermissionError):
    def __init__(self, status: str, detail: str = ""):
        self.status = status
        super().__init__(detail or status)


@dataclass
class VerifiedAction:
    """A verified action that has not been recorded yet."""

    session: Session
    record: SignedActionRecord


def action_payload(*, actor_id: str, actor_kind: str, action_type: str,
                   target_kind: str, target_id: str,
                   target_version_token: str, mission_id: str, nonce: str,
                   timestamp: str, command: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(command, Mapping):
        raise ValueError("signed command must be an object")
    return {
        "actor_id": actor_id,
        "actor_kind": actor_kind,
        "action_type": action_type,
        "target_kind": target_kind,
        "target_id": target_id,
        "target_version_token": target_version_token,
        "mission_id": mission_id,
        "nonce": nonce,
        "timestamp": timestamp,
        "command": dict(command),
    }


def sign_action(private_pem: str, **fields: Any) -> dict[str, Any]:
    payload = action_payload(**fields)
    return {
        "payload": payload,
        "signature": sign(private_pem, payload),
        "payload_digest": sha256(payload),
    }


def _nonce_seen(store, nonce: str) -> bool:
    return any(
        record["nonce"] == nonce for record in store.records_of("signed_action")
    )


def verify_action(store, registry: KeyRegistry, sessions: SessionManager, *,
                  session_id: str, payload: Mapping[str, Any],
                  signature_hex: str, expected_action_type: str,
                  expected_target_kind: str, expected_target_id: str,
                  current_version_token: str, mission_id: str,
                  marking: Marking) -> VerifiedAction:
    """Check a signed action in a fixed order. Nothing is written here."""
    if not isinstance(payload, Mapping):
        raise SignatureRejected(INVALID_SIGNATURE, "signed payload is not an object")
    try:
        session = sessions.resolve(registry, session_id)
    except AuthError as exc:
        raise SignatureRejected(EXPIRED_SESSION, str(exc)) from exc
    if payload.get("actor_id") != session.actor_id \
            or payload.get("actor_kind") != session.actor_kind:
        raise SignatureRejected(
            WRONG_ACTOR, "signed actor is not the authenticated actor")
    if payload.get("mission_id") != mission_id:
        raise SignatureRejected(WRONG_MISSION, "action is for a different mission")
    if payload.get("action_type") != expected_action_type \
            or payload.get("target_kind") != expected_target_kind \
            or payload.get("target_id") != expected_target_id:
        raise SignatureRejected(
            WRONG_TARGET, "signed target/action is not the operation performed")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or not nonce or _nonce_seen(store, nonce):
        raise SignatureRejected(REPLAYED_NONCE, "nonce missing or already used")
    if payload.get("target_version_token") != current_version_token:
        raise SignatureRejected(
            STALE_VERSION, "target has moved since the action was signed")
    timestamp = payload.get("timestamp")
    try:
        skew = abs(
            (parse_time(timestamp) - parse_time(sessions.now_fn())).total_seconds()
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise SignatureRejected(CLOCK_SKEW, "action timestamp is unparseable") from exc
    if skew > MAX_CLOCK_SKEW_SECONDS:
        raise SignatureRejected(
            CLOCK_SKEW, "action timestamp deviates from server time")
    public_key = registry.public_key_of(session.key_id)
    if public_key is None or not verify(public_key, signature_hex, payload):
        raise SignatureRejected(INVALID_SIGNATURE, "signature does not verify")
    status = registry.verification_status_at(session.key_id, timestamp)
    if status != VALID:
        raise SignatureRejected(
            KEY_NOT_VALID, f"key status at signing: {status}")

    record = SignedActionRecord(
        action_id="act-" + sha256({
            "actor": session.actor_id,
            "key": session.key_id,
            "nonce": nonce,
        })[:24],
        actor_id=session.actor_id,
        actor_kind=session.actor_kind,
        key_id=session.key_id,
        action_type=expected_action_type,
        target_kind=expected_target_kind,
        target_id=expected_target_id,
        target_version_token=current_version_token,
        mission_id=mission_id,
        nonce=nonce,
        timestamp=timestamp,
        payload_digest=sha256(dict(payload)),
        signature=signature_hex,
        signed_payload=dict(payload),
        recorded_time=sessions.now_fn(),
        marking=marking,
    )
    return VerifiedAction(session=session, record=record)


def commit_action(store, verified: VerifiedAction, *, record_actor: str,
                  recorded_time: str | None = None) -> dict[str, Any]:
    """Record the signed action. Call this only after the command itself succeeded."""
    if recorded_time is not None:
        verified.record = dataclasses.replace(
            verified.record, recorded_time=recorded_time)

    def nonce_unused(_store) -> None:
        if _nonce_seen(store, verified.record.nonce):
            raise SignatureRejected(
                REPLAYED_NONCE, "nonce was committed concurrently")

    event = store.append(
        "SIGNED_ACTION_RECORDED",
        verified.record,
        recorded_time=verified.record.recorded_time,
        actor=record_actor,
        condition=nonce_unused,
    )
    return event["record"]
