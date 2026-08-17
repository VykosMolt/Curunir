"""Forecast lifecycle: authored probabilities only, append-only movement,
typed resolution, coverage-gated absence, terminal immutability."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ForecastRecord, ResolutionRule
from curunir_analytic.forecasts import (create_forecast, forecast_id_for,
                                        refresh_forecast, resolve_forecast_human,
                                        try_machine_resolution, update_probability,
                                        withdraw_forecast)
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.substrate import resolve_candidate
from curunir_fabric.contracts import ExecutionRecord
from curunir_semantic.contracts import ClaimStateRecord
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0,
                              make_analytic)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

NOW = "2026-08-17T12:00:00+00:00"
HORIZON = "2026-08-17T18:00:00+00:00"
ACME = "LEI:ACMELEI000000000001"


def _rule(expected="INACTIVE"):
    return ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads {expected}",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value=expected,
        absence_min_successful_sources=1,
        absence_required_source_ids=("gleif",))


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


def _forecast(ctx, by_predicate, probability=0.35, expected="INACTIVE",
              horizon=HORIZON, **kwargs):
    kwargs.setdefault("supporting_claim_ids", [by_predicate["entity_status"]])
    return create_forecast(
        ctx, question=f"Will {ACME} entity_status read {expected} by {horizon}?",
        outcome_semantics=f"TRUE iff GLEIF entity_status equals {expected!r} "
                          f"at or before the horizon",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon, resolution=_rule(expected),
        probability=probability,
        probability_basis="registry has been stable for years; base rate low",
        author="jan", domain="corporate-registry", **kwargs)


# ---- contract law ----------------------------------------------------------


def test_probability_is_authored_never_machine_made():
    rule = ResolutionRule(kind="HUMAN_JUDGMENT", criteria="analyst settles")
    base = dict(forecast_id="f1", version=1, question="q?",
                outcome_semantics="TRUE iff x", proposition_refs=(("claim", "c1"),),
                horizon_time=HORIZON, resolution=rule,
                probability_basis="b",
                basis=None, assumption_ids=(), indicator_ids=(),
                author="a", domain="d", status="OPEN",
                inference_id="", proposal_id="", outcome="",
                resolved_time="", resolution_evidence_refs=(),
                resolver_id="", resolver_kind="", change_reason="",
                history=(), recorded_time=NOW, marking=MARK)
    from curunir_analytic.contracts import BasisSummary
    basis = BasisSummary(supporting_claim_ids=("c1",), contradicting_claim_ids=(),
                         observation_count=1, manifestation_count=1, source_count=1,
                         origin_families=("f",), degraded_claim_count=0,
                         languages=(), earliest_time="", latest_time="")
    base["basis"] = basis
    with pytest.raises(ValueError, match="never machine-derived"):
        ForecastRecord(**{**base, "probability": 0.5, "provenance_kind": "RULE",
                          "authority": "ANALYST_ASSESSMENT"})
    with pytest.raises(ValueError, match="never observed or derived"):
        ForecastRecord(**{**base, "probability": 0.5, "provenance_kind": "ANALYST",
                          "authority": "DERIVED"})
    for certainty in (0.0, 1.0, -0.1, 1.1):
        with pytest.raises(ValueError, match="strictly inside"):
            ForecastRecord(**{**base, "probability": certainty,
                              "provenance_kind": "ANALYST",
                              "authority": "ANALYST_ASSESSMENT"})
    with pytest.raises(ValueError, match="number, not a judgment"):
        ForecastRecord(**{**base, "probability": 0.5, "probability_basis": "",
                          "provenance_kind": "ANALYST",
                          "authority": "ANALYST_ASSESSMENT"})
    with pytest.raises(ValueError, match="evidence that settled"):
        ForecastRecord(**{**base, "probability": 0.5,
                          "provenance_kind": "ANALYST",
                          "authority": "ANALYST_ASSESSMENT",
                          "status": "RESOLVED_TRUE", "outcome": "TRUE",
                          "resolved_time": NOW, "resolver_id": "x",
                          "resolver_kind": "HUMAN"})


# ---- lifecycle -------------------------------------------------------------


def test_recreation_cannot_silently_move_a_probability(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    with pytest.raises(ValueError, match="explicit[\\s\\S]*update"):
        _forecast(ctx, by_predicate, probability=0.62)
    # a same-probability re-call folds evidence and completes, never duplicates
    again = _forecast(ctx, by_predicate, probability=0.35,
                      supporting_claim_ids=[by_predicate["entity_status"],
                                            by_predicate["legal_name"]])
    assert by_predicate["legal_name"] in again["basis"]["supporting_claim_ids"]
    assert forecast["forecast_id"] == again["forecast_id"]


def test_update_is_append_only_with_reason_and_actor(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    with pytest.raises(ValueError, match="human act"):
        update_probability(ctx, forecast["forecast_id"], probability=0.62,
                           reason="machine hunch", actor_id="svc",
                           actor_kind="SERVICE")
    with pytest.raises(ValueError, match="states its reason"):
        update_probability(ctx, forecast["forecast_id"], probability=0.62,
                           reason="", actor_id="jan", actor_kind="HUMAN")
    updated = update_probability(
        ctx, forecast["forecast_id"], probability=0.62,
        reason="registration status disputed by an independent family",
        evidence_refs=(by_predicate["registration_status"],),
        actor_id="jan", actor_kind="HUMAN")
    assert updated["probability"] == 0.62
    versions = ctx.store.analytic_versions("analytic_forecast",
                                           forecast["forecast_id"])
    assert [v["probability"] for v in versions] == [0.35, 0.62], \
        "0.35 is history, never overwritten"
    assert "0.35 → 0.62" in versions[-1]["change_reason"]
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(forecast["forecast_id"])]
    assert "PROBABILITY_UPDATED" in kinds


def test_model_update_goes_through_the_gate(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {
            "question": forecast["question"], "probability": 0.7,
            "horizon_time": forecast["horizon_time"]})
    proposed = assist.propose(ctx, task="reforecast",
                              target_kind="analytic_forecast",
                              inputs={}, input_refs=())
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    # a different number than accepted is refused by binding
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        update_probability(ctx, forecast["forecast_id"], probability=0.9,
                           reason="model says", actor_id="", actor_kind="SERVICE",
                           provenance_kind="MODEL",
                           inference_id=resolved["inference_id"],
                           proposal_id=resolved["proposal_id"])
    updated = update_probability(ctx, forecast["forecast_id"], probability=0.7,
                                 reason="accepted model re-forecast",
                                 actor_id="", actor_kind="SERVICE",
                                 provenance_kind="MODEL",
                                 inference_id=resolved["inference_id"],
                                 proposal_id=resolved["proposal_id"])
    assert updated["authority"] == "SUPPORTED_INFERENCE"
    assert updated["probability"] == 0.7


def test_degraded_basis_flags_but_never_moves_the_number(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    state = ClaimStateRecord(
        state_id="st-1", claim_id=by_predicate["entity_status"], state="DISPUTED",
        reason="independent disagreement", caused_by="c", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    refreshed = refresh_forecast(ctx, forecast["forecast_id"], caused_by="chg")
    assert refreshed["status"] == "UPDATE_REQUIRED"
    assert refreshed["probability"] == 0.35, \
        "the machine flags a stale number; it never moves one"
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(forecast["forecast_id"])]
    assert "BASIS_DEGRADED" in kinds


# ---- resolution ------------------------------------------------------------


def test_machine_true_resolution_is_evidence_bound(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35, expected="INACTIVE")
    # the registry updates: entity_status becomes INACTIVE (same family, newer)
    v1 = next(m for m in ctx.store.records_of("fabric_manifestation"))
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED,
                        media_type="application/json",
                        retrieval_time="2026-08-17T14:00:00+00:00",
                        prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] == "RESOLVED_TRUE"
    assert resolved["outcome"] == "TRUE"
    assert by_predicate["entity_status"] in resolved["resolution_evidence_refs"]
    assert resolved["resolver_kind"] == "SERVICE"
    # a settled forecast is immutable
    with pytest.raises(ValueError, match="settled"):
        update_probability(ctx, forecast["forecast_id"], probability=0.9,
                           reason="x", actor_id="jan", actor_kind="HUMAN")
    with pytest.raises(ValueError, match="settled"):
        withdraw_forecast(ctx, forecast["forecast_id"], note="x",
                          actor_id="jan", actor_kind="HUMAN")


def test_false_by_absence_is_coverage_gated(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    # horizon in the past relative to the ticking clock
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         expected="NEVER_SO", horizon="2026-08-17T12:04:00+00:00")
    # claim evidence predates the horizon and no coverage since → NOT resolved
    waiting = refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    assert waiting["status"] == "HORIZON_PASSED", \
        "silence without coverage is not FALSE"
    gaps = [r for r in ctx.store.open_review_items()
            if r["kind"] == "COVERAGE_GAP"
            and r["subject_id"] == forecast["forecast_id"]]
    assert gaps, "the coverage block is durable, reviewable state"
    # the declared coverage is then achieved: gleif successfully re-searched
    execution = ExecutionRecord(
        execution_id="exec-cov-1", plan_id="", query_id="q1", source_id="gleif",
        connector_id="gleif-lei-v1", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] == "RESOLVED_FALSE"
    assert "coverage was achieved" in resolved["change_reason"]
    assert "exec-cov-1" in resolved["resolution_evidence_refs"]


def test_truncated_search_does_not_resolve_false_by_absence(tmp_path):
    # V6.7 §7.1: a byte-capped (truncated) retrieval did not see the whole
    # source, so it cannot establish absence — a resource limit must never
    # become proof of absence (a RESOLVED_FALSE forecast).
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35,
                         expected="NEVER_SO", horizon="2026-08-17T12:04:00+00:00")
    refresh_forecast(ctx, forecast["forecast_id"], caused_by="tick")
    # the declared source was searched but the retrieval was TRUNCATED
    execution = ExecutionRecord(
        execution_id="exec-trunc-1", plan_id="", query_id="q1", source_id="gleif",
        connector_id="gleif-lei-v1", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0,
        request_url="https://api.gleif.org/api/v1/lei-records/ACMELEI000000000001",
        http_status=200, policy_decision="ALLOW", error_class=None,
        error_detail="", manifestation_ids=(),
        started_time=ctx.now_fn(), completed_time=ctx.now_fn(),
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=MARK,
        truncated=True)
    ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                     recorded_time=ctx.now_fn(), actor="t")
    resolved = try_machine_resolution(ctx, forecast["forecast_id"])
    assert resolved["status"] != "RESOLVED_FALSE", \
        "a truncated search must not resolve a forecast FALSE by absence"


def test_human_resolution_requires_evidence_and_human(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    forecast = _forecast(ctx, by_predicate, probability=0.35)
    with pytest.raises(ValueError, match="requires a human"):
        resolve_forecast_human(ctx, forecast["forecast_id"], outcome="TRUE",
                               evidence_refs=("x",), note="n",
                               actor_id="svc", actor_kind="SERVICE")
    with pytest.raises(ValueError, match="requires evidence"):
        resolve_forecast_human(ctx, forecast["forecast_id"], outcome="TRUE",
                               evidence_refs=(), note="n",
                               actor_id="jan", actor_kind="HUMAN")
    voided = resolve_forecast_human(ctx, forecast["forecast_id"], outcome="VOID",
                                    evidence_refs=(), note="question ill-posed",
                                    actor_id="jan", actor_kind="HUMAN")
    assert voided["status"] == "RESOLVED_VOID"
    assert voided["resolver_id"] == "jan"
