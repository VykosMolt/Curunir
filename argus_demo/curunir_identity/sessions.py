"""Challenge-response sessions — the authentication layer.

An actor authenticates by signing a server-issued, single-use, short-lived
challenge with its private key; the server verifies against the enrolled public
key and issues a short-lived session. This is DEPLOYMENT state, never the
mission log: sessions expire, are re-checked against live key status on every
use (so a mid-session revocation stops further use), and carry no authority of
their own — a session proves *who*, not *what they may do*. Authority is
resolved separately (authz ≠ authn); the client never asserts its own identity
or roles, only proves possession of its key.

A session is authentication continuity; individual load-bearing acts are
additionally signed (see actions.py), so a copied session token still cannot
forge a signed action without the private key.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Callable

from curunir_operational.canonical import parse_time

from .crypto import verify
from .registry import VALID, KeyRegistry

CHALLENGE_TTL_SECONDS = 120
SESSION_TTL_SECONDS = 900  # 15 minutes


class AuthError(PermissionError):
    pass


@dataclass
class Session:
    session_id: str
    actor_id: str
    actor_kind: str
    key_id: str
    issued_time: str
    expires_time: str


@dataclass
class SessionManager:
    now_fn: Callable[[], str]
    challenge_ttl: int = CHALLENGE_TTL_SECONDS
    session_ttl: int = SESSION_TTL_SECONDS
    _pending: dict[str, dict] = field(default_factory=dict)
    _sessions: dict[str, Session] = field(default_factory=dict)

    def _plus(self, now: str, seconds: int) -> str:
        from datetime import timedelta
        return (parse_time(now) + timedelta(seconds=seconds)).isoformat()

    # ---- authentication --------------------------------------------------

    def issue_challenge(self, actor_id: str) -> dict[str, str]:
        """A fresh single-use nonce the actor must sign to prove key
        possession. Bound to the actor and expiring quickly."""
        now = self.now_fn()
        nonce = secrets.token_hex(32)
        self._pending[nonce] = {"actor_id": actor_id, "issued_time": now,
                                "expires_time": self._plus(now, self.challenge_ttl)}
        return {"actor_id": actor_id, "nonce": nonce,
                "purpose": "curunir-authenticate"}

    @staticmethod
    def challenge_payload(actor_id: str, nonce: str) -> dict[str, str]:
        """The exact object the actor signs to authenticate — bound to the
        actor and this nonce, so a signature cannot be reused for another
        actor or challenge."""
        return {"purpose": "curunir-authenticate", "actor_id": actor_id, "nonce": nonce}

    def authenticate(self, registry: KeyRegistry, *, actor_id: str, nonce: str,
                     signature_hex: str) -> Session:
        now = self.now_fn()
        pending = self._pending.pop(nonce, None)  # single use: consumed on attempt
        if pending is None or pending["actor_id"] != actor_id:
            raise AuthError("unknown or already-used challenge")
        if parse_time(now) > parse_time(pending["expires_time"]):
            raise AuthError("challenge expired")
        key = registry.active_key_for(actor_id)
        if key is None:
            raise AuthError("no active key for actor")
        if not verify(key["public_key"], signature_hex,
                      self.challenge_payload(actor_id, nonce)):
            raise AuthError("challenge signature does not verify")
        session = Session(session_id=secrets.token_hex(24), actor_id=actor_id,
                          actor_kind=key["actor_kind"], key_id=key["key_id"],
                          issued_time=now, expires_time=self._plus(now, self.session_ttl))
        self._sessions[session.session_id] = session
        return session

    # ---- resolution ------------------------------------------------------

    def resolve(self, registry: KeyRegistry, session_id: str) -> Session:
        """The live session for a token, or refuse. Re-checks expiry AND the
        key's current status every time, so a session dies the moment its key
        is revoked — not only at expiry."""
        now = self.now_fn()
        session = self._sessions.get(session_id)
        if session is None:
            raise AuthError("unknown session")
        if parse_time(now) > parse_time(session.expires_time):
            self._sessions.pop(session_id, None)
            raise AuthError("session expired")
        current = registry.current(session.key_id)
        if current is None or current["status"] != "ACTIVE":
            self._sessions.pop(session_id, None)
            raise AuthError("session key is no longer active")
        return session

    def revoke_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
