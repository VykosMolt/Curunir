"""Calibration: a forecast is scored on the probability that stood when it
resolved, hindsight is refused, coverage is reported, and scoring never writes."""
from __future__ import annotations

import math

import pytest

from curunir_analytic.calibration import (brier, calibration_buckets,
                                          expected_calibration_error, log_score,
                                          scoreboard, scored_forecasts,
                                          standing_probability)
from curunir_analytic.contracts import ResolutionRule
from curunir_analytic.forecasts import (create_forecast, resolve_forecast_human,
                                        try_machine_resolution,
                                        update_probability, withdraw_forecast)

from analytic_support import GLEIF_ACME_SUSPENDED, make_analytic, seed_acme
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _forecast(ctx, by_predicate, *, question, probability, author="jan",
              domain="corporate-registry", expected="INACTIVE"):
    rule = ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))
    return create_forecast(
        ctx, question=question,
        outcome_semantics=f"TRUE iff entity_status equals {expected!r}",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=HORIZON, resolution=rule, probability=probability,
        probability_basis="registry base rates", author=author, domain=domain,
        supporting_claim_ids=[by_predicate["entity_status"]])


# Pure math


def test_proper_scores_are_the_textbook_functions():
    assert brier(0.7, True) == pytest.approx(0.09)
    assert brier(0.7, False) == pytest.approx(0.49)
    assert log_score(0.7, True) == pytest.approx(-math.log(0.7))
    assert log_score(0.7, False) == pytest.approx(-math.log(0.3))


def test_reliability_table_reports_empty_buckets():
    rows = [{"probability": 0.62, "outcome": "TRUE"},
            {"probability": 0.65, "outcome": "FALSE"},
            {"probability": 0.05, "outcome": "FALSE"}]
    buckets = calibration_buckets(rows)
    assert len(buckets) == 10
    sixties = next(b for b in buckets if b["range"] == "[0.6,0.7)")
    assert sixties["count"] == 2
    assert sixties["mean_probability"] == pytest.approx(0.635)
    assert sixties["observed_rate"] == pytest.approx(0.5)
    empty = next(b for b in buckets if b["range"] == "[0.3,0.4)")
    assert empty["count"] == 0 and empty["observed_rate"] is None, \
        "where nobody ever forecast is part of the picture"
    ece = expected_calibration_error(rows)
    assert ece == pytest.approx((2 / 3) * abs(0.635 - 0.5)
                                + (1 / 3) * abs(0.05 - 0.0))


def test_hindsight_leakage_raises():
    versions = [
        {"forecast_id": "f1", "version": 1, "probability": 0.30,
         "status": "OPEN", "recorded_time": "2026-08-17T12:00:00+00:00"},
        {"forecast_id": "f1", "version": 2, "probability": 0.95,
         "status": "RESOLVED_TRUE",
         "recorded_time": "2026-08-17T13:00:00+00:00"},
    ]
    with pytest.raises(ValueError, match="hindsight"):
        standing_probability(versions)


def test_scoring_survives_a_frozen_clock():
    """A clock that stamps every version alike still scores, and reads no hindsight."""
    same = "2026-08-17T12:00:00+00:00"
    versions = [
        {"forecast_id": "f1", "version": 1, "probability": 0.35,
         "status": "OPEN", "recorded_time": same},
        {"forecast_id": "f1", "version": 2, "probability": 0.62,
         "status": "OPEN", "recorded_time": same},
        {"forecast_id": "f1", "version": 3, "probability": 0.62,
         "status": "RESOLVED_TRUE", "recorded_time": same},
    ]
    assert standing_probability(versions) == 0.62


# Over the store


def test_scoring_uses_the_standing_probability_and_reports_coverage(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    resolved = _forecast(ctx, by_predicate,
                         question=f"Will {ACME} be suspended by {HORIZON}?",
                         probability=0.35)
    still_open = _forecast(ctx, by_predicate,
                           question=f"Will {ACME} relocate by {HORIZON}?",
                           probability=0.10, domain="corporate-structure")
    voided = _forecast(ctx, by_predicate,
                       question=f"Will {ACME} merge by {HORIZON}?",
                       probability=0.20, author="ola")
    update_probability(ctx, resolved["forecast_id"], probability=0.62,
                       reason="filing trouble reported", actor_id="jan",
                       actor_kind="HUMAN")
    # Evidence that makes the resolution rule read TRUE.
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time=ctx.now_fn())
    pipeline.process_new_evidence()
    outcome = try_machine_resolution(ctx, resolved["forecast_id"])
    assert outcome["status"] == "RESOLVED_TRUE"
    resolve_forecast_human(ctx, voided["forecast_id"], outcome="VOID",
                           evidence_refs=(), actor_id="jan", actor_kind="HUMAN",
                           note="the question dissolved: entity wound down")
    rows = scored_forecasts(ctx.store)
    assert len(rows) == 1, "VOID and OPEN are not scoreable outcomes"
    row = rows[0]
    assert row["probability"] == 0.62, \
        "the score belongs to the probability that stood, not the opener"
    assert row["outcome"] == "TRUE"
    assert row["brier"] == pytest.approx((0.62 - 1.0) ** 2)
    assert row["update_count"] == 1
    board = scoreboard(ctx.store)
    assert board["coverage"]["total_forecasts"] == 3
    assert board["coverage"]["scored"] == 1
    assert board["coverage"]["unscored_by_status"] == {"OPEN": 1,
                                                       "RESOLVED_VOID": 1}
    assert board["by_author"]["jan"]["count"] == 1
    assert "ola" not in board["by_author"]
    assert board["by_domain"]["corporate-registry"]["brier_mean"] \
        == pytest.approx(row["brier"])
    assert board["by_horizon_band"]["SHORT"]["count"] == 1


def test_scoring_is_pure_and_appends_nothing(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    _forecast(ctx, by_predicate,
              question=f"Will {ACME} be suspended by {HORIZON}?",
              probability=0.35)
    before = ctx.store.head()["event_count"]
    scoreboard(ctx.store)
    scored_forecasts(ctx.store)
    assert ctx.store.head()["event_count"] == before, \
        "scoring is a reading of the record, never a write to it"
