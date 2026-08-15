"""Impact engine: typed paths ending at objectives, uncertainty propagation,
assumption invalidation reaching dependent paths and objectives, deterministic
path suggestion honest about missing typed routes, human-only response
acceptance."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ImpactEdge
from curunir_analytic.impact import (build_path, create_objective, explain_path,
                                     invalidate_assumption, propose_response_option,
                                     record_assumption, refresh_path,
                                     review_response_option, suggest_path_edges)
from curunir_semantic.contracts import ClaimStateRecord
from curunir_semantic.worldmodel import world_object_id

from analytic_support import GLEIF_ACME, MARK, T0, make_analytic
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")
SUCCESSOR_OBJECT = world_object_id("LEI:SUCCLEI000000000003")

GLEIF_WITH_SUCCESSOR = GLEIF_ACME.replace(
    b'"legalAddress"',
    b'"successorEntity": {"lei": "SUCCLEI000000000003"}, "legalAddress"')


def _seed(pipeline, ctx, body=GLEIF_ACME):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=body, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == "LEI:ACMELEI000000000001"}


def _objective(ctx, depends=(("object", ACME_OBJECT),)):
    return create_objective(ctx, mission_context="m1",
                            statement="Maintain visibility of Acme's legal standing",
                            priority="HIGH", depends_on=depends)


def test_path_must_terminate_at_objective(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                      to_kind="object", to_id="elsewhere", edge_kind="DEPENDENCY",
                      effect_order="DIRECT", authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    with pytest.raises(ValueError, match="terminate at its objective"):
        build_path(ctx, objective_id=objective["objective_id"],
                   summary="broken", edges=(edge,))


def test_uncertain_edge_weakens_whole_path(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    assumption = record_assumption(
        ctx, statement="Acme remains the sole supplier through Q4",
        supporting_claim_ids=(by_predicate["entity_status"],),
        objective_ids=(objective["objective_id"],))
    observed = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="object", to_id="component-x", edge_kind="TYPED_RELATION",
        effect_order="DIRECT", authority="OBSERVED", note="",
        basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    inferred = ImpactEdge(
        edge_id="e2", from_kind="object", from_id="component-x",
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="INFERENCE", effect_order="SECOND_ORDER",
        authority="SUPPORTED_INFERENCE",
        note="component scarcity would delay the programme",
        basis_ids=(), assumption_ids=(assumption["assumption_id"],))
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="supplier exposure", edges=(observed, inferred))
    assert path["path_authority"] == "SUPPORTED_INFERENCE"
    assert "assumption" in path["uncertainty_note"]
    explanation = explain_path(ctx.store, path["path_id"])
    orders = [e["effect_order"] for e in explanation["edges"]]
    assert orders == ["DIRECT", "SECOND_ORDER"]
    assert explanation["edges"][1]["assumptions"] == [
        "Acme remains the sole supplier through Q4"]


def test_invalidated_assumption_reaches_paths_and_objectives(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    assumption = record_assumption(
        ctx, statement="Supplier X remains available through Q4",
        supporting_claim_ids=(by_predicate["entity_status"],))
    edge1 = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="object", to_id="supplier-x", edge_kind="TYPED_RELATION",
        effect_order="DIRECT", authority="OBSERVED", note="",
        basis_ids=(by_predicate["entity_status"],),
        assumption_ids=(assumption["assumption_id"],))
    edge2 = ImpactEdge(
        edge_id="e2", from_kind="object", from_id="supplier-x",
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="SECOND_ORDER", authority="DERIVED",
        note="", basis_ids=(objective["objective_id"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="supply continuity", edges=(edge1, edge2))
    invalidated = invalidate_assumption(
        ctx, assumption["assumption_id"],
        contradicting_claim_ids=(by_predicate["registration_status"],),
        caused_by="chg-7", reason="supplier X ceased operations per new filing")
    assert invalidated["status"] == "INVALIDATED"
    refreshed_path = ctx.store.current_impact_paths()[path["path_id"]]
    assert refreshed_path["status"] == "STALE"
    refreshed_objective = ctx.store.current_objectives()[objective["objective_id"]]
    assert refreshed_objective["status"] == "EXPOSED"
    path_kinds = {t["transition_type"]
                  for t in ctx.store.transitions_for(path["path_id"])}
    assert "ASSUMPTION_INVALIDATED" in path_kinds
    # assumption history: HELD then INVALIDATED, both in the log
    versions = ctx.store.analytic_versions("analytic_assumption",
                                           assumption["assumption_id"])
    assert [v["status"] for v in versions] == ["HELD", "INVALIDATED"]


def test_degraded_evidence_stales_path(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    edge = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
        note="", basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="direct standing dependency", edges=(edge,))
    state = ClaimStateRecord(
        state_id="st-9", claim_id=by_predicate["entity_status"], state="DISPUTED",
        reason="independent source disagrees", caused_by="chg-1", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    refreshed = refresh_path(ctx, path["path_id"], caused_by="chg-1")
    assert refreshed["status"] == "STALE"
    assert "DISPUTED" in refreshed["change_reason"] or \
           "DISPUTED" in next(t["detail"] for t in ctx.store.transitions_for(path["path_id"])
                              if t["transition_type"] == "STALE")


def test_suggest_path_walks_only_typed_relations(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _seed(pipeline, ctx, body=GLEIF_WITH_SUCCESSOR)
    activity = next(a for a in ctx.store.records_of("activity")
                    if a["activity_type"] == "lei_registered")
    # objective depending on the successor entity: reachable only through the
    # evidence-stated SUCCESSOR_OF relation
    objective = create_objective(
        ctx, mission_context="m1", statement="Track successor entity standing",
        depends_on=(("object", SUCCESSOR_OBJECT),))
    edges = suggest_path_edges(ctx.store, activity_id=activity["activity_id"],
                               objective_id=objective["objective_id"])
    assert edges is not None
    kinds = [e.edge_kind for e in edges]
    assert kinds[0] == "EVENT_EFFECT" and kinds[-1] == "DEPENDENCY"
    assert any(e.edge_kind == "TYPED_RELATION"
               and "SUCCESSOR_OF" in e.note for e in edges)
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="event exposure via succession", edges=edges)
    assert path["path_authority"] == "DERIVED"  # deterministic walk, no inference
    # an objective with no typed route yields None, not an invented path
    unreachable = create_objective(
        ctx, mission_context="m1", statement="Track an unrelated asset",
        depends_on=(("object", world_object_id("LEI:UNRELATED0000000009")),))
    assert suggest_path_edges(ctx.store, activity_id=activity["activity_id"],
                              objective_id=unreachable["objective_id"]) is None


def test_response_acceptance_is_human_only(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = _objective(ctx)
    edge = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
        note="", basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="s", edges=(edge,))
    option = propose_response_option(
        ctx, objective_id=objective["objective_id"], path_id=path["path_id"],
        description="qualify a second supplier",
        tradeoffs=("cost", "lead time"),
        uncertainty_note="depends on unverified supplier capacity")
    with pytest.raises(ValueError, match="human"):
        review_response_option(ctx, option["option_id"], accept=True,
                               actor_id="svc", actor_kind="SERVICE", note="x")
    accepted = review_response_option(ctx, option["option_id"], accept=True,
                                      actor_id="jan", actor_kind="HUMAN",
                                      note="proceed with qualification")
    assert accepted["status"] == "ACCEPTED"
    assert accepted["human_actor"] == "jan"
