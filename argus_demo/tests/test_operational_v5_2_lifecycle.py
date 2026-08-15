"""Lifecycle-state architecture tests (contract Section 8).

Every prohibited upward entailment in Section 8.3 is asserted, the five
critical error categories are asserted reachable, and the concrete V5.1
plan-as-implementation case is asserted closed.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_2 import lifecycle as L

pytestmark = pytest.mark.no_db


def test_all_twenty_four_states_present():
    # The contract lists 22 substantive states plus UNKNOWN.
    assert len(L.LIFECYCLE_STATES) == 23
    for state in ("CONCEPTUAL", "PROPOSED", "ANNOUNCED", "POLICY_ADOPTED",
                  "BUDGET_REQUESTED", "FUNDED", "PROCUREMENT_PLANNED",
                  "PROCUREMENT_OPEN", "CONTRACT_AWARDED", "IN_DEVELOPMENT",
                  "TECHNICALLY_AVAILABLE", "PILOT_PLANNED", "PILOT_ACTIVE",
                  "PILOT_COMPLETED", "EXERCISED", "DEPLOYMENT_PLANNED",
                  "DEPLOYED_LIMITED", "DEPLOYED", "OPERATIONAL", "SUSPENDED",
                  "CANCELLED", "RETIRED", "UNKNOWN"):
        assert state in L.LIFECYCLE_STATES


def test_all_fifteen_evidence_acts_present():
    assert len(L.EVIDENCE_ACTS) == 15
    assert set(L.ACT_MAXIMUM_STATE) == set(L.EVIDENCE_ACTS)


@pytest.mark.parametrize("weaker,stronger", [
    (weaker, stronger)
    for weaker, forbidden in L.PROHIBITED_UPWARD_ENTAILMENTS
    for stronger in forbidden
])
def test_prohibited_upward_entailments(weaker, stronger):
    assert not L.entails(weaker, stronger)


def test_downward_entailment_holds():
    assert L.entails("OPERATIONAL", "DEPLOYED")
    assert L.entails("DEPLOYED", "DEPLOYED_LIMITED")
    assert L.entails("CONTRACT_AWARDED", "PROCUREMENT_OPEN")
    assert L.entails("PILOT_COMPLETED", "PILOT_ACTIVE")


def test_lifecycle_is_a_dag_not_a_chain():
    # A pilot does not presuppose a procurement, and development does not
    # presuppose funding; a linear ladder would license both.
    assert not L.comparable("PILOT_ACTIVE", "CONTRACT_AWARDED")
    assert not L.comparable("IN_DEVELOPMENT", "FUNDED")


def test_terminal_states_do_not_entail_active_states():
    for terminal in sorted(L.TERMINAL_STATES):
        assert not L.entails(terminal, "OPERATIONAL")
        assert not L.entails(terminal, "DEPLOYED")


def test_unknown_entails_nothing():
    assert not L.entails("UNKNOWN", "PROPOSED")
    assert not L.entails("OPERATIONAL", "UNKNOWN")
    assert L.entails("UNKNOWN", "UNKNOWN")


def test_entailment_matrix_is_complete():
    matrix = L.entailment_matrix()
    assert set(matrix) == set(L.LIFECYCLE_STATES)
    for row in matrix.values():
        assert set(row) == set(L.LIFECYCLE_STATES)


# ---------------------------------------------------------------------------
# Derivation: futurity dominates the state noun
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,language,expected", [
    # The concrete V5.1 report-faithfulness failure.
    ("Dann soll die Verbindung wieder in Betrieb gehen.", "de", "DEPLOYMENT_PLANNED"),
    ("The Commission plans to deploy the system in 2027.", "en", "DEPLOYMENT_PLANNED"),
    ("La Commission prevoit de deployer le systeme.", "fr", "DEPLOYMENT_PLANNED"),
    ("Il sistema sara operativo dal 2027.", "it", "DEPLOYMENT_PLANNED"),
    ("The Commission has deployed the system across all member states.", "en", "DEPLOYED"),
    ("El sistema esta en funcionamiento desde 2025.", "es", "OPERATIONAL"),
    ("The system was tested during an exercise in June.", "en", "EXERCISED"),
    ("Council adopted the regulation on 16 January 2026.", "en", "POLICY_ADOPTED"),
    ("A call for tenders was published.", "en", "PROCUREMENT_OPEN"),
    ("The contract was awarded to the consortium.", "en", "CONTRACT_AWARDED"),
    ("The pilot phase is running at three sites.", "en", "PILOT_ACTIVE"),
    ("Funding was approved by the budget authority.", "en", "FUNDED"),
    ("Die Kommission hat das Vorhaben angekuendigt.", "de", "ANNOUNCED"),
    ("The project was cancelled.", "en", "CANCELLED"),
])
def test_derivation(text, language, expected):
    assert L.derive_lifecycle(text, language).state == expected


def test_planning_cue_records_what_it_demoted():
    reading = L.derive_lifecycle(
        "Dann soll die Verbindung wieder in Betrieb gehen.", "de")
    assert reading.demoted_from == "OPERATIONAL"
    assert reading.planning_cue == "soll"
    assert reading.evidence_act == "ACTOR_INTENTION"


def test_reading_never_claims_a_state_its_act_cannot_license():
    for text, language in [("The system is operational.", "en"),
                           ("Der Vertrag wurde vergeben.", "de"),
                           ("Le systeme est en service.", "fr")]:
        reading = L.derive_lifecycle(text, language)
        assert L.act_licenses(reading.evidence_act, reading.state)


# ---------------------------------------------------------------------------
# Claim integration
# ---------------------------------------------------------------------------

def test_assertion_refuses_a_state_its_evidence_cannot_license():
    with pytest.raises(ValueError, match="plan-as-implementation"):
        L.lifecycle_assertion(
            actor="an institution", target_object="a system",
            state="OPERATIONAL", evidence_act="ACTOR_INTENTION",
            support_span_ids=["span-1"])


def test_assertion_requires_actor_object_and_support():
    with pytest.raises(ValueError):
        L.lifecycle_assertion(actor="", target_object="a system",
                              state="PROPOSED", evidence_act="ACTOR_INTENTION",
                              support_span_ids=["span-1"])
    with pytest.raises(ValueError):
        L.lifecycle_assertion(actor="an institution", target_object="a system",
                              state="PROPOSED", evidence_act="ACTOR_INTENTION",
                              support_span_ids=[])


def test_unqualified_vendor_delivery_claim_is_refused():
    with pytest.raises(ValueError, match="qualification"):
        L.lifecycle_assertion(
            actor="a supplier", target_object="its platform", state="DEPLOYED",
            evidence_act="DEPLOYMENT_EVIDENCE", support_span_ids=["span-1"],
            source_authority="VENDOR_SELF_DESCRIPTION")


def test_qualified_vendor_delivery_claim_is_admissible():
    assertion = L.lifecycle_assertion(
        actor="a supplier", target_object="its platform", state="DEPLOYED",
        evidence_act="DEPLOYMENT_EVIDENCE", support_span_ids=["span-1"],
        source_authority="VENDOR_SELF_DESCRIPTION",
        qualification="the supplier is describing its own product")
    assert assertion.state == "DEPLOYED"
    assert "OPERATIONAL" in assertion.prohibited_stronger_formulations
    assert "DEPLOYED_LIMITED" in assertion.permitted_entailments


# ---------------------------------------------------------------------------
# Report wording guard
# ---------------------------------------------------------------------------

def test_wording_guard_refuses_strengthening():
    verdict = L.check_report_wording("The system has deployed nationwide.", "PROPOSED")
    assert not verdict.permitted
    assert verdict.violation == "LIFECYCLE_STRENGTHENED"


def test_wording_guard_catches_a_status_annotation():
    # V5.1 rendered "(status: operational)" beside a sentence whose German
    # modal reads as a plan.  The prose reads as planned; the annotation does
    # not, and the annotation is what the guard must catch.
    verdict = L.check_report_wording(
        "Dann soll die Verbindung wieder in Betrieb gehen (status: operational).",
        "DEPLOYMENT_PLANNED", language="de")
    assert not verdict.permitted
    assert verdict.sentence_state == "OPERATIONAL"


def test_wording_guard_allows_compression():
    verdict = L.check_report_wording("The regulation was adopted.", "OPERATIONAL")
    assert verdict.permitted


def test_wording_guard_refuses_a_state_with_no_claim_state():
    verdict = L.check_report_wording("The system is operational.", "UNKNOWN")
    assert not verdict.permitted
    assert verdict.violation == "LIFECYCLE_STATE_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Critical error classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("claimed,supported,expected", [
    ("OPERATIONAL", "DEPLOYMENT_PLANNED", "plan_as_implementation"),
    ("DEPLOYED", "PROPOSED", "plan_as_implementation"),
    ("DEPLOYED", "ANNOUNCED", "announcement_as_existing_capability"),
    ("OPERATIONAL", "PILOT_ACTIVE", "pilot_as_operational"),
    ("DEPLOYED", "EXERCISED", "exercise_as_deployment"),
    ("OPERATIONAL", "OPERATIONAL", None),
    ("DEPLOYED", "OPERATIONAL", None),
])
def test_classify_lifecycle_error(claimed, supported, expected):
    assert L.classify_lifecycle_error(claimed, supported) == expected


def test_vendor_claim_as_demonstrated_fact():
    assert L.classify_lifecycle_error(
        "DEPLOYED", "DEPLOYED", source_authority="VENDOR_SELF_DESCRIPTION",
        qualified=False) == "vendor_claim_as_demonstrated_fact"
    assert L.classify_lifecycle_error(
        "DEPLOYED", "DEPLOYED", source_authority="VENDOR_SELF_DESCRIPTION",
        qualified=True) is None


# ---------------------------------------------------------------------------
# Full-population migration
# ---------------------------------------------------------------------------

def test_migration_corrects_a_plan_recorded_as_operational():
    correction = L.migrate_claim({
        "claim_id": "historical-1",
        "normalized_statement": "Dann soll die Verbindung wieder in Betrieb gehen.",
        "language": "de", "modality": "OPERATIONAL",
    }, origin_milestone="V5_1")
    assert correction is not None
    assert correction.error_class == "plan_as_implementation"
    assert correction.corrected_state == "DEPLOYMENT_PLANNED"
    assert correction.original_modality == "OPERATIONAL"
    assert correction.version == 1


def test_migration_leaves_a_sound_claim_alone():
    assert L.migrate_claim({
        "claim_id": "historical-2",
        "normalized_statement": "Council adopted the regulation on 16 January 2026.",
        "language": "en", "modality": "ASSERTED", "lifecycle_state": "POLICY_ADOPTED",
    }, origin_milestone="V5_1") is None


def test_migration_never_rewrites_history_in_place():
    result = L.migrate_population([
        {"claim_id": "h1", "normalized_statement": "It will be deployed in 2028.",
         "language": "en", "modality": "DEPLOYED"},
    ], origin_milestone="V5_1")
    assert result["historical_objects_rewritten_in_place"] == 0
    assert result["correction_count"] == 1
    assert result["corrections"][0]["subject_id"] == "h1"


def test_population_audit_reports_zero_on_a_clean_population():
    counts = L.audit_population([
        {"state": "POLICY_ADOPTED", "evidence_act": "FORMAL_POLICY_DECISION"},
        {"state": "DEPLOYMENT_PLANNED", "evidence_act": "ACTOR_INTENTION"},
    ])
    assert all(value == 0 for value in counts.values())


def test_population_audit_catches_a_bypassed_constructor():
    counts = L.audit_population([
        {"state": "OPERATIONAL", "evidence_act": "ACTOR_INTENTION"},
    ])
    assert counts["plan_as_implementation"] == 1
