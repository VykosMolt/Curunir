from __future__ import annotations

from dataclasses import replace

import pytest

from curunir_operational.v4.investigation import (
    DiscoveryBudget, amend_case, decide_access, deterministic_plan, preregister_case,
    transition_case,
)

pytestmark = pytest.mark.no_db
STAMP = "2026-07-22T09:00:00+00:00"


def case(**overrides):
    values = dict(
        case_id="CASE-A", title="Bounded case",
        research_question="What do public primary sources state about a bounded institutional programme and its limitations?",
        purpose="Public-source research shadow", scope=("programme existence",),
        exclusions=("private individuals",), geographic_scope=("EU",),
        temporal_scope=("2024-01-01T00:00:00+00:00", STAMP), languages=("en", "fr", "de"),
        entities_of_interest=("PROGRAMME",), source_classes=("OFFICIAL", "SECONDARY"),
        evidence_requirements=("official programme statement",),
        prohibited_inference_classes=("deployment without evidence",), discovery_budget=3,
        acquisition_budget=2, review_policy="HUMAN_REVIEW_REQUIRED", stop_rules=("budget reached",),
        owning_node="STRATEGIC_EVIDENCE_NODE", access_marking={"releasability": ["PUBLIC"]},
        created_time=STAMP,
    ); values.update(overrides); return preregister_case(**values)


def test_preregistration_immutable_original_scope_and_versioned_change():
    original = case()
    assert original.status == "PREREGISTERED" and len(original.integrity_hash) == 64
    with pytest.raises(ValueError, match="immutable"):
        amend_case(original, changes={"research_question": "changed"}, rationale="result-driven")
    amended, version = amend_case(original, changes={"scope": ("programme existence", "governance")},
                                   rationale="Explicit preregistered coverage amendment", created_time=STAMP)
    assert amended.version == 2 and version.parent_version_id and original.scope != amended.scope
    assert original.research_question == amended.research_question


def test_case_budget_stop_cancellation_and_supersession():
    original = case()
    assert transition_case(original, "CANCELLED", "CANCELLED").status == "CANCELLED"
    assert transition_case(original, "SUPERSEDED", "SUPERSEDED").status == "SUPERSEDED"
    planner = DiscoveryBudget(original)
    for index, language in enumerate(("en", "fr", "de")):
        planner.issue(subquestion_id="SQ", formulation=f"official query {index}", language=language,
                      provider="FIXTURE", provider_category="PUBLIC_WEB", reason="coverage",
                      expected_source_class="OFFICIAL", execution_time=STAMP)
    with pytest.raises(ValueError, match="budget"):
        planner.issue(subquestion_id="SQ", formulation="over", language="en", provider="FIXTURE",
                      provider_category="PUBLIC_WEB", reason="coverage", expected_source_class="OFFICIAL")
    assert planner.finish("budget reached", STAMP).stop_reason == "budget reached"


def test_multilingual_query_lineage_duplicate_lead_and_snippet_not_evidence():
    original = case(); planner = DiscoveryBudget(original)
    query = planner.issue(subquestion_id="SQ-1", formulation="site:europa.eu programme officiel",
                          language="fr", provider="PUBLIC_SEARCH_CAPTURE",
                          provider_category="PUBLIC_WEB", reason="French official coverage",
                          expected_source_class="OFFICIAL", execution_time=STAMP)
    rows = ({"url": "https://europa.eu/a", "title": "A", "snippet": "lead only"},
            {"url": "https://europa.eu/a", "title": "duplicate", "snippet": "still lead"})
    leads = planner.admit_results(query, rows)
    assert len(leads) == 1 and not leads[0].evidence_eligible
    assert planner.queries[-1].subquestion_id == "SQ-1" and planner.queries[-1].language == "fr"


def test_access_policy_public_denials_blocks_metadata_and_unknown():
    original = case(); planner = DiscoveryBudget(original)
    query = planner.issue(subquestion_id="SQ", formulation="q", language="en", provider="P",
                          provider_category="OFFICIAL_SITE", reason="r", expected_source_class="OFFICIAL",
                          execution_time=STAMP)
    lead = planner.admit_results(query, ({"url": "https://example.eu/public"},))[0]
    assert decide_access(original, lead, decided_time=STAMP).state == "ALLOW_PUBLIC_RETRIEVAL"
    assert decide_access(original, lead, credential_required=True, decided_time=STAMP).state == "DENY_CREDENTIAL_REQUIRED"
    assert decide_access(original, lead, waf_or_captcha=True, decided_time=STAMP).state == "BLOCKED_TECHNICALLY"
    assert decide_access(original, lead, metadata_only=True, decided_time=STAMP).state == "ALLOW_METADATA_ONLY"
    assert decide_access(original, lead, out_of_scope=True, decided_time=STAMP).state == "DENY_OUT_OF_SCOPE"
    assert decide_access(original, lead, personal_data_risk=True, decided_time=STAMP).state == "DENY_PERSONAL_DATA_RISK"
    assert decide_access(original, lead, unknown=True, decided_time=STAMP).state == "UNKNOWN_REQUIRES_REVIEW"


def test_deterministic_plan_contains_contradiction_sources_languages_and_stops():
    result = deterministic_plan(case(), ({"statement": "What is officially announced?",
                                          "source_classes": ("OFFICIAL",), "minimum_sources": 2},))
    assert result["subquestions"][0]["contradiction_need"]
    assert result["coverage_requirements"]["languages"] == ["en", "fr", "de"]
    assert result["stop_rules"] and result["prohibited_inferences"]


def test_invalid_broad_question_and_forced_search_evidence_refused():
    with pytest.raises(ValueError, match="bounded"):
        case(research_question="find everything about ARCADIA")
    original = case(); planner = DiscoveryBudget(original)
    query = planner.issue(subquestion_id="SQ", formulation="q", language="en", provider="P",
                          provider_category="PUBLIC_WEB", reason="r", expected_source_class="OFFICIAL",
                          execution_time=STAMP)
    lead = planner.admit_results(query, ({"url": "https://example.eu", "snippet": "not evidence"},))[0]
    with pytest.raises(ValueError, match="never evidence"):
        replace(lead, evidence_eligible=True)


def test_all_three_discovery_provider_categories_preserve_query_lineage():
    original = case(); planner = DiscoveryBudget(original)
    categories = ("OFFICIAL_SITE", "PUBLIC_WEB", "CITATION_EXPANSION")
    for index, category in enumerate(categories):
        query = planner.issue(subquestion_id=f"SQ-{index}", formulation=f"bounded {category}",
                              language=original.languages[index], provider=f"PROVIDER-{index}",
                              provider_category=category, reason="bounded coverage",
                              expected_source_class="OFFICIAL", execution_time=STAMP)
        planner.admit_results(query, ({"url": f"https://example.eu/{index}"},))
    assert [item.provider_category for item in planner.queries] == list(categories)
    assert all(item.result_lead_ids and item.follow_up_decision for item in planner.queries)
