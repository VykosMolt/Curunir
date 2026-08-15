"""V5.8.1 §8 — D27: an evaluation plan that can answer the question it asks.

The pilot reviewed ten units a surface and reported point estimates against a
0.95 threshold.  Ten units cannot adjudicate 0.95 in either direction: the 95%
interval on 10/10 runs from 0.72 to 1.0, so a perfect score and a 0.75 score are
not distinguishable.  Reporting such a number beside a threshold invites the
reader to treat it as a verdict, which is the failure D27 names.

So the plan is frozen before acquisition and states, per surface: how many
adjudicable units are required, how they are stratified, what may be replaced
from reserve and what may not, and what interval precision must be achieved
before any threshold verdict is permitted.  A run that misses the precision
requirement reports NOT_ADJUDICABLE rather than a number next to 0.95.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..v5_1.models import Record, now_utc, sha256, stable_id


class SamplingViolation(RuntimeError):
    """The evaluation was asked to report a verdict its sample cannot carry."""


#: §8.1 — minimum *adjudicable* units, not assigned packets.  A packet that
#: comes back a construction defect has told us about the corpus, not about
#: production, and must not be counted toward the capability sample.
MINIMUM_ADJUDICABLE: Mapping[str, int] = {
    "SURFACE_1_EXTRACTION": 120,
    "SURFACE_2_SOURCE_ROLES": 100,
    "SURFACE_3_DEPENDENCE": 100,
    "SURFACE_4_CLAIM_SUPPORT": 120,
    "SURFACE_5_TEMPORAL": 100,
    "SURFACE_6_PUBLICATION": 100,
}

#: §8.3 — the precision a threshold verdict requires.  Half-width is of the
#: 95% Wilson interval on the point estimate.
MAX_INTERVAL_HALF_WIDTH = 0.05
CLEAN_THRESHOLD = 0.95

#: §8.2 — prospective strata.  Declared here, before acquisition, so that a
#: stratum that turns out to be embarrassing cannot be dropped afterwards.
STRATA: Mapping[str, tuple[str, ...]] = {
    "SURFACE_1_EXTRACTION": (
        "accepted", "recoverable", "quarantined", "rejected", "numeric",
        "qualitative", "table", "prose", "HTML", "PDF", "original",
        "translation", "anaphoric_subject", "explicit_actor"),
    "SURFACE_2_SOURCE_ROLES": (
        "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
        "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY",
        "SYNDICATED_BY", "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY"),
    "SURFACE_3_DEPENDENCE": (
        "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
        "MIRROR_MANIFESTATION", "COMMON_EVIDENCE_BASIS_CONFIRMED",
        "PARTIAL_DEPENDENCE", "SHARED_DATA_INDEPENDENT_ANALYSIS",
        "INDEPENDENCE_SUPPORTED", "NO_DEPENDENCE_FOUND", "INDEPENDENCE_UNKNOWN",
        "DEPENDENCE_DISPUTED"),
    "SURFACE_4_CLAIM_SUPPORT": (
        "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
        "CONTEXT_DEPENDENT_SUPPORT", "CONTRADICTED", "NOT_SUPPORTED",
        "WRONG_SCOPE", "WRONG_TIME", "WRONG_ENTITY", "WRONG_MODALITY",
        "WRONG_POLARITY", "INFERENCE_ONLY",
        "lifecycle_plan_vs_implementation", "lifecycle_pilot_vs_operational",
        "lifecycle_announcement_vs_capability", "lifecycle_retracted_vs_current"),
    "SURFACE_5_TEMPORAL": (
        "LOGICAL_CONTRADICTION", "TEMPORAL_UPDATE", "SCOPE_DIFFERENCE",
        "DEFINITION_DIFFERENCE", "SOURCE_DISAGREEMENT", "NUMERIC_DISAGREEMENT",
        "IDENTITY_DISAGREEMENT", "POLARITY_CONFLICT", "QUALIFICATION",
        "CORRECTION", "RETRACTION", "SUPERSESSION", "UNRESOLVED", "NO_CONFLICT"),
    "SURFACE_6_PUBLICATION": (
        "PUBLISHED", "PUBLISHED_WITH_QUALIFICATION", "REJECTED_UNSUPPORTED",
        "REPORT_BLOCKED", "qualification_required", "counterevidence_required"),
}

#: §8.4 — the only grounds on which a reserve may replace a principal unit.
REPLACEABLE_CAUSES: tuple[str, ...] = (
    "ACCESS_FAILURE",
    "PACKET_CONSTRUCTION_DEFECT_FOUND_BEFORE_EXPOSURE",
    "PROSPECTIVELY_PERMITTED_UNRESOLVABLE_UNIT",
    "INVALID_DUPLICATE_IDENTITY",
)
#: Stated as a denial so the audit can assert it rather than assume it.
NON_REPLACEABLE_CAUSES: tuple[str, ...] = (
    "ORDINARY_PRODUCTION_ERROR",
    "DISAGREEMENT_WITH_THE_PANEL",
    "LOW_INTERIM_SCORE",
    "CONSTRUCTION_DEFECT_FOUND_AFTER_EXPOSURE",
)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """Point estimate and 95% Wilson interval."""
    if total <= 0:
        return (0.0, 0.0, 1.0)
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    spread = (z / (1 + z * z / total)) * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total))
    return (p, max(0.0, centre - spread), min(1.0, centre + spread))


def half_width(successes: int, total: int) -> float:
    _, low, high = wilson(successes, total)
    return (high - low) / 2


def units_required_for_precision(expected_rate: float = 0.95,
                                 target_half_width: float = MAX_INTERVAL_HALF_WIDTH
                                 ) -> int:
    """How many adjudicable units the declared precision needs.

    Solved by search rather than by the normal approximation, because at rates
    near 1.0 the approximation is exactly where it is least trustworthy.
    """
    for total in range(10, 5000):
        successes = round(expected_rate * total)
        if half_width(successes, total) <= target_half_width:
            return total
    raise SamplingViolation("no attainable sample size for the requested precision")


@dataclass(frozen=True)
class SurfacePlan(Record):
    """One surface's prospective plan."""

    plan_id: str
    surface: str
    target_population: str
    sampling_unit: str
    minimum_adjudicable_units: int
    principal_units: int
    reserve_units: int
    strata: tuple[str, ...]
    source_family_grouping: str
    class_minima: Mapping[str, int]
    lifecycle_minima: Mapping[str, int]
    confidence_method: str
    reserve_policy: tuple[str, ...]
    missing_unit_policy: str
    unresolvable_unit_policy: str
    critical_failure_rule: str
    stopping_rule: str
    recorded_time: str


@dataclass(frozen=True)
class SamplingPlan(Record):
    """The frozen plan for one generation."""

    plan_id: str
    generation: str
    threshold: float
    max_interval_half_width: float
    surfaces: tuple[SurfacePlan, ...]
    interim_inspection_prohibited: bool
    recorded_time: str

    def for_surface(self, surface: str) -> SurfacePlan:
        for plan in self.surfaces:
            if plan.surface == surface:
                return plan
        raise SamplingViolation(f"no plan for {surface}")


def build_plan(generation: str, *, overprovision: float = 1.6) -> SamplingPlan:
    """§8 — the plan, computed from the declared precision, not chosen by hand."""
    needed = units_required_for_precision()
    surfaces = []
    for surface, minimum in MINIMUM_ADJUDICABLE.items():
        adjudicable = max(minimum, needed)
        principal = int(round(adjudicable * overprovision))
        strata = STRATA[surface]
        surfaces.append(SurfacePlan(
            stable_id("v5-8-1-surface-plan", generation, surface), surface,
            f"all frozen {surface} units of {generation}",
            {"SURFACE_1_EXTRACTION": "extraction candidate",
             "SURFACE_2_SOURCE_ROLES": "(source, entity, role) question",
             "SURFACE_3_DEPENDENCE": "ordered proposition pair",
             "SURFACE_4_CLAIM_SUPPORT": "claim with its evidence bundle",
             "SURFACE_5_TEMPORAL": "ordered proposition pair",
             "SURFACE_6_PUBLICATION": "report proposition"}[surface],
            adjudicable, principal, principal - adjudicable, strata,
            "source_family_id; no stratum may draw more than 35% of its units "
            "from one family",
            {stratum: 3 for stratum in strata},
            {stratum: 3 for stratum in strata if stratum.startswith("lifecycle_")},
            "Wilson score interval at 95%, reported with the effective "
            "adjudicable n; no normal approximation near the boundary",
            REPLACEABLE_CAUSES,
            "a unit with no production prediction is recorded INVALID and "
            "replaced from reserve only if its cause is replaceable",
            "an EPISTEMICALLY_UNRESOLVABLE panel answer is reported in its own "
            "column and excluded from the correctness denominator; replacement "
            "is permitted because this plan says so prospectively",
            "zero-tolerance conditions are counted over every reviewed unit, "
            "adjudicable or not, and are never subject to the interval",
            "review the full principal allocation; stop only when the "
            "allocation is exhausted or every surface has met its adjudicable "
            "minimum at the declared precision",
            now_utc()))
    return SamplingPlan(
        stable_id("v5-8-1-sampling-plan", generation), generation,
        CLEAN_THRESHOLD, MAX_INTERVAL_HALF_WIDTH, tuple(surfaces), True, now_utc())


# ===========================================================================
# §8.3 — the verdict rule
# ===========================================================================

#: V5.8.1 defect D37.  A conditional precision -- "correct among accepted" --
#: is only a measurement when the accepted population can support one.  Four
#: sessions reported accepted precision of 0.310, 0.667 and 0.500 over accepted
#: populations of 3, 3 and 2 units of 120, and compared them as though the
#: movement meant something.  It did not.  The minimum is derived from the same
#: half-width requirement the surface plans use, not chosen by hand afterwards.
ADJUDICABILITY_STATES: tuple[str, ...] = (
    "ADJUDICABLE",
    "NOT_ADJUDICABLE_INSUFFICIENT_ACCEPTED_N",
    "NOT_ADJUDICABLE_INSUFFICIENT_EFFECTIVE_N",
    "NOT_ADJUDICABLE_CONFIDENCE_TOO_WIDE",
)

#: Minimum distinct source families before a rate is treated as a property of
#: the system rather than of one document.
MIN_EFFECTIVE_FAMILIES = 3


def minimum_adjudicable_accepted(expected_rate: float = 0.95,
                                 half_width: float = MAX_INTERVAL_HALF_WIDTH) -> int:
    """How many accepted units a precision estimate needs, from the frozen rule."""
    return units_required_for_precision(expected_rate=expected_rate,
                                        target_half_width=half_width)


def precision_adjudicability(*, accepted_n: int, correct_accepted_n: int,
                             effective_families: int = 0,
                             half_width_requirement: float = MAX_INTERVAL_HALF_WIDTH
                             ) -> dict[str, Any]:
    """Report a conditional precision with the evidence for believing it.

    Returns the estimate *and* whether it may be used.  A NOT_ADJUDICABLE
    verdict is not a failure of the system under test; it is a statement that
    this measurement cannot decide anything, in either direction.
    """
    minimum = minimum_adjudicable_accepted(half_width=half_width_requirement)
    rate, low, high = wilson(correct_accepted_n, accepted_n)
    width = round((high - low) / 2, 4)
    if accepted_n < minimum:
        verdict = "NOT_ADJUDICABLE_INSUFFICIENT_ACCEPTED_N"
    elif effective_families and effective_families < MIN_EFFECTIVE_FAMILIES:
        verdict = "NOT_ADJUDICABLE_INSUFFICIENT_EFFECTIVE_N"
    elif width > half_width_requirement:
        verdict = "NOT_ADJUDICABLE_CONFIDENCE_TOO_WIDE"
    else:
        verdict = "ADJUDICABLE"
    return {
        "accepted_n": accepted_n,
        "correct_accepted_n": correct_accepted_n,
        "point_estimate": round(rate, 4) if accepted_n else None,
        "confidence_interval": [round(low, 4), round(high, 4)],
        "interval_half_width": width,
        "confidence_method": "WILSON_SCORE_95",
        "effective_source_families": effective_families,
        "minimum_adjudicable_accepted_n": minimum,
        "adjudicability": verdict,
        "may_contribute_to_pass": verdict == "ADJUDICABLE",
    }


def surface_verdict(*, surface: str, correct: int, adjudicable: int,
                    critical_failures: int, plan: SamplingPlan) -> dict[str, Any]:
    """Decide one surface, refusing to decide when the sample cannot carry it."""
    surface_plan = plan.for_surface(surface)
    point, low, high = wilson(correct, adjudicable)
    width = (high - low) / 2
    powered = (adjudicable >= surface_plan.minimum_adjudicable_units
               and width <= plan.max_interval_half_width)
    if critical_failures:
        verdict = "FAIL"
        reason = (f"{critical_failures} zero-tolerance failures; the interval is "
                  "irrelevant to a condition that must be zero")
    elif not powered:
        verdict = "NOT_ADJUDICABLE"
        reason = (f"effective adjudicable n={adjudicable} "
                  f"(required {surface_plan.minimum_adjudicable_units}) with 95% "
                  f"interval half-width {width:.4f} "
                  f"(required <= {plan.max_interval_half_width}); no threshold "
                  "verdict is available in either direction")
    elif low >= plan.threshold:
        verdict = "PASS_HARDENED"
        reason = (f"the whole 95% interval [{low:.4f}, {high:.4f}] lies at or "
                  f"above {plan.threshold}")
    elif point >= plan.threshold:
        verdict = "PASS_POINT_ESTIMATE_ONLY"
        reason = (f"point estimate {point:.4f} meets the threshold but the "
                  f"interval [{low:.4f}, {high:.4f}] reaches below it")
    else:
        verdict = "FAIL"
        reason = f"point estimate {point:.4f} is below {plan.threshold}"
    return {
        "surface": surface, "adjudicable_units": adjudicable, "correct": correct,
        "point_estimate": round(point, 4),
        "interval_95": [round(low, 4), round(high, 4)],
        "interval_half_width": round(width, 4),
        "minimum_adjudicable_units": surface_plan.minimum_adjudicable_units,
        "adequately_powered": powered, "critical_failures": critical_failures,
        "threshold": plan.threshold, "verdict": verdict, "reason": reason,
    }


def audit_reserve_use(replacements: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """§8.4 — every replacement must name a prospectively permitted cause."""
    rows = list(replacements)
    illegal = [row for row in rows if row.get("cause") not in REPLACEABLE_CAUSES]
    return {
        "replacements": len(rows),
        "by_cause": {cause: sum(1 for row in rows if row.get("cause") == cause)
                     for cause in sorted({str(row.get("cause")) for row in rows})},
        "illegal_replacements": illegal,
        "verdict": "PASS_HARDENED" if not illegal else "FAIL",
    }


__all__ = [
    "ADJUDICABILITY_STATES", "MIN_EFFECTIVE_FAMILIES",
    "minimum_adjudicable_accepted", "precision_adjudicability",
    "SamplingViolation", "MINIMUM_ADJUDICABLE", "MAX_INTERVAL_HALF_WIDTH",
    "CLEAN_THRESHOLD", "STRATA", "REPLACEABLE_CAUSES", "NON_REPLACEABLE_CAUSES",
    "wilson", "half_width", "units_required_for_precision", "SurfacePlan",
    "SamplingPlan", "build_plan", "surface_verdict", "audit_reserve_use",
]
