"""Single reuse point for canonical serialization, hashing and identifiers.

Every hash, canonical byte sequence and deterministic identifier in the
operational plane flows through this module, so the coupling to the wider
repository stays confined to one replaceable seam (see sovereignty manifest).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

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


MAX_CANONICAL_DEPTH = 256


def validate_interchange(value: Any, *, _depth: int = 0) -> None:
    """Validate the value domain shared by records, signatures, and imports.

    The external kernel supplies canonical ordering and hashing.  Curunír owns
    the stricter interchange boundary: canonical state is ordinary JSON with
    string keys, finite numbers, well-formed Unicode, and bounded nesting.  A
    value refused here cannot be persisted through another serializer and then
    become unreadable or byte-divergent later.
    """
    if _depth > MAX_CANONICAL_DEPTH:
        raise ValueError("canonical value exceeds the maximum nesting depth")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("lone surrogate is not valid interchange Unicode") from exc
        return
    if isinstance(value, int):
        try:
            str(value)
        except ValueError as exc:
            raise ValueError("integer is too large for deterministic interchange") from exc
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not valid interchange JSON")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical JSON object keys must be strings")
            validate_interchange(key, _depth=_depth + 1)
            validate_interchange(item, _depth=_depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_interchange(item, _depth=_depth + 1)
        return
    raise ValueError(f"unsupported canonical value type: {type(value).__name__}")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def parse_json_strict(data: bytes | str, *, label: str = "JSON") -> Any:
    """Strictly parse a foreign JSON value and apply the canonical domain."""
    try:
        text = data.decode("utf-8", errors="strict") if isinstance(data, bytes) else data
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token: {token}")),
        )
        validate_interchange(value)
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError,
            TypeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def canonical_line(value: Any) -> str:
    validate_interchange(value)
    return canonical_bytes(value).decode()
