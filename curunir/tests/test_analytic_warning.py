"""Warnings: the tier comes from one named table and nowhere else, weak evidence
caps how far it can rise, re-projection only appends, and a settled forecast
closes its warning."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ImpactEdge, ResolutionRule, WarningRecord
from curunir_analytic.forecasts import (create_forecast, try_machine_resolution,
                                        update_probability)
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.warning import (TIER_RULE_V1, derive_tier,
                                      evidence_confidence, probability_band,
                                      project_warning, refresh_warnings,
                                      time_pressure)
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME_SUSPENDED, MARK, T0, make_analytic,
                              seed_acme)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _objective(ctx, priority="HIGH"):
    return create_objective(
        ctx, mission_context="m1",
        statement="Maintain visibility of Acme's legal standing",
        priority=priority)


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
        ctx, question=f"Will {ACME} be suspended by {HORIZON}?",
        outcome_semantics="TRUE iff entity_status reads INACTIVE",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=rule, probability=probability,
        probability_basis="registry base rates", author="jan",
        domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _path(ctx, by_predicate, objective):
    acme_object = world_object_id(ACME)
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=acme_object,
                      to_kind="mission_objective",
                      to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],),
                      assumption_ids=())
    return build_path(ctx, objective_id=objective["objective_id"],
                      summary="registry standing exposure", edges=(edge,))


# ---- the named rule --------------------------------------------------------


def test_tier_comes_from_the_table_with_recorded_adjustments():
    tier, notes = derive_tier("LIKELY", "CRITICAL", "NEAR", "STRONG")
    assert tier == "CRITICAL" and any("table[CRITICAL][LIKELY]" in n
                                      for n in notes)
    tier, notes = derive_tier("POSSIBLE", "HIGH", "IMMINENT", "STRONG")
    assert tier == "PRIORITY", "imminence bumps exactly one tier"
    assert any("IMMINENT" in n for n in notes)
    tier, notes = derive_tier("VERY_LIKELY", "CRITICAL", "NEAR", "WEAK")
    assert tier == "PRIORITY", \
        "an unsupported number does not drive CRITICAL alone"
    assert any("caps at PRIORITY" in n for n in notes)


def test_component_derivations_are_typed():
    assert probability_band(0.05) == "REMOTE"
    assert probability_band(0.62) == "LIKELY"
    assert probability_band(0.80) == "VERY_LIKELY"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-08-17T11:00:00+00:00") == "PASSED"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-08-18T12:00:00+00:00") == "IMMINENT"
    assert time_pressure("2026-08-17T12:00:00+00:00",
                         "2026-09-10T12:00:00+00:00") == "NEAR"
    assert evidence_confidence({"origin_families": (),
                                "degraded_claim_count": 0}) == "NONE"
    assert evidence_confidence({"origin_families": ("gleif",),
                                "degraded_claim_count": 0}) == "WEAK"
    assert evidence_confidence({"origin_families": ("a", "b"),
                                "degraded_claim_count": 1}) == "WEAK", \
        "a degraded basis does not count at full strength"
    assert evidence_confidence({"origin_families": ("a", "b", "c"),
                                "degraded_claim_count": 0}) == "STRONG"


def test_a_free_tier_is_unconstructible():
    with pytest.raises(ValueError, match="named rule"):
        WarningRecord(
            warning_id="w1", version=1, mission_context="m1",
            objective_id="o1", forecast_id="f1", impact_path_ids=(),
            probability_band="LIKELY", consequence="HIGH",
            time_pressure="NEAR", evidence_confidence="MODERATE",
            tier="CRITICAL", tier_rule_id="",
            component_basis=(("probability_band", "b"),),
            status="ACTIVE", change_reason="", history=(),
            recorded_time=T0, marking=MARK)


# ---- projection over the store --------------------------------------------


def test_projection_binds_forecast_objective_and_paths(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = _objective(ctx, priority="HIGH")
    path = _path(ctx, by_predicate, objective)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    assert warning["tier"] == "PRIORITY"  # table[HIGH][LIKELY]
    assert warning["tier_rule_id"] == TIER_RULE_V1
    assert warning["impact_path_ids"] == [path["path_id"]] \
        or tuple(warning["impact_path_ids"]) == (path["path_id"],), \
        "the typed reason this forecast threatens this objective"
    assert warning["evidence_confidence"] == "WEAK"  # only one source family
    basis_components = {component for component, _ in warning["component_basis"]}
    assert {"probability_band", "consequence", "time_pressure",
            "evidence_confidence", "tier_rule"} <= basis_components


def test_reprojection_is_append_only_and_names_what_moved(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = _objective(ctx, priority="HIGH")
    _path(ctx, by_predicate, objective)
    # A near, not imminent, horizon, so nothing bumps the tier on its own.
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         horizon="2026-09-10T12:00:00+00:00")
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    assert warning["tier"] == "ATTENTION"  # table[HIGH][POSSIBLE], no bump
    before = ctx.store.head()["event_count"]
    unchanged = refresh_warnings(ctx)
    assert ctx.store.head()["event_count"] == before, \
        "unchanged inputs append nothing"
    assert unchanged[0]["version"] == warning["version"]
    update_probability(ctx, forecast["forecast_id"], probability=0.62,
                       reason="filing trouble", actor_id="jan",
                       actor_kind="HUMAN")
    escalated = refresh_warnings(ctx)[0]
    assert escalated["tier"] == "PRIORITY"
    assert escalated["status"] == "ESCALATED"
    versions = ctx.store.analytic_versions("strategic_warning",
                                           warning["warning_id"])
    assert [v["tier"] for v in versions] == ["ATTENTION", "PRIORITY"], \
        "the old tier is history, never overwritten"
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(warning["warning_id"])]
    assert kinds == ["RAISED", "ESCALATED"]


def test_settled_forecast_resolves_its_warning(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = _objective(ctx)
    _path(ctx, by_predicate, objective)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    warning = project_warning(ctx, forecast_id=forecast["forecast_id"],
                              objective_id=objective["objective_id"])
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    assert try_machine_resolution(
        ctx, forecast["forecast_id"])["status"] == "RESOLVED_TRUE"
    resolved = refresh_warnings(ctx)[0]
    assert resolved["status"] == "RESOLVED"
    # A resolved warning is done; refreshing again must add nothing.
    before = ctx.store.head()["event_count"]
    refresh_warnings(ctx)
    assert ctx.store.head()["event_count"] == before


def test_warning_cannot_be_raised_about_a_settled_question(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = _objective(ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.62)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    try_machine_resolution(ctx, forecast["forecast_id"])
    with pytest.raises(ValueError, match="settled"):
        project_warning(ctx, forecast_id=forecast["forecast_id"],
                        objective_id=objective["objective_id"])
