"""Single reuse point for canonical serialization, hashing and identifiers.

Every hash, canonical byte sequence and deterministic identifier in the
operational plane flows through this module, so the coupling to the wider
repository stays confined to one replaceable seam (see sovereignty manifest).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.prospective.freezing import canonical_bytes, sha256, write_json  # noqa: F401 (re-exported)
from argus.source_intelligence.models import digest_id, require_aware, require_sha256, stable_json  # noqa: F401
from argus.extract import propose_mentions  # noqa: F401
from argus.public_web_transport_v4 import retrieve_public_bytes_v4  # noqa: F401

CONTRACT_VERSION = "curunir-operational-contracts-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def require_aware_or_none(value: str | None) -> None:
    if value is not None:
        require_aware(value)


def canonical_line(value: Any) -> str:
    return canonical_bytes(value).decode()
