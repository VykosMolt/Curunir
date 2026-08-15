"""V5.7 §7 — one precedence relation from extraction validity to claim support.

Defect P1 was not a disagreement about an answer.  It was two questions asked at
two pipeline levels with no stated order between them:

    candidate_semantic_validity   — did we successfully build a proposition?
    addresses_proposition         — does that proposition engage the claim?

Without a precedence relation, a span that was never a proposition could be
recorded as ``NOT_SUPPORTED`` — which asserts something false: that valid
evidence exists and fails to address the claim.  It does not exist.  The V5.6.1
adjudicator hit this and had to resolve it by hand, consistently, 73 times.

Here the order is a state machine, and the illegal combinations are refused by
the constructor rather than documented in prose.

    EXTRACTION_INVALID                       → no support classification at all
    EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR → boundary repair owed
    EXTRACTION_VALID_NOT_EVIDENCE_BOUND      → evidence binding owed
    EXTRACTION_VALID_EVIDENCE_BOUND          → and only then, addressability
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from ..v5_6 import schema as S


class PrecedenceViolation(RuntimeError):
    """A state combination that the pipeline order makes impossible."""


# ---------------------------------------------------------------------------
# §7.2 — extraction / evidence-construction states
# ---------------------------------------------------------------------------

EXTRACTION_STATES: tuple[str, ...] = (
    "EXTRACTION_INVALID",
    "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
    "EXTRACTION_VALID_NOT_EVIDENCE_BOUND",
    "EXTRACTION_VALID_EVIDENCE_BOUND",
    "EXTRACTION_EPISTEMICALLY_UNRESOLVABLE",
    "EXTRACTION_CONSTRUCTION_DEFECT",
)

#: The only state from which a proposition may be tested against a claim.
ADDRESSABLE_FROM = "EXTRACTION_VALID_EVIDENCE_BOUND"

#: §13.1 — these are upstream construction outcomes.  None of them is a
#: claim-support label, and none of them may be counted as NOT_SUPPORTED.
UPSTREAM_CONSTRUCTION_STATES = frozenset({
    "EXTRACTION_INVALID", "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
    "EXTRACTION_VALID_NOT_EVIDENCE_BOUND", "EXTRACTION_CONSTRUCTION_DEFECT",
    "EXTRACTION_EPISTEMICALLY_UNRESOLVABLE",
})

# §7.3 — addressability states.
ADDRESSABILITY_STATES: tuple[str, ...] = (
    "VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION",
    "VALID_EVIDENCE_ADDRESSING_PROPOSITION",
    "ADDRESSABILITY_EPISTEMICALLY_UNRESOLVABLE",
    "ADDRESSABILITY_NOT_REACHED",
)

#: The full ordered pipeline, as a data structure rather than as a convention.
PIPELINE_ORDER: tuple[str, ...] = (
    "candidate_construction_valid",
    "semantic_proposition_valid",
    "boundary_repair_resolved",
    "evidence_bound",
    "addresses_proposition",
    "entity_alignment", "predicate_alignment", "object_alignment",
    "scope_alignment", "time_alignment", "polarity_alignment",
    "modality_alignment", "lifecycle_alignment", "attribution_alignment",
    "support_completeness",
)

#: §7.4 — what each extraction state licenses downstream.
DOWNSTREAM_LICENCE: Mapping[str, str] = {
    "EXTRACTION_INVALID":
        "no claim-support classification; upstream construction failure",
    "EXTRACTION_CONSTRUCTION_DEFECT":
        "no claim-support classification; the packet posed no answerable question",
    "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR":
        "no final support classification; bounded boundary repair required",
    "EXTRACTION_VALID_NOT_EVIDENCE_BOUND":
        "no final support classification; evidence binding required",
    "EXTRACTION_EPISTEMICALLY_UNRESOLVABLE":
        "no claim-support classification; validity itself is unresolved",
    "EXTRACTION_VALID_EVIDENCE_BOUND":
        "proposition addressability may now be evaluated",
}


def may_evaluate_addressability(extraction_state: str) -> bool:
    if extraction_state not in EXTRACTION_STATES:
        raise PrecedenceViolation(f"unknown extraction state: {extraction_state!r}")
    return extraction_state == ADDRESSABLE_FROM


def support_class_permitted(extraction_state: str, addressability: str
                            ) -> tuple[str, ...]:
    """§7.4 — which support classes this pipeline position can produce.

    An empty tuple is a real answer: it means the question has not been reached,
    and recording any support class would assert something the pipeline has not
    established.
    """
    if extraction_state not in EXTRACTION_STATES:
        raise PrecedenceViolation(f"unknown extraction state: {extraction_state!r}")
    if addressability not in ADDRESSABILITY_STATES:
        raise PrecedenceViolation(f"unknown addressability state: {addressability!r}")
    if extraction_state != ADDRESSABLE_FROM:
        return ()
    if addressability == "VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION":
        # Valid evidence that engages a different proposition.  This is the ONLY
        # route to NOT_SUPPORTED — and it is unavailable to anything upstream.
        return ("NOT_SUPPORTED",)
    if addressability == "ADDRESSABILITY_EPISTEMICALLY_UNRESOLVABLE":
        return ("EPISTEMICALLY_UNRESOLVABLE",)
    if addressability == "ADDRESSABILITY_NOT_REACHED":
        return ()
    return tuple(c for c in S.SUPPORT_CLASSES if c != "CONSTRUCTION_DEFECT")


@dataclass(frozen=True)
class PipelineState(Record):
    """One unit's position in the pipeline, with the illegal states unbuildable."""

    state_id: str
    unit_id: str
    extraction_state: str
    addressability: str
    support_class: str | None
    first_material_failure: str | None
    boundary_repair_plan: Mapping[str, Any] | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.extraction_state not in EXTRACTION_STATES:
            raise PrecedenceViolation(
                f"unknown extraction state: {self.extraction_state!r}")
        if self.addressability not in ADDRESSABILITY_STATES:
            raise PrecedenceViolation(
                f"unknown addressability state: {self.addressability!r}")

        # §7.5 — invalid extraction may not be addressable.
        if self.extraction_state != ADDRESSABLE_FROM and \
                self.addressability != "ADDRESSABILITY_NOT_REACHED":
            raise PrecedenceViolation(
                f"{self.extraction_state} cannot carry addressability "
                f"{self.addressability}: no evidence proposition was successfully "
                "constructed, so there is nothing to address the claim with")

        permitted = support_class_permitted(self.extraction_state, self.addressability)
        if self.support_class is not None and self.support_class not in permitted:
            if not permitted:
                raise PrecedenceViolation(
                    f"{self.extraction_state} with {self.addressability} reaches no "
                    f"support classification at all; {self.support_class} asserts a "
                    f"judgement the pipeline never made.  {DOWNSTREAM_LICENCE[self.extraction_state]}")
            raise PrecedenceViolation(
                f"{self.support_class} is not permitted at "
                f"{self.extraction_state} / {self.addressability}; permitted: "
                f"{list(permitted)}")

        # A specific mismatch presupposes that the evidence engages the family.
        if self.support_class in S.SPECIFIC_MISMATCH and \
                self.addressability != "VALID_EVIDENCE_ADDRESSING_PROPOSITION":
            raise PrecedenceViolation(
                f"{self.support_class} names which dimension failed, which "
                "presupposes the evidence is about this proposition at all")

        if self.extraction_state == "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR" \
                and not self.boundary_repair_plan:
            raise PrecedenceViolation(
                "a recoverable boundary must name the repair that would recover "
                "it; 'recoverable' without a repair plan is just a rejection "
                "wearing a softer word")

    @property
    def reached_support(self) -> bool:
        return self.support_class is not None

    @property
    def is_upstream_failure(self) -> bool:
        return self.extraction_state in UPSTREAM_CONSTRUCTION_STATES


def pipeline_state(*, unit_id: str, extraction_state: str,
                   addressability: str = "ADDRESSABILITY_NOT_REACHED",
                   support_class: str | None = None,
                   first_material_failure: str | None = None,
                   boundary_repair_plan: Mapping[str, Any] | None = None
                   ) -> PipelineState:
    return PipelineState(
        stable_id("v5-7-state", unit_id, extraction_state, addressability),
        unit_id, extraction_state, addressability, support_class,
        first_material_failure, boundary_repair_plan, now_utc())


# ---------------------------------------------------------------------------
# §11 — the recoverable-boundary state, as production must carry it
# ---------------------------------------------------------------------------

#: §11.3 — the extraction terminal vocabulary, now able to say "recoverable".
#: Versioned rather than widened in place, because old metrics computed over the
#: smaller vocabulary are not comparable to metrics computed over this one.
TERMINAL_STATES_V2: tuple[str, ...] = (
    "ACCEPTED_CANDIDATE", "EVIDENCE_BOUND", "SEMANTICALLY_PARSED",
    "RECOVERABLE_WITH_BOUNDARY_REPAIR", "QUARANTINED", "REJECTED",
    "EPISTEMICALLY_UNRESOLVABLE", "CONSTRUCTION_DEFECT",
)

TERMINAL_INTERFACE_VERSION = "V5_7_TERMINAL_STATES_2"

REPAIR_DIRECTIONS: tuple[str, ...] = (
    "EXPAND_LEFT_CONTEXT", "EXPAND_RIGHT_CONTEXT", "EXPAND_BOTH_DIRECTIONS",
    "REPLACE_WITH_ALTERNATIVE_CANDIDATE", "RECONSTRUCT_FROM_TABLE_OR_LAYOUT",
)


@dataclass(frozen=True)
class BoundaryRepairPlan(Record):
    """§11.2 — what would recover this candidate, recorded rather than implied."""

    plan_id: str
    unit_id: str
    repair_direction: str
    required_context: str
    preferred_alternative_candidate: str | None
    exact_quotation_after_repair: str
    paraphrase_permission_before_repair: str
    reason: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.repair_direction not in REPAIR_DIRECTIONS:
            raise PrecedenceViolation(
                f"unknown repair direction: {self.repair_direction!r}")
        if not self.reason.strip():
            raise PrecedenceViolation("a repair plan must say why the repair works")


def boundary_repair_plan(*, unit_id: str, repair_direction: str,
                         required_context: str, reason: str,
                         preferred_alternative_candidate: str | None = None,
                         exact_quotation_after_repair: str = "UNKNOWN",
                         paraphrase_permission_before_repair: str = "UNKNOWN"
                         ) -> BoundaryRepairPlan:
    return BoundaryRepairPlan(
        stable_id("v5-7-repair", unit_id, repair_direction), unit_id,
        repair_direction, required_context, preferred_alternative_candidate,
        exact_quotation_after_repair, paraphrase_permission_before_repair,
        reason, now_utc())


#: §11: the mapping from the reference's typed answer to the V2 terminal state.
#: ``RECOVERABLE_WITH_BOUNDARY_REPAIR`` maps to ITSELF — the whole point of the
#: repair is that it no longer collapses into a neighbouring state.
def terminal_state_v2(*, semantic_validity: str, wording_as_written: str,
                      exact_quotation: str) -> str:
    if semantic_validity == "CONSTRUCTION_DEFECT":
        return "CONSTRUCTION_DEFECT"
    if semantic_validity == "EPISTEMICALLY_UNRESOLVABLE":
        return "EPISTEMICALLY_UNRESOLVABLE"
    if semantic_validity == "SEMANTICALLY_INVALID":
        return "REJECTED"
    if semantic_validity == "SEMANTICALLY_INCOMPLETE":
        return "QUARANTINED"
    if semantic_validity == "RECOVERABLE_WITH_BOUNDARY_REPAIR":
        return "RECOVERABLE_WITH_BOUNDARY_REPAIR"
    if wording_as_written == "PERMITTED_AS_WRITTEN" and \
            exact_quotation != "EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING":
        return "ACCEPTED_CANDIDATE"
    return "EVIDENCE_BOUND"


def extraction_state_from_terminal(terminal: str) -> str:
    """The pipeline state a terminal extraction state puts a unit in."""
    return {
        "ACCEPTED_CANDIDATE": "EXTRACTION_VALID_EVIDENCE_BOUND",
        "EVIDENCE_BOUND": "EXTRACTION_VALID_EVIDENCE_BOUND",
        "SEMANTICALLY_PARSED": "EXTRACTION_VALID_NOT_EVIDENCE_BOUND",
        "RECOVERABLE_WITH_BOUNDARY_REPAIR":
            "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
        "QUARANTINED": "EXTRACTION_VALID_NOT_EVIDENCE_BOUND",
        "REJECTED": "EXTRACTION_INVALID",
        "EPISTEMICALLY_UNRESOLVABLE": "EXTRACTION_EPISTEMICALLY_UNRESOLVABLE",
        "CONSTRUCTION_DEFECT": "EXTRACTION_CONSTRUCTION_DEFECT",
    }[terminal]


def audit_precedence(states: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """§9 — how many records assert a support class they never reached."""
    rows, violations = list(states), []
    for row in rows:
        extraction = row.get("extraction_state")
        addressability = row.get("addressability", "ADDRESSABILITY_NOT_REACHED")
        support = row.get("support_class")
        if extraction in UPSTREAM_CONSTRUCTION_STATES and support:
            violations.append({"unit": row.get("unit_id"), "extraction": extraction,
                               "support_class": support,
                               "why": "upstream construction failure recorded as a "
                                      "claim-support judgement"})
        elif support and support not in support_class_permitted(
                extraction, addressability):
            violations.append({"unit": row.get("unit_id"), "extraction": extraction,
                               "addressability": addressability,
                               "support_class": support,
                               "why": "support class not reachable from this state"})
    return {"records": len(rows), "precedence_violations": len(violations),
            "examples": violations[:10],
            "verdict": "PASS" if not violations else "FAIL"}


__all__ = [
    "PrecedenceViolation", "EXTRACTION_STATES", "ADDRESSABILITY_STATES",
    "ADDRESSABLE_FROM", "UPSTREAM_CONSTRUCTION_STATES", "PIPELINE_ORDER",
    "DOWNSTREAM_LICENCE", "may_evaluate_addressability", "support_class_permitted",
    "PipelineState", "pipeline_state", "TERMINAL_STATES_V2",
    "TERMINAL_INTERFACE_VERSION", "REPAIR_DIRECTIONS", "BoundaryRepairPlan",
    "boundary_repair_plan", "terminal_state_v2", "extraction_state_from_terminal",
    "audit_precedence",
]
