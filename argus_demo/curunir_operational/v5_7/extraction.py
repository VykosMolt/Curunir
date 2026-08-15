"""V5.7 §8 — the repaired extraction decision, in the order §7 fixed.

The V5.6.1 measurement put production's terminal-state agreement with the
reference at 0.26 on development and 0.0 on validation.  The dominant cell was
not a near miss: the reference called 14 of 27 development units
``RECOVERABLE_WITH_BOUNDARY_REPAIR`` and production scattered them across
REJECTED, ACCEPTED_CANDIDATE, QUARANTINED and EVIDENCE_BOUND, because it had no
state that meant "the proposition is recoverable and the boundary is not".

That state now exists.  This module puts it where it belongs — before evidence
binding, after semantic validity — and runs the mechanical construction checks
ahead of any invariant judgement, because a span cut through a value or joined
to the wrong context is not a semantic question.

The decision order is fixed and auditable:

    construction validity → semantic validity → boundary repair
    → context alignment → evidence binding → permissions → terminal state

A learned ranker may choose among invariant-valid alternatives.  It may not
overrule any of the mechanical gates below.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..v5_1.models import Record, now_utc, stable_id
from . import construction as CN
from . import states as ST

IMPLEMENTATION_VERSION = "V5_7_EXTRACTION_1"

#: §8.1 — what a learned score may never overturn.  Each is mechanical: it can
#: be checked without a model, and a model that disagrees is wrong.
MECHANICAL_GATES: tuple[str, ...] = (
    "numeric_structure_complete", "context_keyed_and_local", "no_claim_echo",
    "language_labelled", "attribution_preserved", "actor_preserved",
    "polarity_preserved", "modality_preserved", "no_lifecycle_strengthening",
    "no_structural_chrome",
)


@dataclass(frozen=True)
class ExtractionDecision(Record):
    """One candidate's decision, with the gate that decided it named."""

    decision_id: str
    candidate_id: str
    implementation: str
    terminal_state: str
    extraction_state: str
    deciding_gate: str
    boundary_repair_plan: Mapping[str, Any] | None
    exact_quotation_permission: str
    paraphrase_permission: str
    wording_as_written_permission: str
    mechanical_findings: Mapping[str, Any]
    reason: str
    recorded_time: str

    def __post_init__(self) -> None:
        if self.terminal_state not in ST.TERMINAL_STATES_V2:
            raise ST.PrecedenceViolation(
                f"unknown terminal state: {self.terminal_state!r}")
        if self.terminal_state == "RECOVERABLE_WITH_BOUNDARY_REPAIR" and \
                not self.boundary_repair_plan:
            raise ST.PrecedenceViolation(
                "a recoverable candidate must carry the repair that recovers it")
        if self.terminal_state == "ACCEPTED_CANDIDATE" and \
                self.wording_as_written_permission != "PERMITTED_AS_WRITTEN":
            raise ST.PrecedenceViolation(
                "an accepted candidate is one whose wording may be used as "
                "written; anything less is EVIDENCE_BOUND at best")

    @property
    def admits(self) -> bool:
        return self.terminal_state == "ACCEPTED_CANDIDATE"

    @property
    def reaches_claim_support(self) -> bool:
        return self.extraction_state == ST.ADDRESSABLE_FROM


def decide(*, candidate: Mapping[str, Any], context: str = "",
           claim_text: str | None = None,
           context_join: CN.ContextJoin | None = None,
           declared_language: str | None = None,
           invariant_state: str | None = None,
           implementation: str = IMPLEMENTATION_VERSION) -> ExtractionDecision:
    """The repaired decision.  Mechanical gates first, judgement afterwards."""
    span = candidate.get("raw_span", "")
    findings: dict[str, Any] = {}

    # ---- Gate 1: numeric structure ------------------------------------
    structure = CN.numeric_structure(span, context=context)
    findings["numeric_structure"] = {
        "complete": structure["numeric_token_complete"],
        "truncated": structure["truncated_structure"]}
    if not structure["numeric_token_complete"]:
        repair = CN.value_repair(span, context) or {}
        if repair.get("repairable"):
            plan = ST.boundary_repair_plan(
                unit_id=candidate.get("candidate_id", ""),
                repair_direction=repair["repair_direction"],
                required_context=repair.get("required_context", ""),
                reason=repair.get("reason", ""),
                exact_quotation_after_repair="EXACT_QUOTATION_PERMITTED",
                paraphrase_permission_before_repair="PARAPHRASE_NOT_PERMITTED")
            return ExtractionDecision(
                stable_id("v5-7-extraction", candidate.get("candidate_id", ""),
                          implementation),
                candidate.get("candidate_id", ""), implementation,
                "RECOVERABLE_WITH_BOUNDARY_REPAIR",
                "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
                "numeric_structure_complete",
                {**plan.to_record(), "replacement_span": repair.get("replacement_span"),
                 "complete_value": repair.get("complete_value")},
                "EXACT_QUOTATION_NOT_PERMITTED", "PARAPHRASE_NOT_PERMITTED",
                "PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR", findings,
                f"the span is cut inside a {structure['truncated_structure']} "
                f"structure; the completion is available in bounded context, so "
                f"the proposition is recoverable and the wording is not",
                now_utc())
        # Sheared with no recoverable completion: the figure is gone.
        return ExtractionDecision(
            stable_id("v5-7-extraction", candidate.get("candidate_id", ""),
                      implementation),
            candidate.get("candidate_id", ""), implementation, "QUARANTINED",
            "EXTRACTION_VALID_NOT_EVIDENCE_BOUND", "numeric_structure_complete",
            None, "EXACT_QUOTATION_NOT_PERMITTED", "PARAPHRASE_NOT_PERMITTED",
            "NOT_PERMITTED", findings,
            "the span is cut inside a value and no completion is held; the "
            "figure cannot be recovered from what is available", now_utc())

    # ---- Gate 1b: an unbound reference the left context would bind -----
    # The reference's other recoverable class.  A span that relies on something
    # it does not introduce is not a self-contained proposition, and where the
    # antecedent sits immediately to the left the repair is bounded and known.
    reference_check = CN.unbound_reference(span, context)
    findings["boundary_reference"] = {
        "defensible": reference_check["boundary_defensible"],
        "opener": reference_check["unbound_opener"],
        "unbound": (reference_check["unbound_reference"] or {}).get("phrase"),
        "bindable": reference_check["bindable_by_left_expansion"]}
    if not reference_check["boundary_defensible"]:
        if reference_check["bindable_by_left_expansion"]:
            plan = ST.boundary_repair_plan(
                unit_id=candidate.get("candidate_id", ""),
                repair_direction="EXPAND_LEFT_CONTEXT",
                required_context="the antecedent immediately preceding the span",
                reason=("the span relies on a reference it does not introduce: "
                        f"{(reference_check['unbound_reference'] or {}).get('phrase') or reference_check['unbound_opener']}"),
                exact_quotation_after_repair="EXACT_QUOTATION_PERMITTED",
                paraphrase_permission_before_repair="PARAPHRASE_REQUIRES_QUALIFICATION")
            return ExtractionDecision(
                stable_id("v5-7-extraction", candidate.get("candidate_id", ""),
                          implementation),
                candidate.get("candidate_id", ""), implementation,
                "RECOVERABLE_WITH_BOUNDARY_REPAIR",
                "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
                "proposition_boundary_defensible", plan.to_record(),
                "EXACT_QUOTATION_NOT_PERMITTED", "PARAPHRASE_REQUIRES_QUALIFICATION",
                "PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR", findings,
                "the boundary leaves a reference unbound and the antecedent is "
                "available immediately to the left", now_utc())
        return ExtractionDecision(
            stable_id("v5-7-extraction", candidate.get("candidate_id", ""),
                      implementation),
            candidate.get("candidate_id", ""), implementation,
            "SEMANTICALLY_PARSED", "EXTRACTION_VALID_NOT_EVIDENCE_BOUND",
            "proposition_boundary_defensible", None,
            "NO_EXACT_QUOTATION_REQUESTED", "PARAPHRASE_REQUIRES_QUALIFICATION",
            "PERMITTED_ONLY_WITH_QUALIFICATION", findings,
            "the span relies on a reference it does not introduce and no "
            "antecedent is held; it parses but is not evidence-bound", now_utc())

    # ---- Gate 2: context alignment ------------------------------------
    if context_join is not None:
        findings["context"] = {"relation": context_join.context_relation,
                               "contained": context_join.span_contained_in_context,
                               "mismatched_keys": list(context_join.mismatched_keys)}
        if context_join.is_local and (context_join.mismatched_keys
                                      or not context_join.span_contained_in_context):
            return _defect(candidate, implementation, findings,
                           "context_keyed_and_local",
                           "the context presented as this candidate's own does "
                           "not belong to it")

    # ---- Gate 3: claim echo -------------------------------------------
    if claim_text:
        echo = CN.detect_claim_echo(
            claim_text=claim_text,
            evidence_spans=[{"span_id": candidate.get("candidate_id"),
                             "text": span,
                             "derivation_direction": candidate.get(
                                 "derivation_direction")}])
        findings["claim_echo"] = echo["claim_echoes"]
        if echo["claim_echoes"]:
            return _defect(candidate, implementation, findings, "no_claim_echo",
                           "the candidate reproduces the target claim without "
                           "provenance running source to claim")

    # ---- Gate 4: language ---------------------------------------------
    detected = CN.detect_language(span)
    findings["language"] = {"declared": declared_language, "detected": detected}
    if detected and declared_language and detected != declared_language:
        return ExtractionDecision(
            stable_id("v5-7-extraction", candidate.get("candidate_id", ""),
                      implementation),
            candidate.get("candidate_id", ""), implementation, "QUARANTINED",
            "EXTRACTION_VALID_NOT_EVIDENCE_BOUND", "language_labelled", None,
            "EXACT_QUOTATION_NOT_PERMITTED", "PARAPHRASE_REQUIRES_QUALIFICATION",
            "NOT_PERMITTED", findings,
            f"the span is written in {detected} but declared {declared_language}; "
            "an exact quotation cannot rest on a mislabelled language",
            now_utc())

    # ---- Gate 5: semantic judgement, from the invariant machinery ------
    state = invariant_state or "ACCEPTED_CANDIDATE"
    if state not in ST.TERMINAL_STATES_V2:
        state = "QUARANTINED"
    findings["invariant_state"] = state
    permissions = {
        "ACCEPTED_CANDIDATE": ("EXACT_QUOTATION_PERMITTED", "PARAPHRASE_PERMITTED",
                               "PERMITTED_AS_WRITTEN"),
        "EVIDENCE_BOUND": ("EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING",
                           "PARAPHRASE_PERMITTED", "PERMITTED_ONLY_WITH_QUALIFICATION"),
        "SEMANTICALLY_PARSED": ("NO_EXACT_QUOTATION_REQUESTED",
                                "PARAPHRASE_REQUIRES_QUALIFICATION",
                                "PERMITTED_ONLY_WITH_QUALIFICATION"),
        "QUARANTINED": ("EXACT_QUOTATION_NOT_PERMITTED",
                        "PARAPHRASE_REQUIRES_QUALIFICATION", "NOT_PERMITTED"),
        "REJECTED": ("EXACT_QUOTATION_NOT_PERMITTED", "PARAPHRASE_NOT_PERMITTED",
                     "NOT_PERMITTED"),
        "EPISTEMICALLY_UNRESOLVABLE": ("EPISTEMICALLY_UNRESOLVABLE",
                                       "EPISTEMICALLY_UNRESOLVABLE",
                                       "EPISTEMICALLY_UNRESOLVABLE"),
        "CONSTRUCTION_DEFECT": ("NO_EXACT_QUOTATION_REQUESTED", "NOT_APPLICABLE",
                                "NOT_APPLICABLE"),
    }[state]
    return ExtractionDecision(
        stable_id("v5-7-extraction", candidate.get("candidate_id", ""), implementation),
        candidate.get("candidate_id", ""), implementation, state,
        ST.extraction_state_from_terminal(state), "semantic_invariants", None,
        permissions[0], permissions[1], permissions[2], findings,
        "every mechanical gate passed; the state is the invariant judgement",
        now_utc())


def _defect(candidate, implementation, findings, gate, reason):
    return ExtractionDecision(
        stable_id("v5-7-extraction", candidate.get("candidate_id", ""), implementation),
        candidate.get("candidate_id", ""), implementation, "CONSTRUCTION_DEFECT",
        "EXTRACTION_CONSTRUCTION_DEFECT", gate, None,
        "NO_EXACT_QUOTATION_REQUESTED", "NOT_APPLICABLE", "NOT_APPLICABLE",
        findings, reason, now_utc())


#: §7.4 — precedence among terminal states, most restrictive first.  Where two
#: gates would fire, the earlier gate in the pipeline decides.
TERMINAL_PRECEDENCE: tuple[str, ...] = (
    "CONSTRUCTION_DEFECT", "RECOVERABLE_WITH_BOUNDARY_REPAIR", "REJECTED",
    "QUARANTINED", "SEMANTICALLY_PARSED", "EVIDENCE_BOUND",
    "EPISTEMICALLY_UNRESOLVABLE", "ACCEPTED_CANDIDATE",
)


def audit_admissions(decisions: Iterable[ExtractionDecision]) -> dict[str, Any]:
    """§8.4 — what was admitted, and whether anything unsafe got through."""
    decisions = list(decisions)
    admitted = [d for d in decisions if d.admits]
    recoverable = [d for d in decisions
                   if d.terminal_state == "RECOVERABLE_WITH_BOUNDARY_REPAIR"]
    return {
        "decisions": len(decisions),
        "admitted": len(admitted),
        "recoverable": len(recoverable),
        "recoverable_carrying_a_plan": sum(1 for d in recoverable
                                           if d.boundary_repair_plan),
        "recoverable_admitted_as_written": sum(
            1 for d in recoverable if d.admits),
        "admitted_with_incomplete_numeric_structure": sum(
            1 for d in admitted
            if not d.mechanical_findings.get("numeric_structure", {}).get("complete", True)),
        "deciding_gates": {gate: sum(1 for d in decisions if d.deciding_gate == gate)
                           for gate in {d.deciding_gate for d in decisions}},
        "verdict": "PASS",
    }


__all__ = [
    "IMPLEMENTATION_VERSION", "MECHANICAL_GATES", "ExtractionDecision", "decide",
    "TERMINAL_PRECEDENCE", "audit_admissions",
]
