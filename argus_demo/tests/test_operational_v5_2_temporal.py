"""V5.2 repaired relation classifier (contract Section 13).

Two things are proved here: that all fourteen relation classes are reachable
with a constructed example, and that the specific V5.1 overreaches are closed.
"""
from __future__ import annotations

import pytest

from curunir_operational.v4.models import ClaimUnit
from curunir_operational.v5_1.contradiction import (
    apply_notice, definition_evidence, initial_claim_state, notice_record,
    subject_identity, temporal_frame,
)
from curunir_operational.v5_1.models import EvidenceRef, RELATION_CLASSES
from curunir_operational.v5_2.temporal import (
    DECISION_ORDER, RELATION_TRIGGERS, identity_dispute, relate,
    relation_class_coverage,
)

pytestmark = pytest.mark.no_db


def _claim(claim_id, subject, predicate, value, *, temporal=(None, None), geo=(),
           modality="ASSERTED", polarity="POSITIVE", text=None):
    body = text if text is not None else f"{subject} {predicate} {value}"
    return ClaimUnit(claim_id, "case-v5-2", body, body, subject, predicate, value,
                     temporal, geo, modality, polarity, "EXTRACTED",
                     ("basis-syn",), ("cand-syn",), "UNREVIEWED", 1)


def _notice_evidence():
    return EvidenceRef("EXPLICIT_TEXT_SPAN", "source-syn-1", None,
                       "notice span quoted from the issuing document")


def _identity_evidence():
    return EvidenceRef("OFFICIAL_IDENTIFIER", "source-syn-1", None,
                       "the two sources record different register identifiers "
                       "for the same designator")


# --- one constructed example per relation class ----------------------------

def _no_conflict():
    left = _claim("c-nc-1", "operator-1", "route length", "12 km")
    right = _claim("c-nc-2", "operator-1", "route length", "12 km")
    return relate(left, right)


def _temporal_update():
    left = _claim("c-tu-1", "operator-1", "route length", "12 km",
                  temporal=("2023", "2023"))
    right = _claim("c-tu-2", "operator-1", "route length", "18 km",
                   temporal=("2025", "2025"))
    return relate(left, right)


def _scope_difference():
    left = _claim("c-sd-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"), geo=("zone-a", "zone-b"))
    right = _claim("c-sd-2", "operator-1", "route length", "18 km",
                   temporal=("2024", "2024"), geo=("zone-a",))
    return relate(left, right)


def _definition_difference():
    left = _claim("c-dd-1", "operator-1", "coverage share", "40 %",
                  temporal=("2024", "2024"))
    right = _claim("c-dd-2", "operator-1", "coverage share", "60 %",
                   temporal=("2024", "2024"))
    definition = definition_evidence(
        term="coverage", left_definition="share of sites",
        right_definition="share of population",
        evidence=(EvidenceRef("EXPLICIT_TEXT_SPAN", "source-syn-2", None,
                              "the two documents define the term differently"),))
    return relate(left, right, definitions=(definition,))


def _source_disagreement():
    left = _claim("c-sr-1", "operator-1", "designated tier", "tier-alpha",
                  temporal=("2024", "2024"))
    right = _claim("c-sr-2", "operator-1", "designated tier", "tier-beta",
                   temporal=("2024", "2024"))
    return relate(left, right)


def _numeric_disagreement():
    left = _claim("c-nd-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-nd-2", "operator-1", "route length", "18 km",
                   temporal=("2024", "2024"))
    return relate(left, right)


def _identity_disagreement():
    left = _claim("c-id-1", "shared-designator entry a", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-id-2", "shared-designator entry b", "route length", "18 km",
                   temporal=("2024", "2024"))
    dispute = identity_dispute(
        designator="shared-designator", left_entity_id="entity-1",
        right_entity_id="entity-2", evidence=(_identity_evidence(),))
    return relate(left, right, dispute=dispute)


def _polarity_conflict():
    left = _claim("c-pc-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-pc-2", "operator-1", "route length", "12 km",
                   temporal=("2024", "2024"), polarity="NEGATIVE")
    return relate(left, right)


def _qualification():
    left = _claim("c-ql-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"), modality="CONDITIONAL")
    right = _claim("c-ql-2", "operator-1", "route length", "18 km",
                   temporal=("2024", "2024"))
    return relate(left, right)


def _notice_pair(relation):
    left = _claim("c-nt-1", "operator-1", "route length", "12 km")
    right = _claim("c-nt-2", "operator-1", "route length", "18 km")
    notice = notice_record(content_relation=relation, issuing_document_id="doc-syn-1",
                           evidence=(_notice_evidence(),),
                           target_claim_ids=("c-nt-1", "c-nt-2"))
    return left, right, notice


def _correction():
    left, right, notice = _notice_pair("CORRECTS")
    return relate(left, right, (notice,))


def _retraction():
    left, right, notice = _notice_pair("RETRACTS")
    return relate(left, right, (notice,))


def _supersession():
    left, right, notice = _notice_pair("SUPERSEDES")
    return relate(left, right, (notice,))


def _unresolved():
    left = _claim("c-un-1", "operator-1", "route length", "12 km")
    right = _claim("c-un-2", "operator-1", "route length", "18 km")
    return relate(left, right)


def _logical_contradiction():
    left = _claim("c-lc-1", "operator-1", "clearance state", "authorized",
                  temporal=("2024", "2024"))
    right = _claim("c-lc-2", "operator-1", "clearance state", "not authorized",
                   temporal=("2024", "2024"))
    return relate(left, right)


_EXAMPLES = {
    "NO_CONFLICT": _no_conflict,
    "TEMPORAL_UPDATE": _temporal_update,
    "SCOPE_DIFFERENCE": _scope_difference,
    "DEFINITION_DIFFERENCE": _definition_difference,
    "SOURCE_DISAGREEMENT": _source_disagreement,
    "NUMERIC_DISAGREEMENT": _numeric_disagreement,
    "IDENTITY_DISAGREEMENT": _identity_disagreement,
    "POLARITY_CONFLICT": _polarity_conflict,
    "QUALIFICATION": _qualification,
    "CORRECTION": _correction,
    "RETRACTION": _retraction,
    "SUPERSESSION": _supersession,
    "UNRESOLVED": _unresolved,
    "LOGICAL_CONTRADICTION": _logical_contradiction,
}


def test_every_relation_class_has_a_documented_trigger():
    assert frozenset(RELATION_TRIGGERS) == RELATION_CLASSES
    assert len(DECISION_ORDER) == 8
    assert all(len(trigger) > 40 for trigger in RELATION_TRIGGERS.values())


@pytest.mark.parametrize("relation", sorted(_EXAMPLES))
def test_relation_class_is_reachable(relation):
    assessment = _EXAMPLES[relation]().relation
    assert assessment == relation


def test_all_fourteen_classes_are_exercised_together():
    report = relation_class_coverage(builder() for builder in _EXAMPLES.values())
    assert report["classes_required"] == 14
    assert report["classes_exercised"] == 14
    assert report["missing"] == ()
    assert report["coverage_complete"] is True
    assert report["assessments"] == 14


# --- V5.1 defect closures --------------------------------------------------

def test_different_entities_are_no_conflict_not_identity_disagreement():
    """The 5254-relation V5.1 overreach: textually different subjects with no
    identity resolution were called IDENTITY_DISAGREEMENT."""
    left = _claim("c-de-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-de-2", "operator-2", "route length", "18 km",
                   temporal=("2024", "2024"))
    assessment = relate(left, right)
    assert assessment.relation == "NO_CONFLICT"
    assert assessment.outcome.outcome == "RESOLVED_WITH_MATERIAL_QUALIFICATION"
    assert "UNRESOLVED" in assessment.outcome.material_qualifications


def test_resolved_different_entity_is_no_conflict():
    left = _claim("c-rd-1", "operator-1", "route length", "12 km")
    right = _claim("c-rd-2", "operator-2", "route length", "18 km")
    identity = subject_identity(
        left_subject="operator-1", right_subject="operator-2", outcome="DIFFERENT_ENTITY",
        evidence=(_identity_evidence(),))
    assert relate(left, right, identity=identity).relation == "NO_CONFLICT"


def test_identity_disagreement_requires_affirmative_evidence():
    """An identity assessment that came back conflicting is a disagreement; an
    assessment that came back merely ambiguous is not."""
    left = _claim("c-ia-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-ia-2", "operator-2", "route length", "18 km",
                   temporal=("2024", "2024"))
    ambiguous = subject_identity(left_subject="operator-1", right_subject="operator-2",
                                 outcome="AMBIGUOUS")
    unresolved = relate(left, right, identity=ambiguous)
    assert unresolved.relation == "UNRESOLVED"
    assert unresolved.identity_blocker

    disputed = subject_identity(left_subject="operator-1", right_subject="operator-2",
                                outcome="AMBIGUOUS", evidence=(_identity_evidence(),))
    assert relate(left, right, identity=disputed).relation == "IDENTITY_DISAGREEMENT"


def test_identity_dispute_must_cover_the_pair():
    left = _claim("c-ic-1", "operator-1", "route length", "12 km")
    right = _claim("c-ic-2", "operator-2", "route length", "18 km")
    dispute = identity_dispute(designator="other-designator", left_entity_id="entity-1",
                               right_entity_id="entity-2",
                               evidence=(_identity_evidence(),))
    with pytest.raises(ValueError, match="does not cover"):
        relate(left, right, dispute=dispute)


def test_identity_dispute_requires_evidence_and_two_resolutions():
    with pytest.raises(ValueError, match="affirmative identity evidence"):
        identity_dispute(designator="d", left_entity_id="entity-1",
                         right_entity_id="entity-2", evidence=())
    with pytest.raises(ValueError, match="divergent resolutions"):
        identity_dispute(designator="d", left_entity_id="entity-1",
                         right_entity_id="entity-1",
                         evidence=(_identity_evidence(),))


def _plan_and_implementation(*, left_time, right_time):
    left = _claim("c-pl-1", "operator-1", "installed unit count", "200",
                  temporal=left_time,
                  text="the operator plans to deploy the counting units")
    right = _claim("c-pl-2", "operator-1", "installed unit count", "350",
                   temporal=right_time,
                   text="the operator has deployed the counting units")
    return left, right


def test_plan_versus_implementation_is_never_a_disagreement():
    left, right = _plan_and_implementation(left_time=("2024", "2024"),
                                           right_time=("2024", "2024"))
    assessment = relate(left, right)
    assert assessment.relation == "NO_CONFLICT"
    assert assessment.relation not in {"LOGICAL_CONTRADICTION", "NUMERIC_DISAGREEMENT"}
    assert "DEPLOYMENT_PLANNED" in assessment.rationale
    assert "DEPLOYED" in assessment.rationale


def test_plan_to_implementation_over_disjoint_periods_is_a_temporal_update():
    left, right = _plan_and_implementation(left_time=("2022", "2022"),
                                           right_time=("2025", "2025"))
    assert relate(left, right).relation == "TEMPORAL_UPDATE"


def test_supplied_lifecycle_states_override_the_derived_reading():
    left = _claim("c-ls-1", "operator-1", "installed unit count", "200",
                  temporal=("2024", "2024"))
    right = _claim("c-ls-2", "operator-1", "installed unit count", "350",
                   temporal=("2024", "2024"))
    assert relate(left, right).relation == "NUMERIC_DISAGREEMENT"
    entailed = relate(left, right, left_lifecycle="DEPLOYMENT_PLANNED",
                      right_lifecycle="OPERATIONAL")
    assert entailed.relation == "NO_CONFLICT"


def test_incomparable_lifecycle_branches_are_a_scope_difference():
    left = _claim("c-lb-1", "operator-1", "programme stage", "stage-alpha",
                  temporal=("2024", "2024"))
    right = _claim("c-lb-2", "operator-1", "programme stage", "stage-beta",
                   temporal=("2024", "2024"))
    assessment = relate(left, right, left_lifecycle="PILOT_ACTIVE",
                        right_lifecycle="PROCUREMENT_OPEN")
    assert assessment.relation == "SCOPE_DIFFERENCE"
    assert "PILOT_ACTIVE" in assessment.rationale
    assert "PROCUREMENT_OPEN" in assessment.rationale


def test_unresolved_carries_a_substantive_demonstration():
    assessment = _unresolved()
    assert assessment.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    demonstration = assessment.outcome.evidence_insufficiency_demonstration or ""
    assert len(demonstration) >= 40
    assert "event-time" in demonstration
    assert "scope" in demonstration and "lifecycle" in demonstration


def test_unresolved_runs_the_earlier_tests_first():
    """V5.1 emitted UNRESOLVED for any divergence with unknown event time; the
    scope, definition and lifecycle tests now settle those pairs first."""
    left = _claim("c-ur-1", "operator-1", "route length", "12 km", geo=("zone-a",))
    right = _claim("c-ur-2", "operator-1", "route length", "18 km", geo=("zone-b",))
    assert relate(left, right).relation == "NO_CONFLICT"

    nested_left = _claim("c-ur-3", "operator-1", "route length", "12 km",
                         geo=("zone-a", "zone-b"))
    nested_right = _claim("c-ur-4", "operator-1", "route length", "18 km",
                          geo=("zone-a",))
    assert relate(nested_left, nested_right).relation == "SCOPE_DIFFERENCE"

    planned = _claim("c-ur-5", "operator-1", "installed unit count", "200",
                     text="the operator plans to deploy the counting units")
    delivered = _claim("c-ur-6", "operator-1", "installed unit count", "350",
                       text="the operator has deployed the counting units")
    assert relate(planned, delivered).relation == "NO_CONFLICT"


def test_organizational_scope_is_tested_alongside_geography():
    left = _claim("c-os-1", "operator-1", "headcount", "40",
                  temporal=("2024", "2024"))
    right = _claim("c-os-2", "operator-1", "headcount", "90",
                   temporal=("2024", "2024"))
    assert relate(left, right).relation == "NUMERIC_DISAGREEMENT"
    scoped = relate(left, right, left_organizational_scope=("division-a",),
                    right_organizational_scope=("division-b",))
    assert scoped.relation == "NO_CONFLICT"
    assert "organizational" in scoped.rationale


def test_logical_contradiction_requires_mutual_exclusivity():
    """Divergent categorical values are a source disagreement; only an explicit
    negation or a declared exclusive vocabulary rules out every reading."""
    assert _source_disagreement().relation == "SOURCE_DISAGREEMENT"
    left = _claim("c-ex-1", "operator-1", "designated tier", "tier-alpha",
                  temporal=("2024", "2024"))
    right = _claim("c-ex-2", "operator-1", "designated tier", "tier-beta",
                   temporal=("2024", "2024"))
    declared = relate(left, right,
                      exclusive_value_sets=(("tier-alpha", "tier-beta", "tier-gamma"),))
    assert declared.relation == "LOGICAL_CONTRADICTION"


def test_preliminary_and_final_values_qualify_rather_than_conflict():
    left = _claim("c-pf-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"), modality="PRELIMINARY")
    right = _claim("c-pf-2", "operator-1", "route length", "18 km",
                   temporal=("2024", "2024"), modality="FINAL")
    assessment = relate(left, right)
    assert assessment.relation == "QUALIFICATION"
    assert "PRELIMINARY" in assessment.outcome.material_qualifications


def test_uninterpretable_units_remain_a_capability_failure():
    left = _claim("c-uu-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"))
    right = _claim("c-uu-2", "operator-1", "route length", "18 kg",
                   temporal=("2024", "2024"))
    assessment = relate(left, right)
    assert assessment.relation == "UNRESOLVED"
    assert assessment.outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert assessment.outcome.capability_failure_class == "SEMANTIC_TYPE_ERROR"


# --- notices still govern first -------------------------------------------

def test_notices_govern_before_every_semantic_test():
    left = _claim("c-ng-1", "operator-1", "route length", "12 km",
                  temporal=("2024", "2024"), geo=("zone-a",))
    right = _claim("c-ng-2", "operator-2", "route length", "18 km",
                   temporal=("2025", "2025"), geo=("zone-b",))
    notice = notice_record(content_relation="CORRECTS", issuing_document_id="doc-syn-1",
                           evidence=(_notice_evidence(),),
                           target_claim_ids=("c-ng-1",))
    assessment = relate(left, right, (notice,))
    assert assessment.relation == "CORRECTION"
    assert assessment.temporal_basis == "NOTICE_GOVERNED"
    assert assessment.governed_claim_ids == ("c-ng-1",)
    assert assessment.notice_ids == (notice.notice_id,)


def test_supersession_keeps_its_declared_scope_and_targets():
    assessment = _supersession()
    assert assessment.supersession_scope_kind == "CLAIM_SCOPE"
    assert assessment.supersession_target_ids == ("c-nt-1", "c-nt-2")


def test_whole_document_notice_still_requires_the_document_mapping():
    left = _claim("c-wd-1", "operator-1", "route length", "12 km")
    right = _claim("c-wd-2", "operator-1", "route length", "18 km")
    notice = notice_record(content_relation="SUPERSEDES", issuing_document_id="doc-syn-1",
                           evidence=(_notice_evidence(),), target_document_id="doc-syn-0",
                           scope_kind="WHOLE_DOCUMENT_SCOPE",
                           notice_span_coverage="DOCUMENT_LEVEL")
    with pytest.raises(ValueError, match="claim-to-document mapping"):
        relate(left, right, (notice,))
    governed = relate(left, right, (notice,),
                      claim_documents={"c-wd-1": "doc-syn-0", "c-wd-2": "doc-syn-1"})
    assert governed.relation == "SUPERSESSION"
    assert governed.governed_claim_ids == ("c-wd-1",)


def test_claim_version_state_machine_still_versions_and_keeps_history():
    correction = _correction()
    state = initial_claim_state("c-nt-1")
    corrected = apply_notice(state, correction)
    assert corrected.status == "CORRECTED"
    assert corrected.version == 2
    assert corrected.history == (state.state_id,)

    retraction = _retraction()
    retracted = apply_notice(corrected, retraction)
    assert retracted.status == "RETRACTED"
    assert retracted.version == 3
    assert len(retracted.history) == 2
    with pytest.raises(ValueError, match="terminal"):
        apply_notice(retracted, retraction)


# --- explicit frames and argument compatibility ----------------------------

def test_explicit_frames_override_claim_temporal_scope():
    left = _claim("c-fr-1", "operator-1", "route length", "12 km")
    right = _claim("c-fr-2", "operator-1", "route length", "18 km")
    frames = (temporal_frame(claim_id="c-fr-1", event_time=("2023", "2023"),
                             publication_time="2026-01-01"),
              temporal_frame(claim_id="c-fr-2", event_time=("2025", "2025"),
                             publication_time="2026-02-01"))
    assessment = relate(left, right, (), frames)
    assert assessment.relation == "TEMPORAL_UPDATE"
    assert assessment.temporal_basis == "DIFFERENT_EVENT_TIME"


def test_duplicate_frames_for_one_claim_are_rejected():
    left = _claim("c-df-1", "operator-1", "route length", "12 km")
    right = _claim("c-df-2", "operator-1", "route length", "18 km")
    frames = (temporal_frame(claim_id="c-df-1", event_time=("2023", "2023")),
              temporal_frame(claim_id="c-df-1", event_time=("2024", "2024")))
    with pytest.raises(ValueError, match="duplicate temporal frame"):
        relate(left, right, (), frames)


def test_relation_requires_two_distinct_claims():
    claim = _claim("c-sm-1", "operator-1", "route length", "12 km")
    with pytest.raises(ValueError, match="two distinct claims"):
        relate(claim, claim)


# --- coverage reporting ----------------------------------------------------

def test_relation_class_coverage_reports_missing_classes():
    report = relation_class_coverage([_no_conflict(), _numeric_disagreement()])
    assert report["exercised"] == ("NO_CONFLICT", "NUMERIC_DISAGREEMENT")
    assert len(report["missing"]) == 12
    assert "LOGICAL_CONTRADICTION" in report["missing"]
    assert report["coverage_complete"] is False
    assert report["counts"]["NO_CONFLICT"] == 1
    assert set(report["triggers_for_missing"]) == set(report["missing"])


def test_relation_class_coverage_accepts_records_and_names():
    report = relation_class_coverage(
        ["CORRECTION", {"relation": "RETRACTION"}, _supersession()])
    assert report["notice_classes_exercised"] == ("CORRECTION", "RETRACTION",
                                                  "SUPERSESSION")
    with pytest.raises(ValueError, match="unknown relation class"):
        relation_class_coverage(["NOT_A_CLASS"])
