"""V5.7 §2 — comparing decisions by their whole payload, not their headline.

Three separate agents, working independently, hit the same wall in V5.6.1 and
V5.7 Gate A: the outcome comparator keyed on ``(support_class, disposition)``
for support and ``(candidate_semantic_validity, wording_as_written_permission)``
for extraction, so a decision that *added a required qualification* or *withdrew
exact quotation* while leaving the headline intact compared as identical.  One
adjudicator put it exactly: a genuine qualification "is currently only
expressible if the class or disposition also moves."

That is defect P2's last consumption site.  A decision is not its label.

This module compares the full canonical payload and distinguishes:

    IDENTICAL                      nothing differs
    NONMATERIAL_WORDING_DIFFERENCE only prose or ordering differs
    MATERIAL_COMPONENT_DIFFERENCE  a component differs; headline may not
    TOP_LEVEL_CLASS_DIFFERENCE     the headline itself differs
    PROTOCOL_INCOMPARABLE          the two speak different vocabularies

``MATERIAL_COMPONENT_DIFFERENCE`` is the case that did not previously exist, and
it is routable exactly like a class difference.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id

PAYLOAD_VERSION = "V5_7_CANONICAL_SEMANTIC_PAYLOAD_1"


class ComparisonViolation(RuntimeError):
    """A comparison was attempted between things that cannot be compared."""


COMPARISON_RESULTS: tuple[str, ...] = (
    "IDENTICAL", "NONMATERIAL_WORDING_DIFFERENCE", "MATERIAL_COMPONENT_DIFFERENCE",
    "TOP_LEVEL_CLASS_DIFFERENCE", "PROTOCOL_INCOMPARABLE",
)

# ---------------------------------------------------------------------------
# §2.1 — what is compared
# ---------------------------------------------------------------------------

EXTRACTION_FIELDS: tuple[str, ...] = (
    "candidate_semantic_validity", "boundary_repair_requirement",
    "evidence_binding_state", "exact_quotation_permission",
    "paraphrase_permission", "wording_as_written_permission", "terminal_state",
    "semantic_actor", "attribution_source", "quoted_speaker",
    "polarity", "modality", "lifecycle_state", "temporal_scope",
    "mapping_satisfied", "first_material_failure",
)

SUPPORT_FIELDS: tuple[str, ...] = (
    "addresses_proposition", "entity_alignment", "predicate_alignment",
    "object_alignment", "scope_alignment", "time_alignment",
    "polarity_alignment", "modality_alignment", "lifecycle_alignment",
    "attribution_alignment", "dependence_limitations", "counterevidence_state",
    "support_completeness", "first_material_failure", "support_class",
    "required_qualifications", "permitted_wording", "publication_disposition",
)

#: The headline pair each unit type used to be compared by — kept ONLY so the
#: comparator can report what the old keying would have said.
TOP_LEVEL_FIELDS: Mapping[str, tuple[str, ...]] = {
    "EXTRACTION": ("candidate_semantic_validity", "wording_as_written_permission"),
    "SUPPORT": ("support_class", "publication_disposition"),
}

#: Field names V5.6.x wrote differently.  A rename is a rename, not a difference.
ALIASES: Mapping[str, str] = {
    "entity_alignment": "actor_alignment",
    "permitted_wording": "wording_permission",
    "publication_disposition": "disposition",
}

#: §2.1 — set-valued fields, where order carries no meaning.
SET_VALUED = frozenset({
    "required_qualifications", "mapping_satisfied", "dependence_limitations",
})

#: Fields that are prose or bookkeeping, never a semantic difference.
NONMATERIAL_FIELDS = frozenset({
    "reasoning", "unit_id", "unit_type", "recorded_time", "seat",
    "preferred_candidate_id",
})

#: §2.1 — the only normalisations permitted, each because the two spellings
#: assert the same thing.  A qualification set is NEVER normalised: dropping a
#: qualification changes what may be published.
_ALIGNMENT_EQUIVALENCE: Mapping[str, str] = {
    "UNRESOLVED": "NO_READING",
    "NOT_APPLICABLE": "NO_READING",
    "ALIGNED": "ALIGNED",
    "MISALIGNED": "MISALIGNED",
}

_ALIGNMENT_FIELDS = frozenset({
    "entity_alignment", "predicate_alignment", "object_alignment",
    "scope_alignment", "time_alignment", "polarity_alignment",
    "modality_alignment", "lifecycle_alignment", "attribution_alignment",
})

#: §2.3 — qualification dimensions whose presence or absence changes what a
#: report may say.  Listed explicitly so a future normalisation cannot quietly
#: swallow one.
TRUTH_BEARING_QUALIFICATIONS: tuple[str, ...] = (
    "ATTRIBUTION_REQUIRED", "VENDOR_REPORTED", "PRELIMINARY", "PILOT_ONLY",
    "EXERCISE_ONLY", "PLAN_OR_INTENTION_ONLY", "ANNOUNCEMENT_ONLY",
    "LIMITED_DEPLOYMENT", "PARTIAL_SCOPE", "GEOGRAPHICALLY_BOUNDED",
    "TEMPORALLY_BOUNDED", "DEPENDENCE_UNRESOLVED", "COUNTEREVIDENCE_PRESENT",
    "DISPUTED", "INFERENCE_MARKING_REQUIRED", "UNCERTAINTY_REQUIRED",
)


def canonical_payload(decision: Mapping[str, Any], unit_type: str
                      ) -> dict[str, Any]:
    """Resolve aliases and set semantics; change nothing else."""
    if unit_type not in TOP_LEVEL_FIELDS:
        raise ComparisonViolation(f"unknown unit type: {unit_type!r}")
    fields = EXTRACTION_FIELDS if unit_type == "EXTRACTION" else SUPPORT_FIELDS
    out: dict[str, Any] = {}
    for name in fields:
        value = decision.get(name)
        if value is None and name in ALIASES:
            value = decision.get(ALIASES[name])
        if name in SET_VALUED:
            value = frozenset(str(v) for v in (value or ()))
        out[name] = value
    return out


def _comparable(value: Any, field: str, *, normalise_alignment: bool) -> Any:
    if field in SET_VALUED:
        return frozenset(value or ())
    if normalise_alignment and field in _ALIGNMENT_FIELDS:
        return _ALIGNMENT_EQUIVALENCE.get(value, value)
    return value


@dataclass(frozen=True)
class PayloadComparison(Record):
    """One comparison, with every differing component named."""

    comparison_id: str
    unit_id: str
    unit_type: str
    result: str
    top_level_identical: bool
    differing_components: tuple[str, ...]
    component_detail: Mapping[str, Any]
    normalised_away: tuple[str, ...]
    would_old_comparator_see_it: bool
    payload_version: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.result not in COMPARISON_RESULTS:
            raise ComparisonViolation(f"unknown comparison result: {self.result!r}")
        if self.result == "MATERIAL_COMPONENT_DIFFERENCE" and \
                not self.differing_components:
            raise ComparisonViolation(
                "a material component difference must name the components")

    @property
    def is_material(self) -> bool:
        return self.result in ("MATERIAL_COMPONENT_DIFFERENCE",
                               "TOP_LEVEL_CLASS_DIFFERENCE")

    @property
    def invisible_to_the_old_comparator(self) -> bool:
        """The case this module exists for: material, yet headline-identical."""
        return self.is_material and self.top_level_identical


def compare(left: Mapping[str, Any], right: Mapping[str, Any], *,
            unit_type: str, unit_id: str = "",
            normalise_alignment: bool = True) -> PayloadComparison:
    """Compare two decisions by their whole canonical payload."""
    if unit_type not in TOP_LEVEL_FIELDS:
        raise ComparisonViolation(f"unknown unit type: {unit_type!r}")
    left_payload = canonical_payload(left, unit_type)
    right_payload = canonical_payload(right, unit_type)

    # §2.2 PROTOCOL_INCOMPARABLE: one side carries none of the vocabulary.
    left_present = {k for k, v in left_payload.items() if v not in (None, frozenset())}
    right_present = {k for k, v in right_payload.items() if v not in (None, frozenset())}
    if not left_present or not right_present:
        return PayloadComparison(
            stable_id("v5-7-comparison", unit_id, "incomparable"), unit_id,
            unit_type, "PROTOCOL_INCOMPARABLE", False, (), {}, (), False,
            PAYLOAD_VERSION, now_utc())

    top = TOP_LEVEL_FIELDS[unit_type]
    top_identical = all(left_payload.get(f) == right_payload.get(f) for f in top)

    differing, detail, normalised = [], {}, []
    fields = EXTRACTION_FIELDS if unit_type == "EXTRACTION" else SUPPORT_FIELDS
    for name in fields:
        raw_left, raw_right = left_payload.get(name), right_payload.get(name)
        if raw_left == raw_right:
            continue
        cooked_left = _comparable(raw_left, name, normalise_alignment=normalise_alignment)
        cooked_right = _comparable(raw_right, name, normalise_alignment=normalise_alignment)
        if cooked_left == cooked_right:
            # Absorbed by a declared equivalence — recorded, never hidden.
            normalised.append(name)
            continue
        differing.append(name)
        detail[name] = {
            "left": sorted(raw_left) if isinstance(raw_left, frozenset) else raw_left,
            "right": sorted(raw_right) if isinstance(raw_right, frozenset) else raw_right,
        }
        if name in SET_VALUED:
            dropped = sorted((raw_left or frozenset()) - (raw_right or frozenset()))
            added = sorted((raw_right or frozenset()) - (raw_left or frozenset()))
            detail[name]["dropped"] = dropped
            detail[name]["added"] = added
            detail[name]["truth_bearing"] = sorted(
                set(dropped + added) & set(TRUTH_BEARING_QUALIFICATIONS))

    if not differing:
        result = "IDENTICAL" if not normalised else "NONMATERIAL_WORDING_DIFFERENCE"
    elif not top_identical:
        result = "TOP_LEVEL_CLASS_DIFFERENCE"
    else:
        result = "MATERIAL_COMPONENT_DIFFERENCE"

    return PayloadComparison(
        stable_id("v5-7-comparison", unit_id, result, "|".join(sorted(differing))),
        unit_id, unit_type, result, top_identical, tuple(sorted(differing)),
        detail, tuple(sorted(normalised)),
        not top_identical,  # the old comparator saw only headline moves
        PAYLOAD_VERSION, now_utc())


def route_material(comparisons: Iterable[PayloadComparison]) -> list[PayloadComparison]:
    """Everything a full-payload comparator must send to an adjudicator."""
    return [c for c in comparisons if c.is_material]


def audit_comparator(comparisons: Iterable[PayloadComparison]) -> dict[str, Any]:
    """§2.4 — what the old keying would have missed."""
    comparisons = list(comparisons)
    invisible = [c for c in comparisons if c.invisible_to_the_old_comparator]
    by_result: dict[str, int] = {}
    for c in comparisons:
        by_result[c.result] = by_result.get(c.result, 0) + 1
    qualification_only = [
        c for c in invisible
        if set(c.differing_components) <= {"required_qualifications",
                                           "permitted_wording"}]
    return {
        "records_compared": len(comparisons),
        "by_result": by_result,
        "top_level_only_agreements": sum(1 for c in comparisons if c.top_level_identical),
        "material_component_differences": sum(
            1 for c in comparisons if c.result == "MATERIAL_COMPONENT_DIFFERENCE"),
        "component_differences_previously_invisible": len(invisible),
        "qualification_only_differences": len(qualification_only),
        "payload_version": PAYLOAD_VERSION,
        "examples": [{"unit": c.unit_id, "components": list(c.differing_components)}
                     for c in invisible[:10]],
        "verdict": "PASS",
    }


__all__ = [
    "PAYLOAD_VERSION", "ComparisonViolation", "COMPARISON_RESULTS",
    "EXTRACTION_FIELDS", "SUPPORT_FIELDS", "TOP_LEVEL_FIELDS", "ALIASES",
    "SET_VALUED", "TRUTH_BEARING_QUALIFICATIONS", "canonical_payload",
    "PayloadComparison", "compare", "route_material", "audit_comparator",
]
