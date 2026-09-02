"""Narratives: how far a statement travelled is not how many independent sources
carry it, propagation edges state their own authority, variants and counter-
narratives are kept apart, and an origin is only the earliest copy seen."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import NarrativeVariant
from curunir_analytic.narratives import (add_variant, assert_independent_adoption,
                                         create_narrative, derive_propagation,
                                         explain_narrative, link_counter_narrative,
                                         refresh_narrative)

from analytic_support import MARK, T0, make_analytic, plant_page, statement_page
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

STATEMENT = "Acme Industri plans to close its Oslo plant this year"


def _seed_statement_pages(pipeline, ctx):
    """The same statement on two pages of one site and one page of another."""
    plant_page(pipeline, url="https://nyhet.example.no/a",
               body=statement_page(STATEMENT), retrieval_time=T0)
    plant_page(pipeline, url="https://nyhet.example.no/b",
               body=statement_page(STATEMENT),
               retrieval_time="2026-08-17T13:00:00+00:00")
    plant_page(pipeline, url="https://industriwatch.example.org/report",
               body=statement_page(STATEMENT),
               retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()
    return sorted(c["claim_id"] for c in ctx.store.current_claims().values()
                  if c["predicate"] == "statement")


def test_reach_and_independence_stay_separate_axes(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    assert len(claim_ids) == 3
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids)
    assert narrative["basis"]["manifestation_count"] == 3   # propagation reach
    assert len(narrative["basis"]["origin_families"]) == 2  # independence
    explanation = explain_narrative(ctx.store, narrative["narrative_id"])
    assert explanation["propagation_reach"] == 3
    assert explanation["source_independence"] == 2
    assert "not a claim about true origin" in explanation["origin"]["caveat"]


def test_propagation_edges_carry_honest_authority(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids)
    edges = derive_propagation(ctx, narrative["narrative_id"])
    relations = {(e["relation"], e["authority"]) for e in edges}
    assert ("SAME_ORIGIN_FAMILY", "DERIVED") in relations
    assert ("LIKELY_DERIVATIVE", "SUPPORTED_INFERENCE") in relations
    derivative = next(e for e in edges if e["relation"] == "LIKELY_DERIVATIVE")
    assert "unseen common origin" in derivative["mechanism"]
    assert derivative["from_family"] != derivative["to_family"]
    assert derive_propagation(ctx, narrative["narrative_id"]) == []


def test_variant_relations_cannot_launder_authority():
    with pytest.raises(ValueError, match="judgment"):
        NarrativeVariant(
            variant_id="v1", narrative_id="n1", relation="FRAMING_SHIFT",
            statement="critics call the closure inevitable",
            claim_ids=("c1",), observation_ids=(), manifestation_ids=("m1",),
            language="en", authority="DERIVED", mechanism="",
            provenance_kind="RULE", inference_id="",
            recorded_time="2026-08-17T12:00:00+00:00", marking=MARK)


def test_variant_folds_into_basis_and_history(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids[:1])
    plant_page(pipeline, url="https://finansavis.example.se/artikel",
               body=statement_page("Acme Industri overväger att stänga Oslofabriken"),
               retrieval_time="2026-08-17T15:00:00+00:00")
    pipeline.process_new_evidence()
    swedish_claim = next(
        c for c in ctx.store.current_claims().values()
        if "överväger" in c["object_or_value"] or "overv" in c["object_or_value"])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in swedish_claim["observation_ids"])
    updated = add_variant(
        ctx, narrative["narrative_id"], relation="CERTAINTY_SHIFT",
        statement=swedish_claim["object_or_value"],
        claim_ids=(swedish_claim["claim_id"],),
        manifestation_ids=(manifestation_id,), language="sv",
        authority="ANALYST_ASSESSMENT",
        mechanism="'plans to close' weakened to 'considers closing'; translation "
                  "and certainty judgment by analyst",
        provenance_kind="ANALYST")
    assert len(updated["variant_ids"]) == 1
    assert swedish_claim["claim_id"] in updated["basis"]["supporting_claim_ids"]
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(narrative["narrative_id"])}
    assert "VARIANT_ADDED" in kinds
    explanation = explain_narrative(ctx.store, narrative["narrative_id"])
    assert explanation["variants"][0]["relation"] == "CERTAINTY_SHIFT"
    assert explanation["variants"][0]["language"] == "sv"


def test_counter_narratives_coexist(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    plant_page(pipeline, url="https://acme-industri.example.no/press",
               body=statement_page("Acme Industri has no plans to close any plant"),
               retrieval_time="2026-08-17T16:00:00+00:00")
    pipeline.process_new_evidence()
    denial_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                        if "no plans" in c["object_or_value"])
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids)
    counter = create_narrative(ctx, statement="Acme Industri has no plans to close any plant",
                               supporting_claim_ids=[denial_claim])
    link_counter_narrative(ctx, narrative["narrative_id"], counter["narrative_id"])
    current = ctx.store.current_narratives()
    assert counter["narrative_id"] in current[narrative["narrative_id"]]["counter_narrative_ids"]
    assert narrative["narrative_id"] in current[counter["narrative_id"]]["counter_narrative_ids"]
    assert current[narrative["narrative_id"]]["status"] == "ACTIVE"
    assert current[counter["narrative_id"]]["status"] == "ACTIVE"


def test_historical_discovery_revises_earliest_observed_only(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids)
    assert narrative["earliest_time"].startswith("2026-08-17T12")
    # An archived 2020 copy of the same statement on another site.
    plant_manifestation(
        pipeline, source_id="wayback",
        native_id="20200105120000/https://gammelt.example.org/sak",
        body=statement_page(STATEMENT).encode(), media_type="text/html",
        retrieval_time="2026-08-17T17:00:00+00:00", temporal_status="HISTORICAL",
        archive_capture_time="2020-01-05T12:00:00+00:00")
    pipeline.process_new_evidence()
    archive_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                         if "gammelt.example.org" in c["subject_ref"])
    from curunir_analytic.themes import update_membership  # noqa: F401 (structure parity)
    updated = add_variant(
        ctx, narrative["narrative_id"], relation="VERBATIM",
        statement=STATEMENT, claim_ids=(archive_claim,),
        manifestation_ids=(next(
            o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
            if o["observation_id"] in ctx.store.current_claims()[archive_claim]["observation_ids"]),),
        authority="DERIVED", provenance_kind="RULE")
    refreshed = refresh_narrative(ctx, narrative["narrative_id"], caused_by="hist-1")
    assert refreshed["earliest_time"].startswith("2020-01-05")
    assert refreshed["origin_status"] == "EARLIEST_OBSERVED_KNOWN"
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(narrative["narrative_id"])}
    assert "ORIGIN_REVISED" in kinds
    transition = next(t for t in ctx.store.transitions_for(narrative["narrative_id"])
                      if t["transition_type"] == "ORIGIN_REVISED")
    assert "not proven origin" in transition["detail"]


def test_independent_adoption_requires_judgment(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claim_ids = _seed_statement_pages(pipeline, ctx)
    narrative = create_narrative(ctx, statement=STATEMENT,
                                 supporting_claim_ids=claim_ids)
    manifestations = [o["manifestation_id"]
                      for o in ctx.store.records_of("semantic_observation")][:2]
    with pytest.raises(ValueError, match="accepted candidate"):
        assert_independent_adoption(
            ctx, narrative["narrative_id"],
            from_manifestation_id=manifestations[0],
            to_manifestation_id=manifestations[1],
            mechanism="looks independent", actor_id="svc", actor_kind="SERVICE")
    with pytest.raises(ValueError, match="not found in the log"):
        assert_independent_adoption(
            ctx, narrative["narrative_id"],
            from_manifestation_id=manifestations[0],
            to_manifestation_id=manifestations[1],
            mechanism="looks independent", actor_id="svc", actor_kind="SERVICE",
            inference_id="inf-fabricated", proposal_id="prop-fabricated")
