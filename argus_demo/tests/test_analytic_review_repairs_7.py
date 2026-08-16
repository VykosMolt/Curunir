"""Round-3 forecasting-review exploit locks: the consumption marker binds to
the act that earned it, post-hoc forecasts cannot pose as calibration, the
late-match item dies with its forecast, and short subject values cannot
attribute the world's searches to one question."""
from __future__ import annotations

import pytest

from curunir_analytic.calibration import scoreboard
from curunir_analytic.contracts import IndicatorEffect, ResolutionRule
from curunir_analytic.forecasts import (create_forecast, refresh_forecast,
                                        resolve_forecast_human,
                                        try_machine_resolution,
                                        update_probability,
                                        _execution_touches_subject,
                                        _subject_match_values)
from curunir_analytic.indicators import arm_indicator, check_indicators

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


def _flip_to_suspended(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()


# ---- R3-1: the marker binds to the act, not to a parameter -----------------


def test_human_update_cannot_carry_an_indicator_id(tmp_path):
    """The round-3 MAJOR: a human update carrying an indicator id burned the
    consumption marker without a single indicator check, then the genuine
    firing silently never executed. The confused call is now refused."""
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
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
    with pytest.raises(ValueError, match="stands on its own authority"):
        update_probability(ctx, forecast["forecast_id"], probability=0.44,
                           reason="my own reading", actor_id="jan",
                           actor_kind="HUMAN",
                           indicator_id=indicator["indicator_id"])
    # no marker was burned: the genuine firing still executes
    _flip_to_suspended(pipeline, ctx)
    check_indicators(ctx)
    assert ctx.store.current_forecasts()[
        forecast["forecast_id"]]["probability"] == 0.62, \
        "the analyst's pre-authorized judgment executes when its trigger fires"


# ---- R3-2: a post-hoc 'forecast' is not calibration ------------------------


def test_forecast_authored_after_its_horizon_feeds_no_aggregate(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    # authored at ~12:0x against a horizon already in the past — but one the
    # seeded evidence (12:00:00) still predates, so machine TRUE is legal
    post_hoc = _forecast(ctx, by_predicate, probability=0.97,
                         expected="ACTIVE", tag="posthoc",
                         horizon="2026-08-17T12:00:30+00:00")
    resolved = try_machine_resolution(ctx, post_hoc["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"  # evidence predates horizon
    board = scoreboard(ctx.store)
    row = next(r for r in board["rows"]
               if r["forecast_id"] == post_hoc["forecast_id"])
    assert row["authored_after_horizon"] is True, \
        "the row says what it is: a statement about a visible outcome"
    assert board["overall"]["count"] == 0, \
        "it feeds no mean, no bucket, no author's reputation"
    assert board["coverage"]["authored_after_horizon"] == 1
    assert board["coverage"]["scored"] == 0


# ---- R3-3: the late-match item dies with its forecast ----------------------


def test_latematch_item_closes_when_the_forecast_settles(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.30,
                         horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    _flip_to_suspended(pipeline, ctx)
    try_machine_resolution(ctx, forecast["forecast_id"])
    assert [r for r in ctx.store.open_review_items()
            if r["kind"] == "EXPECTED_NOT_OBSERVED"
            and r["subject_id"] == forecast["forecast_id"]], "item open"
    resolve_forecast_human(ctx, forecast["forecast_id"], outcome="TRUE",
                           evidence_refs=(by_predicate["entity_status"],),
                           note="the registry did flip in time per the "
                                "source's own dating",
                           actor_id="jan", actor_kind="HUMAN")
    assert not [r for r in ctx.store.open_review_items()
                if r["subject_id"] == forecast["forecast_id"]], \
        "a settled question leaves nothing in the queue"


# ---- R3-4: short subject values cannot bind through URLs -------------------


def test_short_subject_values_do_not_match_urls():
    values = _subject_match_values(("REF:v1",))
    execution = {"query_id": "q-x",
                 "request_url": "https://api.gleif.org/v1/lei-records/ZZZ"}
    assert not _execution_touches_subject(execution, values, {}), \
        "'v1' in every versioned API path attributes nothing"
    # a real-length identifier still binds — and only when delimited
    long_values = _subject_match_values(("LEI:ACMELEI000000000001",))
    hit = {"query_id": "q-y",
           "request_url": "https://api.gleif.org/api/v1/lei-records/"
                          "ACMELEI000000000001"}
    near_miss = {"query_id": "q-z",
                 "request_url": "https://api.gleif.org/api/v1/lei-records/"
                                "XACMELEI000000000001X"}
    assert _execution_touches_subject(hit, long_values, {})
    assert not _execution_touches_subject(near_miss, long_values, {}), \
        "an identifier embedded in a longer token is a different identifier"
    # short values can still bind through the exact plan-query join
    assert _execution_touches_subject(
        {"query_id": "q-x", "request_url": ""}, values, {"q-x": "v1"})
