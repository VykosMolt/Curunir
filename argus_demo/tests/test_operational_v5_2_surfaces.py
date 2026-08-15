"""Source-origin, claim-support and reporting tests (Sections 11, 14, 15).

Each of the concrete V5.1 failure mechanisms these modules repair is asserted
closed with the case that produced it.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_2 import claim_support as CS
from curunir_operational.v5_2 import reporting as R
from curunir_operational.v5_2 import source_origin as SO

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Section 11 — source identity and origin
# ---------------------------------------------------------------------------

def test_every_role_has_decisive_sections_and_a_marker():
    assert set(SO.ROLE_DECISIVE_SECTIONS) == set(SO.SOURCE_ROLES)
    assert set(SO._ROLE_MARKERS) == set(SO.SOURCE_ROLES)


def test_a_host_is_not_a_publisher():
    sections = {"domain": "cdn.example.net", "url": "https://cdn.example.net/a.pdf",
                "hosting_institution": "hosted by an infrastructure provider"}
    resolution = SO.resolve_role(sections, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="an infrastructure provider")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"
    assert resolution.refused_inference == "HOST_AS_PUBLISHER"


def test_the_same_evidence_does_resolve_hosting():
    sections = {"domain": "cdn.example.net", "url": "https://cdn.example.net/a.pdf",
                "hosting_institution": "hosted by an infrastructure provider"}
    assert SO.resolve_role(sections, "HOSTED_BY", dossier_id="d",
                           candidate_agent="an infrastructure provider").state == "RESOLVED"


def test_an_uploader_is_not_an_author():
    sections = {"submitted_by": "submitted by a named individual",
                "publication_metadata": "deposited 2026-05-01"}
    resolution = SO.resolve_role(sections, "AUTHORED_BY", dossier_id="d",
                                 candidate_agent="a named individual")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"
    assert resolution.refused_inference == "UPLOADER_AS_AUTHOR"


def test_a_vendor_is_not_a_programme_owner():
    sections = {"organization_and_programme_records":
                "the company is a consortium member and supplier"}
    resolution = SO.resolve_role(sections, "OWNED_BY", dossier_id="d",
                                 candidate_agent="the company")
    assert resolution.state == "REFUSED_PROHIBITED_INFERENCE"
    assert resolution.refused_inference == "VENDOR_AS_PROGRAMME_OWNER"


def test_an_imprint_resolves_the_publisher():
    sections = {"publication_metadata": "Published by the publications office",
                "title_page": "Official journal of the union"}
    resolution = SO.resolve_role(sections, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="the publications office")
    assert resolution.state == "RESOLVED"
    assert resolution.evidence


def test_present_but_silent_evidence_is_unresolved_not_guessed():
    sections = {"publication_metadata": "Retrieved 2026-07-01",
                "title_page": "Annual report"}
    resolution = SO.resolve_role(sections, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="some institution")
    assert resolution.state == "UNRESOLVED_EVIDENCE_ABSENT"
    assert "does not resolve" in resolution.rationale


def test_absent_sections_are_reported_as_absent_not_as_ambiguity():
    resolution = SO.resolve_role({}, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="some institution")
    assert resolution.state == "UNRESOLVED_EVIDENCE_ABSENT"
    assert resolution.decisive_sections


def test_a_resolved_role_always_carries_evidence():
    with pytest.raises(ValueError, match="evidence"):
        SO.RoleResolution("r", "d", "PUBLISHED_BY", "an institution", "RESOLVED",
                          ("publication_metadata",), (), None, "rationale",
                          "2026-07-24T00:00:00+00:00")


def test_conflation_audit_reports_zero_for_resolver_output():
    sections = {"publication_metadata": "Published by the publications office",
                "issuing_institution": "issued by the directorate-general",
                "archive_or_mirror_relations": "archived snapshot",
                "timestamps": "2026-06-26", "url": "https://example.org/x"}
    resolutions = SO.resolve_all_roles(sections, dossier_id="d",
                                       candidate_agent="an institution")
    assert all(value == 0 for value in SO.conflation_audit(resolutions).values())


def test_role_coverage_reports_missing_roles():
    coverage = SO.role_coverage([])
    assert coverage["roles_required"] == 13
    assert coverage["roles_exercised"] == 0
    assert len(coverage["missing"]) == 13


def test_a_resolved_role_promotes_to_a_graph_edge():
    sections = {"publication_metadata": "Published by the publications office"}
    resolution = SO.resolve_role(sections, "PUBLISHED_BY", dossier_id="d",
                                 candidate_agent="the publications office",
                                 agent_entity_id="entity-1")
    edge = SO.to_role_edge(resolution, subject_id="publication-1")
    assert edge.role == "PUBLISHED_BY"
    assert edge.agent_entity_id == "entity-1"


# ---------------------------------------------------------------------------
# Section 14 — claim support with the lifecycle gate
# ---------------------------------------------------------------------------

def _evidence(statement: str, language: str = "en", **kw) -> dict:
    base = {"evidence_id": "e1", "statement": statement,
            "normalized_statement": statement, "language": language,
            "subject": "the actor", "predicate": "acted",
            "object_or_value": "the object", "modality": "ASSERTED",
            "polarity": "POSITIVE", "correction_state": "CURRENT", "active": True}
    base.update(kw)
    return base


@pytest.mark.parametrize("claimed_state,evidence_text,language,expected", [
    ("OPERATIONAL", "Dann soll die Verbindung wieder in Betrieb gehen.", "de",
     "plan_as_implementation"),
    ("DEPLOYED", "The system was tested during an exercise in June.", "en",
     "exercise_as_deployment"),
    ("OPERATIONAL", "The pilot phase is running at three sites.", "en",
     "pilot_as_operational"),
    ("DEPLOYED", "The Commission announced the creation of the network.", "en",
     "announcement_as_existing_capability"),
])
def test_the_lifecycle_gate_names_the_critical_error(claimed_state, evidence_text,
                                                     language, expected):
    support = CS.gated_assess_support(
        {"claim_id": "c1", "proposition": "a claim", "lifecycle_state": claimed_state},
        [_evidence(evidence_text, language)])
    assert support.critical_error == expected
    assert support.support_state not in CS._AFFIRMATIVE_SUPPORT


def test_an_unqualified_vendor_delivery_claim_is_refused():
    support = CS.gated_assess_support(
        {"claim_id": "c2", "proposition": "a claim", "lifecycle_state": "DEPLOYED",
         "source_authority": "VENDOR_SELF_DESCRIPTION"},
        [_evidence("Our platform has been deployed across the network.")])
    assert support.critical_error == "vendor_claim_as_demonstrated_fact"


def test_a_sound_claim_passes_the_gate():
    support = CS.gated_assess_support(
        {"claim_id": "c3", "proposition": "the council adopted the regulation",
         "lifecycle_state": "POLICY_ADOPTED", "subject": "the council",
         "predicate": "adopted", "object_or_value": "the regulation",
         "modality": "ASSERTED", "polarity": "POSITIVE"},
        [_evidence("The council adopted the regulation on 16 January 2026.",
                   subject="the council", predicate="adopted",
                   object_or_value="the regulation")])
    assert support.lifecycle_verdict == "PASSED"
    assert support.critical_error is None
    assert support.support_state in CS._AFFIRMATIVE_SUPPORT


def test_a_lifecycle_violation_can_never_be_recorded_as_support():
    with pytest.raises(ValueError, match="plan-as-implementation"):
        CS.GatedSupport("g", "c", "FULL_SUPPORT", "REFUSED",
                        "plan_as_implementation", "OPERATIONAL",
                        "DEPLOYMENT_PLANNED", "rationale", None,
                        "2026-07-24T00:00:00+00:00")


def test_a_claim_cannot_be_constructed_above_its_evidence():
    with pytest.raises(ValueError, match="does not license"):
        CS.lifecycle_claim(entity="an institution", proposition="a claim",
                           lifecycle_state="OPERATIONAL",
                           evidence_act="ACTOR_INTENTION",
                           evidence_span_ids=["span-1"])


def test_a_claim_records_what_wording_its_evidence_prohibits():
    claim = CS.lifecycle_claim(
        entity="an institution", proposition="a claim",
        lifecycle_state="DEPLOYMENT_PLANNED", evidence_act="ACTOR_INTENTION",
        evidence_span_ids=["span-1"])
    assert "OPERATIONAL" in claim.prohibited_stronger_formulations
    assert "DEPLOYED" in claim.prohibited_stronger_formulations


def test_the_full_population_audit_reports_zero_on_gated_output():
    supports = [
        CS.gated_assess_support(
            {"claim_id": f"c{index}", "proposition": "a claim",
             "lifecycle_state": "OPERATIONAL"},
            [_evidence("Dann soll die Verbindung wieder in Betrieb gehen.", "de")])
        for index in range(5)]
    audit = CS.audit_accepted_claims(supports)
    assert audit["accepted_claims"] == 0
    assert audit["refused_for_lifecycle"] == 5
    assert audit["all_required_counts_zero"]


def test_support_state_coverage_reports_missing_states():
    coverage = CS.support_state_coverage([])
    assert coverage["states_required"] == 12
    assert coverage["states_exercised"] == 0


# ---------------------------------------------------------------------------
# Section 15 — proposition-first reporting
# ---------------------------------------------------------------------------

def _proposition(text: str, **kw) -> R.ReportableProposition:
    base = dict(text=text, supporting_claim_ids=("c1",),
                support_state="FULL_SUPPORT", lifecycle_state="POLICY_ADOPTED",
                evidence_act="FORMAL_POLICY_DECISION",
                materiality="ANSWERS_THE_RESEARCH_QUESTION")
    base.update(kw)
    return R.reportable_proposition(**base)


@pytest.mark.parametrize("text,kwargs,reason", [
    # Each of the six V5.1 report-faithfulness mechanisms.
    ("We use cookies to improve your browsing experience.", {},
     "SITE_OR_ARCHIVE_FURNITURE"),
    ("Wenn Sie kein Journalist sind, wenden Sie sich bitte an die Pressestelle.",
     {"language": "de"}, "SITE_OR_ARCHIVE_FURNITURE"),
    ("proyectos con valor añadido de la UE.", {"language": "es"},
     "NOT_A_COMPLETE_PROPOSITION"),
    ("The systems in scope may be provided as intermediary services, which "
     "should be interpreted in a technology-neutral manner.", {},
     "NORMATIVE_TEXT_RENDERED_AS_FACT"),
    ("The connection has been returned to operation.",
     {"lifecycle_state": "DEPLOYMENT_PLANNED", "evidence_act": "ACTOR_INTENTION"},
     "LIFECYCLE_STATE_STRENGTHENED"),
    ("The finding was independently confirmed by multiple independent sources.",
     {"dependence_states": ("DERIVATIVE_CONFIRMED",)},
     "UNSUPPORTED_INDEPENDENCE_LANGUAGE"),
    ("The evidence suggests that the programme will expand.", {},
     "UNMARKED_INFERENCE"),
    ("( 8 ) Decision No 768/2008/EC of the European Parliament of 9 July 2008.",
     {}, "BIBLIOGRAPHIC_CITATION"),
])
def test_the_planner_refuses_each_v5_1_mechanism(text, kwargs, reason):
    disposition = R.plan_proposition(_proposition(text, **kwargs), text)
    assert disposition.disposition == "REJECTED_UNSUPPORTED"
    assert disposition.reason == reason


def test_an_asserted_temporal_scope_must_be_carried_by_the_evidence():
    proposition = _proposition("The January 2024 package supports startups.",
                               temporal_scope=("2024-01-01", "2024-01-31"))
    disposition = R.plan_proposition(
        proposition, "The January 2024 package supports startups.",
        supporting_text=["The factories leverage supercomputing capacity."])
    assert disposition.reason == "TEMPORAL_SCOPE_UNSUPPORTED"


def test_a_supported_temporal_scope_publishes():
    proposition = _proposition("The January 2024 package supports startups.",
                               temporal_scope=("2024-01-01", "2024-01-31"))
    disposition = R.plan_proposition(
        proposition, "The January 2024 package supports startups.",
        supporting_text=["The January 2024 package was adopted by the college."])
    assert disposition.disposition == "PUBLISHED"


def test_a_faithful_sentence_publishes():
    text = "The council adopted the regulation on 16 January 2026."
    disposition = R.plan_proposition(_proposition(text), text,
                                     supporting_text=[text])
    assert disposition.disposition == "PUBLISHED"
    assert disposition.rendered_sentence == text


def test_a_dropped_qualification_is_refused():
    proposition = _proposition("The system is planned for 2027.",
                               qualifications=("PLANNED",))
    disposition = R.plan_proposition(
        proposition, "The system will serve every member state from 2027.")
    assert disposition.reason == "MATERIAL_QUALIFICATION_OMITTED"


def test_partial_support_moves_to_the_uncertainty_section():
    text = "The council considered the proposal in January."
    disposition = R.plan_proposition(
        _proposition(text, support_state="PARTIAL_SUPPORT"), text,
        supporting_text=[text])
    assert disposition.disposition == "MOVED_TO_UNCERTAINTY_SECTION"


def test_an_immaterial_proposition_is_omitted_with_a_reason():
    text = "The building has four floors and a public entrance."
    disposition = R.plan_proposition(
        _proposition(text, materiality="BACKGROUND_ONLY"), text,
        supporting_text=[text])
    assert disposition.disposition == "OMITTED_AS_IMMATERIAL"
    assert disposition.reason in R.OMISSION_REASONS


def test_generation_difficulty_is_not_an_omission_reason():
    assert "GENERATION_DIFFICULTY" not in R.OMISSION_REASONS
    with pytest.raises(ValueError, match="omission"):
        R.Disposition("d", "p", "OMITTED_AS_IMMATERIAL", None,
                      "GENERATION_DIFFICULTY", (), "rationale",
                      "2026-07-24T00:00:00+00:00")


def test_the_executive_summary_may_not_strengthen_the_report():
    published = [(_proposition("The council adopted the regulation."),
                  "The council adopted the regulation.")]
    result = R.check_executive_summary(
        ["The system is now operational across the union."], published)
    assert not result["entailed"]
    assert result["findings"][0]["code"] == "EXECUTIVE_SUMMARY_STRENGTHENS_LIFECYCLE"
    assert result["lifecycle_ceiling"] == "POLICY_ADOPTED"


def test_the_executive_summary_may_compress():
    published = [(_proposition("The system is operational.",
                               lifecycle_state="OPERATIONAL",
                               evidence_act="OPERATIONAL_USE_EVIDENCE"),
                  "The system is operational.")]
    result = R.check_executive_summary(["The regulation was adopted."], published)
    assert result["entailed"]


def test_the_report_audit_counts_only_what_was_published():
    text = "The council adopted the regulation on 16 January 2026."
    dispositions = [
        R.plan_proposition(_proposition(text), text, supporting_text=[text]),
        R.plan_proposition(_proposition("We use cookies here."),
                           "We use cookies here."),
    ]
    audit = R.report_audit(dispositions)
    assert audit["published"] == 1
    assert audit["rejected_unsupported"] == 1
    assert audit["all_propositions_dispositioned"]
    assert audit["all_required_counts_zero"]


def test_every_proposition_receives_a_disposition():
    assert len(R.PROPOSITION_DISPOSITIONS) == 5
    for name in ("PUBLISHED", "PUBLISHED_WITH_QUALIFICATION",
                 "MOVED_TO_UNCERTAINTY_SECTION", "REJECTED_UNSUPPORTED",
                 "OMITTED_AS_IMMATERIAL"):
        assert name in R.PROPOSITION_DISPOSITIONS
