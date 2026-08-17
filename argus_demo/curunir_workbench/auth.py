"""Workbench actor registry: bearer token → access context.

This is the V6.6 bounded authentication mechanism, stated honestly: a static
registry file mapping opaque tokens to actor identity and access attributes.
It gives every workbench action an attributable actor and a fail-closed
access context; it is NOT production PKI, credential rotation, or the V6.7
provider-security programme. Signatures on dispositions are the actor
identity recorded through this registry plus the store's hash chain.

The registry file lives OUTSIDE the mission store (it is deployment
configuration, not mission truth) and is never exposed by the API.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

from curunir_operational.access import AccessContext


class AuthError(PermissionError):
    pass


class ActorRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("format") != "curunir-workbench-actors-v1":
            raise ValueError(f"not an actor registry: {self.path}")
        by_token: dict[str, dict] = {}
        by_actor: dict[str, dict] = {}
        for entry in data["actors"]:
            token = entry["token"]
            if token in by_token:
                raise ValueError("duplicate actor token")
            by_token[token] = entry
            by_actor[entry["actor_id"]] = entry
        self._by_token = by_token
        self._by_actor = by_actor
        self._mtime = self.path.stat().st_mtime

    def _context_for_entry(self, entry: dict) -> AccessContext:
        return AccessContext(
            context_id=f"wb-{entry['actor_id']}",
            actor_id=entry["actor_id"],
            # fail-closed: a partial/older entry lacking actor_kind must NOT
            # default to the privileged HUMAN value (it gates every human-only
            # adjudication — approval, forecast resolution, theme adjudication).
            # SERVICE is the least-privilege default; an operator makes a human
            # explicit. (Migration-safety: an entry written before actor_kind
            # existed cannot silently gain human authority.)
            actor_kind=entry.get("actor_kind", "SERVICE"),
            roles=tuple(entry.get("roles", ())),
            compartments=tuple(entry.get("compartments", ())),
            releasability=tuple(entry.get("releasability", ())),
            organisation=entry.get("organisation", ""),
        )

    def context_for_actor(self, actor_id: str) -> AccessContext:
        """The AUTHORIZATION state for an actor id — roles, compartments,
        releasability — resolved from current registry configuration, never
        from the client. Used after cryptographic AUTHENTICATION has proven who
        the actor is: authentication (who) and authorization (what they may do)
        are separate, so a valid key never implies a permission, and authority
        can change without touching the actor's key."""
        try:
            if self.path.stat().st_mtime != self._mtime:
                self._load()
        except OSError:
            raise AuthError("actor registry unavailable")
        entry = self._by_actor.get(actor_id)
        if entry is None or not entry.get("enabled", True):
            raise AuthError("unknown or disabled actor")
        return self._context_for_entry(entry)

    def context_for(self, token: str | None) -> AccessContext:
        if not token:
            raise AuthError("missing bearer token")
        if not token.isascii():
            # Starlette decodes header bytes as latin-1, so a bearer token can
            # carry a byte >= 0x80; secrets.compare_digest RAISES TypeError on a
            # non-ASCII str. No registered token is non-ASCII, so treat it as an
            # ordinary unknown-actor failure rather than letting a TypeError
            # escape context() to an unauthenticated 500 (+ per-request log-flood
            # primitive) on the whole API surface (review NEW-4).
            raise AuthError("unknown or disabled actor")
        # pick up registry edits (e.g. disabling an actor) without a restart
        try:
            if self.path.stat().st_mtime != self._mtime:
                self._load()
        except OSError:
            raise AuthError("actor registry unavailable")
        # constant-time comparison over registered tokens; unknown token and
        # disabled actor are indistinguishable
        entry = None
        for known, candidate in self._by_token.items():
            if secrets.compare_digest(known, token):
                entry = candidate
        if entry is None or not entry.get("enabled", True):
            raise AuthError("unknown or disabled actor")
        return AccessContext(
            context_id=f"wb-{entry['actor_id']}",
            actor_id=entry["actor_id"],
            # fail-closed: a partial/older entry lacking actor_kind must NOT
            # default to the privileged HUMAN value (it gates every human-only
            # adjudication — approval, forecast resolution, theme adjudication).
            # SERVICE is the least-privilege default; an operator makes a human
            # explicit. (Migration-safety: an entry written before actor_kind
            # existed cannot silently gain human authority.)
            actor_kind=entry.get("actor_kind", "SERVICE"),
            roles=tuple(entry.get("roles", ())),
            compartments=tuple(entry.get("compartments", ())),
            releasability=tuple(entry.get("releasability", ())),
            organisation=entry.get("organisation", ""),
        )


def write_registry(path: str | Path, actors: list[dict]) -> None:
    """Write the bearer-token registry atomically at mode 0600 — the tokens
    never exist at umask-default permissions, even momentarily."""
    import os
    path = Path(path)
    content = json.dumps({"format": "curunir-workbench-actors-v1", "actors": actors},
                         indent=1, sort_keys=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)  # atomic; the destination is 0600 from creation
