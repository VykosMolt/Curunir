"""V5.6.1 §9–§12 — disagreement routing, the repaired reference, and lineage.

V5.6's routing could not see the repaired answer shape: it knew one boolean
where there are now five typed dimensions, and it knew ``entity_alignment``
where there is now a typed actor.  So routing is rebuilt here, around one
distinction that decides everything downstream:

    A difference is **material** when it changes a truth condition or a
    permitted output.  Everything else is bookkeeping, and bookkeeping routed to
    an adjudicator is noise that buries the real disagreements.

V5.6 learned that the hard way — its first routing pass sent 114 of 149 records
to adjudication because two seats had written ``UNRESOLVED`` and
``NOT_APPLICABLE`` on a dimension that did not apply.  Neither answer asserts
anything about alignment; the difference is about how a seat spells "no reading
here".  Every such normalisation is declared, named and counted below, because a
normalisation rule is exactly where a real disagreement can be made to vanish.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from ..v5_6 import schema as S
from . import protocol as P

REFERENCE_NAME = "MODEL_PANEL_ADJUDICATED_REFERENCE_V5_6_1"

#: §22 — names this reference may never be given.  It is a panel of models.
PROHIBITED_NAMES = frozenset({
    "HUMAN_GOLD_STANDARD", "EXPERT_GOLD", "OBJECTIVE_TRUTH",
    "HUMAN_VALIDATED_REFERENCE", "GROUND_TRUTH",
})


class ReferenceViolation(RuntimeError):
    """A reference record was assembled in a way that loses what it must keep."""


AGREEMENT_PATTERNS: tuple[str, ...] = (
    "UNANIMOUS", "MAJORITY_TWO_ONE", "THREE_WAY_SPLIT",
)

ADJUDICATION_OUTCOMES: tuple[str, ...] = (
    "AFFIRM_MAJORITY", "AFFIRM_DISSENT", "NARROW_DECISION", "QUALIFY_DECISION",
    "EPISTEMICALLY_UNRESOLVABLE", "PACKET_CONSTRUCTION_DEFECT", "PROTOCOL_DEFECT",
)

DEFECT_OUTCOMES = frozenset({"PACKET_CONSTRUCTION_DEFECT", "PROTOCOL_DEFECT"})

LINEAGE_DISPOSITIONS: tuple[str, ...] = (
    "REUSED_DEPENDENCY_PROVEN", "REGENERATED_AFFIRMED", "REGENERATED_REVERSED",
    "REGENERATED_NARROWED", "REGENERATED_QUALIFIED",
    "REGENERATED_SPLIT_FACT_METACLAIM", "REGENERATED_IDENTITY_CHANGED",
    "INVALIDATED_PROTOCOL_DEFECT", "INVALIDATED_CORPUS_DEFECT",
    "INVALIDATED_INTEGRITY_DEFECT", "REMAINS_EPISTEMICALLY_UNRESOLVABLE",
)


# ===========================================================================
# §9.1 — what is material
# ===========================================================================

#: Coarse families for the typed extraction dimensions.  Two seats inside one
#: family have chosen answers with the same downstream consequence; two seats in
#: different families have not.
_VALIDITY_FAMILY: Mapping[str, str] = {
    "VALID": "ADMISSIBLE",
    "VALID_WITH_MATERIAL_QUALIFICATION": "ADMISSIBLE_QUALIFIED",
    "RECOVERABLE_WITH_BOUNDARY_REPAIR": "RECOVERABLE",
    "SEMANTICALLY_INCOMPLETE": "NOT_ADMISSIBLE",
    "SEMANTICALLY_INVALID": "NOT_ADMISSIBLE",
    "EPISTEMICALLY_UNRESOLVABLE": "UNRESOLVABLE",
    "CONSTRUCTION_DEFECT": "DEFECT",
}

#: §9.2 — the one extraction normalisation.  Which side to expand is a repair
#: instruction, not a truth condition: every direction says the same thing about
#: the span, namely that a bounded expansion completes it.
_REPAIR_FAMILY: Mapping[str, str] = {
    "NO_REPAIR_REQUIRED": "NO_REPAIR",
    "NOT_APPLICABLE": "NO_REPAIR",
    "EXPAND_LEFT_CONTEXT": "BOUNDED_EXPANSION",
    "EXPAND_RIGHT_CONTEXT": "BOUNDED_EXPANSION",
    "EXPAND_BOTH_DIRECTIONS": "BOUNDED_EXPANSION",
    "REPLACE_WITH_ALTERNATIVE_CANDIDATE": "REPLACE_CANDIDATE",
    "RECONSTRUCT_FROM_TABLE_OR_LAYOUT": "RECONSTRUCT",
    "NOT_REPAIRABLE": "NOT_REPAIRABLE",
    "EPISTEMICALLY_UNRESOLVABLE": "UNRESOLVABLE",
}

#: §9.2 — the support normalisation.  ``UNRESOLVED`` and ``NOT_APPLICABLE`` both
#: decline to assert alignment; neither claims the dimension aligns or fails.
#: A seat that writes one where another writes the other has not disagreed about
#: the evidence.  ``ALIGNED`` and ``MISALIGNED`` are never normalised away.
_ALIGNMENT_FAMILY: Mapping[str, str] = {
    "ALIGNED": "ALIGNED",
    "MISALIGNED": "MISALIGNED",
    "UNRESOLVED": "NO_READING",
    "NOT_APPLICABLE": "NO_READING",
}

NORMALISATION_RULES: tuple[Mapping[str, str], ...] = (
    {"dimension": "boundary_repair_requirement",
     "rule": "expansion direction is normalised: LEFT, RIGHT and BOTH are one "
             "BOUNDED_EXPANSION family",
     "why_nonmaterial": "which side to widen is a repair instruction; all three "
                        "assert the same thing about the span, that a bounded "
                        "expansion completes it"},
    {"dimension": "alignment fields",
     "rule": "UNRESOLVED and NOT_APPLICABLE are one NO_READING family",
     "why_nonmaterial": "neither asserts that the dimension aligns or fails; the "
                        "difference is how a seat spells the absence of a reading. "
                        "ALIGNED and MISALIGNED are never normalised."},
    {"dimension": "required_qualifications",
     "rule": "compared as a set, not as an ordered list",
     "why_nonmaterial": "the order in which inseparable qualifications are listed "
                        "changes no truth condition"},
    {"dimension": "mapping_satisfied",
     "rule": "compared as a set; only EXACT_VALUE_QUOTABLE and "
             "PROPOSITION_BOUNDARY_DEFENSIBLE are decision-bearing",
     "why_nonmaterial": "the other mapping requirements gate no permitted output "
                        "on their own; the two named ones gate exact quotation "
                        "and boundary defensibility respectively"},
)

#: Decision-bearing mapping requirements: these two gate a permitted output.
_DECISIVE_MAPPING = ("EXACT_VALUE_QUOTABLE", "PROPOSITION_BOUNDARY_DEFENSIBLE")

#: Fields compared verbatim, because the protocol forbids collapsing their
#: values into one another (§7.7).  ``EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING``
#: and ``EXACT_QUOTATION_NOT_PERMITTED`` are a prohibited collapse, so they are
#: never in one family here either.
_EXTRACTION_VERBATIM = ("exact_quotation_permission", "paraphrase_permission",
                        "wording_as_written_permission")

_SUPPORT_VERBATIM = ("addresses_proposition", "support_class", "wording_permission",
                     "disposition", "first_material_failure", "support_completeness")

_ALIGNMENT_FIELDS = ("actor_alignment", "predicate_alignment", "object_alignment",
                     "scope_alignment", "time_alignment", "polarity_alignment",
                     "modality_alignment", "lifecycle_alignment",
                     "attribution_alignment")


def _differs(values: Sequence[Any]) -> bool:
    first = values[0]
    return any(value != first for value in values[1:])


def material_dimensions(decisions: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Every dimension on which these seats materially disagree.

    Empty means the seats agree on everything that changes a truth condition or
    a permitted output — they may still differ in wording, ordering, or in how
    they spelled the absence of a reading.
    """
    if not decisions:
        return ()
    unit_type = decisions[0].get("unit_type")
    found: list[str] = []

    if unit_type == "EXTRACTION":
        if _differs([_VALIDITY_FAMILY.get(d.get("candidate_semantic_validity"))
                     for d in decisions]):
            found.append("candidate_semantic_validity")
        if _differs([_REPAIR_FAMILY.get(d.get("boundary_repair_requirement"))
                     for d in decisions]):
            found.append("boundary_repair_requirement")
        for field in _EXTRACTION_VERBATIM:
            if _differs([d.get(field) for d in decisions]):
                found.append(field)
        for requirement in _DECISIVE_MAPPING:
            if _differs([requirement in tuple(d.get("mapping_satisfied") or ())
                         for d in decisions]):
                found.append(f"mapping::{requirement}")
        if _differs([d.get("first_material_failure") for d in decisions]):
            found.append("first_material_failure")
        return tuple(found)

    if unit_type == "SUPPORT":
        for field in _SUPPORT_VERBATIM:
            if _differs([d.get(field) for d in decisions]):
                found.append(field)
        for field in _ALIGNMENT_FIELDS:
            if _differs([_ALIGNMENT_FAMILY.get(d.get(field)) for d in decisions]):
                found.append(field)
        if _differs([frozenset(d.get("required_qualifications") or ())
                     for d in decisions]):
            found.append("required_qualifications")
        return tuple(found)

    raise ReferenceViolation(f"unknown unit type for routing: {unit_type!r}")


def nonmaterial_normalisations(decisions: Sequence[Mapping[str, Any]]
                               ) -> tuple[Mapping[str, Any], ...]:
    """Differences a normalisation rule absorbed, recorded rather than hidden."""
    if not decisions:
        return ()
    rows: list[Mapping[str, Any]] = []
    unit_type = decisions[0].get("unit_type")
    if unit_type == "EXTRACTION":
        raw = [d.get("boundary_repair_requirement") for d in decisions]
        if _differs(raw) and not _differs([_REPAIR_FAMILY.get(v) for v in raw]):
            rows.append({"dimension": "boundary_repair_requirement", "values": raw,
                         "family": _REPAIR_FAMILY.get(raw[0]),
                         "rule": "expansion direction"})
        for field in ("mapping_satisfied",):
            sets = [frozenset(d.get(field) or ()) for d in decisions]
            decisive = [frozenset(s & set(_DECISIVE_MAPPING)) for s in sets]
            if _differs(sets) and not _differs(decisive):
                rows.append({"dimension": field,
                             "values": [sorted(s) for s in sets],
                             "rule": "non-decisive mapping requirement"})
    elif unit_type == "SUPPORT":
        for field in _ALIGNMENT_FIELDS:
            raw = [d.get(field) for d in decisions]
            if _differs(raw) and not _differs([_ALIGNMENT_FAMILY.get(v) for v in raw]):
                rows.append({"dimension": field, "values": raw,
                             "family": _ALIGNMENT_FAMILY.get(raw[0]),
                             "rule": "UNRESOLVED / NOT_APPLICABLE both decline to "
                                     "assert alignment"})
        raw_q = [list(d.get("required_qualifications") or ()) for d in decisions]
        if _differs(raw_q) and not _differs([frozenset(q) for q in raw_q]):
            rows.append({"dimension": "required_qualifications", "values": raw_q,
                         "rule": "set, not order"})
    return tuple(rows)


def decisive_value(decision: Mapping[str, Any]) -> Any:
    """The one value that summarises a seat's answer, for the agreement pattern."""
    if decision.get("unit_type") == "EXTRACTION":
        return (_VALIDITY_FAMILY.get(decision.get("candidate_semantic_validity")),
                decision.get("wording_as_written_permission"))
    return (decision.get("support_class"), decision.get("disposition"))


def agreement_pattern(decisions: Sequence[Mapping[str, Any]]) -> str:
    values = [decisive_value(d) for d in decisions]
    distinct = len(set(values))
    if distinct == 1:
        return "UNANIMOUS"
    if distinct == 2:
        return "MAJORITY_TWO_ONE"
    return "THREE_WAY_SPLIT"


def dissent(decisions: Sequence[Mapping[str, Any]], seats: Sequence[str]
            ) -> tuple[Mapping[str, Any], ...]:
    """Every seat whose decisive value is not the modal one.

    A unanimous unit has no dissent.  A three-way split has two dissenters and no
    majority, which is why the adjudicator is not permitted to reach for
    ``AFFIRM_MAJORITY`` there.
    """
    values = [decisive_value(d) for d in decisions]
    counts = {value: values.count(value) for value in values}
    top = max(counts.values())
    modal = [value for value, count in counts.items() if count == top]
    if len(modal) != 1:
        return tuple({"seat": seat, "decisive_value": list(value)}
                     for seat, value in zip(seats, values))
    return tuple({"seat": seat, "decisive_value": list(value)}
                 for seat, value in zip(seats, values) if value != modal[0])


# ===========================================================================
# §11 — the reference record
# ===========================================================================

@dataclass(frozen=True)
class ReferenceRecord(Record):
    """One repaired reference record, carrying everything §11.1 requires."""

    reference_id: str
    object_id: str
    object_type: str
    evidence_bundle_id: str
    protocol_version: str
    semantic_identity_hash: str
    evidence_content_hash: str
    packet_hash: str
    primary_decisions: tuple[Mapping[str, Any], ...]
    primary_seats: tuple[str, ...]
    agreement_pattern: str
    material_disagreement_dimensions: tuple[str, ...]
    nonmaterial_normalisations: tuple[Mapping[str, Any], ...]
    adjudication_outcome: str | None
    adjudication_reasoning: str | None
    dissent: tuple[Mapping[str, Any], ...]
    final_decision: Mapping[str, Any] | None
    epistemic_unresolvability_reason: str | None
    partition: str
    v5_6_reference_id: str | None
    defect_ids: tuple[str, ...]
    lineage_disposition: str
    reference_record_hash: str
    recorded_time: str

    def __post_init__(self) -> None:
        if len(self.primary_decisions) != 3:
            raise ReferenceViolation(
                f"{self.object_id}: a reference record carries all three primary "
                f"decisions, got {len(self.primary_decisions)}")
        if self.agreement_pattern not in AGREEMENT_PATTERNS:
            raise ReferenceViolation(f"unknown agreement pattern: {self.agreement_pattern}")
        if self.adjudication_outcome is not None and \
                self.adjudication_outcome not in ADJUDICATION_OUTCOMES:
            raise ReferenceViolation(
                f"unknown adjudication outcome: {self.adjudication_outcome}")
        if self.lineage_disposition not in LINEAGE_DISPOSITIONS:
            raise ReferenceViolation(
                f"unknown lineage disposition: {self.lineage_disposition}")
        # §10.1 — dissent is preserved.  A disagreed unit that records none has
        # lost it, and a reference that quietly loses dissent is the artefact
        # V5.6 was built to prevent.
        if self.agreement_pattern != "UNANIMOUS" and not self.dissent:
            raise ReferenceViolation(
                f"{self.object_id}: agreement is {self.agreement_pattern} and no "
                "dissent is recorded; dissent is never deleted")
        # §10.2 — a defect is not a semantic label.
        if self.adjudication_outcome in DEFECT_OUTCOMES and self.final_decision:
            raise ReferenceViolation(
                f"{self.object_id}: {self.adjudication_outcome} may not carry a "
                "semantic final decision; the packet posed no answerable question")
        if self.material_disagreement_dimensions and self.adjudication_outcome is None:
            raise ReferenceViolation(
                f"{self.object_id}: material disagreement on "
                f"{list(self.material_disagreement_dimensions)} was not routed to "
                "an adjudicator")
        # §11.2 — a final semantic decision that is a support decision may not
        # pair an unpublishable support class with a factual disposition.
        decision = self.final_decision or {}
        if decision.get("unit_type") == "SUPPORT":
            if S.contradicts(decision.get("support_class"), decision.get("disposition")):
                raise ReferenceViolation(
                    f"{self.object_id}: {decision.get('support_class')} with "
                    f"{decision.get('disposition')} is a logically impossible "
                    "support/publication combination")

    @property
    def is_defect(self) -> bool:
        return self.adjudication_outcome in DEFECT_OUTCOMES


def build_reference_record(*, unit: Mapping[str, Any], lineage: Mapping[str, Any],
                           decisions: Sequence[Mapping[str, Any]],
                           seats: Sequence[str],
                           adjudication: Mapping[str, Any] | None = None,
                           reference_record_hash: str = "") -> ReferenceRecord:
    material = material_dimensions(decisions)
    pattern = agreement_pattern(decisions)
    outcome = (adjudication or {}).get("outcome")
    final = (adjudication or {}).get("final_decision")
    if final is None and not material:
        # Agreement on every decision-bearing dimension: the first seat's answer
        # is the reference answer, and the other two say the same thing.
        final = dict(decisions[0])
    if outcome in DEFECT_OUTCOMES:
        final = None
    reference_id = stable_id("v5-6-1-reference", unit["packet_id"])
    return ReferenceRecord(
        reference_id, lineage["object_id"], lineage["object_type"],
        unit.get("evidence_bundle_id", ""), unit["protocol_version"],
        unit["semantic_identity_hash"], unit["evidence_content_hash"],
        unit["review_packet_hash"], tuple(dict(d) for d in decisions), tuple(seats),
        pattern, tuple(material), nonmaterial_normalisations(decisions),
        outcome, (adjudication or {}).get("reasoning"),
        dissent(decisions, seats), final,
        (adjudication or {}).get("epistemic_unresolvability_reason"),
        lineage["partition"], lineage.get("v5_6_reference_id"),
        tuple(lineage.get("defect_ids") or ()),
        (adjudication or {}).get("lineage_disposition") or _default_lineage(
            pattern, outcome, final),
        reference_record_hash, now_utc())


def _default_lineage(pattern: str, outcome: str | None,
                     final: Mapping[str, Any] | None) -> str:
    if outcome == "PROTOCOL_DEFECT":
        return "INVALIDATED_PROTOCOL_DEFECT"
    if outcome == "PACKET_CONSTRUCTION_DEFECT":
        return "INVALIDATED_CORPUS_DEFECT"
    if outcome == "EPISTEMICALLY_UNRESOLVABLE":
        return "REMAINS_EPISTEMICALLY_UNRESOLVABLE"
    if outcome == "NARROW_DECISION":
        return "REGENERATED_NARROWED"
    if outcome == "QUALIFY_DECISION":
        return "REGENERATED_QUALIFIED"
    return "REGENERATED_AFFIRMED"


def reference_name_is_permitted(name: str) -> bool:
    return name not in PROHIBITED_NAMES


__all__ = [
    "REFERENCE_NAME", "PROHIBITED_NAMES", "ReferenceViolation",
    "AGREEMENT_PATTERNS", "ADJUDICATION_OUTCOMES", "DEFECT_OUTCOMES",
    "LINEAGE_DISPOSITIONS", "NORMALISATION_RULES", "material_dimensions",
    "nonmaterial_normalisations", "agreement_pattern", "dissent",
    "decisive_value", "ReferenceRecord", "build_reference_record",
    "reference_name_is_permitted",
]
