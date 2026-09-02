"""JSON Schemas for model-proposed analytical candidates.

The shape a provider is constrained to emit is generated from the shape the
human review path enforces — `substrate.CANDIDATE_BINDING_KEYS` and the closed
vocabularies in `contracts` — so the two cannot drift apart. `unresolvable_ids`
additionally checks that a candidate cites only identifiers the request showed
it. Kinds whose binding fields are machine-computed are refused here rather
than misrepresented; see `NOT_MODEL_PROPOSABLE`.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .contracts import (INDICATOR_KINDS, INFLUENCE_KINDS,
                        STAKEHOLDER_CONTEXT_KINDS, VARIANT_RELATIONS)
from .substrate import CANDIDATE_BINDING_KEYS

# Kinds a model may not propose, and why. A proposed digest would bind a
# human's acceptance to a value the proposer invented.
NOT_MODEL_PROPOSABLE: Mapping[str, str] = {
    "impact_path": ("binds `edge_chain`, a machine-computed fingerprint over the "
                    "causal chain; a proposed digest would bind acceptance to a "
                    "value the proposer invented"),
}

_STR = {"type": "string", "minLength": 1}
_IDS = {"type": "array", "items": {"type": "string", "minLength": 1},
        "minItems": 1, "uniqueItems": True}

def _enum(values: Iterable[str]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}

# Field shapes by field name; _KIND_FIELD_SHAPES narrows them per kind.
_FIELD_SHAPES: Mapping[str, Any] = {
    "supporting_claim_ids": _IDS, "claim_ids": _IDS, "forecast_ids": _IDS,
    "claims": _IDS,
    # strictly inside (0,1): the contract rejects certainty, so the emitted
    # shape must reject it too
    "probability": {"type": "number", "exclusiveMinimum": 0.0,
                    "exclusiveMaximum": 1.0},
    "horizon_time": {"type": "string",
                     "description": "RFC3339 timestamp with an explicit UTC offset"},
}
_KIND_FIELD_SHAPES: Mapping[tuple[str, str], Any] = {
    ("narrative_variant", "relation"): _enum(VARIANT_RELATIONS),
    ("stakeholder_assessment", "context_kind"): _enum(STAKEHOLDER_CONTEXT_KINDS),
    ("influence_assertion", "kind"): _enum(INFLUENCE_KINDS),
    ("forecast_indicator", "kind"): _enum(INDICATOR_KINDS),
}

# Fields that name an existing record rather than carrying free text.
_ID_FIELD = re.compile(r"(^|_)(id|ids)$")
# Stakeholder contexts whose context_id names nothing in the store.
FREE_LABEL_CONTEXT_KINDS = frozenset({"MISSION", "ISSUE"})


def _is_id_key(key: str) -> bool:
    return bool(_ID_FIELD.search(key)) or key == "claims"


def proposable_kinds() -> tuple[str, ...]:
    """Analytical kinds a model may propose, in declaration order."""
    return tuple(k for k in CANDIDATE_BINDING_KEYS if k not in NOT_MODEL_PROPOSABLE)


def refusal_reason(target_kind: str) -> str | None:
    """Why this kind is not model-proposable, or None if it is."""
    if target_kind in NOT_MODEL_PROPOSABLE:
        return NOT_MODEL_PROPOSABLE[target_kind]
    if target_kind not in CANDIDATE_BINDING_KEYS:
        return f"{target_kind} is not a model-proposable analytical kind"
    return None


def field_shape(target_kind: str, field: str) -> dict[str, Any]:
    shape = _KIND_FIELD_SHAPES.get((target_kind, field)) \
        or _FIELD_SHAPES.get(field) or _STR
    return dict(shape)


def candidate_schema(target_kind: str) -> dict[str, Any]:
    """The JSON Schema a provider must satisfy for this analytical kind."""
    reason = refusal_reason(target_kind)
    if reason is not None:
        raise ValueError(f"no candidate schema for {target_kind}: {reason}")
    keys = CANDIDATE_BINDING_KEYS[target_kind]
    return {
        "type": "object",
        "properties": {key: field_shape(target_kind, key) for key in keys},
        "required": list(keys),
        # The human reviews exactly the binding content, so extra fields
        # would arrive unreviewed.
        "additionalProperties": False,
    }


def id_fields(target_kind: str) -> tuple[str, ...]:
    """Binding fields of this kind that name existing records."""
    return tuple(k for k in CANDIDATE_BINDING_KEYS.get(target_kind, ())
                 if _is_id_key(k))


def available_ids(payload: Any, *, _depth: int = 0) -> frozenset[str]:
    """Every identifier-shaped string reachable in a request payload.

    This is what the provider was shown, and therefore the only thing it is
    allowed to cite back. A string only counts when it sits under an
    identifier-shaped key, directly or as an element of that key's list; free
    text the payload happened to carry is not an identifier the provider may
    cite. The search is structural, so a record nested anywhere still counts.
    """
    if _depth > 64:
        return frozenset()
    found: set[str] = set()
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(key, str) and _is_id_key(key):
                if isinstance(value, str):
                    if value:
                        found.add(value)
                elif isinstance(value, (list, tuple)):
                    found |= {v for v in value if isinstance(v, str) and v}
            found |= available_ids(value, _depth=_depth + 1)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found |= available_ids(item, _depth=_depth + 1)
    return frozenset(found)


def unresolvable_ids(target_kind: str, content: Mapping[str, Any],
                     shown: frozenset[str]) -> tuple[str, ...]:
    """Identifiers the candidate cites that it was never shown."""
    bad: list[str] = []
    for field in id_fields(target_kind):
        if field == "context_id" and content.get("context_kind") in FREE_LABEL_CONTEXT_KINDS:
            continue  # a mission or issue context is a label, not a record
        value = content.get(field)
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for item in candidates:
            if isinstance(item, str) and item and item not in shown:
                bad.append(f"{field}={item}")
    return tuple(sorted(bad))
