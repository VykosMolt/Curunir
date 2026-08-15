"""The Route 2 gate set: accepted precision reports, and does not vote.

The danger of making a precision gate non-binding is that a system which accepts
almost nothing then looks safe.  These tests exist to prove it cannot: the same
abstention that inflates precision fails the recall and completion gates, and a
passing verdict under abstention is refused outright.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import gates as G

pytestmark = pytest.mark.no_db

PASSING = {
    "complete_analysis_candidate_recall": 0.96,
    "compatible_candidate_survival": 0.99,
    "safety_critical_incompatible_rejection": 1.0,
    "selection_accuracy_given_compatible_survival": 0.96,
    "terminal_accuracy_given_correct_selection": 0.96,
    "final_disposition_accuracy_given_correct_terminal": 0.97,
    "subject_state_accuracy": 0.96, "predicate_state_accuracy": 0.96,
    "terminal_accuracy": 0.96, "subject_span_token_f1": 0.93,
    "predicate_span_token_f1": 0.94, "established_unit_recall": 0.93,
    "recoverable_unit_recall": 0.92, "task_completion": 0.93,
    "critical_failures": 0, "accepted_coverage": 0.28,
}


def test_a_fully_passing_system_passes():
    result = G.evaluate(PASSING, grouped={"ARABIC": 0.93, "RUSSIAN": 0.95})
    assert result["verdict"] == "PASS_HARDENED"
    assert result["claim_if_passing"] == G.CLAIM_AUTHORISED


def test_accepted_precision_never_appears_as_a_binding_gate():
    result = G.evaluate({**PASSING, "accepted_precision": 0.10},
                        grouped={"ARABIC": 0.93})
    assert "accepted_precision" not in result["binding_results"]
    assert "accepted_precision" not in result["failing_gates"]
    assert result["verdict"] == "PASS_HARDENED"


def test_a_perfect_accepted_precision_cannot_rescue_a_failing_system():
    failing = {**PASSING, "established_unit_recall": 0.03,
               "task_completion": 0.37, "accepted_precision": 1.0,
               "accepted_coverage": 0.017}
    result = G.evaluate(failing, grouped={})
    assert result["verdict"] == "FAIL"
    assert "established_unit_recall" in result["failing_gates"]
    assert "task_completion" in result["failing_gates"]


def test_near_total_abstention_is_detected_and_cannot_pass():
    """The abstention guard: the current system's own numbers."""
    abstaining = {**PASSING, "accepted_coverage": 0.0167,
                  "established_unit_recall": 0.033, "task_completion": 0.367,
                  "recoverable_unit_recall": 0.237}
    result = G.evaluate(abstaining, grouped={})
    assert result["near_total_abstention"] is True
    assert result["verdict"] == "FAIL"


def test_a_passing_verdict_under_abstention_is_refused_outright():
    report = {"verdict": "PASS_HARDENED", "near_total_abstention": True,
              "failing_gates": [], "binding_results": {},
              "accepted_precision_context": {f: 1 for f in G.REQUIRED_CONTEXT_FIELDS}}
    with pytest.raises(G.GateReportError):
        G.validate_report(report)


def test_a_precision_figure_without_its_denominator_is_refused():
    report = {"verdict": "FAIL", "failing_gates": [], "binding_results": {},
              "accepted_precision_context": {"point_estimate": 0.5}}
    with pytest.raises(G.GateReportError):
        G.validate_report(report)


def test_a_complete_context_block_is_accepted():
    report = {"verdict": "FAIL", "failing_gates": [], "binding_results": {},
              "near_total_abstention": True,
              "accepted_precision_context": {f: 1 for f in G.REQUIRED_CONTEXT_FIELDS}}
    G.validate_report(report)


def test_making_accepted_precision_vote_is_refused():
    report = {"verdict": "FAIL", "failing_gates": ["accepted_precision"],
              "binding_results": {},
              "accepted_precision_context": {f: 1 for f in G.REQUIRED_CONTEXT_FIELDS}}
    with pytest.raises(G.GateReportError):
        G.validate_report(report)


def test_a_failing_group_fails_the_milestone():
    result = G.evaluate(PASSING, grouped={"ARABIC": 0.62, "RUSSIAN": 0.95})
    assert result["verdict"] == "FAIL"
    assert result["binding_results"]["major_group_accuracy"]["failing_groups"] == ["ARABIC"]


def test_an_unmeasured_binding_gate_is_a_failure_not_a_pass():
    partial = {k: v for k, v in PASSING.items() if k != "terminal_accuracy"}
    result = G.evaluate(partial, grouped={})
    assert result["binding_results"]["terminal_accuracy"]["verdict"] == "NOT_MEASURED"
    assert result["verdict"] == "FAIL"


def test_the_forbidden_claims_are_named_explicitly():
    result = G.evaluate(PASSING, grouped={})
    assert "accepted precision >= 0.95" in result["claims_not_permitted"]
    assert "not adjudicable" in result["accepted_precision_note"]
