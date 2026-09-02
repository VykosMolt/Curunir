"""More forecasting exploits, now refused: a firing is spent whatever the number
now reads, coverage must be about this subject, "by the horizon" is part of the
question, and every interrupted write completes rather than repeating."""
from __future__ import annotations

import pytest

from curunir_analytic.calibration import (post_horizon_update_count,
                                          scored_forecasts,
                                          standing_probability)
from curunir_analytic.contracts import IndicatorEffect, ResolutionRule
from curunir_analytic.forecasts import (create_forecast, refresh_forecast,
                                        try_machine_resolution,
                                        update_probability,
                                        _absence_coverage_satisfied)
from curunir_analytic.indicators import arm_indicator, check_indicators
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.substrate import resolve_candidate
from curunir_fabric.contracts import ExecutionRecord

from analytic_support import (GLEIF_ACME_SUSPENDED, MARK, T0, make_analytic,
                              seed_acme)
from semantic_support import advance_clock_past, plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"
ACME_URL = "https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001"


def _rule(expected="INACTIVE"):
    return ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))


def _forecast(ctx, by_predicate, *, probability=0.35, expected="INACTIVE",
              horizon=HORIZON, tag="x"):
    return create_forecast(
        ctx, question=f"[{tag}] Will {ACME} read {expected} by {horizon}?",
        outcome_semantics=f"TRUE iff entity_status equals {expected!r} at or "
                          f"before the horizon",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=_rule(expected),
        probability=probability, probability_basis="registry base rates",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _execution(ctx, *, execution_id, request_url=ACME_URL, source_id="gleif"):
    record = ExecutionRecord(
        execution_id=execution_id, plan_id="", query_id=f"q-{execution_id}",
        source_id=source_id, connector_id="c", connector_version="1",
        operation="LOOKUP", outcome="EXECUTED_EMPTY", result_count=0,
        request_url=request_url, http_status=200, policy_decision="ALLOW",
        error_class=None, error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", record,
                     recorded_time=ctx.now_fn(), actor="t")


def _flip_to_suspended(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()


# ---- a firing is spent, whatever the number now reads ----------------------


def test_stale_firing_cannot_overwrite_later_human_judgment(tmp_path):
    """Moving the number away from the target does not make the firing
    redeemable again."""
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = arm_indicator(
        ctx, description=f"entity_status for {ACME} reads INACTIVE",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                               target_probability=0.62,
                               rationale="a flip mostly settles it",
                               authorized_by="jan", authorized_kind="HUMAN"))
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    update_probability(ctx, forecast["forecast_id"], probability=0.40,
                       reason="the flip is ambiguous on closer reading",
                       actor_id="jan", actor_kind="HUMAN")
    for _ in range(3):
        with pytest.raises(ValueError, match="one act, not a standing power"):
            update_probability(ctx, forecast["forecast_id"], probability=0.62,
                               reason="re-redeeming", actor_id="svc",
                               actor_kind="SERVICE",
                               indicator_id=indicator["indicator_id"])
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.40, \
        "the analyst's later judgment stands"


# ---- coverage must be about this question ----------------------------------


def test_declared_source_searched_about_someone_else_is_not_coverage(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.20,
                         expected="NEVER_SO",
                         horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    # A real search of the right registry, about a different company.
    _execution(ctx, execution_id="exec-other-lei",
               request_url="https://api.gleif.org/api/v1/lei-records/"
                           "SOMEOTHERLEI0000000001")
    still = try_machine_resolution(ctx, forecast["forecast_id"])
    assert still["status"] == "HORIZON_PASSED", \
        "a search of the right site about the wrong entity proves nothing here"
    # The same search about this subject does settle it.
    _execution(ctx, execution_id="exec-this-lei")
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] == "RESOLVED_FALSE"
    assert "exec-this-lei" in resolved["resolution_evidence_refs"]


def test_unattributable_executions_fail_safe(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    seed_acme(pipeline, ctx)
    _execution(ctx, execution_id="exec-anon", request_url="")
    satisfied, _, _ = _absence_coverage_satisfied(
        ctx.store, {"absence_required_source_ids": ("gleif",),
                    "absence_min_successful_sources": 1}, T0,
        subject_refs=(ACME,))
    assert not satisfied, \
        "an execution attributable to no subject does not count for one"


# ---- an indicator that moves a number must name its subject ----------------


def test_attribute_only_pattern_cannot_execute_a_number_move(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)
    with pytest.raises(ValueError, match="must name its subject"):
        arm_indicator(
            ctx, description="any entity_status reads INACTIVE",
            forecast_ids=(forecast["forecast_id"],),
            kind="PRESENCE", direction="SUPPORTS",
            desired_observation_type="ENTITY_ATTRIBUTE",
            desired_attribute="entity_status", expected_value="INACTIVE",
            effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                                   target_probability=0.62,
                                   rationale="r", authorized_by="jan",
                                   authorized_kind="HUMAN"))
    # Without a subject it may still ask for review; it just cannot act.
    armed = arm_indicator(
        ctx, description="any entity_status reads INACTIVE (review)",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_attribute="entity_status", expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"))
    assert armed["status"] == "ARMED"


# ---- a change after the horizon is not an outcome by the horizon -----------


def test_claim_flipping_after_the_horizon_does_not_resolve_true(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30,
                         expected="INACTIVE",
                         horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    # The registry flips, but only after the horizon.
    _flip_to_suspended(pipeline, ctx)
    outcome = try_machine_resolution(ctx, forecast["forecast_id"])
    assert outcome["status"] == "HORIZON_PASSED", \
        "'by the horizon' is part of the question: a late flip is not TRUE"
    items = [r for r in ctx.store.open_review_items()
             if r["kind"] == "EXPECTED_NOT_OBSERVED"
             and r["subject_id"] == forecast["forecast_id"]]
    assert items, "the ambiguity is routed to a human, visibly"
    # Propagation must not settle it quietly either.
    propagate_semantic_changes(ctx)
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["status"] == "HORIZON_PASSED"
    assert not scored_forecasts(ctx.store), "nothing corrupted the scoreboard"


# ---- a move made after the horizon is not the probability that stood -------


def test_post_horizon_update_is_not_scored_as_standing():
    same_day = "2026-08-17T{}:00:00+00:00"
    versions = [
        {"forecast_id": "f1", "version": 1, "probability": 0.03,
         "status": "OPEN", "recorded_time": same_day.format("10")},
        {"forecast_id": "f1", "version": 2, "probability": 0.97,
         "status": "HORIZON_PASSED", "recorded_time": same_day.format("13")},
        {"forecast_id": "f1", "version": 3, "probability": 0.97,
         "status": "RESOLVED_TRUE", "recorded_time": same_day.format("14")},
    ]
    horizon = same_day.format("12")
    assert standing_probability(versions, horizon) == 0.03, \
        "a number moved with the outcome in sight scores nothing"
    assert post_horizon_update_count(versions, horizon) == 1, \
        "and the chase is surfaced, not hidden"


# ---- an absence indicator sees the whole window ----------------------------


def test_pre_deadline_search_cannot_cover_the_window_it_did_not_see(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, horizon="2026-09-10T12:00:00+00:00")
    deadline = "2026-08-17T12:20:00+00:00"
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline=deadline, coverage_required_source_ids=("gleif",))
    _execution(ctx, execution_id="exec-early")  # hours before the deadline
    advance_clock_past(ctx.now_fn, deadline)
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "COVERAGE_BLOCKED", \
        "a search before the deadline says nothing about the window after it"
    _execution(ctx, execution_id="exec-at-deadline")
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "FIRED"


def test_absence_is_defeated_by_state_that_already_held_at_arming(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    # The watched state already holds before the indicator is armed.
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=T0)
    pipeline.process_new_evidence()
    by_predicate = {c["predicate"]: c["claim_id"]
                    for c in ctx.store.current_claims().values()
                    if c["subject_ref"] == ACME}
    forecast = _forecast(ctx, by_predicate, horizon="2026-09-10T12:00:00+00:00")
    deadline = "2026-08-17T12:15:00+00:00"
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline=deadline, coverage_required_source_ids=("gleif",))
    advance_clock_past(ctx.now_fn, deadline)
    _execution(ctx, execution_id="exec-cov")
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "RETIRED", \
        "'we never saw it' cannot be asserted about a state that held all along"


# ---- interrupted writes complete, and status stays honest ------------------


def test_crash_recovered_firing_still_closes_the_coverage_gap(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, horizon="2026-09-10T12:00:00+00:00")
    deadline = "2026-08-17T12:15:00+00:00"
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE status appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline=deadline, coverage_required_source_ids=("gleif",))
    advance_clock_past(ctx.now_fn, deadline)
    check_indicators(ctx)  # blocks: gap opens
    assert any(r["subject_id"] == indicator["indicator_id"]
               for r in ctx.store.open_review_items())
    _execution(ctx, execution_id="exec-cov")

    real_append = ctx.store.append
    state = {"crashed": False}

    def failing_append(event_type, record, **kwargs):
        data = record.to_record() if hasattr(record, "to_record") else record
        if event_type == "FORECAST_INDICATOR_RECORDED" \
                and data.get("status") == "FIRED" and not state["crashed"]:
            real_append(event_type, record, **kwargs)
            state["crashed"] = True
            raise OSError("crash right after the FIRED version")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = failing_append
    with pytest.raises(OSError):
        check_indicators(ctx)
    ctx.store.append = real_append
    check_indicators(ctx)  # recovery completes effect AND gap closure
    assert not [r for r in ctx.store.open_review_items()
                if r["subject_id"] == indicator["indicator_id"]], \
        "the recovery path closes the gap the normal path closes"
    before = ctx.store.head()["event_count"]
    check_indicators(ctx)
    assert ctx.store.head()["event_count"] == before


def test_model_update_crash_completes_instead_of_refusing(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {
            "question": forecast["question"], "probability": 0.7,
            "horizon_time": forecast["horizon_time"]})
    proposed = assist.propose(ctx, task="reforecast",
                              target_kind="analytic_forecast",
                              inputs={}, input_refs=())
    accepted = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan",
                                 actor_kind="HUMAN")

    real_append = ctx.store.append

    def failing_append(event_type, record, **kwargs):
        if event_type == "ANALYTIC_TRANSITION_RECORDED":
            raise OSError("crash between the version and its transition")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = failing_append
    with pytest.raises(OSError):
        update_probability(ctx, forecast["forecast_id"], probability=0.7,
                           reason="accepted model re-forecast", actor_id="",
                           actor_kind="SERVICE", provenance_kind="MODEL",
                           inference_id=accepted["inference_id"],
                           proposal_id=accepted["proposal_id"])
    ctx.store.append = real_append
    # The version landed without its transition; the retry finishes it.
    completed = update_probability(
        ctx, forecast["forecast_id"], probability=0.7,
        reason="accepted model re-forecast", actor_id="",
        actor_kind="SERVICE", provenance_kind="MODEL",
        inference_id=accepted["inference_id"],
        proposal_id=accepted["proposal_id"])
    assert completed["probability"] == 0.7
    updated_transitions = [
        t for t in ctx.store.transitions_for(forecast["forecast_id"])
        if t["transition_type"] == "PROBABILITY_UPDATED"]
    assert len(updated_transitions) == 1, "completed, not duplicated"
    assert len(ctx.store.analytic_versions(
        "analytic_forecast", forecast["forecast_id"])) == 2


def test_update_does_not_unpass_a_passed_horizon(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.20,
                         expected="NEVER_SO",
                         horizon="2026-08-17T12:04:00+00:00")
    passed = refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    assert passed["status"] == "HORIZON_PASSED"
    updated = update_probability(ctx, forecast["forecast_id"], probability=0.10,
                                 reason="late reflection before resolution",
                                 actor_id="jan", actor_kind="HUMAN")
    assert updated["status"] == "HORIZON_PASSED", \
        "the horizon is a fact about the clock, not a review flag"


# ---- an indicator on a settled question expires ----------------------------


def test_indicator_on_settled_questions_expires_and_stops_driving_collection(
        tmp_path):
    from curunir_analytic.collect import analytic_collection_needs
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    indicator = arm_indicator(
        ctx, description=f"no INACTIVE appears for {ACME}",
        forecast_ids=(forecast["forecast_id"],),
        kind="ABSENCE", direction="UNDERMINES",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME, desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
        deadline="2026-09-01T12:00:00+00:00",
        coverage_required_source_ids=("gleif",))
    _flip_to_suspended(pipeline, ctx)
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"
    check_indicators(ctx)
    assert ctx.store.current_indicators()[indicator["indicator_id"]]["status"] \
        == "EXPIRED_UNFIRED", "a dead question's watcher expires"
    assert not [n for n in analytic_collection_needs(ctx.store)
                if n["source_id"] == indicator["indicator_id"]], \
        "and no longer drives collection"
    before = ctx.store.head()["event_count"]
    check_indicators(ctx)
    assert ctx.store.head()["event_count"] == before
