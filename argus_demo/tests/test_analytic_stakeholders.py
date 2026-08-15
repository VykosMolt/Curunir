"""Stakeholder/influence engine: contextual assessments, position vs interest
separation, temporal position change, identity caveats, typed evidence-bound
influence, deterministic discovery from world-model relations."""
from __future__ import annotations

import pytest

from curunir_analytic.stakeholders import (add_position, assert_influence,
                                           create_assessment, discover_stakeholders,
                                           explain_assessment, link_influence,
                                           refresh_assessment, supersede_influence,
                                           supersede_position)
from curunir_analytic.contracts import StakeholderPosition
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")

WIKIDATA_ACME = b"""{
  "entities": {"Q77777": {"id": "Q77777",
    "labels": {"en": {"value": "Acme Industri"}, "nb": {"value": "Acme Industri AS"}},
    "claims": {
      "P1278": [{"mainsnak": {"datavalue": {"value": "ACMELEI000000000001"}}}],
      "P856": [{"mainsnak": {"datavalue": {"value": "https://acme-industri.example.no"}}}]
    }}}
}"""


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == "LEI:ACMELEI000000000001"}


def test_position_and_inferred_interest_stay_separate(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    # the world model must link the entity to its site (OPERATES from
    # Wikidata) for a site statement to be an OBSERVED public position
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q77777",
                        body=WIKIDATA_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T12:30:00+00:00")
    plant_page(pipeline, url="https://acme-industri.example.no/press",
               body=statement_page("Acme Industri supports the proposed emissions rule"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    statement_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                           if "supports the proposed" in c["object_or_value"])
    # the assessed entity is the one the world model links to the site; the
    # LEI entity's equivalence is a PROPOSED association, deliberately not
    # assumed here
    qid_object = world_object_id("WIKIDATA_QID:Q77777")
    assessment = create_assessment(
        ctx, entity_object_id=qid_object, context_kind="ISSUE",
        context_id="emissions-rule", role_in_context="regulated party",
        supporting_claim_ids=[by_predicate["entity_status"]])
    public = StakeholderPosition(
        position_id="pos-pub", kind="PUBLIC_POSITION",
        statement="Acme Industri supports the proposed emissions rule",
        stance="UNRESOLVED", authority="OBSERVED",
        claim_ids=(statement_claim,), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    add_position(ctx, assessment["assessment_id"], public, caused_by="test")
    interest = StakeholderPosition(
        position_id="pos-int", kind="INFERRED_INTEREST",
        statement="the rule may raise compliance costs for smaller competitors",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(statement_claim,), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False,
        note="inferred from market structure; no source states this")
    updated = add_position(ctx, assessment["assessment_id"], interest, caused_by="test")
    kinds = {(p["kind"], p["authority"]) for p in updated["positions"]}
    assert ("PUBLIC_POSITION", "OBSERVED") in kinds
    assert ("INFERRED_INTEREST", "SUPPORTED_INFERENCE") in kinds
    transition_kinds = {t["transition_type"]
                       for t in ctx.store.transitions_for(assessment["assessment_id"])}
    assert "INTEREST_INFERRED" in transition_kinds and "POSITION_ADDED" in transition_kinds


def test_position_changes_preserve_prior_state(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="emissions-rule", role_in_context="regulated party",
        supporting_claim_ids=[by_predicate["entity_status"]])
    original = StakeholderPosition(
        position_id="pos-1", kind="PUBLIC_POSITION",
        statement="supports the rule", stance="UNRESOLVED", authority="OBSERVED",
        claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
        valid_from="2026-08-01T00:00:00+00:00", valid_to=None,
        superseded=False, note="")
    add_position(ctx, assessment["assessment_id"], original, caused_by="t1")
    replacement = StakeholderPosition(
        position_id="pos-2", kind="PUBLIC_POSITION",
        statement="opposes the amended rule", stance="UNRESOLVED",
        authority="OBSERVED", claim_ids=(by_predicate["entity_status"],),
        relationship_ids=(), valid_from="2026-08-15T00:00:00+00:00",
        valid_to=None, superseded=False, note="")
    updated = supersede_position(ctx, assessment["assessment_id"], "pos-1",
                                 replacement, caused_by="t2",
                                 rationale="public statement changed")
    by_id = {p["position_id"]: p for p in updated["positions"]}
    assert by_id["pos-1"]["superseded"] is True
    assert by_id["pos-1"]["valid_to"] is not None
    assert by_id["pos-2"]["superseded"] is False
    assert by_id["pos-1"]["statement"] == "supports the rule"  # history intact
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(assessment["assessment_id"])}
    assert "POSITION_CHANGED" in kinds


def test_identity_ambiguity_travels_with_assessment(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _seed(pipeline, ctx)
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q77777",
                        body=WIKIDATA_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    ambiguities = [r for r in ctx.store.open_review_items()
                   if r["kind"] == "IDENTITY_AMBIGUITY"]
    assert ambiguities, "expected cross-scheme identity ambiguity"
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="emissions-rule", role_in_context="regulated party",
        supporting_claim_ids=list(ctx.store.current_claims())[:1])
    assert assessment["identity_caveats"], \
        "open identity ambiguity must surface on the assessment"
    # resolving the review item clears the caveat on refresh, as a transition
    from curunir_semantic.contracts import ReviewItem
    item = ambiguities[0]
    resolved = ReviewItem(item_id=item["item_id"], kind=item["kind"],
                          subject_kind=item["subject_kind"], subject_id=item["subject_id"],
                          detail=item["detail"], evidence_refs=tuple(item["evidence_refs"]),
                          status="RESOLVED", resolution_note="human reviewed: same entity",
                          recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("REVIEW_ITEM_RECORDED", resolved,
                     recorded_time=resolved.recorded_time, actor="jan")
    refreshed = refresh_assessment(ctx, assessment["assessment_id"], caused_by="review")
    assert refreshed["identity_caveats"] == []
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(assessment["assessment_id"])}
    assert "IDENTITY_CAVEAT_CHANGED" in kinds


def test_influence_is_typed_and_history_preserving(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    other = world_object_id("LEI:OTHERLEI00000000002")
    formal = assert_influence(
        ctx, source_object_id=ACME_OBJECT, target_object_id=other,
        kind="FORMAL_AUTHORITY_OVER",
        mechanism="board appointment right stated in filing",
        authority="ANALYST_ASSESSMENT",
        claim_ids=(by_predicate["entity_status"],))
    informal = assert_influence(
        ctx, source_object_id=ACME_OBJECT, target_object_id=other,
        kind="LIKELY_INFLUENCES",
        mechanism="repeated coordinated statements; no formal tie in evidence",
        authority="SUPPORTED_INFERENCE",
        claim_ids=(by_predicate["entity_status"],))
    assert formal["influence_id"] != informal["influence_id"]
    assert {formal["kind"], informal["kind"]} == {"FORMAL_AUTHORITY_OVER",
                                                  "LIKELY_INFLUENCES"}
    # idempotent while active
    again = assert_influence(
        ctx, source_object_id=ACME_OBJECT, target_object_id=other,
        kind="LIKELY_INFLUENCES", mechanism="x", authority="SUPPORTED_INFERENCE")
    assert again["version"] == informal["version"]
    ended = supersede_influence(ctx, informal["influence_id"],
                                reason="coordination ceased per new evidence",
                                caused_by="chg-9")
    assert ended["status"] == "SUPERSEDED"
    versions = ctx.store.analytic_versions("influence_assertion",
                                           informal["influence_id"])
    assert [v["status"] for v in versions] == ["ACTIVE", "SUPERSEDED"]


def test_discovery_derives_only_what_world_model_states(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _seed(pipeline, ctx)
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q77777",
                        body=WIKIDATA_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    site_object = world_object_id("URL:https://acme-industri.example.no")
    created = discover_stakeholders(ctx, context_kind="ISSUE",
                                    context_id="acme-web-presence",
                                    relevant_object_ids=[site_object])
    assert created, "OPERATES relation should yield a stakeholder"
    assessment = created[0]
    position = assessment["positions"][0]
    assert position["kind"] == "FORMAL_ROLE"
    assert position["authority"] == "OBSERVED"
    assert position["relationship_ids"], "role must be backed by the relation"
    # no interests, stances or informal influence were invented
    assert all(p["kind"] == "FORMAL_ROLE" for p in assessment["positions"])
    assert all(p["stance"] == "UNRESOLVED" for p in assessment["positions"])
    explanation = explain_assessment(ctx.store, assessment["assessment_id"])
    assert explanation["context"] == "ISSUE:acme-web-presence"


def test_owns_relation_yields_observed_influence(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    from curunir_operational.contracts import (EvidenceRef, ProvenanceSummary,
                                               RelationshipVersion)
    other = world_object_id("LEI:OTHERLEI00000000002")
    observation = next(iter(ctx.store.records_of("semantic_observation")))
    provenance = ProvenanceSummary(
        mode="EVIDENTIARY", source_ids=("gleif",),
        evidence=(EvidenceRef(
            source_object_id=observation["manifestation_id"],
            document_id=observation["document_id"],
            content_sha256=observation["anchors"][0]["content_sha256"],
            assertion_id=observation["observation_id"],
            evidence_basis_id="b" * 8, identity_status="IDENTITY_UNKNOWN",
            authority_state="AUTHORITY_NOT_ASSESSED", independence_status="UNRESOLVED",
            claim_basis_status="DIRECT_FIELD", review_state="UNREVIEWED",
            mapping_status="EXACT_FIELD_PATH"),))
    relation = RelationshipVersion(
        relationship_id="rel-owns-1", version=1, relation_type="OWNS",
        source_object_id=ACME_OBJECT, target_object_id=other,
        valid_from=None, valid_to=None, recorded_time=ctx.now_fn(),
        evidence_refs=(observation["observation_id"],), derivation="EVIDENCE",
        confidence="UNKNOWN", status="ACTIVE", marking=MARK, provenance=provenance,
        rationale="test ownership")
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED", relation,
                     recorded_time=relation.recorded_time, actor="t")
    created = discover_stakeholders(ctx, context_kind="MISSION", context_id="m1",
                                    relevant_object_ids=[other])
    assessment = next(a for a in created if a["entity_object_id"] == ACME_OBJECT)
    linked = ctx.store.current_stakeholder_assessments()[assessment["assessment_id"]]
    assert linked["influence_ids"], "OWNS should yield a linked influence assertion"
    influence = ctx.store.current_influence_assertions()[linked["influence_ids"][0]]
    assert influence["kind"] == "OWNS"
    assert influence["authority"] == "OBSERVED"
    assert influence["relationship_ids"] == ["rel-owns-1"]
