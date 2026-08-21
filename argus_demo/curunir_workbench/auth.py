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
import os
import secrets
import stat
from pathlib import Path

from curunir_operational.access import AccessContext
from curunir_operational.canonical import parse_json_strict, validate_interchange


class AuthError(PermissionError):
    pass


class ActorRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._load()

    def _load(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags)
        except OSError as exc:
            raise ValueError(f"actor registry is unavailable: {self.path}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("actor registry must be a single-link regular file")
            if info.st_size > 1024 * 1024:
                raise ValueError("actor registry exceeds the 1 MiB bound")
            raw = b""
            while chunk := os.read(fd, 64 * 1024):
                raw += chunk
        finally:
            os.close(fd)
        data = parse_json_strict(raw, label="actor registry")
        if not isinstance(data, dict):
            raise ValueError(f"not an actor registry: {self.path}")
        if data.get("format") != "curunir-workbench-actors-v1":
            raise ValueError(f"not an actor registry: {self.path}")
        by_token: dict[str, dict] = {}
        by_actor: dict[str, dict] = {}
        actors = data.get("actors")
        if not isinstance(actors, list):
            raise ValueError("actor registry actors must be a list")
        for entry in actors:
            if not isinstance(entry, dict):
                raise ValueError("actor registry entries must be objects")
            token = entry.get("token")
            actor_id = entry.get("actor_id")
            if not isinstance(token, str) or not token or not token.isascii():
                raise ValueError("actor tokens must be non-empty ASCII")
            if not isinstance(actor_id, str) or not actor_id:
                raise ValueError("actor_id must be non-empty text")
            if token in by_token or actor_id in by_actor:
                raise ValueError("duplicate actor token or actor_id")
            by_token[token] = entry
            by_actor[actor_id] = entry
        self._by_token = by_token
        self._by_actor = by_actor
        self._mtime = self.path.stat().st_mtime

    @staticmethod
    def _context(entry: dict) -> AccessContext:
        return AccessContext(
            context_id=f"wb-{entry['actor_id']}",
            actor_id=entry["actor_id"],
            # Missing migration-era metadata cannot silently gain human-only
            # adjudication authority.
            actor_kind=entry.get("actor_kind", "SERVICE"),
            roles=tuple(entry.get("roles", ())),
            compartments=tuple(entry.get("compartments", ())),
            releasability=tuple(entry.get("releasability", ())),
            organisation=entry.get("organisation", ""),
        )

    def _reload_if_changed(self) -> None:
        try:
            if self.path.stat().st_mtime != self._mtime:
                self._load()
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise AuthError("actor registry unavailable") from exc

    def context_for_actor(self, actor_id: str) -> AccessContext:
        """Resolve authorization for a cryptographically authenticated id."""
        self._reload_if_changed()
        entry = self._by_actor.get(actor_id)
        if entry is None or not entry.get("enabled", True):
            raise AuthError("unknown or disabled actor")
        return self._context(entry)

    def context_for(self, token: str | None) -> AccessContext:
        if not token:
            raise AuthError("missing bearer token")
        if not token.isascii():
            raise AuthError("unknown or disabled actor")
        self._reload_if_changed()
        # constant-time comparison over registered tokens; unknown token and
        # disabled actor are indistinguishable
        entry = None
        for known, candidate in self._by_token.items():
            if secrets.compare_digest(known, token):
                entry = candidate
        if entry is None or not entry.get("enabled", True):
            raise AuthError("unknown or disabled actor")
        return self._context(entry)


def write_registry(path: str | Path, actors: list[dict]) -> None:
    """Write the bearer-token registry atomically at mode 0600 — the tokens
    never exist at umask-default permissions, even momentarily."""
    path = Path(path)
    payload = {"format": "curunir-workbench-actors-v1", "actors": actors}
    validate_interchange(payload)
    content = json.dumps(payload, indent=1, sort_keys=True, allow_nan=False)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)  # atomic; the destination is 0600 from creation
