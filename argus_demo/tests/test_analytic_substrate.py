"""Shared analytical substrate: contracts lock the epistemic law, the basis
arithmetic counts dependence honestly, transitions are idempotent history,
and model candidates cannot promote themselves."""
from __future__ import annotations

import pytest

from curunir_analytic.basis import compute_basis, describe_descent
from curunir_analytic.contracts import (AnalogueDimension, BasisSummary,
                                        HistoricalAnalogue, ImpactEdge, ImpactPath,
                                        InfluenceAssertion, StakeholderPosition,
                                        weakest_authority)
from curunir_analytic.substrate import (DependencyIndex, record_candidate,
                                        record_transition, resolve_candidate)

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              statement_page)

pytestmark = pytest.mark.no_db

NOW = "2026-08-17T12:00:00+00:00"


# ---- contract invariants (the epistemic law) -----------------------------


def _basis(supporting=("c1",), families=("f1",), manifestations=1):
    return BasisSummary(
        supporting_claim_ids=supporting, contradicting_claim_ids=(),
        observation_count=1, manifestation_count=manifestations, source_count=1,
        origin_families=families, degraded_claim_count=0, languages=(),
        earliest_time="", latest_time="", stated_valid_from="", stated_valid_to="",
        unresolved_claim_ids=(), coverage_notes=(), note="t")


def test_independence_cannot_exceed_reach():
    with pytest.raises(ValueError, match="independence is bounded by reach"):
        _basis(families=("f1", "f2"), manifestations=1)


def test_inferred_interest_can_never_be_observed():
    with pytest.raises(ValueError, match="never .*observed"):
        StakeholderPosition(
            position_id="p1", kind="INFERRED_INTEREST",
            statement="benefits from barriers", stance="UNRESOLVED",
            authority="OBSERVED", claim_ids=("c1",), relationship_ids=(),
            valid_from=None, valid_to=None, superseded=False, note="")


def test_public_position_requires_claims():
    with pytest.raises(ValueError, match="claims that carry it"):
        StakeholderPosition(
            position_id="p1", kind="PUBLIC_POSITION", statement="supports Y",
            stance="UNRESOLVED", authority="OBSERVED", claim_ids=(),
            relationship_ids=(), valid_from=None, valid_to=None,
            superseded=False, note="")


def test_observed_stance_labeling_needs_source_stated_basis():
    with pytest.raises(ValueError, match="stance UNRESOLVED"):
        StakeholderPosition(
            position_id="p1", kind="PUBLIC_POSITION", statement="supports Y",
            stance="SUPPORTS", authority="OBSERVED", claim_ids=("c1",),
            relationship_ids=(), valid_from=None, valid_to=None,
            superseded=False, note="")


def test_likely_influences_cannot_claim_observation():
    with pytest.raises(ValueError, match="inference"):
        InfluenceAssertion(
            influence_id="i1", version=1, source_object_id="a", target_object_id="b",
            kind="LIKELY_INFLUENCES", mechanism="repeated coordination",
            authority="OBSERVED", claim_ids=("c1",), relationship_ids=(),
            valid_from=None, valid_to=None, status="ACTIVE",
            provenance_kind="ANALYST", inference_id="", proposal_id="",
            change_reason="", history=(), recorded_time=NOW, marking=MARK)


def test_impact_edge_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="adjacency without evidence"):
        ImpactEdge(edge_id="e1", from_kind="object", from_id="a",
                   to_kind="object", to_id="b", edge_kind="TYPED_RELATION",
                   effect_order="DIRECT", authority="OBSERVED", note="",
                   basis_ids=(), assumption_ids=())


def test_path_authority_is_weakest_edge_validated():
    strong = ImpactEdge(edge_id="e1", from_kind="activity", from_id="ev",
                        to_kind="object", to_id="a", edge_kind="EVENT_EFFECT",
                        effect_order="DIRECT", authority="OBSERVED", note="",
                        basis_ids=("cl1",), assumption_ids=())
    weak = ImpactEdge(edge_id="e2", from_kind="object", from_id="a",
                      to_kind="mission_objective", to_id="o", edge_kind="INFERENCE",
                      effect_order="SECOND_ORDER", authority="SUPPORTED_INFERENCE",
                      note="exposure via dependency", basis_ids=(), assumption_ids=())
    assert weakest_authority([e.authority for e in (strong, weak)]) == "SUPPORTED_INFERENCE"
    with pytest.raises(ValueError, match="weakest edge authority"):
        ImpactPath(path_id="p1", version=1, objective_id="o", summary="s",
                   edges=(strong, weak), status="ASSESSED",
                   path_authority="OBSERVED", uncertainty_note="one link inferred",
                   assumption_ids=(), provenance_kind="RULE", inference_id="",
                   proposal_id="", change_reason="", history=(),
                   recorded_time=NOW, marking=MARK)


def test_path_edges_must_connect():
    e1 = ImpactEdge(edge_id="e1", from_kind="activity", from_id="ev",
                    to_kind="object", to_id="a", edge_kind="EVENT_EFFECT",
                    effect_order="DIRECT", authority="OBSERVED", note="",
                    basis_ids=("cl1",), assumption_ids=())
    e2 = ImpactEdge(edge_id="e2", from_kind="object", from_id="DIFFERENT",
                    to_kind="mission_objective", to_id="o", edge_kind="DEPENDENCY",
                    effect_order="SECOND_ORDER", authority="OBSERVED", note="",
                    basis_ids=("r1",), assumption_ids=())
    with pytest.raises(ValueError, match="must connect"):
        ImpactPath(path_id="p1", version=1, objective_id="o", summary="s",
                   edges=(e1, e2), status="ASSESSED", path_authority="OBSERVED",
                   uncertainty_note="", assumption_ids=(), provenance_kind="RULE",
                   inference_id="", proposal_id="", change_reason="", history=(),
                   recorded_time=NOW, marking=MARK)


def test_analogue_requires_transfer_risks_and_is_never_observed():
    dim = AnalogueDimension(dimension="EVENT_TYPE", detail="both are exits", basis_ids=("c1",))
    with pytest.raises(ValueError, match="transfer-risk"):
        HistoricalAnalogue(
            analogue_id="a1", version=1, query_kind="analytic_theme", query_id="t1",
            episode_id="ep1", matched=(dim,), mismatched=(), transfer_risks=(),
            retrieval_method="structural", authority="SUPPORTED_INFERENCE",
            status="PROPOSED", provenance_kind="RULE", inference_id="",
            change_reason="", history=(), recorded_time=NOW, marking=MARK)
    with pytest.raises(ValueError, match="never a direct observation"):
        HistoricalAnalogue(
            analogue_id="a1", version=1, query_kind="analytic_theme", query_id="t1",
            episode_id="ep1", matched=(dim,), mismatched=(),
            transfer_risks=("constraint differs",), retrieval_method="structural",
            authority="OBSERVED", status="PROPOSED", provenance_kind="RULE",
            inference_id="", change_reason="", history=(),
            recorded_time=NOW, marking=MARK)


# ---- basis arithmetic over real store state -------------------------------


def test_basis_counts_derivatives_as_one_family(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    # the same GLEIF record retrieved twice: one origin family, reach 2
    plant_page(pipeline, url="lei/ACMELEI000000000001", body=GLEIF_ACME.decode(),
               retrieval_time=T0, source_id="gleif")
    from semantic_support import plant_manifestation
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001?v=2",
                        body=GLEIF_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = ctx.store.current_claims()
    assert claims, "expected claims from GLEIF evidence"
    status_claims = [c for c in claims.values() if c["predicate"] == "entity_status"]
    assert status_claims
    basis = compute_basis(ctx.store, [c["claim_id"] for c in status_claims])
    assert basis.origin_family_count == 1
    assert basis.manifestation_count >= 1
    assert "never count twice" in basis.note


def test_descent_reaches_anchor_and_source(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_page(pipeline, url="lei/ACMELEI000000000001", body=GLEIF_ACME.decode(),
               retrieval_time=T0, source_id="gleif")
    pipeline.process_new_evidence()
    claim_id = next(c["claim_id"] for c in ctx.store.current_claims().values()
                    if c["predicate"] == "legal_name")
    descent = describe_descent(ctx.store, claim_id)
    assert descent["claim_state"] == "CURRENT"
    step = descent["descent"][0]
    assert step["source_id"] == "gleif"
    assert step["anchors"][0]["kind"] == "FIELD"
    assert step["anchors"][0]["field_path"].endswith("legalName.name")
    assert step["origin_family"]


# ---- transitions and candidates ------------------------------------------


def test_transitions_are_idempotent_per_cause(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    kwargs = dict(subject_kind="analytic_theme", subject_id="t1",
                  transition_type="STRENGTHENED", detail="new independent family",
                  caused_by="change-1")
    first = record_transition(ctx, **kwargs)
    second = record_transition(ctx, **kwargs)
    assert first is not None and second is None
    assert len(ctx.store.transitions_for("t1")) == 1


def test_model_candidate_cannot_self_promote(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    proposal = record_candidate(ctx, target_kind="analytic_theme",
                                content={"title": "Registry instability",
                                         "supporting_claim_ids": ("c1",)},
                                inference_id="inf-1")
    assert proposal["status"] == "PROPOSED"
    with pytest.raises(ValueError, match="only a human"):
        resolve_candidate(ctx, proposal["proposal_id"], accept=True,
                          actor_id="pipeline", actor_kind="SERVICE")
    resolved = resolve_candidate(ctx, proposal["proposal_id"], accept=True,
                                 actor_id="jan", actor_kind="HUMAN")
    assert resolved["status"] == "ACCEPTED"
    with pytest.raises(ValueError, match="already"):
        resolve_candidate(ctx, proposal["proposal_id"], accept=False,
                          actor_id="jan", actor_kind="HUMAN")


def test_candidate_requires_inference_identity(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    with pytest.raises(ValueError, match="inference record"):
        record_candidate(ctx, target_kind="analytic_theme",
                         content={"title": "x", "supporting_claim_ids": ("c1",)},
                         inference_id="")


# ---- dependency index -----------------------------------------------------


def test_dependency_index_finds_dependents(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_page(pipeline, url="lei/ACMELEI000000000001", body=GLEIF_ACME.decode(),
               retrieval_time=T0, source_id="gleif")
    pipeline.process_new_evidence()
    claim_id = next(iter(ctx.store.current_claims()))
    from curunir_analytic.contracts import ThemeRecord
    from curunir_analytic.substrate import append_version
    theme = ThemeRecord(
        theme_id="t1", version=1, title="Acme registry state", description="",
        status="EMERGING", authority="DERIVED", parent_theme_id="", lineage=(),
        basis=compute_basis(ctx.store, [claim_id]),
        entity_ids=(), event_ids=(), relation_ids=(),
        valid_from=None, valid_to=None, provenance_kind="RULE", inference_id="",
        proposal_id="", change_reason="", history=("CREATED",),
        recorded_time=ctx.now_fn(), marking=MARK)
    append_version(ctx, theme)
    index = DependencyIndex(ctx.store)
    assert ("analytic_theme", "t1") in index.affected_by(claim_ids=[claim_id])
    assert index.affected_by(claim_ids=["nonexistent"]) == set()
