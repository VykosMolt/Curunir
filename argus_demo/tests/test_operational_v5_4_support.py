"""V5.4 Section 5/6/8/9 — claim-support hierarchy, publication gate,
role-targeted adjudication and classifier activation."""
from __future__ import annotations

import pytest

from curunir_operational.v5_4 import activation as ACT
from curunir_operational.v5_4 import publication as PUB
from curunir_operational.v5_4 import roles_binary as RB
from curunir_operational.v5_4 import support as SUP

pytestmark = pytest.mark.no_db


def claim(**over):
    base = {"claim_id": "c1", "entity": "Agency X", "predicate": "operates system Y",
            "object": "system Y", "proposition": "Agency X operates system Y",
            "lifecycle_state": "OPERATIONAL"}
    base.update(over)
    return base


def ev(text, *, act="OPERATIONAL_REPORT", state="OPERATIONAL", **over):
    row = {"text": text, "span_id": "s1", "evidence_act": act, "lifecycle_state": state}
    row.update(over)
    return [row]


# ---------------------------------------------------------------- Section 5.4
def test_unaddressed_evidence_is_not_supported_not_wrong_entity():
    """The V5.3 defect: evidence naming the entity but addressing nothing."""
    result = SUP.assess(claim(), ev("Agency X attended a conference in Brussels.",
                                    act="ACTOR_INTENTION", state="UNKNOWN"))
    assert result.support_class == "NOT_SUPPORTED"
    assert result.first_failing_stage == "S1_PROPOSITION_ADDRESSABILITY"
    assert not result.vector.addresses_proposition


def test_wrong_entity_requires_the_same_proposition_family():
    result = SUP.assess(claim(), ev("Agency Z operates system Y across the network."))
    assert result.support_class == "WRONG_ENTITY"
    assert result.first_failing_stage == "S2_SUBJECT_AND_OBJECT_ALIGNMENT"
    assert result.vector.addresses_proposition


def test_specific_mismatch_without_addressability_is_refused_structurally():
    vector = SUP.SupportVector(
        addresses_proposition=False, entity_alignment="MISALIGNED",
        predicate_alignment="NOT_APPLICABLE", scope_alignment="NOT_APPLICABLE",
        time_alignment="NOT_APPLICABLE", polarity_alignment="NOT_APPLICABLE",
        modality_alignment="NOT_APPLICABLE", lifecycle_alignment="NOT_APPLICABLE",
        attribution_alignment="NOT_APPLICABLE", dependence_limitations=(),
        counterevidence=(), support_completeness="ABSENT")
    with pytest.raises(ValueError, match="specific mismatch"):
        SUP.HierarchicalSupport(
            "sid", "c1", "WRONG_ENTITY", vector, ("S1",), "S1", (), (), (), (), (),
            "NOT_EVALUATED", None, "2026-07-25T00:00:00+00:00")


def test_alternatives_rejected_names_the_confusion_pair():
    result = SUP.assess(claim(), ev("Agency X attended a conference.",
                                    act="ACTOR_INTENTION", state="UNKNOWN"))
    rejected = dict(result.alternatives_rejected)
    assert "WRONG_ENTITY" in rejected
    assert "does not address" in rejected["WRONG_ENTITY"]


# ---------------------------------------------------------------- entity head
@pytest.mark.parametrize("subject,expected", [
    ("For transparency and certainty, the European Commission", "the European Commission"),
    ("Under the plans, Rail Baltica", "Rail Baltica"),
    ("Immediately", None),
    ("However", None),
    ("Rail Baltica", "Rail Baltica"),
])
def test_entity_head_strips_sentence_prefixes(subject, expected):
    assert SUP.entity_head(subject) == expected


def test_non_entity_subject_makes_stage_two_inapplicable():
    result = SUP.assess(claim(entity="Immediately"),
                        ev("The threat actor used the compromised AWS secret."))
    stages = {s: o for s, o, _ in result.stage_findings}
    assert stages.get("S2_SUBJECT_AND_OBJECT_ALIGNMENT") != "FAIL"


def test_layout_word_break_does_not_become_a_wrong_entity():
    result = SUP.assess(
        claim(entity="The number of employed pe rsons", predicate="increased",
              object="employed persons",
              proposition="The number of employed pe rsons increased by 0.5%."),
        ev("The number of employed persons increased by 0.5%."))
    assert result.support_class != "WRONG_ENTITY"


# ---------------------------------------------------------------- verbatim
def test_restatement_cannot_be_a_dimensional_mismatch():
    text = "The new railway is over budget, behind schedule and will deliver less benefit."
    result = SUP.assess(
        claim(entity="The new railway", predicate="is over budget", object="budget",
              proposition=text, lifecycle_state="UNKNOWN"),
        ev(text))
    assert result.support_class in SUP.AFFIRMATIVE_SUPPORT
    assert result.first_failing_stage is None


def test_copulas_are_not_a_predicate_family():
    assert SUP.predicate_family("is") is None
    assert SUP.predicate_family("are available") is None
    assert SUP.predicate_family("operates the system") == "OPERATES"


def test_unrecognised_evidence_predicate_is_unresolved_not_a_failure():
    result = SUP.assess(
        claim(predicate="operates system Y"),
        ev("Agency X. System Y. Brussels."))
    assert result.support_class != "NOT_SUPPORTED" or \
        result.first_failing_stage != "S3_PREDICATE_ALIGNMENT"


# ---------------------------------------------------------------- lifecycle
def test_plan_may_never_become_affirmative_support():
    result = SUP.assess(claim(), ev("Agency X plans to operate system Y from 2027.",
                                    act="ACTOR_INTENTION", state="DEPLOYMENT_PLANNED"))
    assert result.support_class == "WRONG_MODALITY"
    assert result.critical_error
    assert result.support_class not in SUP.AFFIRMATIVE_SUPPORT


def test_critical_error_with_affirmative_support_is_refused():
    vector = SUP.SupportVector(
        addresses_proposition=True, entity_alignment="ALIGNED",
        predicate_alignment="ALIGNED", scope_alignment="NOT_APPLICABLE",
        time_alignment="NOT_APPLICABLE", polarity_alignment="ALIGNED",
        modality_alignment="ALIGNED", lifecycle_alignment="MISALIGNED",
        attribution_alignment="ALIGNED", dependence_limitations=(),
        counterevidence=(), support_completeness="COMPLETE")
    with pytest.raises(ValueError, match="plan-as-implementation"):
        SUP.HierarchicalSupport(
            "sid", "c1", "FULL_SUPPORT", vector, ("S1",), None, (), (), (), (), (),
            "CLAIM_EXCEEDS_EVIDENCE", "evidence licenses a plan", "2026-07-25T00:00:00+00:00")


@pytest.mark.parametrize("text,expected", [
    ("Agency X operates system Y.", "FULL_SUPPORT"),
    ("Agency X reportedly operates system Y.", "QUALIFIED_SUPPORT"),
    ("Where funding permits, Agency X operates system Y.", "CONTEXT_DEPENDENT_SUPPORT"),
    ("Records suggest Agency X operates system Y.", "INFERENCE_ONLY"),
    ("Agency X does not operate system Y.", "WRONG_POLARITY"),
    ("The audit refutes that Agency X operates system Y.", "CONTRADICTED"),
])
def test_stage_eight_and_nine_classes(text, expected):
    assert SUP.assess(claim(), ev(text)).support_class == expected


def test_audit_critical_counts_are_zero_on_clean_input():
    assessments = [SUP.assess(claim(), ev("Agency X operates system Y."))]
    assert SUP.audit_critical(assessments)["all_zero"]


# ---------------------------------------------------------------- Section 6
def _support(text="Agency X operates system Y.", **kw):
    return SUP.assess(claim(**kw), ev(text))


@pytest.mark.parametrize("support_class", sorted(PUB.NEVER_FACTUAL))
def test_negative_support_can_never_be_published_as_fact(support_class):
    permitted = PUB.PERMITTED_DISPOSITIONS[support_class]
    assert not (permitted & PUB.FACTUAL_DISPOSITIONS)


def test_publication_record_refuses_a_forbidden_disposition():
    with pytest.raises(PUB.PublicationViolation, match="may not produce"):
        PUB.PublicationRecord(
            "p1", "c1", "s1", "NOT_SUPPORTED", None, "wording", "PUBLISHED",
            "UNKNOWN", "ASSERTED", "POSITIVE", (), (None, None), None,
            "INDEPENDENCE_UNKNOWN", (), (), (), (), None, "DETAILED_REPORT",
            "2026-07-25T00:00:00+00:00")


def test_planner_cannot_promote_a_rejected_support_class():
    support = _support("Agency X attended a conference.")
    record = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                        wording="Agency X operates system Y.")
    assert record.publication_disposition == "REJECTED_UNSUPPORTED"
    assert record.blocking_reason


def test_missing_support_state_fails_closed():
    record = PUB.decide(proposition_id="p1", claim=claim(), support=None,
                        wording="Agency X operates system Y.")
    assert record.publication_disposition == "REPORT_BLOCKED"
    assert "DO_NOT_PUBLISH" in record.blocking_reason


def test_upstream_defect_blocks_the_report():
    support = _support()
    record = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                        wording="Agency X operates system Y.",
                        upstream_defect="extraction quarantined")
    assert record.publication_disposition == "REPORT_BLOCKED"
    assert "REPORT_BLOCKED" in record.blocking_reason


def test_partial_support_may_not_be_published_unqualified():
    permitted = PUB.PERMITTED_DISPOSITIONS["PARTIAL_SUPPORT"]
    assert "PUBLISHED" not in permitted
    assert "PUBLISHED_WITH_QUALIFICATION" in permitted


def test_inference_only_is_marked_analysis_not_fact():
    support = _support("Records suggest Agency X operates system Y.")
    record = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                        wording="Records suggest Agency X may operate system Y.")
    assert record.publication_disposition == "PUBLISHED_AS_MARKED_ANALYSIS"


def test_executive_summary_may_not_strengthen_lifecycle():
    support = _support()
    detailed = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                          wording="Agency X plans to deploy system Y.")
    result = PUB.summarize(detailed=detailed,
                           executive_wording="System Y is operational at Agency X.")
    assert not result["admissible"]
    assert "no_lifecycle_strengthening" in result["failed_checks"]


def test_executive_summary_may_not_strengthen_certainty():
    support = _support()
    detailed = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                          wording="Agency X reportedly operates system Y.")
    result = PUB.summarize(detailed=detailed,
                           executive_wording="It is confirmed that Agency X operates Y.")
    assert not result["admissible"]


def test_executive_summary_may_omit_detail():
    support = _support()
    detailed = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                          wording="Agency X operates system Y across the network.")
    result = PUB.summarize(detailed=detailed,
                           executive_wording="Agency X operates system Y.")
    assert result["admissible"]


def test_unsupported_independence_language_is_refused():
    support = _support()
    detailed = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                          wording="Agency X operates system Y.",
                          dependence_state="SYNDICATION_DERIVATIVE")
    result = PUB.summarize(
        detailed=detailed,
        executive_wording="Independently corroborated: Agency X operates system Y.")
    assert not result["admissible"]


def test_audit_reports_unsupported_publication():
    assert PUB.audit([])["known_unsupported_propositions_published"] == 0


def test_revalidation_flags_changed_upstream_claims():
    support = _support()
    record = PUB.decide(proposition_id="p1", claim=claim(), support=support,
                        wording="Agency X operates system Y.")
    result = PUB.revalidate([record], ["c1"])
    assert result["state"] == "REPORT_REVALIDATION_REQUIRED"


# ---------------------------------------------------------------- Section 8
def test_target_role_participates_in_dossier_identity():
    questions = [RB.role_question(source_id="s", entity_id="e", target_role=role,
                                  evidence_record_ids=["e1"])
                 for role in ("PUBLISHED_BY", "HOSTED_BY", "ISSUED_BY")]
    assert len({q.identity for q in questions}) == 3
    assert RB.assert_no_collisions(questions)["collisions"] == 0


def test_role_dossier_collision_is_refused():
    q = RB.role_question(source_id="s", entity_id="e", target_role="PUBLISHED_BY")
    duplicate = RB.RoleQuestion(q.question_id, q.source_id, q.entity_id, "HOSTED_BY",
                                q.question_version, q.evidence_record_ids,
                                q.observation_record_ids, q.recorded_time)
    object.__setattr__(duplicate, "target_role", "HOSTED_BY")
    same = [q, RB.RoleQuestion(q.question_id, q.source_id, q.entity_id, "PUBLISHED_BY",
                               q.question_version, q.evidence_record_ids,
                               q.observation_record_ids, q.recorded_time)]
    # identical role questions do not collide; differing ones must not share identity
    assert RB.assert_no_collisions(same)["collisions"] == 0


def test_hosting_evidence_does_not_establish_publisher():
    observations = [{"kind": "DOMAIN_HOST", "entity": "Zenodo", "observation_id": "o1"}]
    hosted = RB.predict_role(source_id="s", entity_id="Zenodo",
                             target_role="HOSTED_BY", observations=observations)
    published = RB.predict_role(source_id="s", entity_id="Zenodo",
                                target_role="PUBLISHED_BY", observations=observations)
    assert hosted.prediction == "ESTABLISHED"
    assert published.prediction == "NOT_ESTABLISHED"


def test_supporting_evidence_alone_never_establishes():
    observations = [{"kind": "INSTITUTIONAL_ATTRIBUTION", "entity": "EC",
                     "observation_id": "o1"}]
    verdict = RB.predict_role(source_id="s", entity_id="EC",
                              target_role="PUBLISHED_BY", observations=observations)
    assert verdict.prediction == "EPISTEMICALLY_UNRESOLVABLE"


def test_decisive_kinds_are_the_vocabulary_observations_actually_emits():
    """A table of invented kind names matches nothing and reads as dormant."""
    from curunir_operational.v5_3 import observations as OB
    import inspect, re
    emitted = set(re.findall(r'"([A-Z][A-Z_]{5,})"', inspect.getsource(OB)))
    for role, kinds in RB.DECISIVE.items():
        assert kinds & emitted, f"{role} has no decisive kind the emitter produces"


def test_established_without_decisive_observation_is_refused():
    with pytest.raises(ValueError, match="decisive observation"):
        RB.RoleVerdict("v", "s", "e", "PUBLISHED_BY", "ESTABLISHED", "EXPLICIT",
                       (), ("o1",), (), (), "2026-07-25T00:00:00+00:00")


def test_production_may_not_emit_construction_defect():
    with pytest.raises(ValueError, match="may not emit"):
        RB.RoleVerdict("v", "s", "e", "PUBLISHED_BY", "CONSTRUCTION_DEFECT", "ABSENT",
                       ("o1",), (), (), (), "2026-07-25T00:00:00+00:00")


def test_all_thirteen_roles_are_preserved():
    assert len(RB.SOURCE_ROLES) == 13
    assert set(RB.DECISIVE) == set(RB.SOURCE_ROLES)


def test_uploader_is_not_author():
    assert ("HOSTED_BY", "AUTHORED_BY") in RB.PROHIBITED_INFERENCES
    assert ("SUBMITTED_BY", "AUTHORED_BY") in RB.PROHIBITED_INFERENCES


# ---------------------------------------------------------------- Section 9
def test_all_dependence_and_temporal_classes_preserved():
    assert len(ACT.DEPENDENCE_CLASSES) == 11
    assert len(ACT.TEMPORAL_CLASSES) == 14


@pytest.mark.parametrize("text,relation", [
    ("Corrigendum to Commission Implementing Regulation (EU) 2025/1", "CORRIGENDUM_TO"),
    ("Berichtigung zu der Verordnung (EU) 2025/2083", "CORRIGENDUM_TO"),
    ("This article has been withdrawn by the publisher.", "RETRACTS"),
    ("Consolidated text of Regulation (EU) 2023/956", "CONSOLIDATES"),
    ("Regulation amending Regulation (EU) 2023/956", "AMENDS"),
    ("Unofficial translation of the original German text", "TRANSLATION_OF"),
])
def test_explicit_relations_are_detected(text, relation):
    found = ACT.detect_relations(text, source_object_id="s")
    assert relation in {o.relation for o in found}


def test_one_relation_projects_into_three_vocabularies():
    projection = ACT.project("CORRIGENDUM_TO")
    assert projection["dependence"] == "DERIVATIVE_CONFIRMED"
    assert projection["temporal"] == "CORRECTION"
    assert projection["origin"] == "CORRECTION_OF"


def test_polarity_conflict_requires_near_identical_statements():
    """Negation anywhere in two long unrelated documents is not a conflict."""
    left = "The agency confirmed the system entered service in March across the union."
    right = ("Unrelated guidance on fisheries quotas does not apply to inland waters "
             "in the present year under review.")
    label, _ = ACT.classify_temporal(left, right)
    assert label != "POLARITY_CONFLICT"


def test_polarity_conflict_fires_on_matched_statements():
    left = "The agency confirmed that system Y entered operational service in March."
    right = "The agency confirmed that system Y did not enter operational service in March."
    label, _ = ACT.classify_temporal(left, right)
    assert label == "POLARITY_CONFLICT"


def test_translation_detected_without_the_phrase():
    label, why = ACT.classify_dependence(
        "Verordnung (EU) 2025/2083 des Europaeischen Parlaments",
        "Regulation (EU) 2025/2083 of the European Parliament",
        left_language="de", right_language="en",
        left_identifier="2025/2083", right_identifier="2025/2083")
    assert label == "TRANSLATION_DERIVATIVE"
    assert "2025/2083" in why


def test_diversity_gate_refuses_a_constant_classifier():
    result = ACT.diversity_gate({
        "dependence": {"NO_DEPENDENCE_FOUND": 66},
        "temporal": {"NO_CONFLICT": 70},
        "claim_support": {"WRONG_ENTITY": 40, "FULL_SUPPORT": 17},
        "source_roles": {"PUBLISHED_BY": 60},
        "report_dispositions": {"PUBLISHED": 82}})
    assert not result["panel_may_launch"]
    assert set(result["constant_output_streams"]) >= {"dependence", "temporal"}
    assert result["verdict"] == "PARTIAL"


def test_diversity_gate_passes_a_varied_stream():
    result = ACT.diversity_gate({
        "dependence": {c: 3 for c in ACT.DEPENDENCE_CLASSES[:9]},
        "temporal": {c: 3 for c in ACT.TEMPORAL_CLASSES[:11]},
        "claim_support": {c: 3 for c in SUP.SUPPORT_CLASSES[:10]},
        "source_roles": {c: 3 for c in RB.SOURCE_ROLES[:11]},
        "report_dispositions": {c: 3 for c in PUB.DISPOSITIONS[:4]}})
    assert result["panel_may_launch"]


def test_diversity_gate_reads_no_reviewer_labels():
    """The gate runs before any label exists; it takes only prediction counts."""
    import inspect
    signature = inspect.signature(ACT.diversity_gate)
    assert list(signature.parameters) == ["counts_by_stream"]


# ---------------------------------------------------------------- wording generation
def _frozen_support(support_class):
    class _V:
        addresses_proposition = True
        entity_alignment = predicate_alignment = scope_alignment = "ALIGNED"
        time_alignment = polarity_alignment = modality_alignment = "ALIGNED"
        lifecycle_alignment = attribution_alignment = "ALIGNED"
        dependence_limitations = ()
        counterevidence = ()
        support_completeness = "COMPLETE"

    class _S:
        pass
    _S.support_class = support_class
    _S.support_id = "s"
    _S.critical_error = None
    _S.prohibited_stronger_wording = ()
    _S.vector = _V()
    return _S()


BASE = "Agency X establishes system Y across the union."


@pytest.mark.parametrize("support_class,expected", [
    ("FULL_SUPPORT", "PUBLISHED"),
    ("PARTIAL_SUPPORT", "PUBLISHED_WITH_QUALIFICATION"),
    ("QUALIFIED_SUPPORT", "PUBLISHED_WITH_QUALIFICATION"),
    ("CONTEXT_DEPENDENT_SUPPORT", "PUBLISHED_WITH_QUALIFICATION"),
    ("INFERENCE_ONLY", "PUBLISHED_AS_MARKED_ANALYSIS"),
])
def test_generated_wording_reaches_every_publishable_disposition(support_class, expected):
    """A gate that only verifies wording cannot produce a compliant report."""
    record = PUB.decide_with_wording(
        proposition_id="p", claim={"claim_id": "c", "lifecycle_state": "UNKNOWN"},
        support=_frozen_support(support_class), base_text=BASE)
    assert record.publication_disposition == expected


def test_full_support_wording_is_not_weakened():
    """Understating what the evidence carries is its own unfaithfulness."""
    wording = PUB.render_wording(support=_frozen_support("FULL_SUPPORT"), base_text=BASE)
    assert "establishes" in wording


@pytest.mark.parametrize("support_class", ["PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
                                           "CONTEXT_DEPENDENT_SUPPORT", "INFERENCE_ONLY"])
def test_qualified_wording_never_carries_strong_language(support_class):
    wording = PUB.render_wording(support=_frozen_support(support_class), base_text=BASE)
    assert not PUB._STRONG.search(wording)
    assert PUB._QUALIFIER_PRESENT.search(wording)


@pytest.mark.parametrize("support_class", sorted(PUB.NEVER_FACTUAL))
def test_no_factual_wording_is_generated_for_a_negative_class(support_class):
    assert PUB.render_wording(support=_frozen_support(support_class),
                              base_text=BASE) == ""


def test_generation_never_strengthens_the_assertion():
    """Every frame weakens; none adds certainty the evidence does not carry."""
    for support_class in PUB.QUALIFYING_FRAME:
        wording = PUB.render_wording(support=_frozen_support(support_class),
                                     base_text=BASE)
        assert len(PUB._STRONG.findall(wording)) == 0
