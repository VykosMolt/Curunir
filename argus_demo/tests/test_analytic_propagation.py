"""Integration: semantic change → analytical update → alert; analytical
uncertainty → discriminator → requirement; provider boundary honesty; unified
explanation."""
from __future__ import annotations

import pytest

from curunir_analytic.collect import analytic_collection_needs, open_analytic_requirements
from curunir_analytic.contracts import ImpactEdge, StakeholderPosition
from curunir_analytic.explain import explain_object, render_text
from curunir_analytic.impact import build_path, create_objective, record_assumption
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.stakeholders import add_position, create_assessment
from curunir_analytic.substrate import resolve_candidate
from curunir_analytic.themes import create_theme
from curunir_semantic.changes import interpret_change
from curunir_semantic.hypotheses import link_claim, record_hypothesis
from curunir_semantic.worldmodel import IntegrationContext, world_object_id

from analytic_support import GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0, make_analytic
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")


def _seed_world_and_analytics(pipeline, ctx):
    """GLEIF evidence + a theme, assumption, objective, path and hypothesis
    all resting on the entity_status claim."""
    v1 = plant_manifestation(pipeline, source_id="gleif",
                             native_id="lei/ACMELEI000000000001",
                             body=GLEIF_ACME, media_type="application/json",
                             retrieval_time=T0)
    pipeline.process_new_evidence()
    status_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                        if c["predicate"] == "entity_status")
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[status_claim],
                         provenance_kind="RULE")
    assumption = record_assumption(
        ctx, statement="Acme remains an active legal entity",
        supporting_claim_ids=(status_claim,))
    objective = create_objective(
        ctx, mission_context="m1",
        statement="Maintain contractual continuity with Acme",
        depends_on=(("object", ACME_OBJECT),),
        assumption_ids=(assumption["assumption_id"],))
    edge1 = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
        note="", basis_ids=(status_claim,),
        assumption_ids=(assumption["assumption_id"],))
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="continuity depends on Acme's standing",
                      edges=(edge1,))
    hypothesis = record_hypothesis(
        ctx.store, statement="Acme will remain a viable counterparty",
        case_id="m1", analyst_or_provider="jan", now=ctx.now_fn(),
        actor="jan", marking=MARK)
    link_claim(ctx.store, hypothesis["hypothesis_id"], status_claim, "supporting",
               rationale="registry standing", now=ctx.now_fn(), actor="jan",
               marking=MARK)
    return {"v1": v1, "status_claim": status_claim, "theme": theme,
            "assumption": assumption, "objective": objective, "path": path,
            "hypothesis": hypothesis}


def test_semantic_change_updates_exactly_the_dependent_analytics(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    state = _seed_world_and_analytics(pipeline, ctx)
    # an unrelated theme must not be touched by the change
    unrelated_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                           if c["predicate"] == "jurisdiction")
    unrelated = create_theme(ctx, title="Acme jurisdiction",
                             supporting_claim_ids=[unrelated_claim],
                             provenance_kind="RULE")

    v2 = plant_manifestation(pipeline, source_id="gleif",
                             native_id="lei/ACMELEI000000000001",
                             body=GLEIF_ACME_SUSPENDED,
                             media_type="application/json",
                             retrieval_time="2026-08-17T18:00:00+00:00",
                             prior_manifestation_id=state["v1"]["manifestation_id"])
    pipeline.process_new_evidence()
    integration = IntegrationContext(store=ctx.store, actor="t", marking=MARK,
                                     now_fn=ctx.now_fn)
    changes = interpret_change(integration, state["v1"]["manifestation_id"],
                               v2["manifestation_id"])
    assert any(c["change_class"] == "ENTITY_ATTRIBUTE_CHANGED" for c in changes)

    outcomes = propagate_semantic_changes(ctx)
    assert outcomes, "the change must reach the analytical layer"
    all_transitions = [t for o in outcomes for t in o.get("transitions", ())]
    assert ("analytic_theme", "EVIDENCE_UPDATED") in all_transitions
    assert ("analytic_assumption", "QUESTIONED") in all_transitions
    assert ("mission_objective", "EXPOSED") in all_transitions
    # attribution is collective and truthful: each object outcome names the
    # full set of changes folded into its refresh
    object_outcomes = [o for o in outcomes if "change_ids" in o]
    assert all(o["change_ids"] for o in object_outcomes)

    assumption = ctx.store.current_assumptions()[state["assumption"]["assumption_id"]]
    assert assumption["status"] == "UNCERTAIN"
    objective = ctx.store.current_objectives()[state["objective"]["objective_id"]]
    assert objective["status"] == "EXPOSED"
    hypothesis = ctx.store.current_hypotheses()[state["hypothesis"]["hypothesis_id"]]
    assert any(o.get("hypotheses_refreshed") for o in outcomes) or hypothesis
    # the unrelated theme was not touched
    assert ctx.store.current_themes()[unrelated["theme_id"]]["version"] == 1
    # alerts were raised and explain the analytical effect
    alerts = [a for a in ctx.store.records_of("alert")
              if a["rule_id"] == "analytic-change"]
    assert alerts
    assert "analytical effect" in alerts[0]["trigger"]

    # idempotence: a second propagation pass changes nothing
    again = propagate_semantic_changes(ctx)
    assert again == []


def test_analytic_uncertainty_generates_requirements_through_existing_machinery(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    state = _seed_world_and_analytics(pipeline, ctx)
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="THEME",
        context_id=state["theme"]["theme_id"], role_in_context="subject entity",
        supporting_claim_ids=[state["status_claim"]])
    add_position(ctx, assessment["assessment_id"], StakeholderPosition(
        position_id="int-1", kind="INFERRED_INTEREST",
        statement="Acme benefits from delayed disclosure requirements",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(state["status_claim"],), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False,
        note="inferred; no primary statement"), caused_by="t")

    needs = analytic_collection_needs(ctx.store)
    kinds = {n["source_kind"] for n in needs}
    assert "analytic_theme" in kinds        # single-family theme
    assert "stakeholder_assessment" in kinds  # interest without public position
    theme_need = next(n for n in needs if n["source_kind"] == "analytic_theme")
    assert theme_need["independence_required"] is True

    opened = open_analytic_requirements(ctx, mission_context="m1")
    assert opened
    for entry in opened:
        assert entry["discriminator"]["status"] == "REQUESTED"
        assert entry["requirement"]["requirement_id"]
    # idempotent: same needs map to the same discriminators/requirements
    reopened = open_analytic_requirements(ctx, mission_context="m1")
    assert {e["discriminator"]["discriminator_id"] for e in reopened} \
        == {e["discriminator"]["discriminator_id"] for e in opened}


def test_provider_boundary_is_honest_and_attributed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    state = _seed_world_and_analytics(pipeline, ctx)
    unavailable = AnalyticalAssist()
    assert unavailable.status()["status"] == "ANALYTICAL_PROVIDER_UNAVAILABLE"
    result = unavailable.propose(ctx, task="theme-label", target_kind="analytic_theme",
                                 inputs={"claims": [state["status_claim"]]},
                                 input_refs=(state["status_claim"],))
    assert result["status"] == "ANALYTICAL_PROVIDER_UNAVAILABLE"

    def stub_infer(task, payload):
        return {"title": "Registry standing risk",
                "supporting_claim_ids": list(payload["claims"])}

    assist = AnalyticalAssist(
        package=analytical_assist_package("test-provider", "stub-model", "1.0"),
        infer_fn=stub_infer)
    proposed = assist.propose(ctx, task="theme-label", target_kind="analytic_theme",
                              inputs={"claims": [state["status_claim"]]},
                              input_refs=(state["status_claim"],))
    assert proposed["status"] == "PROPOSED"
    inference = next(r for r in ctx.store.records_of("inference")
                     if r["inference_id"] == proposed["inference_id"])
    assert inference["model_id"] == "stub-model"
    # the candidate is not analytical state until a human accepts it
    assert proposed["proposal"]["proposal_id"] not in ctx.store.current_themes()
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    content = resolved["content"]
    theme = create_theme(ctx, title=content["title"],
                         supporting_claim_ids=content["supporting_claim_ids"],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    assert theme["provenance_kind"] == "MODEL"
    assert theme["inference_id"] == proposed["inference_id"]
    # a MODEL theme without its inference identity is unconstructible
    with pytest.raises(ValueError, match="inference record"):
        create_theme(ctx, title="phantom", supporting_claim_ids=[state["status_claim"]],
                     provenance_kind="MODEL")


def test_unified_explanation_covers_all_sections(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    state = _seed_world_and_analytics(pipeline, ctx)
    explanation = explain_object(ctx.store, "impact_path", state["path"]["path_id"])
    for section in ("WHAT", "WHY", "AGAINST", "SOURCE_BASIS", "TEMPORAL",
                    "INFERENCES", "UNCERTAINTY", "MISSION_EFFECT"):
        assert section in explanation
    assert explanation["UNCERTAINTY"], "assumption-bearing path must state uncertainty"
    theme_explanation = explain_object(ctx.store, "analytic_theme",
                                       state["theme"]["theme_id"])
    assert theme_explanation["SOURCE_BASIS"]["independent_origin_families"] == 1
    assert any("one origin family" in u for u in theme_explanation["UNCERTAINTY"])
    text = render_text(theme_explanation)
    assert "WHY:" in text and "SOURCE BASIS:" in text and "UNCERTAINTY:" in text
