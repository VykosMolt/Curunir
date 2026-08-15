"""V5.8.1 — the frozen D25 gate set, after the Route 2 decision.

Accepted precision is reported and does not vote.  D40 established that the
prospective design can never supply an adjudicable denominator for it: 82
accepted units are required and 3-4 are effectively available after
source-family clustering.  Spending 1,800-2,700 further development units to
power a downstream conditional metric, for an implementation that has not met
its basic semantic gates, was declined.

The risk this creates is obvious and is guarded explicitly: a system that
accepts almost nothing would trivially satisfy a precision gate it never
exercises.  So established-unit recall, recoverable recall, coverage and task
completion are all binding, and all of them fail under abstention.  The gate set
cannot be passed by refusing to answer.
"""

from __future__ import annotations

from typing import Any

#: Gates that decide the milestone verdict.
BINDING_GATES: dict[str, float] = {
    # layer-specific
    "complete_analysis_candidate_recall": 0.95,
    "compatible_candidate_survival": 0.95,
    "safety_critical_incompatible_rejection": 1.00,
    "selection_accuracy_given_compatible_survival": 0.95,
    "terminal_accuracy_given_correct_selection": 0.95,
    "final_disposition_accuracy_given_correct_terminal": 0.95,
    # direct semantic
    "subject_state_accuracy": 0.95,
    "predicate_state_accuracy": 0.95,
    "terminal_accuracy": 0.95,
    "subject_span_token_f1": 0.90,
    "predicate_span_token_f1": 0.90,
    # coverage and completion — the abstention guard
    "established_unit_recall": 0.90,
    "recoverable_unit_recall": 0.90,
    "task_completion": 0.90,
    # grouped
    "major_group_accuracy": 0.90,
}

#: Gates that must be exactly zero.
ZERO_GATES: tuple[str, ...] = ("critical_failures",)

#: Reported, never decisive.  Listing it here is the mechanism: a metric in this
#: set cannot contribute to PASS or FAIL however it is computed.
NON_BINDING_REPORTED: frozenset[str] = frozenset({"accepted_precision"})

#: Every non-binding metric must still be reported with the evidence that shows
#: why it is not decisive.  Omitting any of these is a reporting failure.
REQUIRED_CONTEXT_FIELDS: tuple[str, ...] = (
    "accepted_n", "accepted_coverage", "point_estimate", "confidence_interval",
    "effective_source_families", "minimum_adjudicable_accepted_n",
)

#: Below this accepted coverage the system is abstaining rather than deciding.
#: It is not itself a gate — the recall gates already fail — but it must be
#: stated in the report so no reader mistakes abstention for precision.
ABSTENTION_COVERAGE_FLOOR = 0.05

CLAIM_AUTHORISED = (
    "The role-binding system met the predeclared state-accuracy, span, recall, "
    "coverage, completion, layer-specific, grouped, and zero-critical-failure "
    "gates on prospective model-panel evaluation.")

CLAIM_FORBIDDEN: tuple[str, ...] = (
    "accepted precision >= 0.95",
    "high-precision acceptance",
    "95% precision established",
)

ACCEPTED_PRECISION_WORDING = (
    "Accepted precision was not adjudicable at the predeclared confidence "
    "precision because the accepted subset was too small. The estimate and "
    "interval are reported descriptively and do not contribute to the milestone "
    "verdict.")


class GateReportError(RuntimeError):
    """A report tried to let a non-binding metric decide, or omitted its context."""


def evaluate(observed: dict[str, Any], *, grouped: dict[str, float] | None = None
             ) -> dict[str, Any]:
    """Decide the milestone verdict from the binding gates only."""
    results: dict[str, Any] = {}
    failures: list[str] = []
    for name, floor in BINDING_GATES.items():
        if name == "major_group_accuracy":
            continue
        value = observed.get(name)
        if value is None:
            results[name] = {"observed": None, "gate": floor,
                             "verdict": "NOT_MEASURED"}
            failures.append(name)
            continue
        passed = value >= floor
        results[name] = {"observed": value, "gate": floor,
                         "verdict": "PASS" if passed else "FAIL"}
        if not passed:
            failures.append(name)
    for name in ZERO_GATES:
        value = observed.get(name)
        passed = value == 0
        results[name] = {"observed": value, "gate": 0,
                         "verdict": "PASS" if passed else "FAIL"}
        if not passed:
            failures.append(name)

    group_failures = sorted(
        name for name, value in (grouped or {}).items()
        if value < BINDING_GATES["major_group_accuracy"])
    results["major_group_accuracy"] = {
        "gate": BINDING_GATES["major_group_accuracy"],
        "failing_groups": group_failures,
        "verdict": "PASS" if not group_failures else "FAIL"}
    if group_failures:
        failures.append("major_group_accuracy")

    coverage = observed.get("accepted_coverage")
    abstaining = coverage is not None and coverage < ABSTENTION_COVERAGE_FLOOR
    return {
        "binding_results": results,
        "failing_gates": failures,
        "verdict": "PASS_HARDENED" if not failures else "FAIL",
        "non_binding_reported": sorted(NON_BINDING_REPORTED),
        "accepted_precision_note": ACCEPTED_PRECISION_WORDING,
        "near_total_abstention": abstaining,
        "claim_if_passing": CLAIM_AUTHORISED,
        "claims_not_permitted": list(CLAIM_FORBIDDEN),
    }


def validate_report(report: dict[str, Any]) -> None:
    """Refuse a report that lets a non-binding metric vote, or hides its context."""
    for metric in NON_BINDING_REPORTED:
        if metric in report.get("failing_gates", ()):
            raise GateReportError(
                f"{metric} is non-binding and must not appear as a failing gate")
        if metric in report.get("binding_results", {}):
            raise GateReportError(
                f"{metric} is non-binding and must not appear in binding results")
        context = report.get(f"{metric}_context")
        if context is None:
            raise GateReportError(f"{metric} reported without its context block")
        missing = [f for f in REQUIRED_CONTEXT_FIELDS if f not in context]
        if missing:
            raise GateReportError(
                f"{metric} context is missing {missing}; a precision figure "
                "without its denominator, interval and minimum is not a report")
    if report.get("near_total_abstention") and report.get("verdict") == "PASS_HARDENED":
        raise GateReportError(
            "a passing verdict under near-total abstention is impossible: the "
            "recall and completion gates must have failed")


__all__ = [
    "BINDING_GATES", "ZERO_GATES", "NON_BINDING_REPORTED",
    "REQUIRED_CONTEXT_FIELDS", "ABSTENTION_COVERAGE_FLOOR", "CLAIM_AUTHORISED",
    "CLAIM_FORBIDDEN", "ACCEPTED_PRECISION_WORDING", "GateReportError",
    "evaluate", "validate_report",
]
