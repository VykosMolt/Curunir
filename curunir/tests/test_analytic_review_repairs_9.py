"""Round-9 analytic repairs: a human act spends no model candidate, a path id
collision refuses, only identifier-shaped strings count as shown identifiers,
the offline backend can fill every proposable kind, timestamps compare as
instants, and the CLI refuses a missing argument."""
from __future__ import annotations

import pytest

from curunir_analytic.basis import compute_basis
from curunir_analytic.candidate_schema import (available_ids, candidate_schema,
                                               proposable_kinds, unresolvable_ids)
from curunir_analytic.cli import main as cli_main
from curunir_analytic.contracts import (BasisSummary, ImpactEdge, ResolutionRule,
                                        ThemeRecord)
from curunir_analytic.forecasts import create_forecast, refresh_forecast
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.model_backends import DeterministicBackend
from curunir_analytic.providers import AnalyticalAssist
from curunir_analytic.substrate import resolve_candidate
from curunir_analytic.themes import create_theme

from analytic_support import (GLEIF_ACME, GLEIF_ACME_SUSPENDED, MARK, T0,
                              make_analytic, plant_page, statement_page)
from semantic_support import clock, plant_manifestation

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"


def _seed(pipeline, ctx, body=GLEIF_ACME, retrieval_time=T0):
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001", body=body,
                        media_type="application/json",
                        retrieval_time=retrieval_time)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"]
            for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


def _accepted_theme_candidate(ctx, claim_id):
    """A model theme candidate a human has accepted: (proposal, inference id)."""
    backend = DeterministicBackend()
    assist = AnalyticalAssist(package=backend.package(), backend=backend)
    result = assist.propose(ctx, task="label the registry cluster",
                            target_kind="analytic_theme",
                            inputs={"claims": [claim_id]}, input_refs=(claim_id,))
    assert result["status"] == "PROPOSED", result
    accepted = resolve_candidate(ctx, result["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    return accepted, result["inference_id"]


# ---- B1: a human act spends no candidate ----------------------------------

def test_an_analyst_creation_does_not_consume_a_model_candidate(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    claim_id = by_predicate["entity_status"]
    accepted, inference_id = _accepted_theme_candidate(ctx, claim_id)
    proposal_id = accepted["proposal_id"]

    analyst = create_theme(ctx, title="analyst's own theme",
                           supporting_claim_ids=(claim_id,),
                           provenance_kind="ANALYST", proposal_id=proposal_id)
    assert analyst["proposal_id"] == ""

    materialized = create_theme(
        ctx, title=accepted["content"]["title"],
        supporting_claim_ids=tuple(accepted["content"]["supporting_claim_ids"]),
        provenance_kind="MODEL", inference_id=inference_id,
        proposal_id=proposal_id)
    assert materialized["provenance_kind"] == "MODEL"
    assert materialized["proposal_id"] == proposal_id
    assert materialized["authority"] == "SUPPORTED_INFERENCE"


def test_a_non_model_record_cannot_carry_a_proposal_id():
    summary = BasisSummary(
        supporting_claim_ids=("c1",), contradicting_claim_ids=(),
        observation_count=0, manifestation_count=0, source_count=0,
        origin_families=(), degraded_claim_count=0, languages=(),
        earliest_time="", latest_time="")
    with pytest.raises(ValueError, match="proposal"):
        ThemeRecord(
            theme_id="t1", version=1, title="t", description="",
            status="EMERGING", authority="ANALYST_ASSESSMENT",
            parent_theme_id="", lineage=(), basis=summary,
            entity_ids=(), event_ids=(), relation_ids=(),
            valid_from=None, valid_to=None, provenance_kind="ANALYST",
            inference_id="", proposal_id="anprop-borrowed", change_reason="",
            history=(), recorded_time=T0, marking=MARK)


# ---- B2: a different chain is not the same path ---------------------------

def test_two_chains_reusing_an_edge_id_do_not_collide_silently(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1",
                                 statement="Keep Acme's standing visible",
                                 priority="HIGH")
    objective_id = objective["objective_id"]

    def _edge(from_id, note):
        return ImpactEdge(
            edge_id="E1", from_kind="object", from_id=from_id,
            to_kind="mission_objective", to_id=objective_id,
            edge_kind="DEPENDENCY", effect_order="DIRECT", authority="OBSERVED",
            note=note, basis_ids=(by_predicate["entity_status"],),
            assumption_ids=())

    first = build_path(ctx, objective_id=objective_id, summary="first chain",
                       edges=(_edge("supplier-a", "first reasoning"),))
    assert first["edges"][0]["from_id"] == "supplier-a"
    with pytest.raises(ValueError, match="different edge chain"):
        build_path(ctx, objective_id=objective_id, summary="second chain",
                   edges=(_edge("supplier-b", "second reasoning"),))


def test_rebuilding_the_same_chain_still_reconciles(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1",
                                 statement="Keep Acme's standing visible",
                                 priority="HIGH")
    edge = ImpactEdge(
        edge_id="E1", from_kind="object", from_id="supplier-a",
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="OBSERVED",
        note="same reasoning", basis_ids=(by_predicate["entity_status"],),
        assumption_ids=())
    first = build_path(ctx, objective_id=objective["objective_id"],
                       summary="chain", edges=(edge,))
    again = build_path(ctx, objective_id=objective["objective_id"],
                       summary="chain", edges=(edge,))
    assert again["path_id"] == first["path_id"]
    assert again["version"] == first["version"]


# ---- B3: only identifier-shaped strings are "shown" ------------------------

def test_a_claim_statement_is_not_an_available_identifier():
    payload = {"task": "propose",
               "evidence": {"claims": [{"claim_id": "c1",
                                        "statement": "Acme is ISSUED"}]}}
    shown = available_ids(payload)
    assert "c1" in shown
    assert "Acme is ISSUED" not in shown
    assert "propose" not in shown
    assert unresolvable_ids(
        "analytic_theme",
        {"title": "t", "supporting_claim_ids": ["Acme is ISSUED"]},
        shown) == ("supporting_claim_ids=Acme is ISSUED",)


# ---- B4: the offline backend can fill every proposable kind ---------------

_EVIDENCE = {
    "claims": ["claim-1"],
    "supporting_claim_ids": ["claim-1"],
    "claim_ids": ["claim-1"],
    "forecast_ids": ["forecast-1"],
    "narrative_id": "narrative-1",
    "from_manifestation_id": "manifestation-1",
    "to_manifestation_id": "manifestation-2",
    "entity_object_id": "object-1",
    "context_id": "theme-1",
    "source_object_id": "object-1",
    "target_object_id": "object-2",
    "objective_id": "objective-1",
    "path_id": "path-1",
    "episode_id": "episode-1",
    "query_id": "theme-1",
}


def test_the_offline_backend_cites_only_shown_ids_for_every_proposable_kind():
    backend = DeterministicBackend()
    shown = available_ids(_EVIDENCE)
    for kind in proposable_kinds():
        content = backend.infer("exercise the seam", _EVIDENCE, kind)
        assert unresolvable_ids(kind, content, shown) == (), kind


def test_the_offline_backend_picks_the_safest_enum_member():
    backend = DeterministicBackend()
    indicator = backend.infer("t", _EVIDENCE, "forecast_indicator")
    # ABSENCE additionally demands a deadline and named coverage sources, so
    # an offline candidate that chose it could never be materialized
    assert indicator["kind"] == "PRESENCE"
    assert indicator["forecast_ids"] == ["forecast-1"]


# ---- B5: timestamps compare as instants, not as text ----------------------

def test_an_offset_bearing_timestamp_orders_by_instant(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_page(pipeline, source_id="wayback",
               url="https://earlier.example.org/sak",
               body=statement_page("Alpha issued a distinctive public notice"),
               retrieval_time="2026-08-17T17:00:00+00:00",
               temporal_status="HISTORICAL",
               archive_capture_time="2020-01-05T12:00:00+02:00")
    plant_page(pipeline, source_id="wayback",
               url="https://later.example.org/sak",
               body=statement_page("Beta issued a distinctive public notice"),
               retrieval_time="2026-08-17T17:05:00+00:00",
               temporal_status="HISTORICAL",
               archive_capture_time="2020-01-05T11:00:00+00:00")
    pipeline.process_new_evidence()
    claims = [c["claim_id"] for c in ctx.store.current_claims().values()
              if c["subject_ref"].startswith("URL:")
              and "example.org" in c["subject_ref"]]
    assert len(claims) >= 2
    basis = compute_basis(ctx.store, claims)
    # 12:00+02:00 is 10:00 UTC — earlier than 11:00+00:00, though it sorts later
    assert basis.earliest_time == "2020-01-05T12:00:00+02:00"
    assert basis.latest_time == "2020-01-05T11:00:00+00:00"


# ---- B6: a passed horizon resolves on the pass that notices it ------------

def test_a_moved_basis_past_the_horizon_still_reaches_machine_resolution(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    horizon = "2026-08-17T14:00:00+00:00"
    forecast = create_forecast(
        ctx, question=f"Will {ACME} read INACTIVE by {horizon}?",
        outcome_semantics="TRUE iff entity_status equals 'INACTIVE'",
        proposition_refs=(("claim", by_predicate["entity_status"]),),
        horizon_time=horizon,
        resolution=ResolutionRule(
            kind="CLAIM_PREDICATE",
            criteria=f"GLEIF entity_status for {ACME} reads INACTIVE",
            claim_subject_ref=ACME, claim_attribute="entity_status",
            expected_value="INACTIVE",
            absence_min_successful_sources=1,
            absence_required_source_ids=("gleif",)),
        probability=0.3, probability_basis="registry base rates",
        author="jan", domain="corporate-registry",
        supporting_claim_ids=[by_predicate["entity_status"]])
    ctx.now_fn = clock(150)          # 14:30, past the horizon
    pipeline.now_fn = ctx.now_fn
    passed = refresh_forecast(ctx, forecast["forecast_id"], caused_by="clock")
    assert passed["status"] == "HORIZON_PASSED"

    # the answer arrives with an evidence state at or before the horizon
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    settled = refresh_forecast(ctx, forecast["forecast_id"], caused_by="evidence")
    assert settled["status"] == "RESOLVED_TRUE"


# ---- B7: the emitted bound is the enforced bound --------------------------

def test_the_probability_schema_excludes_the_endpoints_the_contract_rejects():
    shape = candidate_schema("analytic_forecast")["properties"]["probability"]
    assert shape["exclusiveMinimum"] == 0.0
    assert shape["exclusiveMaximum"] == 1.0
    assert "minimum" not in shape and "maximum" not in shape


# ---- B8: the CLI refuses a missing argument -------------------------------

@pytest.mark.parametrize("command", ["explain", "history"])
def test_cli_reports_a_missing_argument_instead_of_crashing(tmp_path, command):
    make_analytic(tmp_path)
    with pytest.raises(SystemExit):
        cli_main(["--root", str(tmp_path), command])
    with pytest.raises(SystemExit):
        cli_main(["--root", str(tmp_path), command, "analytic_theme"])
