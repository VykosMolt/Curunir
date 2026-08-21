"""Bounded challenge-response authentication and short-lived sessions."""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from curunir_operational.canonical import parse_time

from .crypto import verify
from .registry import KeyRegistry


CHALLENGE_TTL_SECONDS = 120
SESSION_TTL_SECONDS = 900
MAX_PENDING_CHALLENGES = 4096
MAX_SESSIONS = 8192


class AuthError(PermissionError):
    pass


@dataclass(frozen=True)
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
    _lock: threading.RLock = field(default_factory=threading.RLock,
                                        repr=False)

    @staticmethod
    def challenge_payload(actor_id: str, nonce: str) -> dict[str, str]:
        return {
            "purpose": "curunir-authenticate",
            "actor_id": actor_id,
            "nonce": nonce,
        }

    @staticmethod
    def _plus(now: str, seconds: int) -> str:
        return (parse_time(now) + timedelta(seconds=seconds)).isoformat()

    def _prune_pending(self, now: str, owner: str) -> None:
        current = parse_time(now)
        expired = [
            nonce for nonce, pending in self._pending.items()
            if current > parse_time(pending["expires_time"])
        ]
        for nonce in expired:
            self._pending.pop(nonce, None)
        while len(self._pending) >= MAX_PENDING_CHALLENGES:
            owned = [
                nonce for nonce, pending in self._pending.items()
                if pending["owner"] == owner
            ]
            if not owned:
                raise AuthError(
                    "challenge capacity is full; a principal cannot evict "
                    "another principal's pending challenge")
            victim = min(
                owned,
                key=lambda nonce: self._pending[nonce]["issued_time"],
            )
            self._pending.pop(victim, None)

    def issue_challenge(self, actor_id: str, *, owner: str = "") -> dict[str, str]:
        if not isinstance(actor_id, str) or not actor_id:
            raise AuthError("actor_id is required")
        now = self.now_fn()
        owner = owner or actor_id
        with self._lock:
            self._prune_pending(now, owner)
            nonce = secrets.token_hex(32)
            self._pending[nonce] = {
                "actor_id": actor_id,
                "owner": owner,
                "issued_time": now,
                "expires_time": self._plus(now, self.challenge_ttl),
            }
        return {
            "actor_id": actor_id,
            "nonce": nonce,
            "purpose": "curunir-authenticate",
        }

    def _prune_sessions(self, now: str, actor_id: str) -> None:
        current = parse_time(now)
        expired = [
            session_id for session_id, session in self._sessions.items()
            if current > parse_time(session.expires_time)
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        while len(self._sessions) >= MAX_SESSIONS:
            owned = [
                session_id for session_id, session in self._sessions.items()
                if session.actor_id == actor_id
            ]
            if not owned:
                raise AuthError(
                    "session capacity is full; a principal cannot evict "
                    "another principal's session")
            victim = min(
                owned,
                key=lambda session_id: self._sessions[session_id].issued_time,
            )
            self._sessions.pop(victim, None)

    def authenticate(self, registry: KeyRegistry, *, actor_id: str, nonce: str,
                     signature_hex: str) -> Session:
        now = self.now_fn()
        with self._lock:
            pending = self._pending.pop(nonce, None)
            if pending is None or pending["actor_id"] != actor_id:
                raise AuthError("unknown or already-used challenge")
            if parse_time(now) > parse_time(pending["expires_time"]):
                raise AuthError("challenge expired")
            payload = self.challenge_payload(actor_id, nonce)
            key = next(
                (candidate for candidate in registry.active_keys_for(actor_id)
                 if verify(candidate["public_key"], signature_hex, payload)),
                None,
            )
            if key is None:
                raise AuthError("no active key verifies the challenge")
            self._prune_sessions(now, actor_id)
            session = Session(
                session_id=secrets.token_hex(24),
                actor_id=actor_id,
                actor_kind=key["actor_kind"],
                key_id=key["key_id"],
                issued_time=now,
                expires_time=self._plus(now, self.session_ttl),
            )
            self._sessions[session.session_id] = session
            return session

    def resolve(self, registry: KeyRegistry, session_id: str) -> Session:
        now = self.now_fn()
        with self._lock:
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
        with self._lock:
            self._sessions.pop(session_id, None)
