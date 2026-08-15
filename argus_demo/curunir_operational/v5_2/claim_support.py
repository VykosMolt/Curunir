"""Claim support with a lifecycle gate (contract Section 14).

Two V5.1 results meet here.

The clean held-out run scored 0 of 68 on claim support, with 61 of 68 review
units marked PACKET_DEFECT: the packet shipped a claim beside the single span
it had been extracted from, so support was circular, and omitted
counterevidence, source role, dependence, correction state and the stronger
formulations the evidence prohibits.  The dossier architecture repairs the
review unit; this module repairs the decision.

Separately, a full-population support audit found 71 plan-as-implementation
acceptances.  V5.1 could not have caught them: it compared a claim's flat
modality against an evidence item's flat modality, and PLANNED and DEPLOYED
were sibling values of one field with no ordering.  Here every support
decision runs the lifecycle gate first — evidence whose act cannot license
the claimed state does not support the claim at any strength, and the failure
is named with the critical-error category the closure gate counts.

Research shadow only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..v5_1.claim_support import (
    SemanticClaim, SupportAssessment, assess_support, classify_claim,
)
from ..v5_1.models import (
    EVIDENCE_LIFECYCLE_STATES, MODALITIES, POLARITIES, Record, SUPPORT_STATES,
    capability_outcome, now_utc, stable_id,
)
from . import lifecycle

# Support states that assert the claim is carried by the evidence.  A
# lifecycle violation may never resolve to one of these.
_AFFIRMATIVE_SUPPORT = frozenset({
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT",
})

# The support state a lifecycle violation resolves to.  WRONG_MODALITY is the
# V5.1 vocabulary's name for "the evidence asserts a different manner of
# assertion than the claim does", which is precisely what a plan presented as
# an implementation is.
_LIFECYCLE_VIOLATION_STATE = "WRONG_MODALITY"


@dataclass(frozen=True)
class LifecycleClaim(Record):
    """A claim carrying everything Section 14.1 requires it to preserve."""

    claim_id: str
    entity: str
    proposition: str
    lifecycle_state: str
    evidence_act: str
    polarity: str
    modality: str
    valid_time: tuple[str | None, str | None]
    scope: tuple[str, ...]
    attribution: str | None
    evidence_span_ids: tuple[str, ...]
    dependence_state: str
    correction_state: str
    source_authority: str
    qualification: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        lifecycle.normalize_state(self.lifecycle_state)
        if self.evidence_act not in lifecycle.ACT_MAXIMUM_STATE:
            raise ValueError(f"unknown evidence act: {self.evidence_act}")
        if self.polarity not in POLARITIES:
            raise ValueError(f"unknown polarity: {self.polarity}")
        if self.modality not in MODALITIES:
            raise ValueError(f"unknown modality: {self.modality}")
        if self.correction_state not in EVIDENCE_LIFECYCLE_STATES:
            raise ValueError(f"unknown correction state: {self.correction_state}")
        if self.source_authority not in lifecycle.SOURCE_AUTHORITIES:
            raise ValueError(f"unknown source authority: {self.source_authority}")
        if not self.entity.strip():
            raise ValueError("a claim requires an explicit entity")
        if not self.evidence_span_ids:
            raise ValueError("a claim requires at least one evidence span")
        if not lifecycle.act_licenses(self.evidence_act, self.lifecycle_state):
            raise ValueError(
                f"evidence act {self.evidence_act} does not license lifecycle "
                f"state {self.lifecycle_state}")

    @property
    def prohibited_stronger_formulations(self) -> tuple[str, ...]:
        state = lifecycle.normalize_state(self.lifecycle_state)
        if state == "UNKNOWN":
            return tuple(s for s in lifecycle.LIFECYCLE_STATES if s != "UNKNOWN")
        allowed = lifecycle.PRESUPPOSED[state] | {state}
        return tuple(s for s in lifecycle.LIFECYCLE_STATES
                     if s not in allowed and s != "UNKNOWN")


def lifecycle_claim(*, entity: str, proposition: str, lifecycle_state: str,
                    evidence_act: str, evidence_span_ids: Iterable[str],
                    polarity: str = "POSITIVE", modality: str = "ASSERTED",
                    valid_time: tuple[str | None, str | None] = (None, None),
                    scope: Iterable[str] = (), attribution: str | None = None,
                    dependence_state: str = "INDEPENDENCE_UNKNOWN",
                    correction_state: str = "CURRENT",
                    source_authority: str = "UNKNOWN_AUTHORITY",
                    qualification: str | None = None) -> LifecycleClaim:
    spans = tuple(evidence_span_ids)
    state = lifecycle.normalize_state(lifecycle_state)
    return LifecycleClaim(
        stable_id("v5-2-claim", entity, proposition, state, "|".join(spans)),
        entity.strip(), proposition.strip(), state, evidence_act.strip().upper(),
        polarity, modality, tuple(valid_time), tuple(scope), attribution, spans,
        dependence_state, correction_state, source_authority, qualification,
        now_utc())


# ---------------------------------------------------------------------------
# Section 14.2 — support decision with the lifecycle gate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GatedSupport(Record):
    """A V5.1 support assessment with the lifecycle verdict applied."""

    gated_id: str
    claim_id: str
    support_state: str
    lifecycle_verdict: str
    critical_error: str | None
    claimed_state: str
    supported_state: str
    rationale: str
    underlying_assessment_id: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.support_state not in SUPPORT_STATES:
            raise ValueError(f"unknown support state: {self.support_state}")
        if self.critical_error and self.support_state in _AFFIRMATIVE_SUPPORT:
            raise ValueError(
                "a lifecycle critical error may never resolve to affirmative "
                "support; this is the plan-as-implementation defect")


def _evidence_state(item: Any) -> tuple[str, str]:
    """The lifecycle state and evidence act one evidence item establishes."""
    get = (item.get if isinstance(item, Mapping)
           else lambda key, default=None, obj=item: getattr(obj, key, default))
    act = get("evidence_act")
    state = get("lifecycle_state")
    if state and act:
        return lifecycle.normalize_state(str(state)), str(act).strip().upper()
    text = str(get("normalized_statement") or get("text") or
               get("statement") or get("proposition") or "")
    language = str(get("language") or "en")
    reading = lifecycle.derive_lifecycle(
        text, language, evidence_act=str(act).strip().upper() if act else None)
    return reading.state, reading.evidence_act


def strongest_supported_state(evidence_items: Iterable[Any]) -> tuple[str, str]:
    """The strongest lifecycle state the evidence collectively licenses."""
    best_state, best_act = "UNKNOWN", "ACTOR_INTENTION"
    for item in evidence_items:
        state, act = _evidence_state(item)
        if state == "UNKNOWN":
            continue
        if best_state == "UNKNOWN" or lifecycle.stronger_than(state, best_state):
            best_state, best_act = state, act
        elif not lifecycle.comparable(state, best_state) and \
                len(lifecycle.PRESUPPOSED[state]) > len(lifecycle.PRESUPPOSED[best_state]):
            # Different branches: prefer the one with more presuppositions,
            # deterministically, and record both through the assessment.
            best_state, best_act = state, act
    return best_state, best_act


def gated_assess_support(claim_like: Any, evidence_items: Sequence[Any], *,
                         claimed_state: str | None = None,
                         source_authority: str | None = None,
                         qualified: bool | None = None) -> GatedSupport:
    """Assess support, refusing any claim the evidence cannot license.

    The lifecycle gate runs before the V5.1 semantic assessment, because a
    plan cannot partially support an implementation claim: the strength
    question does not arise once the state question is answered.
    """
    get = (claim_like.get if isinstance(claim_like, Mapping)
           else lambda key, default=None, obj=claim_like: getattr(obj, key, default))
    claim_id = str(get("claim_id") or get("id") or
                   stable_id("claim", str(get("proposition") or "")))
    text = str(get("proposition") or get("normalized_statement") or
               get("text") or "")
    language = str(get("language") or "en")

    if claimed_state is not None:
        claimed = lifecycle.normalize_state(claimed_state)
    elif get("lifecycle_state"):
        claimed = lifecycle.normalize_state(str(get("lifecycle_state")))
    else:
        claimed = lifecycle.derive_lifecycle(text, language).state

    authority = source_authority or str(get("source_authority") or
                                        "UNKNOWN_AUTHORITY")
    if authority not in lifecycle.SOURCE_AUTHORITIES:
        authority = "UNKNOWN_AUTHORITY"
    is_qualified = (qualified if qualified is not None
                    else bool(get("qualification") or get("qualifications")))

    supported, _act = strongest_supported_state(evidence_items)

    if claimed != "UNKNOWN":
        error = lifecycle.classify_lifecycle_error(
            claimed, supported, source_authority=authority, qualified=is_qualified)
        if error:
            return GatedSupport(
                stable_id("gated-support", claim_id, claimed, supported),
                claim_id, _LIFECYCLE_VIOLATION_STATE, "REFUSED", error, claimed,
                supported,
                f"the claim asserts lifecycle state {claimed}; the strongest "
                f"state the evidence licenses is {supported}, which does not "
                f"entail it ({error})", None, now_utc())

    assessment = assess_support(claim_like, evidence_items)
    return GatedSupport(
        stable_id("gated-support", claim_id, claimed, supported),
        claim_id, assessment.state, "PASSED", None, claimed, supported,
        f"lifecycle gate passed ({supported} entails {claimed}); "
        f"{assessment.rationale}", assessment.assessment_id, now_utc())


# ---------------------------------------------------------------------------
# Section 14.3 — full-population lifecycle audit
# ---------------------------------------------------------------------------

REQUIRED_ZERO_COUNTS = (
    "plan_as_implementation",
    "vendor_claim_as_demonstrated_fact",
    "exercise_as_deployment",
    "pilot_as_operational",
    "announcement_as_existing_capability",
)


def audit_accepted_claims(supports: Iterable[GatedSupport | Mapping[str, Any]]
                          ) -> dict[str, Any]:
    """Count residual critical errors over an accepted-claim population.

    An accepted claim is one whose support state is affirmative.  The audit
    reports zero when no affirmative support survived a lifecycle violation,
    which is the condition Section 14.3 requires.
    """
    counts = {name: 0 for name in REQUIRED_ZERO_COUNTS}
    counts["lifecycle_state_strengthened"] = 0
    accepted = refused = 0
    for item in supports:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        state = str(get("support_state") or "")
        error = get("critical_error")
        if state in _AFFIRMATIVE_SUPPORT:
            accepted += 1
            if error:
                counts[str(error)] = counts.get(str(error), 0) + 1
        elif error:
            refused += 1
    return {
        "accepted_claims": accepted,
        "refused_for_lifecycle": refused,
        "critical_error_counts": counts,
        "all_required_counts_zero": all(counts[name] == 0
                                        for name in REQUIRED_ZERO_COUNTS),
    }


def support_state_coverage(supports: Iterable[GatedSupport | Mapping[str, Any]]
                           ) -> dict[str, Any]:
    """Which of the twelve support states a population exercised."""
    seen = {state: 0 for state in sorted(SUPPORT_STATES)}
    for item in supports:
        get = (item.get if isinstance(item, Mapping)
               else lambda key, obj=item: getattr(obj, key, None))
        state = str(get("support_state") or "")
        if state in seen:
            seen[state] += 1
    return {
        "states_required": len(seen),
        "states_exercised": sum(1 for count in seen.values() if count),
        "counts": seen,
        "missing": [state for state, count in seen.items() if not count],
    }


def violation_outcome(support: GatedSupport):
    """A refused claim recorded as a capability outcome, not a silent drop."""
    return capability_outcome(
        subject_kind="CLAIM_SUPPORT_RELATION", subject_id=support.claim_id,
        outcome="REJECTED_UNSUPPORTED", rationale=support.rationale)
