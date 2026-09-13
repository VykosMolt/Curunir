"""Exploits against the forecasting plane, each now refused. Two patterns run
through them: an authorization that was checked but not scoped — a firing used
on another forecast, unrelated searches passed off as coverage, one acceptance
spent twice — and recovery paths that skipped a rule the main path enforces."""
from __future__ import annotations

import pytest

from curunir_analytic.calibration import scoreboard, standing_probability
from curunir_analytic.contracts import (ImpactEdge, IndicatorEffect,
                                        IndicatorRecord, ResolutionRule,
                                        WarningRecord)
from curunir_analytic.forecasts import (create_forecast, refresh_forecast,
                                        try_machine_resolution,
                                        update_probability,
                                        _absence_coverage_satisfied)
from curunir_analytic.indicators import arm_indicator, check_indicators
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.substrate import resolve_candidate
from curunir_analytic.warning import TIER_RULE_V1
from curunir_fabric.contracts import ExecutionRecord, ManifestationRecord
from curunir_semantic.contracts import ClaimStateRecord

from analytic_support import (GLEIF_ACME_SUSPENDED, MARK, T0, make_analytic,
                              seed_acme)
from semantic_support import advance_clock_past, plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
FAR_HORIZON = "2026-09-10T12:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _rule(expected="INACTIVE"):
    return ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))


def _forecast(ctx, by_predicate, *, question_tag="a", probability=0.35,
              expected="INACTIVE", horizon=HORIZON):
    return create_forecast(
        ctx, question=f"[{question_tag}] Will {ACME} read {expected} "
                      f"by {horizon}?",
        outcome_semantics=f"TRUE iff entity_status equals {expected!r}",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=_rule(expected),
        probability=probability, probability_basis="registry base rates",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _armed_apply_indicator(ctx, forecast, target=0.62):
    return arm_indicator(
        ctx, description=f"entity_status for {ACME} reads INACTIVE "
                         f"(for {forecast['forecast_id'][:12]})",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                               target_probability=target,
                               rationale="a registry flip mostly settles it",
                               authorized_by="jan", authorized_kind="HUMAN"))


def _flip_to_suspended(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()


def _accepted_reforecast(ctx, forecast, probability):
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {
            "question": forecast["question"], "probability": probability,
            "horizon_time": forecast["horizon_time"]})
    proposed = assist.propose(ctx, task="reforecast",
                              target_kind="analytic_forecast",
                              inputs={}, input_refs=())
    return resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                             accept=True, actor_id="jan", actor_kind="HUMAN")


# A firing authorizes its own forecasts, once


def test_fired_indicator_is_not_a_bearer_token_for_other_forecasts(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    named = _forecast(ctx, by_predicate, question_tag="named",
                      probability=0.35)
    unrelated = _forecast(ctx, by_predicate, question_tag="unrelated",
                          probability=0.10, expected="NEVER_SO",
                          horizon=FAR_HORIZON)
    indicator = _armed_apply_indicator(ctx, named)
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "FIRED"
    with pytest.raises(ValueError, match="does not name forecast"):
        update_probability(ctx, unrelated["forecast_id"], probability=0.62,
                           reason="riding someone else's authorization",
                           actor_id="svc", actor_kind="SERVICE",
                           indicator_id=indicator["indicator_id"])
    assert ctx.store.current_forecasts()[unrelated["forecast_id"]]
    assert ctx.store.current_forecasts()[
        unrelated["forecast_id"]]["probability"] == 0.10


def test_fired_indicator_is_one_shot_not_forever_redeemable(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = _armed_apply_indicator(ctx, forecast)
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.62
    before = ctx.store.head()["event_count"]
    for _ in range(5):
        with pytest.raises(ValueError, match="one\\s+act, not a standing"):
            update_probability(ctx, forecast["forecast_id"], probability=0.62,
                               reason="re-redeeming the same firing",
                               actor_id="svc", actor_kind="SERVICE",
                               indicator_id=indicator["indicator_id"])
    assert ctx.store.head()["event_count"] == before, \
        "a consumed firing appends nothing, however often it is retried"
    versions = ctx.store.analytic_versions("analytic_forecast",
                                           forecast["forecast_id"])
    assert sum(1 for v in versions if v["probability"] == 0.62) == 1


# Absence must name where it looked, and only that counts


def test_machine_resolvable_rule_must_name_its_coverage_sources():
    with pytest.raises(ValueError, match="NAME the sources"):
        ResolutionRule(kind="EVENT_OCCURRED",
                       criteria="an insolvency filing appears",
                       event_activity_type="insolvency_filing",
                       event_subject_ref=ACME)
    with pytest.raises(ValueError, match="more sources than it names"):
        ResolutionRule(kind="CLAIM_PREDICATE", criteria="c",
                       claim_subject_ref=ACME, claim_attribute="entity_status",
                       expected_value="X",
                       absence_min_successful_sources=2,
                       absence_required_source_ids=("gleif",))


def test_unrelated_searches_are_noise_not_coverage(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    seed_acme(pipeline, ctx)
    store = ctx.store
    execution = ExecutionRecord(
        execution_id="exec-unrelated", plan_id="", query_id="q-other",
        source_id="some-unrelated-newswire", connector_id="x",
        connector_version="1", operation="SEARCH",
        outcome="EXECUTED_WITH_RESULTS", result_count=3, request_url="u",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    store.append("FABRIC_EXECUTION_RECORDED", execution,
                 recorded_time=ctx.now_fn(), actor="t")
    satisfied, _, explanation = _absence_coverage_satisfied(
        store, {"absence_required_source_ids": ("gleif",),
                "absence_min_successful_sources": 1}, T0)
    assert not satisfied and "gleif" in explanation, \
        "a search of an unrelated source proves nothing about this question"
    # A rule that names no source can never be satisfied.
    satisfied, _, explanation = _absence_coverage_satisfied(
        store, {"absence_required_source_ids": (),
                "absence_min_successful_sources": 1}, T0)
    assert not satisfied and "naming where to look" in explanation


def test_absence_indicator_must_name_its_coverage_sources(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    with pytest.raises(ValueError, match="NAME the sources"):
        arm_indicator(ctx, description="no lapse appears anywhere",
                      forecast_ids=(forecast["forecast_id"],),
                      kind="ABSENCE", direction="UNDERMINES",
                      desired_subject_ref=ACME,
                      desired_attribute="entity_status",
                      effect=IndicatorEffect(mode="REVIEW_ONLY"),
                      deadline="2026-08-17T13:00:00+00:00",
                      coverage_required_source_ids=())


def test_unconstrained_presence_indicator_is_unconstructible(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    with pytest.raises(ValueError, match="unconstrained pattern"):
        arm_indicator(ctx, description="anything happens",
                      forecast_ids=(forecast["forecast_id"],),
                      kind="PRESENCE", direction="SUPPORTS",
                      effect=IndicatorEffect(mode="REVIEW_ONLY"))


# An acceptance is spent once, across every version


def test_acceptance_cannot_be_respent_after_a_later_version_frees_it(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    first = _accepted_reforecast(ctx, forecast, 0.70)
    second = _accepted_reforecast(ctx, forecast, 0.20)
    update_probability(ctx, forecast["forecast_id"], probability=0.70,
                       reason="accepted model re-forecast", actor_id="",
                       actor_kind="SERVICE", provenance_kind="MODEL",
                       inference_id=first["inference_id"],
                       proposal_id=first["proposal_id"])
    update_probability(ctx, forecast["forecast_id"], probability=0.20,
                       reason="accepted model re-forecast", actor_id="",
                       actor_kind="SERVICE", provenance_kind="MODEL",
                       inference_id=second["inference_id"],
                       proposal_id=second["proposal_id"])
    # The current version no longer names the first proposal, but the log
    # still records that it was spent.
    with pytest.raises(ValueError, match="already[\\s\\S]*materialized"):
        update_probability(ctx, forecast["forecast_id"], probability=0.70,
                           reason="re-spending the freed acceptance",
                           actor_id="", actor_kind="SERVICE",
                           provenance_kind="MODEL",
                           inference_id=first["inference_id"],
                           proposal_id=first["proposal_id"])


def test_analyst_version_does_not_wear_a_model_trail(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    accepted = _accepted_reforecast(ctx, forecast, 0.70)
    update_probability(ctx, forecast["forecast_id"], probability=0.70,
                       reason="accepted model re-forecast", actor_id="",
                       actor_kind="SERVICE", provenance_kind="MODEL",
                       inference_id=accepted["inference_id"],
                       proposal_id=accepted["proposal_id"])
    human = update_probability(ctx, forecast["forecast_id"], probability=0.55,
                               reason="I disagree with the model",
                               actor_id="jan", actor_kind="HUMAN")
    assert human["provenance_kind"] == "ANALYST"
    assert human["inference_id"] == "" and human["proposal_id"] == "", \
        "a record's stated provenance matches its identifiers"
    model_version = next(v for v in ctx.store.analytic_versions(
        "analytic_forecast", forecast["forecast_id"])
        if v["provenance_kind"] == "MODEL")
    assert model_version["inference_id"], "the model version keeps its trail"


# A settled forecast is history


def test_recreation_folds_nothing_into_a_settled_forecast(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    _flip_to_suspended(pipeline, ctx)
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"
    versions_before = len(ctx.store.analytic_versions(
        "analytic_forecast", forecast["forecast_id"]))
    again = create_forecast(
        ctx, question=forecast["question"],
        outcome_semantics=forecast["outcome_semantics"],
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=forecast["horizon_time"], resolution=_rule(),
        probability=0.35, probability_basis="registry base rates",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"],
                              by_predicate["legal_name"]])
    assert again["status"] == "RESOLVED_TRUE"
    assert len(ctx.store.analytic_versions(
        "analytic_forecast", forecast["forecast_id"])) == versions_before, \
        "the evidentiary basis of a settled judgment is never rewritten"


# An old page arriving late is not the event happening


def test_presence_does_not_fire_on_archival_backfill(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30)
    indicator = _armed_apply_indicator(ctx, forecast)
    # An archived capture of the older world, ingested after arming.
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED,
                        media_type="application/json",
                        retrieval_time=ctx.now_fn(),
                        temporal_status="HISTORICAL",
                        archive_capture_time="2019-01-01T00:00:00+00:00")
    pipeline.process_new_evidence()
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "ARMED", "history arriving late is not the watched event occurring"
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.30


# A machine act never clears a flag raised for a person


def test_indicator_firing_preserves_update_required(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = _armed_apply_indicator(ctx, forecast)
    state = ClaimStateRecord(
        state_id="st-deg", claim_id=by_predicate["entity_status"],
        state="DISPUTED", reason="independent disagreement", caused_by="c",
        superseded_by="", actor_id="t", actor_kind="SERVICE",
        recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    flagged = refresh_forecast(ctx, forecast["forecast_id"], caused_by="deg")
    assert flagged["status"] == "UPDATE_REQUIRED"
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    moved = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert moved["probability"] == 0.62, "the pre-authorized number applies"
    assert moved["status"] == "UPDATE_REQUIRED", \
        "the flag was raised for a human; only a human clears it"


# Coverage arriving closes the gap on the next ordinary pass


def test_coverage_arriving_resolves_the_blocked_forecast_via_propagation(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.20,
                         expected="NEVER_SO",
                         horizon="2026-08-17T12:04:00+00:00")
    blocked = refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    assert blocked["status"] == "HORIZON_PASSED"
    execution = ExecutionRecord(
        execution_id="exec-cov", plan_id="", query_id="q1", source_id="gleif",
        connector_id="gleif-lei-v1", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")
    propagate_semantic_changes(ctx)
    resolved = ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert resolved["status"] == "RESOLVED_FALSE", \
        "propagation notices arrived coverage without an explicit poke"
    gaps = [r for r in ctx.store.open_review_items()
            if r["kind"] == "COVERAGE_GAP"
            and r["subject_id"] == forecast["forecast_id"]]
    assert not gaps, "a settled question's coverage gap does not outlive it"
    before = ctx.store.head()["event_count"]
    propagate_semantic_changes(ctx)
    assert ctx.store.head()["event_count"] == before


# An interrupted firing finishes on the next pass


def test_interrupted_firing_completes_on_the_next_pass(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30)
    indicator = _armed_apply_indicator(ctx, forecast)
    _flip_to_suspended(pipeline, ctx)

    real_append = ctx.store.append
    state = {"crashed": False}

    def failing_append(event_type, record, **kwargs):
        data = record.to_record() if hasattr(record, "to_record") else record
        if event_type == "FORECAST_INDICATOR_RECORDED" \
                and data.get("status") == "FIRED" and not state["crashed"]:
            real_append(event_type, record, **kwargs)
            state["crashed"] = True
            raise OSError("simulated crash right after the FIRED version")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = failing_append
    with pytest.raises(OSError):
        check_indicators(ctx)
    ctx.store.append = real_append
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "FIRED"
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.30, "effect not yet run"
    check_indicators(ctx)
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.62, \
        "the recovery pass completes the human's pre-authorized judgment"
    before = ctx.store.head()["event_count"]
    check_indicators(ctx)
    check_indicators(ctx)
    assert ctx.store.head()["event_count"] == before


def test_completed_firing_never_restomps_a_later_human_move(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30)
    _armed_apply_indicator(ctx, forecast)
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    update_probability(ctx, forecast["forecast_id"], probability=0.45,
                       reason="the flip is ambiguous on inspection",
                       actor_id="jan", actor_kind="HUMAN")
    check_indicators(ctx)
    check_indicators(ctx)
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.45, \
        "a recovery pass never re-stomps a human's later judgment"


# Scoring reads version order, not timestamps


def test_hindsight_check_uses_versions_not_timestamps():
    same = "2026-08-17T12:00:00+00:00"
    versions = [
        {"forecast_id": "f1", "version": 1, "probability": 0.30,
         "status": "OPEN", "recorded_time": same},
        {"forecast_id": "f1", "version": 2, "probability": 0.95,
         "status": "RESOLVED_TRUE", "recorded_time": same},
    ]
    with pytest.raises(ValueError, match="hindsight"):
        standing_probability(versions)


# A tier the named rule does not yield cannot be built


def test_tier_must_be_what_the_named_rule_yields():
    base = dict(warning_id="w1", version=1, mission_context="m1",
                objective_id="o1", forecast_id="f1", impact_path_ids=(),
                probability_band="REMOTE", consequence="LOW",
                time_pressure="DISTANT", evidence_confidence="STRONG",
                tier_rule_id=TIER_RULE_V1,
                component_basis=(("probability_band", "b"),),
                status="ACTIVE", change_reason="", history=(),
                recorded_time=T0, marking=MARK)
    with pytest.raises(ValueError, match="the rule produces ROUTINE"):
        WarningRecord(**{**base, "tier": "CRITICAL"})
    with pytest.raises(ValueError, match="unknown warning tier rule"):
        WarningRecord(**{**base, "tier": "ROUTINE",
                          "tier_rule_id": "my-own-generous-rule"})
    assert WarningRecord(**{**base, "tier": "ROUTINE"}).tier == "ROUTINE"


# A blocked absence indicator fires when coverage finally arrives


def test_blocked_absence_indicator_fires_once_coverage_arrives(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         horizon=FAR_HORIZON)
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline="2026-08-17T12:08:00+00:00",
        coverage_min_successful_sources=1,
        coverage_required_source_ids=("gleif",))
    advance_clock_past(ctx.now_fn, "2026-08-17T12:08:00+00:00")
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "COVERAGE_BLOCKED"
    execution = ExecutionRecord(
        execution_id="exec-late", plan_id="", query_id="q1", source_id="gleif",
        connector_id="gleif-lei-v1", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")
    check_indicators(ctx)
    fired = ctx.store.current_indicators()[indicator["indicator_id"]]
    assert fired["status"] == "FIRED", \
        "late coverage still answers the absence question, honestly late"
    gaps = [r for r in ctx.store.open_review_items()
            if r["subject_id"] == indicator["indicator_id"]
            and r["kind"] == "COVERAGE_GAP"]
    assert not gaps, "the gap closes when the question is answered"
