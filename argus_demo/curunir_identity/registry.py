"""Replay-backed Ed25519 public-key ownership and lifecycle."""
from __future__ import annotations

from typing import Any, Callable

from curunir_operational.access import Marking, inherited_marking
from curunir_operational.canonical import parse_time

from .contracts import ActorKeyRecord
from .crypto import key_id_for, normalize_public_key


VALID = "VALID"
NOT_YET_ENROLLED = "NOT_YET_ENROLLED"
REVOKED = "REVOKED"
RETIRED = "RETIRED"
COMPROMISED = "COMPROMISED"
UNKNOWN = "UNKNOWN"
MAX_ACTIVE_KEYS_PER_ACTOR = 8
_COMPROMISE_TAG = "COMPROMISED"


class _NoAppend(Exception):
    def __init__(self, record: dict[str, Any]):
        self.record = record


class KeyRegistry:
    """One-owner key families with atomic, bounded enrollment."""

    def __init__(self, store, *, actor: str = "identity-registry",
                 marking: Marking | None = None,
                 now_fn: Callable[[], str] | None = None):
        self.store = store
        self.actor = actor
        self._marking = marking
        self._now_fn = now_fn

    def _now(self, value: str | None) -> str:
        resolved = value or (self._now_fn() if self._now_fn else None)
        if resolved is None:
            raise ValueError("key lifecycle mutation needs a timestamp")
        parse_time(resolved)
        return resolved

    def _marking_for(self) -> Marking:
        if self._marking is None:
            raise ValueError("key registry needs a marking for mutation")
        return self._marking

    def versions(self, key_id: str) -> list[dict]:
        return sorted(
            (record for record in self.store.records_of("actor_key")
             if record["key_id"] == key_id),
            key=lambda record: record.get("version", 1),
        )

    def current(self, key_id: str) -> dict | None:
        versions = self.versions(key_id)
        return versions[-1] if versions else None

    def active_keys_for(self, actor_id: str) -> list[dict]:
        key_ids = {
            record["key_id"] for record in self.store.records_of("actor_key")
            if record["actor_id"] == actor_id
        }
        return sorted(
            (current for key_id in key_ids
             if (current := self.current(key_id)) is not None
             and current["status"] == "ACTIVE"),
            key=lambda record: (record["enrolled_time"], record["key_id"]),
        )

    def active_key_for(self, actor_id: str) -> dict | None:
        active = self.active_keys_for(actor_id)
        return active[-1] if active else None

    def _enroll(self, *, actor_id: str, actor_kind: str,
                public_key_hex: str, now: str,
                supersedes_key_id: str = "") -> dict[str, Any]:
        public_key = normalize_public_key(public_key_hex)
        key_id = key_id_for(public_key)
        record = ActorKeyRecord(
            key_id=key_id,
            version=1,
            actor_id=actor_id,
            actor_kind=actor_kind,
            public_key=public_key,
            status="ACTIVE",
            enrolled_time=now,
            status_time=now,
            supersedes_key_id=supersedes_key_id,
            reason="rotated-in" if supersedes_key_id else "enrolled",
            recorded_time=now,
            marking=self._marking_for(),
        )

        def condition(_store) -> None:
            existing = self.current(key_id)
            if existing is not None:
                if existing["actor_id"] != actor_id:
                    raise ValueError(
                        "public key is already enrolled to a different actor")
                if existing["actor_kind"] != actor_kind:
                    raise ValueError("an actor key cannot change actor kind")
                if existing["status"] == "ACTIVE" \
                        and existing.get("supersedes_key_id", "") == supersedes_key_id:
                    raise _NoAppend(existing)
                raise ValueError(
                    f"this key is {existing['status']} for {actor_id}; "
                    "enroll a fresh key")
            owned = [item for item in self.store.records_of("actor_key")
                     if item["actor_id"] == actor_id]
            if owned and any(item["actor_kind"] != actor_kind for item in owned):
                raise ValueError("an actor's enrolled keys must share actor_kind")
            if len(self.active_keys_for(actor_id)) >= MAX_ACTIVE_KEYS_PER_ACTOR:
                raise ValueError(
                    f"actor already has the maximum "
                    f"{MAX_ACTIVE_KEYS_PER_ACTOR} active signing keys")

        try:
            event = self.store.append(
                "ACTOR_KEY_RECORDED",
                record,
                recorded_time=now,
                actor=self.actor,
                condition=condition,
            )
        except _NoAppend as done:
            if compromised and not str(done.record.get("reason", "")).startswith(
                    _COMPROMISE_TAG):
                return self._transition(
                    key_id, status, reason, now, compromised=True)
            return done.record
        return event["record"]

    def enroll(self, *, actor_id: str, actor_kind: str, public_key_hex: str,
               now: str | None = None) -> dict[str, Any]:
        """Add one device key without retiring any other device."""
        return self._enroll(
            actor_id=actor_id,
            actor_kind=actor_kind,
            public_key_hex=public_key_hex,
            now=self._now(now),
        )

    def _transition(self, key_id: str, status: str, reason: str,
                    now: str, *, compromised: bool = False) -> dict[str, Any]:
        current = self.current(key_id)
        if current is None:
            raise ValueError(f"unknown key: {key_id}")
        already_compromised = str(current.get("reason", "")).startswith(
            _COMPROMISE_TAG)
        if current["status"] in ("REVOKED", "RETIRED") \
                and not (compromised and not already_compromised):
            return current
        expected_version = current["version"]
        record = ActorKeyRecord(
            key_id=key_id,
            version=expected_version + 1,
            actor_id=current["actor_id"],
            actor_kind=current["actor_kind"],
            public_key=current["public_key"],
            status=status,
            enrolled_time=current["enrolled_time"],
            status_time=now,
            supersedes_key_id=current.get("supersedes_key_id", ""),
            reason=(f"{_COMPROMISE_TAG}: {reason}" if compromised else reason),
            recorded_time=now,
            marking=inherited_marking(self._marking_for(), [current["marking"]]),
        )

        def condition(_store) -> None:
            live = self.current(key_id)
            if live is None:
                raise ValueError(f"unknown key: {key_id}")
            if live["version"] != expected_version:
                raise _NoAppend(live)

        try:
            event = self.store.append(
                "ACTOR_KEY_RECORDED",
                record,
                recorded_time=now,
                actor=self.actor,
                condition=condition,
            )
        except _NoAppend as done:
            return done.record
        return event["record"]

    def revoke(self, key_id: str, *, reason: str = "revoked",
               compromised: bool = False,
               now: str | None = None) -> dict[str, Any]:
        return self._transition(
            key_id,
            "REVOKED",
            reason,
            self._now(now),
            compromised=compromised,
        )

    def rotate(self, *, actor_id: str, new_public_key_hex: str,
               now: str | None = None) -> dict[str, Any]:
        """Enroll the replacement first, then retire its recorded predecessor.

        The order cannot brick the actor.  ``supersedes_key_id`` makes a retry
        after a crash finish the retirement instead of leaving the old key live.
        """
        timestamp = self._now(now)
        new_key_id = key_id_for(new_public_key_hex)
        existing_new = self.current(new_key_id)
        predecessor_id = (
            existing_new.get("supersedes_key_id", "")
            if existing_new is not None else ""
        )
        if not predecessor_id:
            predecessor = self.active_key_for(actor_id)
            if predecessor is None:
                raise ValueError(
                    "rotation requires an active predecessor; use enroll")
            if predecessor["key_id"] == new_key_id:
                return predecessor
            predecessor_id = predecessor["key_id"]
            new_record = self._enroll(
                actor_id=actor_id,
                actor_kind=predecessor["actor_kind"],
                public_key_hex=new_public_key_hex,
                now=timestamp,
                supersedes_key_id=predecessor_id,
            )
        else:
            if existing_new["actor_id"] != actor_id:
                raise ValueError("replacement key belongs to a different actor")
            new_record = existing_new
        self._transition(predecessor_id, "RETIRED", "rotated", timestamp)
        return new_record

    def public_key_of(self, key_id: str) -> str | None:
        current = self.current(key_id)
        return current["public_key"] if current else None

    def verification_status_at(self, key_id: str, at_time: str) -> str:
        versions = self.versions(key_id)
        if not versions:
            return UNKNOWN
        latest = versions[-1]
        if latest["status"] == "REVOKED" \
                and str(latest.get("reason", "")).startswith(_COMPROMISE_TAG):
            return COMPROMISED
        at = parse_time(at_time)
        if at < parse_time(versions[0]["enrolled_time"]):
            return NOT_YET_ENROLLED
        effective = None
        for version in versions:
            if parse_time(version["status_time"]) <= at:
                effective = version
        if effective is None or effective["status"] == "ACTIVE":
            return VALID
        return effective["status"]
