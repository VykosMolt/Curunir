"""Structured-output schemas for model-proposed analytical candidates.

One source of truth. ``substrate.CANDIDATE_BINDING_KEYS`` already declares, per
analytical kind, the fields a candidate must bind before ``record_candidate``
will accept it, and ``contracts`` already declares the closed vocabularies those
fields draw from. This module turns both into a JSON Schema, so the shape a
provider is *constrained* to emit is generated from the shape the human review
path *enforces* — they cannot drift apart, because there is only one of them.

Two properties this buys:

* A candidate missing a binding field is close to unproducible, and
  ``record_candidate`` still refuses it if a provider emits one anyway.
* A candidate may only cite identifiers it was shown. ``unresolvable_ids``
  checks emitted identifiers against the ids present in the request payload;
  the backends treat a violation as a provider error, so a hallucinated claim
  id is retained as an INVALID inference rather than becoming a plausible
  proposal a human has to catch by eye.

Kinds whose binding fields are machine-computed are refused here rather than
misrepresented — see ``NOT_MODEL_PROPOSABLE``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .contracts import (INDICATOR_KINDS, INFLUENCE_KINDS,
                        STAKEHOLDER_CONTEXT_KINDS, VARIANT_RELATIONS)
from .substrate import CANDIDATE_BINDING_KEYS

# `impact_path` binds `edge_chain`, which is impact.edge_chain_fingerprint():
# an ordered digest over each edge's content. A language model cannot compute
# it, and inviting one to emit the field would invite a fabricated digest that
# binds a human's acceptance to nothing. The path stays machine-built.
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

# Field shapes, keyed by (kind, field) where a kind narrows it, else by field.
_FIELD_SHAPES: Mapping[str, Any] = {
    "supporting_claim_ids": _IDS, "claim_ids": _IDS, "forecast_ids": _IDS,
    "claims": _IDS,
    "probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
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
        # A provider may not invent fields. The human reviews exactly the
        # binding content; anything else would be unreviewed cargo.
        "additionalProperties": False,
    }


def id_fields(target_kind: str) -> tuple[str, ...]:
    """Binding fields of this kind that name existing records."""
    return tuple(k for k in CANDIDATE_BINDING_KEYS.get(target_kind, ())
                 if _ID_FIELD.search(k) or k == "claims")


def available_ids(payload: Any, *, _depth: int = 0) -> frozenset[str]:
    """Every identifier-shaped string reachable in a request payload.

    This is what the provider was shown, and therefore the only thing it is
    allowed to cite back. Collected structurally rather than by field name so a
    record nested anywhere in the payload still counts as shown.
    """
    if _depth > 64:
        return frozenset()
    found: set[str] = set()
    if isinstance(payload, str):
        if payload:
            found.add(payload)
    elif isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(key, str) and _ID_FIELD.search(key) and isinstance(value, str):
                found.add(value)
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
        value = content.get(field)
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for item in candidates:
            if isinstance(item, str) and item and item not in shown:
                bad.append(f"{field}={item}")
    return tuple(sorted(bad))
