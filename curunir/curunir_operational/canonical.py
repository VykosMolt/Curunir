"""One place for canonical serialization, hashing and identifiers.

Every hash, canonical byte sequence and deterministic id in the operational
plane goes through here, so the dependency on the wider repository stays in
one replaceable seam.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping

from argus.prospective.freezing import canonical_bytes, sha256  # noqa: F401 (re-exported)
from argus.source_intelligence.models import digest_id, require_aware, require_sha256  # noqa: F401

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
    """Check that a value is ordinary JSON: string keys, finite numbers,
    well-formed Unicode, bounded nesting.

    A value refused here cannot be written by one serializer and then read
    back differently, or not at all, by another.
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


def parse_external_json(data: bytes | str, *, label: str = "external JSON"
                        ) -> tuple[Any, bool]:
    """Parse foreign JSON without hiding duplicate-key or Unicode defects.

    A lone surrogate is repaired to U+FFFD and the returned flag says so.
    Ambiguous structure, non-finite numbers, excessive depth, invalid UTF-8 and
    key collisions stay refusals.
    """
    repaired = False

    def clean(value: Any, depth: int = 0) -> Any:
        nonlocal repaired
        if depth > MAX_CANONICAL_DEPTH:
            raise ValueError("external JSON exceeds the maximum nesting depth")
        if isinstance(value, str):
            normalized = value.encode("utf-8", "replace").decode("utf-8")
            repaired |= normalized != value
            return normalized
        if value is None or isinstance(value, (bool, int, float)):
            validate_interchange(value)
            return value
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                normalized_key = clean(key, depth + 1)
                if normalized_key in result:
                    raise ValueError("external JSON keys collide after Unicode repair")
                result[normalized_key] = clean(item, depth + 1)
            return result
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value]
        raise ValueError(f"unsupported external JSON type: {type(value).__name__}")

    try:
        text = data.decode("utf-8", errors="strict") if isinstance(data, bytes) else data
        parsed = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token: {token}")),
        )
        value = clean(parsed)
        validate_interchange(value)
        return value, repaired
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError,
            TypeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc


def canonical_line(value: Any) -> str:
    validate_interchange(value)
    return canonical_bytes(value).decode()
