"""Single reuse point for canonical serialization, hashing and identifiers.

Every hash, canonical byte sequence and deterministic identifier in the
operational plane flows through this module, so the coupling to the wider
repository stays confined to one replaceable seam (see sovereignty manifest).
"""
from __future__ import annotations

import math
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


def reject_non_finite(value: Any) -> None:
    """Raise ValueError if a float NaN / Infinity / -Infinity appears anywhere in
    `value`. These are the surrogate class's TWIN: the kernel `canonical_bytes`
    uses json.dumps at its default allow_nan=True, so a non-finite float
    serializes to a bare `NaN`/`Infinity` token — NOT valid RFC-8259 JSON. Left
    unchecked it lands in the append-only event log (which then no longer parses
    under any strict third-party auditor), permanently 500s the record projection
    that replays it, replicates through export/delta bundles, and can enter a
    cryptographic signed_payload — all while verify_chain still reports valid.
    Refused here at the single serialization seam so no record can carry one
    (review NEW-C)."""
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                "non-finite float (NaN/Infinity) is not valid interchange JSON; refused")
    elif isinstance(value, dict):
        for k, v in value.items():
            reject_non_finite(k)
            reject_non_finite(v)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            reject_non_finite(item)


def canonical_line(value: Any) -> str:
    reject_non_finite(value)
    return canonical_bytes(value).decode()
