"""The analytic layer rebuilds from its log alone — across a restart and across
export and import, with no network — and an interrupted write completes on the
next run instead of leaving half a record."""
from __future__ import annotations

import socket

import pytest

from curunir_analytic.contracts import ImpactEdge, StakeholderPosition
from curunir_analytic.explain import explain_object
from curunir_analytic.impact import build_path, create_objective, record_assumption
from curunir_analytic.stakeholders import add_position, create_assessment
from curunir_analytic.store import AnalyticStore
from curunir_analytic.substrate import AnalyticContext
from curunir_analytic.themes import create_theme
from curunir_semantic.worldmodel import world_object_id

from analytic_support import GLEIF_ACME, MARK, T0, make_analytic
from semantic_support import clock, plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")


def _build_full_state(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    status_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                        if c["predicate"] == "entity_status")
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[status_claim], provenance_kind="RULE")
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="THEME",
        context_id=theme["theme_id"], role_in_context="subject entity",
        supporting_claim_ids=[status_claim])
    add_position(ctx, assessment["assessment_id"], StakeholderPosition(
        position_id="p1", kind="INFERRED_INTEREST", statement="interest",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(status_claim,), relationship_ids=(), valid_from=None,
        valid_to=None, superseded=False, note="n"), caused_by="t")
    assumption = record_assumption(ctx, statement="Acme remains active",
                                   supporting_claim_ids=(status_claim,))
    objective = create_objective(ctx, mission_context="m1",
                                 statement="Track Acme standing",
                                 depends_on=(("object", ACME_OBJECT),))
    path = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                      edges=(ImpactEdge(
                          edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                          to_kind="mission_objective",
                          to_id=objective["objective_id"], edge_kind="DEPENDENCY",
                          effect_order="DIRECT", authority="DERIVED", note="",
                          basis_ids=(status_claim,),
                          assumption_ids=(assumption["assumption_id"],)),))
    return {"theme": theme, "assessment": assessment, "objective": objective,
            "path": path, "assumption": assumption, "status_claim": status_claim}


def _analytic_view(store: AnalyticStore) -> dict:
    return {
        "themes": store.current_themes(),
        "narratives": store.current_narratives(),
        "assessments": store.current_stakeholder_assessments(),
        "influence": store.current_influence_assertions(),
        "objectives": store.current_objectives(),
        "assumptions": store.current_assumptions(),
        "paths": store.current_impact_paths(),
        "transitions": store.records_of("analytic_transition"),
    }


def test_restart_reconstructs_analytical_state_without_network(tmp_path, monkeypatch):
    pipeline, ctx = make_analytic(tmp_path)
    state = _build_full_state(pipeline, ctx)
    before = _analytic_view(ctx.store)

    # Stand in for a fresh process: no network, reload from disk.
    def _no_network(*args, **kwargs):
        raise AssertionError("replay must not touch the network")
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    reloaded = AnalyticStore(tmp_path / "store")
    after = _analytic_view(reloaded)
    assert after == before
    # Explanations must work from the reloaded store alone.
    explanation = explain_object(reloaded, "analytic_theme",
                                 state["theme"]["theme_id"])
    assert explanation["WHY"][0]["state"] == "CURRENT"
    assert reloaded.verify_chain()["valid"] is True


def test_export_import_round_trip_preserves_analytics(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _build_full_state(pipeline, ctx)
    manifest = ctx.store.export_to(tmp_path / "export")
    imported = AnalyticStore.import_from(tmp_path / "export", tmp_path / "imported")
    assert _analytic_view(imported) == _analytic_view(ctx.store)
    assert imported.head()["head_hash"] == manifest["head_hash"]


def test_interrupted_creation_completes_on_rerun(tmp_path):
    """A crash before the CREATED transition is completed by the re-run."""
    pipeline, ctx = make_analytic(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    status_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                        if c["predicate"] == "entity_status")

    real_append = ctx.store.append
    calls = {"n": 0}

    def failing_append(event_type, record, **kwargs):
        if event_type == "ANALYTIC_TRANSITION_RECORDED":
            calls["n"] += 1
            raise OSError("simulated crash before transition landed")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = failing_append
    with pytest.raises(OSError):
        create_theme(ctx, title="Acme registry standing",
                     supporting_claim_ids=[status_claim], provenance_kind="RULE")
    ctx.store.append = real_append

    # The theme landed but its transition did not.
    theme_id = next(iter(ctx.store.current_themes()))
    assert ctx.store.transitions_for(theme_id) == []
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[status_claim], provenance_kind="RULE")
    assert theme["version"] == 1  # no duplicate object version
    transitions = ctx.store.transitions_for(theme_id)
    assert [t["transition_type"] for t in transitions] == ["CREATED"]
    create_theme(ctx, title="Acme registry standing",
                 supporting_claim_ids=[status_claim], provenance_kind="RULE")
    assert len(ctx.store.transitions_for(theme_id)) == 1
    assert len(ctx.store.analytic_versions("analytic_theme", theme_id)) == 1
