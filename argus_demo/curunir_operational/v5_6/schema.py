"""V5.6 §9–§14 — the dependency-consistent adjudication schema.

Five stages, in order, each constrained by the last:

    A  extraction validity
    B  claim support
    C  required qualification
    D  permitted factual wording
    E  publication conformance

The V5.3 protocol asked B and E as free, independent questions.  That is what
produced fifteen contradictions in thirty overlapping propositions, four of them
unanimous on both sides.  Here E is *derived* from B, C and D; a reviewer cannot
make a free publication judgment, and the illegal combinations are not merely
rejected on submission — they are absent from the form.

Section 10.2 also decomposes the overloaded "mapping precision" concept.  A span
can be perfectly locatable and still unfit to carry an exact quotation, and an
approximate span can be entirely adequate for a qualitative claim.  One scalar
could not say that, which is why two milestones argued about whether HTML was
worse than PDF when the real question was what each claim needed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class SchemaViolation(RuntimeError):
    """An adjudication was constructed that the dependency schema forbids."""


STAGES: tuple[str, ...] = (
    "A_EXTRACTION_VALIDITY",
    "B_CLAIM_SUPPORT",
    "C_REQUIRED_QUALIFICATION",
    "D_PERMITTED_WORDING",
    "E_PUBLICATION_CONFORMANCE",
)

# ---------------------------------------------------------------------------
# §10 — Stage A, semantic extraction adjudication.
# ---------------------------------------------------------------------------

INVARIANT_DIMENSIONS: tuple[str, ...] = (
    "proposition_complete", "referential_subject_complete", "predicate_complete",
    "object_or_value_complete", "attribution_complete", "polarity_preserved",
    "modality_preserved", "lifecycle_state_preserved", "temporal_scope_complete",
    "geographic_scope_complete", "quantity_and_unit_complete", "discourse_assertive",
    "context_dependency_resolved", "layout_relation_preserved",
    "mapping_sufficient_for_claim_type", "no_structural_chrome",
    "no_unrelated_proposition_contamination", "no_attribution_shift",
    "no_actor_shift", "no_scope_strengthening", "no_lifecycle_strengthening",
)

INVARIANT_LABELS: tuple[str, ...] = (
    "SATISFIED", "VIOLATED", "UNRESOLVED_FROM_AVAILABLE_EVIDENCE",
    "NOT_APPLICABLE", "CONSTRUCTION_DEFECT",
)

#: §10.2 — mapping decomposed by what the claim actually needs.  One scalar
#: conflated all five, so an exact percentage and an institution name were held
#: to the same standard.
MAPPING_REQUIREMENTS: tuple[str, ...] = (
    "TEXT_LOCATABLE",
    "PROPOSITION_BOUNDARY_DEFENSIBLE",
    "SEMANTIC_CONTEXT_SUFFICIENT",
    "LAYOUT_RELATION_PRESERVED",
    "EXACT_VALUE_QUOTABLE",
)

EXTRACTION_RESULTS: tuple[str, ...] = (
    "ACCEPTED_CANDIDATE", "EVIDENCE_BOUND", "SEMANTICALLY_PARSED", "QUARANTINED",
    "REJECTED", "EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT",
)


@dataclass(frozen=True)
class StageAExtraction(Record):
    """One seat's extraction adjudication for one candidate."""

    candidate_id: str
    seat_id: str
    invariants: Mapping[str, str]
    mapping_satisfied: tuple[str, ...]
    result: str
    first_material_failure: str | None
    bounded_context_would_repair: bool
    preferred_candidate_id: str | None
    exact_quotation_permitted: bool
    paraphrase_only: bool
    reasoning: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.result not in EXTRACTION_RESULTS:
            raise SchemaViolation(f"unknown extraction result: {self.result}")
        unknown = set(self.invariants) - set(INVARIANT_DIMENSIONS)
        if unknown:
            raise SchemaViolation(f"unknown invariant dimensions: {sorted(unknown)}")
        bad = {k: v for k, v in self.invariants.items() if v not in INVARIANT_LABELS}
        if bad:
            raise SchemaViolation(f"invalid invariant labels: {bad}")
        for requirement in self.mapping_satisfied:
            if requirement not in MAPPING_REQUIREMENTS:
                raise SchemaViolation(f"unknown mapping requirement: {requirement}")
        # §10.2 / §28 mutation 17: an exact quotation needs an exact mapping.
        if self.exact_quotation_permitted and \
                "EXACT_VALUE_QUOTABLE" not in self.mapping_satisfied:
            raise SchemaViolation(
                "exact quotation permitted without EXACT_VALUE_QUOTABLE mapping; a "
                "figure that is quoted must be exactly locatable")
        if self.exact_quotation_permitted and self.paraphrase_only:
            raise SchemaViolation("a candidate cannot be both quotable and paraphrase-only")
        if self.result == "ACCEPTED_CANDIDATE" and self.first_material_failure:
            raise SchemaViolation(
                "an accepted candidate cannot record a material invariant failure")


# ---------------------------------------------------------------------------
# §11 — Stage B, claim support.
# ---------------------------------------------------------------------------

SUPPORT_CLASSES: tuple[str, ...] = (
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT", "CONTRADICTED", "NOT_SUPPORTED", "WRONG_SCOPE",
    "WRONG_TIME", "WRONG_ENTITY", "WRONG_MODALITY", "WRONG_POLARITY",
    "INFERENCE_ONLY", "EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT",
)

AFFIRMATIVE_SUPPORT = frozenset({
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT"})

SPECIFIC_MISMATCH = frozenset({
    "WRONG_SCOPE", "WRONG_TIME", "WRONG_ENTITY", "WRONG_MODALITY",
    "WRONG_POLARITY"})

#: §11.1 — the order a seat must answer in.
SUPPORT_QUESTION_ORDER: tuple[str, ...] = (
    "addresses_proposition_family", "entity_alignment", "predicate_alignment",
    "object_alignment", "scope_alignment", "time_alignment", "polarity_alignment",
    "modality_and_lifecycle_alignment", "attribution_preserved",
    "support_completeness",
)

ALIGNMENT_VALUES = ("ALIGNED", "MISALIGNED", "UNRESOLVED", "NOT_APPLICABLE")

#: §11.3 — strengthenings the protocol may never reward.
FORBIDDEN_STRENGTHENINGS: tuple[str, ...] = (
    "plan_as_implementation", "announcement_as_present_capability",
    "funding_as_deployment", "procurement_as_operation",
    "pilot_as_operational_rollout", "exercise_as_operational_use",
    "vendor_statement_as_demonstrated_fact",
)


@dataclass(frozen=True)
class StageBSupport(Record):
    """One seat's claim-support adjudication, with its full support vector."""

    proposition_id: str
    seat_id: str
    addresses_proposition: bool
    entity_alignment: str
    predicate_alignment: str
    object_alignment: str
    scope_alignment: str
    time_alignment: str
    polarity_alignment: str
    modality_alignment: str
    lifecycle_alignment: str
    attribution_alignment: str
    dependence_limitations: tuple[str, ...]
    counterevidence_state: str
    support_completeness: str
    first_material_failure: str | None
    support_class: str
    reasoning: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.support_class not in SUPPORT_CLASSES:
            raise SchemaViolation(f"unknown support class: {self.support_class}")
        for name in ("entity_alignment", "predicate_alignment", "object_alignment",
                     "scope_alignment", "time_alignment", "polarity_alignment",
                     "modality_alignment", "lifecycle_alignment",
                     "attribution_alignment"):
            if getattr(self, name) not in ALIGNMENT_VALUES:
                raise SchemaViolation(f"{name} must be one of {ALIGNMENT_VALUES}")
        # §11.1 — a specific mismatch requires addressability first.
        if self.support_class in SPECIFIC_MISMATCH and not self.addresses_proposition:
            raise SchemaViolation(
                f"{self.support_class} is a specific mismatch and is available only "
                "after proposition addressability passes (Section 11.1)")
        if self.support_class in AFFIRMATIVE_SUPPORT and self.first_material_failure:
            raise SchemaViolation(
                "affirmative support cannot record a material failure")


# ---------------------------------------------------------------------------
# §12 — Stage C, required qualification.
# ---------------------------------------------------------------------------

QUALIFICATION_DIMENSIONS: tuple[str, ...] = (
    "ATTRIBUTION_REQUIRED", "PLAN_OR_INTENTION_ONLY", "ANNOUNCEMENT_ONLY",
    "VENDOR_REPORTED", "PRELIMINARY", "PARTIAL_SCOPE", "LIMITED_DEPLOYMENT",
    "PILOT_ONLY", "EXERCISE_ONLY", "TEMPORALLY_BOUNDED", "GEOGRAPHICALLY_BOUNDED",
    "DEPENDENCE_UNRESOLVED", "COUNTEREVIDENCE_PRESENT", "DISPUTED",
    "INFERENCE_MARKING_REQUIRED", "UNCERTAINTY_REQUIRED", "NO_MATERIAL_QUALIFICATION",
)


@dataclass(frozen=True)
class StageCQualification(Record):
    """Which information may not be removed without changing truth conditions."""

    proposition_id: str
    seat_id: str
    support_class: str
    required: tuple[str, ...]
    reasoning: str
    recorded_time: str

    def __post_init__(self) -> None:
        unknown = set(self.required) - set(QUALIFICATION_DIMENSIONS)
        if unknown:
            raise SchemaViolation(f"unknown qualification dimensions: {sorted(unknown)}")
        if "NO_MATERIAL_QUALIFICATION" in self.required and len(self.required) > 1:
            raise SchemaViolation(
                "NO_MATERIAL_QUALIFICATION cannot be combined with a qualification")
        # §12 — an inferential support class always requires inference marking.
        if self.support_class == "INFERENCE_ONLY" and \
                "INFERENCE_MARKING_REQUIRED" not in self.required:
            raise SchemaViolation(
                "INFERENCE_ONLY support requires INFERENCE_MARKING_REQUIRED")


# ---------------------------------------------------------------------------
# §13 — Stage D, permitted factual wording.
# ---------------------------------------------------------------------------

WORDING_PERMISSIONS: tuple[str, ...] = (
    "DIRECT_FACTUAL_PUBLICATION",
    "ATTRIBUTED_METACLAIM_ONLY",
    "INFERENCE_OR_UNCERTAINTY_ONLY",
    "NO_PUBLICATION_PERMITTED",
)


@dataclass(frozen=True)
class StageDWording(Record):
    """The contract between claim support and report generation."""

    proposition_id: str
    seat_id: str
    support_class: str
    permission: str
    maximally_supported_wording: str
    minimally_qualified_wording: str
    prohibited_stronger_wording: tuple[str, ...]
    strongest_permitted_verb: str
    permitted_lifecycle_term: str
    required_attribution: str | None
    required_uncertainty: str | None
    required_temporal_scope: str | None
    required_dependence_qualification: str | None
    reasoning: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.permission not in WORDING_PERMISSIONS:
            raise SchemaViolation(f"unknown wording permission: {self.permission}")
        if self.support_class in SPECIFIC_MISMATCH | {"NOT_SUPPORTED", "CONTRADICTED"} \
                and self.permission == "DIRECT_FACTUAL_PUBLICATION":
            raise SchemaViolation(
                f"{self.support_class} may not permit direct factual publication")
        if self.support_class == "INFERENCE_ONLY" and \
                self.permission == "DIRECT_FACTUAL_PUBLICATION":
            raise SchemaViolation(
                "INFERENCE_ONLY may not permit unmarked direct factual publication")


# ---------------------------------------------------------------------------
# §14 — Stage E, publication conformance.  Derived, never freely judged.
# ---------------------------------------------------------------------------

DISPOSITIONS: tuple[str, ...] = (
    "PUBLISHED", "PUBLISHED_WITH_QUALIFICATION", "MOVED_TO_UNCERTAINTY_SECTION",
    "REJECTED_UNSUPPORTED", "OMITTED_AS_IMMATERIAL", "REPORT_REVALIDATION_REQUIRED",
)

FACTUAL_DISPOSITIONS = frozenset({"PUBLISHED", "PUBLISHED_WITH_QUALIFICATION"})

#: §14.3 — the permitted mapping.  Everything outside it is unrepresentable.
PERMITTED_DISPOSITIONS: Mapping[str, frozenset[str]] = {
    "FULL_SUPPORT": frozenset({"PUBLISHED", "PUBLISHED_WITH_QUALIFICATION",
                               "MOVED_TO_UNCERTAINTY_SECTION", "OMITTED_AS_IMMATERIAL"}),
    "PARTIAL_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                  "MOVED_TO_UNCERTAINTY_SECTION",
                                  "OMITTED_AS_IMMATERIAL"}),
    "QUALIFIED_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                    "MOVED_TO_UNCERTAINTY_SECTION",
                                    "OMITTED_AS_IMMATERIAL"}),
    "CONTEXT_DEPENDENT_SUPPORT": frozenset({"PUBLISHED_WITH_QUALIFICATION",
                                            "MOVED_TO_UNCERTAINTY_SECTION",
                                            "OMITTED_AS_IMMATERIAL"}),
    "INFERENCE_ONLY": frozenset({"MOVED_TO_UNCERTAINTY_SECTION",
                                 "OMITTED_AS_IMMATERIAL"}),
    "CONTRADICTED": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "NOT_SUPPORTED": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_SCOPE": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_TIME": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_ENTITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_MODALITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "WRONG_POLARITY": frozenset({"REJECTED_UNSUPPORTED", "MOVED_TO_UNCERTAINTY_SECTION"}),
    "EPISTEMICALLY_UNRESOLVABLE": frozenset({"MOVED_TO_UNCERTAINTY_SECTION",
                                             "REJECTED_UNSUPPORTED"}),
    "CONSTRUCTION_DEFECT": frozenset({"REPORT_REVALIDATION_REQUIRED"}),
}

NEVER_FACTUAL = frozenset({
    "CONTRADICTED", "NOT_SUPPORTED", "WRONG_SCOPE", "WRONG_TIME", "WRONG_ENTITY",
    "WRONG_MODALITY", "WRONG_POLARITY", "INFERENCE_ONLY",
    "EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT"})


def permitted_dispositions(support_class: str, *,
                           required_context_present: bool = True) -> tuple[str, ...]:
    """§14.3 — what this support class may produce, in this context."""
    if support_class not in SUPPORT_CLASSES:
        raise SchemaViolation(f"unknown support class: {support_class}")
    permitted = set(PERMITTED_DISPOSITIONS[support_class])
    if support_class == "CONTEXT_DEPENDENT_SUPPORT" and not required_context_present:
        # Publishable with qualification only when the governing context is
        # actually present in the same section or sentence.
        permitted.discard("PUBLISHED_WITH_QUALIFICATION")
    return tuple(sorted(permitted))


def contradicts(support_class: str, disposition: str) -> bool:
    """§14.2 — is this pair one of the combinations that must be impossible?"""
    if support_class in NEVER_FACTUAL and disposition in FACTUAL_DISPOSITIONS:
        return True
    if support_class in AFFIRMATIVE_SUPPORT and disposition == "REJECTED_UNSUPPORTED":
        return True
    return False


@dataclass(frozen=True)
class StageEPublication(Record):
    """Derived from B, C and D.  Never a free 'does this sound publishable?'."""

    proposition_id: str
    seat_id: str
    support_class: str
    wording_permission: str
    required_qualifications: tuple[str, ...]
    required_context_present: bool
    disposition: str
    published_wording: str
    reasoning: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise SchemaViolation(f"unknown disposition: {self.disposition}")
        allowed = permitted_dispositions(
            self.support_class, required_context_present=self.required_context_present)
        if self.disposition not in allowed:
            raise SchemaViolation(
                f"support class {self.support_class} may not produce "
                f"{self.disposition}; permitted: {list(allowed)}")
        if contradicts(self.support_class, self.disposition):
            raise SchemaViolation(
                f"{self.support_class} + {self.disposition} is a cross-surface "
                "contradiction and is unrepresentable (Section 14.2)")
        if self.wording_permission == "NO_PUBLICATION_PERMITTED" and \
                self.disposition in FACTUAL_DISPOSITIONS:
            raise SchemaViolation(
                "Stage D permitted no publication; Stage E may not publish")
        if self.wording_permission == "ATTRIBUTED_METACLAIM_ONLY" and \
                self.disposition in FACTUAL_DISPOSITIONS and \
                "ATTRIBUTION_REQUIRED" not in self.required_qualifications:
            raise SchemaViolation(
                "publication of an attributed metaclaim requires ATTRIBUTION_REQUIRED")


# ---------------------------------------------------------------------------
# The staged adjudication as one object, with the dependency enforced.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StagedAdjudication(Record):
    """One seat's complete, dependency-consistent decision for one proposition."""

    adjudication_id: str
    proposition_id: str
    seat_id: str
    protocol_version: str
    stage_b: StageBSupport
    stage_c: StageCQualification
    stage_d: StageDWording
    stage_e: StageEPublication
    recorded_time: str

    def __post_init__(self) -> None:
        seats = {self.stage_b.seat_id, self.stage_c.seat_id,
                 self.stage_d.seat_id, self.stage_e.seat_id}
        if seats != {self.seat_id}:
            raise SchemaViolation(f"stages belong to different seats: {sorted(seats)}")
        classes = {self.stage_b.support_class, self.stage_c.support_class,
                   self.stage_d.support_class, self.stage_e.support_class}
        if len(classes) != 1:
            raise SchemaViolation(
                f"later stages disagree with Stage B about the support class: "
                f"{sorted(classes)}")
        propositions = {self.stage_b.proposition_id, self.stage_c.proposition_id,
                        self.stage_d.proposition_id, self.stage_e.proposition_id}
        if propositions != {self.proposition_id}:
            raise SchemaViolation("stages reference different propositions")
        if self.stage_e.wording_permission != self.stage_d.permission:
            raise SchemaViolation(
                "Stage E's wording permission must be the one Stage D granted")
        if tuple(self.stage_e.required_qualifications) != tuple(self.stage_c.required):
            raise SchemaViolation(
                "Stage E must carry exactly the qualifications Stage C required")


def illegal_combination_matrix() -> dict[str, Any]:
    """§32 — the matrix, materialised so it can be inspected and tested."""
    rows = []
    illegal = 0
    for support_class in SUPPORT_CLASSES:
        allowed = set(permitted_dispositions(support_class))
        for disposition in DISPOSITIONS:
            legal = disposition in allowed
            row = {"support_class": support_class, "disposition": disposition,
                   "legal": legal,
                   "is_cross_surface_contradiction": contradicts(support_class, disposition)}
            if not legal:
                illegal += 1
            rows.append(row)
    return {
        "support_classes": len(SUPPORT_CLASSES),
        "dispositions": len(DISPOSITIONS),
        "cells": len(rows),
        "illegal_cells": illegal,
        "contradiction_cells": sum(1 for r in rows if r["is_cross_surface_contradiction"]),
        "matrix": rows,
        "rule": ("an illegal cell is not offered on any reviewer form and is refused "
                 "by the constructor; a contradiction cell is the subset that would "
                 "reproduce the V5.3 failure"),
    }
