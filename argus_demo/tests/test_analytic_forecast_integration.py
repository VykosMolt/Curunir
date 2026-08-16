"""Forecast-plane integration locks: one semantic change drives indicator →
pre-authorized update → warning escalation → alert through the normal
propagation pass; uncertainty becomes collection through the existing
machinery; everything replays; interrupted creation completes."""
from __future__ import annotations

import pytest

from curunir_analytic.collect import (analytic_collection_needs,
                                      open_analytic_requirements)
from curunir_analytic.contracts import (ImpactEdge, IndicatorEffect,
                                        ResolutionRule)
from curunir_analytic.forecasts import create_forecast, refresh_forecast
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.indicators import arm_indicator
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.store import AnalyticStore
from curunir_analytic.warning import project_warning
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0,
                              make_analytic)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"
ACME_OBJECT = world_object_id(ACME)


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


def _forecast(ctx, by_predicate, probability=0.35, expected="INACTIVE",
              horizon=HORIZON):
    rule = ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))
    return create_forecast(
        ctx, question=f"Will {ACME} be suspended by {horizon}?",
        outcome_semantics="TRUE iff entity_status reads INACTIVE",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=rule, probability=probability,
        probability_basis="registry base rates", author="jan",
        domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _stand_up_the_plane(pipeline, ctx, by_predicate):
    """Objective + impact path + forecast + pre-authorized indicator +
    warning: the full chain, armed and quiet."""
    objective = create_objective(
        ctx, mission_context="m1",
        statement="Maintain visibility of Acme's legal standing",
        priority="HIGH", depends_on=(("object", ACME_OBJECT),))
    build_path(ctx, objective_id=objective["objective_id"],
               summary="registry standing exposure",
               edges=(ImpactEdge(
                   edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                   to_kind="mission_objective",
                   to_id=objective["objective_id"], edge_kind="DEPENDENCY",
                   effect_order="DIRECT", authority="DERIVED", note="",
                   basis_ids=(by_predicate["entity_status"],),
                   assumption_ids=()),))
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         horizon="2026-09-10T12:00:00+00:00")
    indicator = arm_indicator(
        ctx, description=f"GLEIF entity_status for {ACME} reads INACTIVE",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                               target_probability=0.62,
                               rationale="a registry flip mostly settles it",
                               authorized_by="jan", authorized_kind="HUMAN"))
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    assert warning["tier"] == "ATTENTION"  # HIGH x POSSIBLE, NEAR horizon
    return objective, forecast, indicator, warning


def test_one_evidence_change_drives_the_whole_chain(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective, forecast, indicator, warning = _stand_up_the_plane(
        pipeline, ctx, by_predicate)
    # quiet pass first: nothing pending appends nothing
    propagate_semantic_changes(ctx)
    quiet = ctx.store.head()["event_count"]
    propagate_semantic_changes(ctx)
    assert ctx.store.head()["event_count"] == quiet, \
        "a quiet propagation pass appends nothing"
    # the world changes once
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    outcomes = propagate_semantic_changes(ctx)
    # indicator fired and executed the analyst's recorded judgment
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "FIRED"
    moved = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert moved["probability"] == 0.62, "exactly the pre-authorized target"
    # the warning escalated through the named rule, with an alert
    escalated = ctx.store.current_warnings()[warning["warning_id"]]
    assert escalated["tier"] == "PRIORITY"
    assert escalated["status"] == "ESCALATED"
    plane = next(o for o in outcomes if o["changeset"] == "forecast-plane")
    assert ("strategic_warning", "ESCALATED") in plane["transitions"]
    assert plane["alerts"], "an escalation reaches the mission workflow"
    # the whole cascade is idempotent
    after = ctx.store.head()["event_count"]
    propagate_semantic_changes(ctx)
    assert ctx.store.head()["event_count"] == after


def test_forecast_uncertainty_becomes_collection(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    # a single-family forecast wants independent corroboration
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         horizon="2026-09-10T12:00:00+00:00")
    needs = analytic_collection_needs(ctx.store)
    forecast_needs = [n for n in needs
                      if n["source_id"] == forecast["forecast_id"]]
    assert forecast_needs and forecast_needs[0]["independence_required"]
    assert forecast_needs[0]["desired_attribute"] == "entity_status"
    # a coverage-blocked resolution asks for exactly the declared sources
    blocked = _forecast(ctx, by_predicate, probability=0.20,
                        expected="NEVER_SO", horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, blocked["forecast_id"], caused_by="tick")
    needs = analytic_collection_needs(ctx.store)
    coverage_needs = [n for n in needs if n["source_id"] == blocked["forecast_id"]
                      and "coverage-blocked" in n["question"]]
    assert coverage_needs and "gleif" in coverage_needs[0]["question"]
    # an armed absence indicator asks for its coverage before the deadline
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline="2026-08-17T23:00:00+00:00",
        coverage_required_source_ids=("gleif",))
    needs = analytic_collection_needs(ctx.store)
    indicator_needs = [n for n in needs
                       if n["source_id"] == indicator["indicator_id"]]
    assert indicator_needs and "someone looked" in indicator_needs[0]["question"]
    # the needs open real discriminators and requirements, idempotently
    opened = open_analytic_requirements(ctx, mission_context="m1")
    assert opened
    before = ctx.store.head()["event_count"]
    open_analytic_requirements(ctx, mission_context="m1")
    assert ctx.store.head()["event_count"] == before, \
        "re-opening the same needs appends nothing"


def test_forecast_plane_replays_without_network(tmp_path, monkeypatch):
    import socket
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    _stand_up_the_plane(pipeline, ctx, by_predicate)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    propagate_semantic_changes(ctx)
    view = {
        "forecasts": ctx.store.current_forecasts(),
        "indicators": ctx.store.current_indicators(),
        "warnings": ctx.store.current_warnings(),
        "transitions": ctx.store.records_of("analytic_transition"),
    }

    def _no_network(*args, **kwargs):
        raise AssertionError("replay must not touch the network")
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    reloaded = AnalyticStore(tmp_path / "store")
    assert {
        "forecasts": reloaded.current_forecasts(),
        "indicators": reloaded.current_indicators(),
        "warnings": reloaded.current_warnings(),
        "transitions": reloaded.records_of("analytic_transition"),
    } == view
    assert reloaded.verify_chain()["valid"] is True


def test_interrupted_forecast_creation_completes_on_rerun(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)

    real_append = ctx.store.append

    def failing_append(event_type, record, **kwargs):
        if event_type == "ANALYTIC_TRANSITION_RECORDED":
            raise OSError("simulated crash before transition landed")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = failing_append
    with pytest.raises(OSError):
        _forecast(ctx, by_predicate, probability=0.35)
    ctx.store.append = real_append

    forecast_id = next(iter(ctx.store.current_forecasts()))
    assert ctx.store.transitions_for(forecast_id) == []
    completed = _forecast(ctx, by_predicate, probability=0.35)
    assert completed["version"] == 1, "no duplicate version from completion"
    assert [t["transition_type"]
            for t in ctx.store.transitions_for(forecast_id)] == ["CREATED"]
    # and a third run changes nothing
    _forecast(ctx, by_predicate, probability=0.35)
    assert len(ctx.store.analytic_versions("analytic_forecast",
                                           forecast_id)) == 1
    assert len(ctx.store.transitions_for(forecast_id)) == 1
