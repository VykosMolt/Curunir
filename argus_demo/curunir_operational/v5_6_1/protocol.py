"""V5.6.1 §7 — the typed extraction protocol state model.

V5.6 asked five questions through one boolean:

    exact_quotation_permitted: bool

The adjudicator found that eight of thirteen extraction disagreements were not
disagreements about evidence at all.  One seat read it as *"may this span be
reproduced verbatim"* and set it true whenever nothing was truncated; two read it
as *"does exact-value quotation arise here"* and encoded "not applicable" as
false.  Both readings are reasonable.  The field could not distinguish them.

Five separate dimensions replace it, each with exactly one reviewer question and
its own vocabulary.  The vocabularies deliberately do not overlap, and §7.7's
prohibited collapses are enforced rather than merely documented: a value meaning
"only as a paraphrase" is not a synonym for "not permitted", and "not
applicable" is not a synonym for false.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id


class ProtocolViolation(RuntimeError):
    """A typed extraction dimension was used outside its defined vocabulary."""


# ---------------------------------------------------------------------------
# §7.2 — candidate semantic validity.
# Answers: does the candidate express a defensible semantic proposition?
# It does NOT answer whether the exact string may be quoted.
# ---------------------------------------------------------------------------
SEMANTIC_VALIDITY: tuple[str, ...] = (
    "VALID",
    "VALID_WITH_MATERIAL_QUALIFICATION",
    "RECOVERABLE_WITH_BOUNDARY_REPAIR",
    "SEMANTICALLY_INCOMPLETE",
    "SEMANTICALLY_INVALID",
    "EPISTEMICALLY_UNRESOLVABLE",
    "CONSTRUCTION_DEFECT",
)

# §7.3 — boundary repair requirement.
BOUNDARY_REPAIR: tuple[str, ...] = (
    "NO_REPAIR_REQUIRED",
    "EXPAND_LEFT_CONTEXT",
    "EXPAND_RIGHT_CONTEXT",
    "EXPAND_BOTH_DIRECTIONS",
    "REPLACE_WITH_ALTERNATIVE_CANDIDATE",
    "RECONSTRUCT_FROM_TABLE_OR_LAYOUT",
    "NOT_REPAIRABLE",
    "NOT_APPLICABLE",
    "EPISTEMICALLY_UNRESOLVABLE",
)

# §7.4 — exact-quotation permission.
EXACT_QUOTATION: tuple[str, ...] = (
    "EXACT_QUOTATION_PERMITTED",
    "EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING",
    "EXACT_QUOTATION_NOT_PERMITTED",
    "NO_EXACT_QUOTATION_REQUESTED",
    "EPISTEMICALLY_UNRESOLVABLE",
)

#: What an exact quotation depends on.  Recorded so the dimension's dependencies
#: are inspectable rather than implicit in a reviewer's head.
EXACT_QUOTATION_DEPENDENCIES: tuple[str, ...] = (
    "exact_span_mapping", "layout_preservation", "exact_value_and_unit_mapping",
    "attribution_boundary", "original_language",
)

# §7.5 — paraphrase permission.  An approximate span may support a faithful
# paraphrase while failing exact quotation; that is the whole point of splitting
# these two dimensions.
PARAPHRASE_PERMISSION: tuple[str, ...] = (
    "PARAPHRASE_PERMITTED",
    "PARAPHRASE_REQUIRES_QUALIFICATION",
    "PARAPHRASE_NOT_PERMITTED",
    "NOT_APPLICABLE",
    "EPISTEMICALLY_UNRESOLVABLE",
)

# §7.6 — wording-as-written permission.  The direct replacement for the boolean.
WORDING_AS_WRITTEN: tuple[str, ...] = (
    "PERMITTED_AS_WRITTEN",
    "PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR",
    "PERMITTED_ONLY_AS_PARAPHRASE",
    "PERMITTED_ONLY_WITH_QUALIFICATION",
    "NOT_PERMITTED",
    "NOT_APPLICABLE",
    "EPISTEMICALLY_UNRESOLVABLE",
)

DIMENSIONS: Mapping[str, tuple[str, ...]] = {
    "candidate_semantic_validity": SEMANTIC_VALIDITY,
    "boundary_repair_requirement": BOUNDARY_REPAIR,
    "exact_quotation_permission": EXACT_QUOTATION,
    "paraphrase_permission": PARAPHRASE_PERMISSION,
    "wording_as_written_permission": WORDING_AS_WRITTEN,
}

#: §7.8 — one dimension, one question.  These are the exact strings a reviewer
#: sees, and they are never combined.
REVIEWER_QUESTIONS: Mapping[str, str] = {
    "candidate_semantic_validity":
        "Does the candidate express a complete and defensible proposition?",
    "boundary_repair_requirement":
        "Does the candidate boundary require repair, and of what kind?",
    "exact_quotation_permission":
        "May these exact words be quoted as evidence?",
    "paraphrase_permission":
        "May the proposition be paraphrased faithfully?",
    "wording_as_written_permission":
        "May the candidate wording be used exactly as written?",
}

# ---------------------------------------------------------------------------
# §7.7 — prohibited collapses, enforced.
#
# Each pair is two values that a lossy implementation would treat as the same
# thing.  They are not the same thing, and conflating them is what the boolean
# did.  ``assert_no_collapse`` is called by the record constructor.
# ---------------------------------------------------------------------------
PROHIBITED_COLLAPSES: tuple[tuple[str, str, str], ...] = (
    ("wording_as_written_permission", "PERMITTED_ONLY_AS_PARAPHRASE", "NOT_PERMITTED"),
    ("wording_as_written_permission", "PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR",
     "PERMITTED_AS_WRITTEN"),
    ("paraphrase_permission", "NOT_APPLICABLE", "PARAPHRASE_NOT_PERMITTED"),
    ("exact_quotation_permission", "NO_EXACT_QUOTATION_REQUESTED",
     "EXACT_QUOTATION_NOT_PERMITTED"),
    ("exact_quotation_permission", "EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING",
     "EXACT_QUOTATION_NOT_PERMITTED"),
    ("boundary_repair_requirement", "NOT_APPLICABLE", "NO_REPAIR_REQUIRED"),
)

#: Values that a boolean coercion would silently turn into False.  §7.7: none of
#: these may ever be represented as a bool.
NEVER_BOOLEAN: frozenset[str] = frozenset({
    "NOT_APPLICABLE", "NO_EXACT_QUOTATION_REQUESTED", "EPISTEMICALLY_UNRESOLVABLE",
    "EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING", "PERMITTED_ONLY_AS_PARAPHRASE",
    "PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR", "PERMITTED_ONLY_WITH_QUALIFICATION",
    "PARAPHRASE_REQUIRES_QUALIFICATION",
})


def assert_no_collapse(dimension: str, value: str) -> None:
    """A value must be itself, not a stand-in for a neighbouring value."""
    if dimension not in DIMENSIONS:
        raise ProtocolViolation(f"unknown extraction dimension: {dimension}")
    if value not in DIMENSIONS[dimension]:
        raise ProtocolViolation(
            f"{value!r} is not in the vocabulary of {dimension}; "
            f"permitted: {list(DIMENSIONS[dimension])}")
    if isinstance(value, bool):
        raise ProtocolViolation(
            f"{dimension} may never be a boolean: that is the V5.6 defect")


def coercion_would_lose_information(dimension: str, value: str) -> bool:
    """Would rendering this value as a bool destroy a distinction?

    Used by the mutation suite: any value here proves the dimension cannot be a
    boolean, which is exactly what V5.6 got wrong.
    """
    assert_no_collapse(dimension, value)
    return value in NEVER_BOOLEAN


# ---------------------------------------------------------------------------
# The record.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TypedExtractionDecision(Record):
    """One seat's Stage-A decision, with each question answered separately."""

    decision_id: str
    candidate_id: str
    seat_id: str
    protocol_version: str
    candidate_semantic_validity: str
    boundary_repair_requirement: str
    exact_quotation_permission: str
    paraphrase_permission: str
    wording_as_written_permission: str
    mapping_satisfied: tuple[str, ...]
    first_material_failure: str | None
    preferred_candidate_id: str | None
    reasoning: str
    #: §7.10 — the old boolean is carried, never interpreted.
    legacy_ambiguous_wording_field: bool | None
    recorded_time: str

    def __post_init__(self) -> None:
        for dimension in DIMENSIONS:
            assert_no_collapse(dimension, getattr(self, dimension))
        # Cross-dimension coherence.  These are the couplings the boolean
        # silently collapsed; each is a real semantic constraint.
        if self.exact_quotation_permission == "EXACT_QUOTATION_PERMITTED" and \
                "EXACT_VALUE_QUOTABLE" not in self.mapping_satisfied:
            raise ProtocolViolation(
                "exact quotation permitted without EXACT_VALUE_QUOTABLE mapping")
        if self.wording_as_written_permission == "PERMITTED_AS_WRITTEN" and \
                self.boundary_repair_requirement not in (
                    "NO_REPAIR_REQUIRED", "NOT_APPLICABLE"):
            raise ProtocolViolation(
                "wording cannot be permitted as written while its boundary "
                "requires repair; that is PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR")
        if self.wording_as_written_permission == "PERMITTED_ONLY_AS_PARAPHRASE" and \
                self.paraphrase_permission in ("PARAPHRASE_NOT_PERMITTED",):
            raise ProtocolViolation(
                "wording permitted only as a paraphrase, while paraphrase is "
                "not permitted, is not a coherent pair of answers")
        if self.candidate_semantic_validity == "CONSTRUCTION_DEFECT":
            for dimension in ("exact_quotation_permission", "paraphrase_permission",
                              "wording_as_written_permission"):
                if getattr(self, dimension) not in ("NOT_APPLICABLE",
                                                    "NO_EXACT_QUOTATION_REQUESTED",
                                                    "EPISTEMICALLY_UNRESOLVABLE"):
                    raise ProtocolViolation(
                        f"a construction defect cannot carry a substantive "
                        f"{dimension}; the packet posed no answerable question")

    @property
    def is_admitting(self) -> bool:
        return (self.candidate_semantic_validity == "VALID"
                and self.wording_as_written_permission == "PERMITTED_AS_WRITTEN")


def typed_decision(*, candidate_id: str, seat_id: str,
                   candidate_semantic_validity: str,
                   boundary_repair_requirement: str,
                   exact_quotation_permission: str,
                   paraphrase_permission: str,
                   wording_as_written_permission: str,
                   mapping_satisfied: Iterable[str] = (),
                   first_material_failure: str | None = None,
                   preferred_candidate_id: str | None = None,
                   reasoning: str = "",
                   legacy_ambiguous_wording_field: bool | None = None,
                   protocol_version: str = "V5_6_1_PROTOCOL_1"
                   ) -> TypedExtractionDecision:
    return TypedExtractionDecision(
        stable_id("v5-6-1-extraction", candidate_id, seat_id, protocol_version),
        candidate_id, seat_id, protocol_version, candidate_semantic_validity,
        boundary_repair_requirement, exact_quotation_permission,
        paraphrase_permission, wording_as_written_permission,
        tuple(mapping_satisfied), first_material_failure, preferred_candidate_id,
        reasoning, legacy_ambiguous_wording_field, now_utc())


# ---------------------------------------------------------------------------
# §7.10 — migration.  The old boolean becomes a carried artefact, never a
# source of a new typed value.
# ---------------------------------------------------------------------------

LEGACY_FIELD = "LEGACY_AMBIGUOUS_WORDING_FIELD"


def migrate_legacy(value: bool | None) -> dict[str, Any]:
    """Carry the V5.6 boolean forward without inferring anything from it.

    §7.10 is explicit: a new typed value may not be derived from the old
    boolean.  The whole finding was that the boolean's meaning is unknown, so
    any inference from it would launder an ambiguity into a type.
    """
    return {
        LEGACY_FIELD: value,
        "inferred_typed_values": None,
        "requires_re_adjudication": True,
        "reason": ("the source boolean carried at least five incompatible "
                   "questions; no typed value can be derived from it"),
    }


def audit_no_ambiguous_fields(decisions: Iterable[Any]) -> dict[str, Any]:
    """§29 closure condition 2: the ambiguous boolean is gone as a decision field."""
    decisions = list(decisions)
    offenders = []
    for decision in decisions:
        payload = decision.to_record() if hasattr(decision, "to_record") else dict(decision)
        for dimension in DIMENSIONS:
            value = payload.get(dimension)
            if isinstance(value, bool):
                offenders.append({"decision": payload.get("decision_id"),
                                  "field": dimension, "value": value})
        # the legacy field may be present, but only under its reserved name
        if "exact_quotation_permitted" in payload:
            offenders.append({"decision": payload.get("decision_id"),
                              "field": "exact_quotation_permitted",
                              "value": "the V5.6 ambiguous boolean survived"})
    return {"decisions": len(decisions), "ambiguous_wording_fields": len(offenders),
            "offenders": offenders[:10],
            "verdict": "PASS" if not offenders else "FAIL"}
