"""Focused tests: V5.1 Section 15 claim-support capability."""
from __future__ import annotations

import pytest

from curunir_operational.v5_1.claim_support import (
    EvidenceStatement, SemanticClaim, SupportAssessment, assess_support,
    audit_support_set, classify_claim, guarded_assess_support,
)

pytestmark = pytest.mark.no_db


def claim(**overrides):
    base = dict(
        claim_id="claim-1",
        statement="The northern registry platform is operational across the region.",
        subject="Northern Registry Platform", predicate="is operational",
        object_or_value="", polarity="POSITIVE", modality="ASSERTED",
        temporal_scope=(None, None), geographic_scope=(),
        entity_ids=("entity-platform-1",))
    base.update(overrides)
    return base


def evidence(**overrides):
    base = dict(
        evidence_id="evidence-1",
        statement="The registry platform has been operational since January.",
        subject="Northern Registry Platform", predicate="is operational",
        object_or_value="", polarity="POSITIVE", modality="OPERATIONAL",
        temporal_scope=(None, None), geographic_scope=(),
        entity_ids=("entity-platform-1",), source_class="OFFICIAL_REGISTER",
        correction_state="CURRENT", active=True)
    base.update(overrides)
    return base


# --- classification -----------------------------------------------------

def test_classify_core_classes():
    assert classify_claim(claim(
        statement="The northern agency maintains a public registry.",
        predicate="maintains")) == "DIRECT_FACTUAL_ASSERTION"
    assert classify_claim(claim(modality="REPORTED")) == "ATTRIBUTED_REPORT"
    assert classify_claim(claim()) == "DEPLOYMENT_CLAIM"
    assert classify_claim(claim(
        statement="The regulation prohibits automated screening of residents.",
        predicate="prohibits")) == "LEGAL_OR_REGULATORY_CLAIM"
    assert classify_claim(claim(
        statement="The platform is operated by the northern agency.",
        predicate="operated by")) == "OWNERSHIP_OR_GOVERNANCE_CLAIM"
    assert classify_claim(claim(
        statement="The analysis suite is described as able to detect patterns.",
        predicate="describes capability", modality="VENDOR_DESCRIBED")) == "CAPABILITY_CLAIM"


def test_classify_is_multilingual():
    assert classify_claim(claim(
        statement="Das System ist im Einsatz.", predicate="im Einsatz")) == "DEPLOYMENT_CLAIM"
    assert classify_claim(claim(
        statement="L'agence a lancé un appel d'offres pour la plateforme.",
        predicate="appel d'offres")) == "PROCUREMENT_CLAIM"
    assert classify_claim(claim(
        statement="Aucune preuve de l'existence du programme n'a été trouvée.",
        predicate="aucune preuve trouvée",
        polarity="NEGATIVE")) == "ABSENCE_OF_EVIDENCE_STATEMENT"


def test_classify_content_and_stance_classes():
    assert classify_claim(claim(
        statement="The unit count reached the target.", predicate="counts",
        object_or_value="412 units")) == "NUMERIC_CLAIM"
    assert classify_claim(claim(
        statement="The migration is scheduled.", predicate="scheduled for",
        object_or_value="2024-05")) == "TIMELINE_CLAIM"
    assert classify_claim(claim(
        statement="The outage led to the delayed audit.",
        predicate="led to")) == "CAUSAL_CLAIM"
    assert classify_claim(claim(epistemic_state="INFERRED")) == "INFERENCE"
    assert classify_claim(claim(qualifies_claim_id="claim-9")) == "QUALIFICATION"
    assert classify_claim(claim(
        statement="The rollout may be delayed.", predicate="may be delayed")) == "UNCERTAINTY"
    assert classify_claim(claim(
        statement="The agency never operated the platform.",
        predicate="never operated", polarity="NEGATIVE")) == "NEGATIVE_CLAIM"


def test_classify_rejects_uninterpretable_vocabulary():
    with pytest.raises(ValueError, match="modality"):
        classify_claim(claim(modality="SOMEHOW"))
    with pytest.raises(ValueError, match="polarity"):
        classify_claim(claim(polarity="MIXED"))
    with pytest.raises(ValueError, match="uninterpretable"):
        classify_claim(claim(statement="", predicate=""))


# --- wrong-* paths ------------------------------------------------------

def test_full_support_with_typed_carriers():
    typed_claim = SemanticClaim("claim-1", "The registry platform is operational.",
                                "Northern Registry Platform", "is operational", "",
                                "POSITIVE", "ASSERTED", entity_ids=("entity-platform-1",))
    typed_evidence = EvidenceStatement("evidence-1", "Operational per the annual register.",
                                       "Northern Registry Platform", "is operational", "",
                                       "POSITIVE", "OPERATIONAL",
                                       entity_ids=("entity-platform-1",),
                                       source_class="OFFICIAL_REGISTER")
    result = assess_support(typed_claim, [typed_evidence])
    assert result.state == "FULL_SUPPORT" and result.accepted_for_reporting
    assert result.supporting_evidence_ids == ("evidence-1",)
    assert not result.required_qualifications and not result.retracted_support_blocked


def test_wrong_entity():
    result = assess_support(claim(), [evidence(entity_ids=("entity-other-9",))])
    assert result.state == "WRONG_ENTITY" and not result.accepted_for_reporting


def test_wrong_time_on_disjoint_scopes():
    result = assess_support(
        claim(temporal_scope=("2021-01-01", "2021-12-31")),
        [evidence(temporal_scope=("2023-01-01", "2023-06-30"))])
    assert result.state == "WRONG_TIME" and not result.accepted_for_reporting


def test_wrong_polarity_never_silently_flipped():
    result = assess_support(claim(), [evidence(polarity="NEGATIVE")])
    assert result.state == "WRONG_POLARITY" and not result.accepted_for_reporting
    assert result.supporting_evidence_ids == ()


def test_wrong_scope_and_material_partial_scope():
    partial = assess_support(
        claim(geographic_scope=("region-north", "region-south")),
        [evidence(geographic_scope=("region-north",))])
    assert partial.state == "PARTIAL_SUPPORT" and partial.accepted_for_reporting
    disjoint = assess_support(
        claim(geographic_scope=("region-north",)),
        [evidence(geographic_scope=("region-east",))])
    assert disjoint.state == "WRONG_SCOPE"
    minority = assess_support(
        claim(geographic_scope=("region-north", "region-south", "region-west")),
        [evidence(geographic_scope=("region-north",))])
    assert minority.state == "WRONG_SCOPE"


def test_temporal_scope_dropped_claim_is_never_fully_supported():
    result = assess_support(
        claim(temporal_scope=(None, None)),
        [evidence(temporal_scope=("2024-01-01", "2024-12-31"))])
    assert result.state == "PARTIAL_SUPPORT"
    assert "TEMPORAL_SCOPE_NARROWER_THAN_CLAIM" in result.rationale


# --- modality ladder ----------------------------------------------------

def test_vendor_claim_as_fact_is_wrong_modality():
    result = assess_support(claim(), [evidence(
        source_class="VENDOR_SELF_DESCRIPTION", modality="OPERATIONAL")])
    assert result.state == "WRONG_MODALITY"
    assert result.modality_violations == (("evidence-1", "ASSERTED", "VENDOR_DESCRIBED"),)


def test_plan_as_implementation_is_wrong_modality():
    result = assess_support(claim(), [evidence(modality="PLANNED")])
    assert result.state == "WRONG_MODALITY"
    assert result.modality_violations[0][2] == "PLANNED"


def test_exercise_as_deployment_is_wrong_modality():
    result = assess_support(claim(modality="DEPLOYED"), [evidence(modality="EXERCISED")])
    assert result.state == "WRONG_MODALITY"
    assert result.modality_violations == (("evidence-1", "DEPLOYED", "EXERCISED"),)
    flattened = assess_support(claim(modality="ASSERTED"), [evidence(modality="PILOTED")])
    assert flattened.state == "WRONG_MODALITY"


def test_reported_evidence_cannot_support_unqualified_assertion():
    flattened = assess_support(
        claim(statement="The agency maintains the registry.", predicate="maintains"),
        [evidence(modality="REPORTED")])
    assert flattened.state == "WRONG_MODALITY"
    preserved = assess_support(claim(modality="REPORTED"), [evidence(modality="REPORTED")])
    assert preserved.state == "QUALIFIED_SUPPORT"
    assert preserved.required_qualifications == ("REPORTED",)


def test_stage_matched_exercise_support_keeps_its_marker():
    result = assess_support(claim(modality="EXERCISED"), [evidence(modality="EXERCISED")])
    assert result.state == "QUALIFIED_SUPPORT"
    assert result.required_qualifications == ("EXERCISED",)


def test_context_dependent_support_for_conditional_evidence():
    result = assess_support(claim(modality="CONDITIONAL"), [evidence(modality="CONDITIONAL")])
    assert result.state == "CONTEXT_DEPENDENT_SUPPORT" and result.accepted_for_reporting


# --- lifecycle and absence guards --------------------------------------

def test_retracted_evidence_is_blocked_never_counted():
    result = assess_support(claim(), [evidence(correction_state="RETRACTED")])
    assert result.state == "NOT_SUPPORTED" and result.retracted_support_blocked
    assert result.blocked_evidence_ids == ("evidence-1",)
    assert result.supporting_evidence_ids == ()
    superseded = assess_support(claim(), [evidence(correction_state="SUPERSEDED", active=False)])
    assert superseded.state == "NOT_SUPPORTED" and superseded.retracted_support_blocked


def test_current_corrected_evidence_supports_only_with_marker():
    result = assess_support(claim(), [evidence(correction_state="CORRECTED")])
    assert result.state == "QUALIFIED_SUPPORT"
    assert "CORRECTED" in result.required_qualifications


def test_absence_statement_supported_only_as_absence():
    absence_claim = claim(
        statement="No public records of the coastal pilot programme were found.",
        predicate="no records found", polarity="NEGATIVE")
    absence_evidence = evidence(
        statement="Keine Belege zu dem Pilotprogramm wurden im Archiv gefunden.",
        predicate="keine belege gefunden", polarity="NEGATIVE", modality="ASSERTED")
    result = assess_support(absence_claim, [absence_evidence])
    assert result.claim_class == "ABSENCE_OF_EVIDENCE_STATEMENT"
    assert result.state == "QUALIFIED_SUPPORT"
    assert "UNRESOLVED" in result.required_qualifications


def test_absence_evidence_is_never_negative_proof():
    negative_claim = claim(
        statement="The agency never operated the platform.",
        predicate="never operated", polarity="NEGATIVE")
    absence_evidence = evidence(
        statement="Aucune preuve d'exploitation n'a été trouvée dans les archives.",
        predicate="aucune preuve", polarity="NEGATIVE", modality="ASSERTED")
    result = assess_support(negative_claim, [absence_evidence])
    assert result.claim_class == "NEGATIVE_CLAIM"
    assert result.state == "INFERENCE_ONLY" and not result.accepted_for_reporting


def test_mixed_polarity_evidence_yields_contradicted():
    result = assess_support(claim(), [
        evidence(), evidence(evidence_id="evidence-2", polarity="NEGATIVE")])
    assert result.state == "CONTRADICTED" and not result.accepted_for_reporting


# --- record guards ------------------------------------------------------

def test_assessment_guard_rejects_accepting_wrong_states():
    with pytest.raises(ValueError, match="accepted"):
        SupportAssessment("assessment-x", "claim-1", "DEPLOYMENT_CLAIM", "WRONG_SCOPE",
                          ("evidence-1",), (), (), False, (), (), (), True,
                          "reason", "2026-07-24T00:00:00+00:00")


def test_assessment_guard_qualified_support_requires_markers():
    with pytest.raises(ValueError, match="qualification"):
        SupportAssessment("assessment-x", "claim-1", "DEPLOYMENT_CLAIM", "QUALIFIED_SUPPORT",
                          ("evidence-1",), ("evidence-1",), (), False, (), (), (), True,
                          "reason", "2026-07-24T00:00:00+00:00")


def test_assessment_guard_blocked_evidence_never_supporting():
    with pytest.raises(ValueError, match="never be counted"):
        SupportAssessment("assessment-x", "claim-1", "DEPLOYMENT_CLAIM", "FULL_SUPPORT",
                          ("evidence-1",), ("evidence-1",), ("evidence-1",), True, (), (), (),
                          True, "reason", "2026-07-24T00:00:00+00:00")


def test_typed_carrier_guards():
    with pytest.raises(ValueError, match="polarity"):
        EvidenceStatement("evidence-1", "text", "subject", "predicate", "",
                          "SIDEWAYS", "ASSERTED")
    with pytest.raises(ValueError, match="modality"):
        SemanticClaim("claim-1", "text", "subject", "predicate", "",
                      "POSITIVE", "GUESSED")


# --- capability failure surfacing ---------------------------------------

def test_guarded_assessment_records_system_capability_failure():
    assessment, outcome = guarded_assess_support(claim(), [evidence(modality="???")])
    assert assessment is None and outcome is not None
    assert outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert outcome.subject_kind == "CLAIM_SUPPORT_RELATION"
    assert outcome.capability_failure_class == "MODALITY_LOSS"
    healthy, no_failure = guarded_assess_support(claim(), [evidence()])
    assert healthy is not None and no_failure is None


# --- audit ---------------------------------------------------------------

def test_audit_clean_set_has_zero_counters_and_coverage():
    assessments = [
        assess_support(claim(), [evidence()]),
        assess_support(claim(claim_id="claim-2", modality="REPORTED"),
                       [evidence(modality="REPORTED")]),
        assess_support(claim(claim_id="claim-3"), [evidence(modality="PLANNED")]),
        assess_support(claim(claim_id="claim-4"), [evidence(correction_state="RETRACTED")]),
    ]
    report = audit_support_set(assessments)
    assert report["total_assessments"] == 4 and report["gate_clean"]
    assert report["zero_tolerance_total"] == 0
    assert report["class_coverage"]["DEPLOYMENT_CLAIM"] == 3
    assert report["class_coverage"]["ATTRIBUTED_REPORT"] == 1
    assert "NUMERIC_CLAIM" in report["missing_classes"]
    assert report["state_counts"]["WRONG_MODALITY"] == 1


def test_audit_counts_foreign_zero_tolerance_violations():
    def record(**overrides):
        base = dict(claim_id="claim-x", claim_class="DEPLOYMENT_CLAIM", state="FULL_SUPPORT",
                    accepted_for_reporting=True, supporting_evidence_ids=["evidence-1"],
                    blocked_evidence_ids=[], required_qualifications=[],
                    modality_violations=[])
        base.update(overrides)
        return base

    report = audit_support_set([
        record(state="WRONG_MODALITY", supporting_evidence_ids=[],
               modality_violations=[["evidence-1", "ASSERTED", "VENDOR_DESCRIBED"]]),
        record(state="WRONG_MODALITY", supporting_evidence_ids=[],
               modality_violations=[["evidence-1", "ASSERTED", "PLANNED"]]),
        record(state="WRONG_MODALITY", supporting_evidence_ids=[],
               modality_violations=[["evidence-1", "DEPLOYED", "EXERCISED"]]),
        record(state="WRONG_TIME", supporting_evidence_ids=[]),
        record(state="WRONG_SCOPE", supporting_evidence_ids=[]),
        record(state="WRONG_POLARITY", supporting_evidence_ids=[]),
        record(state="INFERENCE_ONLY", supporting_evidence_ids=[]),
        record(state="NOT_SUPPORTED", supporting_evidence_ids=[]),
        record(blocked_evidence_ids=["evidence-1"]),
    ])
    counters = report["zero_tolerance"]
    assert counters["wrong_modality_accepted"] == 3
    assert counters["vendor_as_fact"] == 1
    assert counters["plan_as_implementation"] == 1
    assert counters["exercise_as_deployment"] == 1
    assert counters["wrong_time_accepted"] == 1
    assert counters["wrong_scope_accepted"] == 1
    assert counters["wrong_polarity_accepted"] == 1
    assert counters["inference_as_fact"] == 1
    assert counters["unsupported_accepted"] == 1
    assert counters["retracted_support_used"] == 1
    assert not report["gate_clean"]


def test_audit_rejects_uninterpretable_records():
    with pytest.raises(ValueError, match="support state"):
        audit_support_set([{"claim_class": "NUMERIC_CLAIM", "state": "KIND_OF_FINE"}])
    with pytest.raises(ValueError, match="qualification"):
        audit_support_set([{"claim_class": "NUMERIC_CLAIM", "state": "QUALIFIED_SUPPORT",
                            "required_qualifications": []}])
