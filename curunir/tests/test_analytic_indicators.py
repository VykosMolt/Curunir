"""Indicators fire only on evidence that arrives after arming, apply exactly the
effect a person authorized, need coverage before absence counts, and fire once."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import IndicatorEffect, IndicatorRecord
from curunir_analytic.forecasts import create_forecast, update_probability
from curunir_analytic.indicators import arm_indicator, check_indicators
from curunir_fabric.contracts import ExecutionRecord
from curunir_analytic.contracts import ResolutionRule

from analytic_support import (GLEIF_ACME_SUSPENDED, MARK, make_analytic,
                              seed_acme)
from semantic_support import advance_clock_past, plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _forecast(ctx, by_predicate, probability=0.35):
    rule = ResolutionRule(kind="HUMAN_JUDGMENT",
                         criteria="analyst settles at horizon")
    return create_forecast(
        ctx, question=f"Will {ACME} be suspended by {HORIZON}?",
        outcome_semantics="TRUE iff entity_status reads INACTIVE by the horizon",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=HORIZON, resolution=rule,
        probability=probability,
        probability_basis="stable registry; low base rate",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _presence_effect(target=0.62):
    return IndicatorEffect(mode="APPLY_PROBABILITY", target_probability=target,
                           rationale="if the registry flips to INACTIVE the "
                                     "question is mostly settled",
                           authorized_by="jan", authorized_kind="HUMAN")


def _arm_presence(ctx, forecast, effect=None):
    return arm_indicator(
        ctx, description=f"GLEIF entity_status for {ACME} reads INACTIVE",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=effect if effect is not None else _presence_effect())


def _flip_to_suspended(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()


# Contract law


def test_pre_authorized_effect_requires_a_human():
    with pytest.raises(ValueError, match="only a human"):
        IndicatorEffect(mode="APPLY_PROBABILITY", target_probability=0.6,
                        rationale="r", authorized_by="svc",
                        authorized_kind="SERVICE")
    with pytest.raises(ValueError, match="inside"):
        IndicatorEffect(mode="APPLY_PROBABILITY", target_probability=1.0,
                        rationale="r", authorized_by="jan",
                        authorized_kind="HUMAN")
    with pytest.raises(ValueError, match="rationale"):
        IndicatorEffect(mode="APPLY_PROBABILITY", target_probability=0.6,
                        rationale="", authorized_by="jan",
                        authorized_kind="HUMAN")


def test_absence_indicator_requires_deadline_and_coverage(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    with pytest.raises(ValueError, match="deadline"):
        arm_indicator(ctx, description="no filing appears",
                      forecast_ids=(forecast["forecast_id"],),
                      kind="ABSENCE", direction="UNDERMINES")
    with pytest.raises(ValueError, match="unfalsifiable"):
        arm_indicator(ctx, description="no filing appears anywhere",
                      forecast_ids=(forecast["forecast_id"],),
                      kind="ABSENCE", direction="UNDERMINES",
                      deadline="2026-08-17T13:00:00+00:00",
                      coverage_min_successful_sources=0)


def test_arming_requires_a_live_forecast(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    with pytest.raises(ValueError, match="unknown forecast"):
        arm_indicator(ctx, description="d", forecast_ids=("nope",),
                      kind="PRESENCE", direction="SUPPORTS")


# Firing


def test_presence_never_fires_on_pre_arming_evidence(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    # ACTIVE was already observed before this indicator was armed.
    indicator = arm_indicator(
        ctx, description=f"entity_status for {ACME} reads ACTIVE",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="ACTIVE", effect=IndicatorEffect(mode="REVIEW_ONLY"))
    check_indicators(ctx)
    current = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert current["status"] == "ARMED", \
        "what made the question worth watching is not its answer"


def test_presence_fires_and_executes_the_authorized_effect_exactly(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = _arm_presence(ctx, forecast)
    armed = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert indicator["indicator_id"] in armed["indicator_ids"]
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    fired = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert fired["status"] == "FIRED"
    assert fired["fired_evidence_refs"], "a firing carries its evidence"
    moved = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert moved["probability"] == 0.62, "exactly the pre-authorized target"
    assert "pre-authorized by jan" in moved["probability_basis"]
    versions = ctx.store.analytic_versions("analytic_forecast",
                                           forecast["forecast_id"])
    assert versions[0]["probability"] == 0.35
    assert versions[-1]["probability"] == 0.62
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(forecast["forecast_id"])]
    assert "INDICATOR_FIRED" in kinds and "PROBABILITY_UPDATED" in kinds


def test_service_cannot_ride_an_unfired_indicator(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    indicator = _arm_presence(ctx, forecast)
    with pytest.raises(ValueError, match="FIRED indicator"):
        update_probability(ctx, forecast["forecast_id"], probability=0.62,
                           reason="jumping the gun", actor_id="svc",
                           actor_kind="SERVICE",
                           indicator_id=indicator["indicator_id"])


def test_service_cannot_bend_the_authorized_target(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    indicator = _arm_presence(ctx, forecast, effect=_presence_effect(0.62))
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    # The indicator already applied 0.62; it cannot be reused for another number.
    with pytest.raises(ValueError, match="exact"):
        update_probability(ctx, forecast["forecast_id"], probability=0.9,
                           reason="stretching the authorization", actor_id="svc",
                           actor_kind="SERVICE",
                           indicator_id=indicator["indicator_id"])


def test_review_only_effect_flags_and_never_moves_the_number(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    _arm_presence(ctx, forecast, effect=IndicatorEffect(mode="REVIEW_ONLY"))
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    flagged = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert flagged["probability"] == 0.35, \
        "REVIEW_ONLY moves nothing: the analyst decides"
    assert flagged["status"] == "UPDATE_REQUIRED"
    items = [r for r in ctx.store.open_review_items()
             if r["subject_id"] == forecast["forecast_id"]]
    assert items, "the flag is durable, reviewable state"


def test_firing_is_one_shot(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    _arm_presence(ctx, forecast)
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    moved = ctx.store.current_forecasts()[forecast["forecast_id"]]
    length = ctx.store.head()["event_count"]
    check_indicators(ctx)
    check_indicators(ctx)
    assert ctx.store.head()["event_count"] == length, \
        "a fired indicator is done: re-checking appends nothing"
    assert ctx.store.current_forecasts()[forecast["forecast_id"]] == moved


# Absence


def _arm_absence(ctx, forecast, deadline="2026-08-17T12:20:00+00:00"):
    return arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline=deadline,
        coverage_min_successful_sources=1,
        coverage_required_source_ids=("gleif",))


def _successful_execution(ctx, execution_id="exec-abs-1"):
    execution = ExecutionRecord(
        execution_id=execution_id, plan_id="", query_id="q1", source_id="gleif",
        connector_id="gleif-lei-v1", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")


def test_absence_waits_for_its_deadline(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    indicator = _arm_absence(ctx, forecast, deadline="2026-08-17T23:00:00+00:00")
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "ARMED"


def test_absence_without_coverage_blocks_instead_of_firing(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = _arm_absence(ctx, forecast, deadline="2026-08-17T12:08:00+00:00")
    advance_clock_past(ctx.now_fn, "2026-08-17T12:08:00+00:00")
    check_indicators(ctx)
    blocked = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert blocked["status"] == "COVERAGE_BLOCKED", \
        "silence without looking is not a finding"
    assert ctx.store.current_forecasts()[forecast["forecast_id"]]["probability"] \
        == 0.35
    gaps = [r for r in ctx.store.open_review_items()
            if r["kind"] == "COVERAGE_GAP"
            and r["subject_id"] == indicator["indicator_id"]]
    assert gaps


def test_absence_fires_only_with_coverage_achieved(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    indicator = _arm_absence(ctx, forecast, deadline="2026-08-17T12:10:00+00:00")
    advance_clock_past(ctx.now_fn, "2026-08-17T12:10:00+00:00")
    # Only a search finishing at or after the deadline counts as coverage.
    _successful_execution(ctx)
    check_indicators(ctx)
    fired = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert fired["status"] == "FIRED"
    assert "exec-abs-1" in fired["fired_evidence_refs"], \
        "the coverage executions are the evidence of the absence"


def test_absence_is_defeated_by_the_observation_arriving(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    indicator = _arm_absence(ctx, forecast, deadline="2026-08-17T12:30:00+00:00")
    _flip_to_suspended(pipeline, ctx)
    _successful_execution(ctx)
    advance_clock_past(ctx.now_fn, "2026-08-17T12:30:00+00:00")
    check_indicators(ctx)
    retired = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert retired["status"] == "RETIRED", \
        "the watched thing happened: the absence is defeated, not fired"
