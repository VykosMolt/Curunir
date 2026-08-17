"""The actor key registry: enrolled public keys and their lifecycle, replayed
from the mission log.

Its one security job is to answer, for any key and any moment: was this key
trustworthy to have signed *then*? Revocation and normal retirement stop future
use but never retroactively void a signature made while the key was ACTIVE
(§77); a key marked compromised is distrusted even for its past signatures. So a
signature stays verifiable for the life of the log, against the exact validity
window the registry records.
"""
from __future__ import annotations

from typing import Any, Callable

from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time

from .contracts import ActorKeyRecord
from .crypto import key_id_for

# verification_status_at outcomes
VALID = "VALID"
NOT_YET_ENROLLED = "NOT_YET_ENROLLED"
REVOKED = "REVOKED"
RETIRED = "RETIRED"
COMPROMISED = "COMPROMISED"
UNKNOWN = "UNKNOWN"

_COMPROMISE_TAG = "COMPROMISED"

# An actor may enrol several device keys, but authentication verifies a challenge
# against EVERY active key, so the count must be bounded — otherwise an enrol
# flood makes authenticate O(K) work per attempt (review F2-round-B). A small cap
# covers realistic multi-device use; revoke an old device to add a new one.
MAX_ACTIVE_KEYS_PER_ACTOR = 8


class KeyRegistry:
    def __init__(self, store, *, actor: str = "identity-registry",
                 marking: Marking | None = None,
                 now_fn: Callable[[], str] | None = None):
        self.store = store
        self.actor = actor
        self._marking = marking
        self._now_fn = now_fn

    # ---- lifecycle -------------------------------------------------------

    def _marking_for(self) -> Marking:
        if self._marking is None:
            raise ValueError("key registry needs a marking to enroll keys")
        return self._marking

    def _append(self, record: ActorKeyRecord) -> dict[str, Any]:
        self.store.append("ACTOR_KEY_RECORDED", record,
                          recorded_time=record.recorded_time, actor=self.actor)
        return record.to_record()

    def versions(self, key_id: str) -> list[dict]:
        return sorted((r for r in self.store.records_of("actor_key")
                       if r["key_id"] == key_id),
                      key=lambda r: r.get("version", 1))

    def current(self, key_id: str) -> dict | None:
        versions = self.versions(key_id)
        return versions[-1] if versions else None

    def enroll(self, *, actor_id: str, actor_kind: str, public_key_hex: str,
               now: str | None = None) -> dict[str, Any]:
        """Enroll a public key as an ACTIVE signing key for an actor. An actor
        may hold several active keys (one per device); revoking one never
        affects the others.

        A key id is a digest of the public key, so a key family has ONE owner
        forever: enrolling a public key already enrolled to a DIFFERENT actor is
        refused (a bearer cannot hijack another actor's key by resubmitting their
        public key). Re-enrolling one's own ACTIVE key is idempotent; re-enrolling
        one's own retired/revoked key is refused (enroll a fresh key) so an
        enrolment can never silently resurrect a retired or compromised key."""
        now = now or (self._now_fn() if self._now_fn else None)
        if now is None:
            raise ValueError("enroll needs a timestamp")
        key_id = key_id_for(public_key_hex)
        existing = self.current(key_id)
        if existing is not None:
            if existing["actor_id"] != actor_id:
                raise ValueError(
                    "public key is already enrolled to a different actor")
            if existing["status"] == "ACTIVE":
                return existing  # idempotent re-enrolment of an own active key
            raise ValueError(
                f"this key is {existing['status']} for {actor_id}; enroll a fresh key")
        # a genuinely new key: bound the actor's active-key count so authenticate
        # (which verifies against every active key) cannot be flooded into O(K).
        if len(self.active_keys_for(actor_id)) >= MAX_ACTIVE_KEYS_PER_ACTOR:
            raise ValueError(
                f"actor already has the maximum {MAX_ACTIVE_KEYS_PER_ACTOR} active "
                f"signing keys; revoke an old device before enrolling a new one")
        return self._append(ActorKeyRecord(
            key_id=key_id, version=1, actor_id=actor_id, actor_kind=actor_kind,
            public_key=public_key_hex, status="ACTIVE", enrolled_time=now,
            status_time=now, supersedes_key_id="", reason="enrolled",
            recorded_time=now, marking=self._marking_for()))

    def _transition(self, key_id: str, status: str, reason: str,
                    now: str) -> dict[str, Any]:
        current = self.current(key_id)
        if current is None:
            raise ValueError(f"unknown key: {key_id}")
        if current["status"] in ("REVOKED", "RETIRED"):
            return current  # terminal; idempotent
        version = self.store.next_family_version("actor_key", "key_id", key_id)
        return self._append(ActorKeyRecord(
            key_id=key_id, version=version, actor_id=current["actor_id"],
            actor_kind=current["actor_kind"], public_key=current["public_key"],
            status=status, enrolled_time=current["enrolled_time"], status_time=now,
            supersedes_key_id=current.get("supersedes_key_id", ""), reason=reason,
            recorded_time=now,
            marking=self._marking_for() if isinstance(current.get("marking"), dict)
            else current["marking"]))

    def revoke(self, key_id: str, *, reason: str = "revoked", compromised: bool = False,
               now: str | None = None) -> dict[str, Any]:
        """Stop future use of a key. Signatures it made while ACTIVE remain
        valid — unless `compromised`, which distrusts even its past signatures.
        Compromise may escalate a key that was already normally revoked or
        retired (compromise discovered after the fact)."""
        now = now or (self._now_fn() if self._now_fn else None)
        current = self.current(key_id)
        if current is None:
            raise ValueError(f"unknown key: {key_id}")
        already_compromised = str(current.get("reason", "")).startswith(_COMPROMISE_TAG)
        # REVOKED/RETIRED are terminal for ordinary revocation; a compromise
        # escalation is the one thing that may still supersede them
        if current["status"] in ("REVOKED", "RETIRED") \
                and not (compromised and not already_compromised):
            return current
        tag = f"{_COMPROMISE_TAG}: {reason}" if compromised else reason
        version = self.store.next_family_version("actor_key", "key_id", key_id)
        return self._append(ActorKeyRecord(
            key_id=key_id, version=version, actor_id=current["actor_id"],
            actor_kind=current["actor_kind"], public_key=current["public_key"],
            status="REVOKED", enrolled_time=current["enrolled_time"], status_time=now,
            supersedes_key_id=current.get("supersedes_key_id", ""), reason=tag,
            recorded_time=now, marking=self._marking_for()))

    def rotate(self, *, actor_id: str, new_public_key_hex: str,
               now: str | None = None) -> dict[str, Any]:
        """Retire the actor's current active key and enroll a new one. The old
        key's historical signatures stay verifiable; new actions use the new
        key. Rotation is not a way to gain a second identity — the actor_id is
        unchanged, so four-eyes still sees one logical actor."""
        now = now or (self._now_fn() if self._now_fn else None)
        old = self.active_key_for(actor_id)
        new_key_id = key_id_for(new_public_key_hex)
        # Enroll the new key FIRST: if it is owned by another actor or is a
        # retired/revoked own key, enroll raises BEFORE the old key is retired, so
        # a failed rotate never bricks the actor (review finding 7). (An actor at
        # the active-key cap must revoke a device before rotating; fail-safe.)
        record = self.enroll(actor_id=actor_id,
                             actor_kind=old["actor_kind"] if old else "HUMAN",
                             public_key_hex=new_public_key_hex, now=now)
        if old is not None and old["key_id"] != new_key_id:
            self._transition(old["key_id"], "RETIRED", "rotated", now)
        if old is not None and record["supersedes_key_id"] == "" \
                and record["version"] == 1 and old["key_id"] != new_key_id:
            # stamp the supersession on the fresh enrollment
            self.store.append("ACTOR_KEY_RECORDED", ActorKeyRecord(
                key_id=new_key_id,
                version=self.store.next_family_version("actor_key", "key_id", new_key_id),
                actor_id=actor_id, actor_kind=record["actor_kind"],
                public_key=new_public_key_hex, status="ACTIVE",
                enrolled_time=record["enrolled_time"], status_time=now,
                supersedes_key_id=old["key_id"], reason="rotated-in", recorded_time=now,
                marking=self._marking_for()), recorded_time=now, actor=self.actor)
            return self.current(new_key_id)
        return record

    # ---- lookups ---------------------------------------------------------

    def active_keys_for(self, actor_id: str) -> list[dict]:
        """ALL currently-ACTIVE keys for an actor (one per enrolled device).
        Authentication verifies a challenge signature against each, so a second
        device never invalidates the first."""
        keys = [self.current(kid) for kid in
                {r["key_id"] for r in self.store.records_of("actor_key")
                 if r["actor_id"] == actor_id}]
        return [k for k in keys if k and k["status"] == "ACTIVE"]

    def active_key_for(self, actor_id: str) -> dict | None:
        """The latest ACTIVE key for an actor (latest wins). Prefer
        active_keys_for for authentication; this is for callers that need a
        single representative key (e.g. rotate)."""
        active = self.active_keys_for(actor_id)
        return max(active, key=lambda k: k["enrolled_time"]) if active else None

    def public_key_of(self, key_id: str) -> str | None:
        current = self.current(key_id)
        return current["public_key"] if current else None

    def verification_status_at(self, key_id: str, at_time: str) -> str:
        """Was this key trustworthy to have signed at `at_time`? VALID only if
        it was ACTIVE then; REVOKED/RETIRED if the lifecycle event predates the
        signature; COMPROMISED if retroactively distrusted; UNKNOWN if no such
        key; NOT_YET_ENROLLED if the signature predates enrollment."""
        versions = self.versions(key_id)
        if not versions:
            return UNKNOWN
        latest = versions[-1]
        if latest["status"] == "REVOKED" and str(latest.get("reason", "")).startswith(_COMPROMISE_TAG):
            return COMPROMISED
        at = parse_time(at_time)
        if at < parse_time(versions[0]["enrolled_time"]):
            return NOT_YET_ENROLLED
        # the status version in effect at `at_time` is the latest whose
        # status_time is at or before it
        effective = None
        for version in versions:
            if parse_time(version["status_time"]) <= at:
                effective = version
        if effective is None or effective["status"] == "ACTIVE":
            return VALID
        return effective["status"]  # REVOKED or RETIRED, effective at that time
