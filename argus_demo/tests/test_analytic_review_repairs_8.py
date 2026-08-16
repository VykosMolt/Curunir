"""Round-4 forecasting-review exploit locks: the hindsight clock is the
evidence record, not just the horizon; an open human adjudication blocks
EVERY machine verdict; a pre-authorization is never silently superseded."""
from __future__ import annotations

import pytest

from curunir_analytic.calibration import scoreboard
from curunir_analytic.contracts import IndicatorEffect, ResolutionRule
from curunir_analytic.forecasts import (create_forecast, refresh_forecast,
                                        try_machine_resolution,
                                        _execution_touches_subject,
                                        _subject_match_values)
from curunir_analytic.indicators import arm_indicator

from analytic_support import (GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0,
                              make_analytic)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


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
        outcome_semantics=f"TRUE iff entity_status equals {expected!r}",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=_rule(expected),
        probability=probability, probability_basis="registry base rates",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])


def _flip(pipeline, ctx, body=GLEIF_ACME_SUSPENDED):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=body, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()


# ---- R4-1: the answer-in-hand clock is the evidence record -----------------


def test_forecast_resolved_by_prior_evidence_feeds_no_aggregate(tmp_path):
    """The round-4 MAJOR: authored two minutes after the claim that resolves
    it, resolved off that same claim — brier ~0, and previously aggregated
    as calibration because the horizon lay comfortably ahead."""
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    posed = _forecast(ctx, by_predicate, probability=0.97, expected="ACTIVE",
                      tag="already-answered")
    resolved = try_machine_resolution(ctx, posed["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == posed["forecast_id"])
    assert row["resolved_by_prior_evidence"] is True, \
        "the resolving claim was on the record before the number was written"
    assert row["authored_after_horizon"] is False, \
        "the horizon flag alone would have missed this shape"
    assert board["overall"]["count"] == 0
    assert board["coverage"]["resolved_by_prior_evidence"] == 1
    assert board["coverage"]["scored"] == 0


def test_genuinely_forward_resolution_still_scores(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35, tag="forward")
    _flip(pipeline, ctx)  # NEW evidence, recorded after authoring
    assert try_machine_resolution(
        ctx, forecast["forecast_id"])["status"] == "RESOLVED_TRUE"
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == forecast["forecast_id"])
    assert row["resolved_by_prior_evidence"] is False
    assert board["overall"]["count"] == 1, \
        "a question settled by evidence that arrived later is real calibration"


# ---- R4-2: an open human adjudication blocks EVERY machine verdict ---------


def test_machine_false_cannot_settle_over_an_open_latematch_item(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30,
                         horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    _flip(pipeline, ctx)  # expected value appears — post-horizon evidence
    try_machine_resolution(ctx, forecast["forecast_id"])
    assert [r for r in ctx.store.open_review_items()
            if r["kind"] == "EXPECTED_NOT_OBSERVED"
            and r["subject_id"] == forecast["forecast_id"]], "human question open"
    _flip(pipeline, ctx, body=GLEIF_ACME)  # the claim flips BACK
    still = try_machine_resolution(ctx, forecast["forecast_id"])
    assert still["status"] == "HORIZON_PASSED", \
        "the machine may not assert what the claim read at a horizon it " \
        "never saw, in either direction, while its own record says a human " \
        "must decide"
    assert [r for r in ctx.store.open_review_items()
            if r["subject_id"] == forecast["forecast_id"]], \
        "and the human question survives"


# ---- R4-5: a pre-authorization is never silently superseded ----------------


def test_rearming_with_a_different_effect_is_refused(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)

    def _arm(target):
        return arm_indicator(
            ctx, description=f"entity_status for {ACME} reads INACTIVE",
            forecast_ids=(forecast["forecast_id"],),
            kind="PRESENCE", direction="SUPPORTS",
            desired_observation_type="ENTITY_ATTRIBUTE",
            desired_subject_ref=ACME, desired_attribute="entity_status",
            expected_value="INACTIVE",
            effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                                   target_probability=target,
                                   rationale="a flip mostly settles it",
                                   authorized_by="jan",
                                   authorized_kind="HUMAN"))

    armed = _arm(0.62)
    with pytest.raises(ValueError, match="never a silent side effect"):
        _arm(0.90)
    assert ctx.store.current_indicators()[
        armed["indicator_id"]]["effect"]["target_probability"] == 0.62
    # identical re-arm (crash-recovery) still completes quietly
    again = _arm(0.62)
    assert again["indicator_id"] == armed["indicator_id"]


# ---- R4-4: delimiters are Unicode-aware ------------------------------------


def test_url_binding_treats_non_ascii_letters_as_token_characters():
    values = _subject_match_values(("ORG:oslobank",))
    foreign = {"query_id": "q",
               "request_url": "https://registry.example/q/oslobankø"}
    assert not _execution_touches_subject(foreign, values, {}), \
        "'oslobank' inside 'oslobankø' is a different name"
    exact = {"query_id": "q",
             "request_url": "https://registry.example/q/oslobank"}
    assert _execution_touches_subject(exact, values, {})


# ---- R4-3: the diagnostic separates 'never looked' from 'could not
#            attribute' -----------------------------------------------------


def test_coverage_diagnostic_names_the_real_gap(tmp_path):
    from curunir_analytic.forecasts import _absence_coverage_satisfied
    from curunir_fabric.contracts import ExecutionRecord
    pipeline, ctx = make_analytic(tmp_path)
    _seed(pipeline, ctx)
    execution = ExecutionRecord(
        execution_id="exec-someone-else", plan_id="", query_id="q-z",
        source_id="gleif", connector_id="c", connector_version="1",
        operation="LOOKUP", outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/OTHERLEI000000000009",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")
    satisfied, _, explanation = _absence_coverage_satisfied(
        ctx.store, {"absence_required_source_ids": ("gleif", "wikidata"),
                    "absence_min_successful_sources": 1}, T0,
        subject_refs=(ACME,))
    assert not satisfied
    assert "no execution attributable" in explanation, \
        "'looked, could not attribute' is stated as such"
    assert "wikidata never successfully searched" in explanation, \
        "'never looked' is stated as such"


# ---- R5 minors: the flag's coverage, its over-reach, the field set ---------


def test_citing_the_prior_observation_does_not_evade_the_flag(tmp_path):
    from curunir_analytic.forecasts import resolve_forecast_human
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    posed = _forecast(ctx, by_predicate, probability=0.97, expected="ACTIVE",
                      tag="obs-cited")
    observation_id = next(
        o["observation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["subject_ref"] == ACME and o["attribute"] == "entity_status")
    resolve_forecast_human(ctx, posed["forecast_id"], outcome="TRUE",
                           evidence_refs=(observation_id,),
                           note="the standing observation settles it",
                           actor_id="jan", actor_kind="HUMAN")
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == posed["forecast_id"])
    assert row["resolved_by_prior_evidence"] is True, \
        "an observation id is ordinary evidence currency, not an escape hatch"
    assert board["overall"]["count"] == 0


def test_same_value_reversion_after_authoring_does_not_unflag(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    posed = _forecast(ctx, by_predicate, probability=0.97, expected="ACTIVE",
                      tag="reversion")
    # the SAME value re-enters from a fresh retrieval after authoring
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    resolved = try_machine_resolution(ctx, posed["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == posed["forecast_id"])
    assert row["resolved_by_prior_evidence"] is True, \
        "a corroborating re-version is not a new answer: the answer entered " \
        "the record before the number was written"
    assert board["overall"]["count"] == 0


def test_honest_negative_forecasts_are_not_excluded(tmp_path):
    from curunir_analytic.forecasts import resolve_forecast_human
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    negative = _forecast(ctx, by_predicate, probability=0.03,
                         horizon="2026-08-17T12:30:00+00:00", tag="negative")
    while ctx.now_fn() <= "2026-08-17T12:30:00+00:00":
        pass
    resolve_forecast_human(ctx, negative["forecast_id"], outcome="FALSE",
                           evidence_refs=(by_predicate["entity_status"],),
                           note="nothing happened; the standing claim shows it",
                           actor_id="jan", actor_kind="HUMAN")
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == negative["forecast_id"])
    assert row["resolved_by_prior_evidence"] is False, \
        "a FALSE outcome cannot be pre-determined by prior evidence: " \
        "excluding it would drop every honest negative forecast"
    assert board["overall"]["count"] == 1


def test_rearm_cannot_silently_retarget_the_watch(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate)

    def _arm(attribute):
        return arm_indicator(
            ctx, description=f"registry standing changes for {ACME}",
            forecast_ids=(forecast["forecast_id"],),
            kind="PRESENCE", direction="SUPPORTS",
            desired_observation_type="ENTITY_ATTRIBUTE",
            desired_subject_ref=ACME, desired_attribute=attribute,
            expected_value="",
            effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                                   target_probability=0.62,
                                   rationale="a change mostly settles it",
                                   authorized_by="jan",
                                   authorized_kind="HUMAN"))

    armed = _arm("entity_status")
    with pytest.raises(ValueError, match="never a silent side effect"):
        _arm("registration_status")
    assert ctx.store.current_indicators()[
        armed["indicator_id"]]["desired_attribute"] == "entity_status", \
        "the watch fires on what the human aimed it at, nothing else"


# ---- R6 closure: the flag is a fact about the resolution, forever ----------


def test_flag_does_not_drift_when_the_claim_later_moves_on(tmp_path):
    """A replayed calibration surface must not change because the world
    changed AFTER the question settled — and 'wait for the claim to move'
    must not be an un-flagging strategy."""
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    posed = _forecast(ctx, by_predicate, probability=0.97, expected="ACTIVE",
                      tag="drift")
    assert try_machine_resolution(
        ctx, posed["forecast_id"])["status"] == "RESOLVED_TRUE"
    board = scoreboard(ctx.store)
    assert next(r for r in board["rows"]
                if r["forecast_id"] == posed["forecast_id"])
    assert board["overall"]["count"] == 0
    # the claim flips long after the resolution
    _flip(pipeline, ctx)
    board_after = scoreboard(ctx.store)
    row = next(r for r in board_after["rows"]
               if r["forecast_id"] == posed["forecast_id"])
    assert row["resolved_by_prior_evidence"] is True, \
        "the flag is anchored at the resolution instant, not at the log head"
    assert board_after["overall"]["count"] == 0


def test_pre_horizon_human_false_on_prior_evidence_is_flagged(tmp_path):
    """A FALSE called BEFORE the horizon closed nothing: citing evidence
    already on the record at authoring is the answer in hand — the cheapest
    way to farm the reputation surface, now refused."""
    from curunir_analytic.forecasts import resolve_forecast_human
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    farmed = _forecast(ctx, by_predicate, probability=0.02,
                       horizon="2027-01-01T00:00:00+00:00", tag="farm")
    resolve_forecast_human(ctx, farmed["forecast_id"], outcome="FALSE",
                           evidence_refs=(by_predicate["entity_status"],),
                           note="obviously not happening",
                           actor_id="jan", actor_kind="HUMAN")
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == farmed["forecast_id"])
    assert row["resolved_by_prior_evidence"] is True
    assert board["overall"]["count"] == 0
    # while the honest post-horizon negative (locked above in
    # test_honest_negative_forecasts_are_not_excluded) still scores
