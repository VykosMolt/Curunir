"""Regression locks for the fourth-round adversarial findings."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ImpactEdge, NarrativeVariant
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.narratives import add_variant, create_narrative
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.stakeholders import create_assessment
from curunir_analytic.substrate import resolve_candidate
from curunir_analytic.themes import create_theme
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")
NOW = "2026-08-17T12:00:00+00:00"


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == "LEI:ACMELEI000000000001"}


def test_q1_model_cannot_rewrite_its_target_kind(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))
    # the model emits its own target_kind + the other kind's binding fields
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {
            "target_kind": "impact_path",
            "title": "Innocent looking theme",
            "supporting_claim_ids": [by_predicate["entity_status"]],
            "objective_id": objective["objective_id"],
            "summary": "s", "edge_ids": ["e-x"]})
    proposed = assist.propose(ctx, task="t", target_kind="analytic_theme",
                              inputs={}, input_refs=())
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    # the engine-declared kind won the merge: it spends only as a theme
    assert resolved["content"]["target_kind"] == "analytic_theme"
    edge = ImpactEdge(edge_id="e-x", from_kind="object", from_id=ACME_OBJECT,
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    with pytest.raises(ValueError, match="not transferable across kinds"):
        build_path(ctx, objective_id=objective["objective_id"], summary="s",
                   edges=(edge,), provenance_kind="MODEL",
                   inference_id=resolved["inference_id"],
                   proposal_id=resolved["proposal_id"])


def test_q2_model_assessment_is_fully_bound(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    from curunir_analytic.contracts import StakeholderPosition
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {
            "entity_object_id": ACME_OBJECT, "context_kind": "ISSUE",
            "context_id": "issue-1", "role_in_context": "regulated party",
            "claims": [by_predicate["entity_status"]]})
    proposed = assist.propose(ctx, task="t", target_kind="stakeholder_assessment",
                              inputs={}, input_refs=())
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    # (a) a model materialization can carry no positions at all
    with pytest.raises(ValueError, match="no positions"):
        create_assessment(
            ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
            context_id="issue-1", role_in_context="regulated party",
            positions=(StakeholderPosition(
                position_id="p1", kind="INFERRED_INTEREST", statement="invented",
                stance="OPPOSES", authority="SUPPORTED_INFERENCE",
                claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
                valid_from=None, valid_to=None, superseded=False, note=""),),
            provenance_kind="MODEL",
            inference_id=resolved["inference_id"],
            proposal_id=resolved["proposal_id"])
    # (b) a model-chosen role differing from the accepted one is refused
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        create_assessment(
            ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
            context_id="issue-1", role_in_context="controlling shareholder",
            provenance_kind="MODEL",
            inference_id=resolved["inference_id"],
            proposal_id=resolved["proposal_id"])
    # (c) unaccepted extra claims are refused
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        create_assessment(
            ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
            context_id="issue-1", role_in_context="regulated party",
            supporting_claim_ids=[by_predicate["entity_status"],
                                  by_predicate["legal_name"]],
            provenance_kind="MODEL",
            inference_id=resolved["inference_id"],
            proposal_id=resolved["proposal_id"])
    # (d) the faithful materialization works
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="issue-1", role_in_context="regulated party",
        supporting_claim_ids=[by_predicate["entity_status"]],
        provenance_kind="MODEL",
        inference_id=resolved["inference_id"],
        proposal_id=resolved["proposal_id"])
    assert assessment["authority"] == "SUPPORTED_INFERENCE"
    assert assessment["positions"] == []


def test_q7_edge_binding_commits_to_content_not_labels(tmp_path):
    """Round-5 note N1: an accepted edge chain binds the edges' CONTENT; a
    materialization reusing the accepted edge_id with different semantics is
    refused."""
    from curunir_analytic.impact import edge_chain_fingerprint
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))
    accepted_edge = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
        note="", basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    chain = edge_chain_fingerprint((accepted_edge,))
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {"objective_id": objective["objective_id"],
                                        "summary": "s", "edge_chain": chain})
    proposed = assist.propose(ctx, task="t", target_kind="impact_path",
                              inputs={}, input_refs=())
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    swapped = ImpactEdge(
        edge_id="e1",  # same label…
        from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="INFERENCE", effect_order="POTENTIAL",  # …different semantics
        authority="SUPPORTED_INFERENCE",
        note="the entity is likely to be delisted", basis_ids=(),
        assumption_ids=())
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        build_path(ctx, objective_id=objective["objective_id"], summary="s",
                   edges=(swapped,), provenance_kind="MODEL",
                   inference_id=resolved["inference_id"],
                   proposal_id=resolved["proposal_id"])
    faithful = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                          edges=(accepted_edge,), provenance_kind="MODEL",
                          inference_id=resolved["inference_id"],
                          proposal_id=resolved["proposal_id"])
    assert faithful["path_authority"] == "DERIVED"


def test_q4_phantom_edge_basis_is_refused(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="OBSERVED", note="",
                      basis_ids=("claim-that-never-existed",), assumption_ids=())
    with pytest.raises(ValueError, match="phantom"):
        build_path(ctx, objective_id=objective["objective_id"], summary="s",
                   edges=(edge,))
    ghost = ImpactEdge(edge_id="e2", from_kind="object", from_id=ACME_OBJECT,
                       to_kind="mission_objective", to_id=objective["objective_id"],
                       edge_kind="DEPENDENCY", effect_order="DIRECT",
                       authority="DERIVED", note="",
                       basis_ids=(by_predicate["entity_status"],),
                       assumption_ids=("assumption-that-never-existed",))
    with pytest.raises(ValueError, match="unknown assumption"):
        build_path(ctx, objective_id=objective["objective_id"], summary="s",
                   edges=(ghost,))


def test_q5_contract_requires_proposal_for_model_subrecords():
    with pytest.raises(ValueError, match="accepted[\\s\\S]*candidate"):
        NarrativeVariant(
            variant_id="v1", narrative_id="n1", relation="PARAPHRASE",
            statement="s", claim_ids=("c1",), observation_ids=(),
            manifestation_ids=("m1",), language="",
            authority="SUPPORTED_INFERENCE", mechanism="m",
            provenance_kind="MODEL", inference_id="inf-1",
            recorded_time=NOW, marking=MARK, proposal_id="")


def test_q6_reconcile_recalls_do_not_inflate_history(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://a.example.no/x",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://b.example.se/y",
               body=statement_page("Acme Industri considers closing the Oslo plant"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    core = next(c for value, c in claims.items() if "plans to close" in value)
    softer = next(c for value, c in claims.items() if "considers" in value)
    narrative = create_narrative(ctx, statement=statement,
                                 supporting_claim_ids=[core["claim_id"]])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in softer["observation_ids"])
    kwargs = dict(relation="CERTAINTY_SHIFT", statement=softer["object_or_value"],
                  claim_ids=(softer["claim_id"],),
                  manifestation_ids=(manifestation_id,),
                  authority="ANALYST_ASSESSMENT", mechanism="weakened",
                  provenance_kind="ANALYST")
    for attempt in range(5):
        add_variant(ctx, narrative["narrative_id"],
                    caused_by=f"novel-cause-{attempt}", **kwargs)
    added = [t for t in ctx.store.transitions_for(narrative["narrative_id"])
             if t["transition_type"] == "VARIANT_ADDED"]
    assert len(added) == 1, "one variant, one transition — regardless of re-calls"
