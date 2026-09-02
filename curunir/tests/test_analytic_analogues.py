"""Historical analogues: episodes must cite evidence, retrieval shows what
matches and what does not, and an analogy never becomes a forecast."""
from __future__ import annotations

import pytest

from curunir_analytic.analogues import (explain_analogue, record_episode,
                                        retrieve_analogues)
from curunir_analytic.contracts import HistoricalEpisode
from curunir_analytic.substrate import AnalyticContext
from curunir_analytic.themes import create_theme
from curunir_operational.access import can_view
from curunir_semantic.worldmodel import world_object_id

from analytic_support import GLEIF_ACME, MARK, T0, make_analytic
from semantic_support import plant_manifestation
from workbench_support import CTX_B, RESTRICTED_MARK

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")
GLEIF_OTHER = GLEIF_ACME.replace(b"ACMELEI000000000001", b"OTHERLEI00000000002") \
    .replace(b"Acme Industri AS", b"Borg Verft AS")


def _seed(pipeline, ctx):
    for native, body in (("lei/ACMELEI000000000001", GLEIF_ACME),
                         ("lei/OTHERLEI00000000002", GLEIF_OTHER)):
        plant_manifestation(pipeline, source_id="gleif", native_id=native,
                            body=body, media_type="application/json",
                            retrieval_time=T0)
    pipeline.process_new_evidence()
    return {(c["subject_ref"], c["predicate"]): c["claim_id"]
            for c in ctx.store.current_claims().values()}


def test_episode_requires_evidence():
    with pytest.raises(ValueError, match="not fabricated"):
        HistoricalEpisode(
            episode_id="e1", version=1, title="Invented affair", summary="",
            actor_object_ids=(), event_ids=(), institutional_setting="",
            mechanism="", constraints=(), outcome="", outcome_claim_ids=(),
            claim_ids=(), valid_from=None, valid_to=None, change_reason="",
            history=(), recorded_time="2026-08-17T12:00:00+00:00", marking=MARK)


def test_retrieval_exposes_structure_and_transfer_risks(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    acme_status = claims[("LEI:ACMELEI000000000001", "entity_status")]
    other_status = claims[("LEI:OTHERLEI00000000002", "entity_status")]
    acme_event = next(a["activity_id"] for a in ctx.store.records_of("activity")
                      if ACME_OBJECT in a["subject_ids"])
    other_event = next(a["activity_id"] for a in ctx.store.records_of("activity")
                       if ACME_OBJECT not in a["subject_ids"])
    episode = record_episode(
        ctx, title="Borg Verft registry lifecycle",
        summary="registration and standing history of a comparable entity",
        actor_object_ids=(world_object_id("LEI:OTHERLEI00000000002"),),
        event_ids=(other_event,),
        institutional_setting="GLEIF LEI registration regime",
        mechanism="registry lifecycle transition",
        constraints=("single jurisdiction",),
        outcome="registration remained issued",
        outcome_claim_ids=(other_status,),
        claim_ids=(other_status, claims[("LEI:OTHERLEI00000000002", "legal_name")]))
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[acme_status],
                         event_ids=(acme_event,), provenance_kind="RULE")
    analogues = retrieve_analogues(ctx, query_kind="analytic_theme",
                                   query_id=theme["theme_id"])
    assert analogues, "shared event type must retrieve the episode"
    analogue = analogues[0]
    matched_dims = {d["dimension"] for d in analogue["matched"]}
    mismatched_dims = {d["dimension"] for d in analogue["mismatched"]}
    assert "EVENT_TYPE" in matched_dims          # both are lei_registered
    assert "ACTOR_CONFIGURATION" in mismatched_dims  # different entities
    assert analogue["transfer_risks"]
    assert any("not outcome prediction" in r for r in analogue["transfer_risks"])
    assert analogue["authority"] == "SUPPORTED_INFERENCE"
    assert not any("forecast" in key or "probability" in key
                   for key in analogue.keys())
    # Retrieving twice must reuse the one record, not make a second.
    again = retrieve_analogues(ctx, query_kind="analytic_theme",
                               query_id=theme["theme_id"])
    assert len(ctx.store.records_of("historical_analogue")) == 1
    assert again[0]["analogue_id"] == analogue["analogue_id"]


def test_explanation_keeps_outcome_as_history_not_forecast(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    other_status = claims[("LEI:OTHERLEI00000000002", "entity_status")]
    other_event = next(a["activity_id"] for a in ctx.store.records_of("activity")
                       if "OTHER" in str(a["evidence_refs"]) or True)
    record_episode(
        ctx, title="Borg Verft registry lifecycle", summary="s",
        actor_object_ids=(world_object_id("LEI:OTHERLEI00000000002"),),
        event_ids=(other_event,), institutional_setting="GLEIF regime",
        mechanism="registry lifecycle", outcome="remained issued",
        outcome_claim_ids=(other_status,), claim_ids=(other_status,))
    theme = create_theme(ctx, title="Acme registry standing",
                         supporting_claim_ids=[
                             claims[("LEI:ACMELEI000000000001", "entity_status")]],
                         event_ids=(next(a["activity_id"]
                                         for a in ctx.store.records_of("activity")),),
                         provenance_kind="RULE")
    analogues = retrieve_analogues(ctx, query_kind="analytic_theme",
                                   query_id=theme["theme_id"])
    explanation = explain_analogue(ctx.store, analogues[0]["analogue_id"])
    assert explanation["episode_outcome"] == "remained issued"
    assert "not a forecast" in explanation["outcome_caveat"]
    assert explanation["matched"] and explanation["transfer_risks"]
    assert explanation["episode_evidence"]


def test_retrieve_analogues_cites_episode_not_its_title(tmp_path):
    """A retrieved analogue cites a restricted episode by id and never quotes it."""
    secret = "OPERATION MOONLIGHT 2014 covert delisting"
    pipeline, ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, ctx)
    other_status = claims[("LEI:OTHERLEI00000000002", "entity_status")]
    other_event = next(
        activity["activity_id"]
        for activity in ctx.store.records_of("activity")
        if ACME_OBJECT not in activity["subject_ids"]
    )
    restricted_ctx = AnalyticContext(
        store=ctx.store, actor="analyst-a", marking=RESTRICTED_MARK,
        now_fn=ctx.now_fn)
    episode = record_episode(
        restricted_ctx,
        title=secret,
        summary="restricted comparison",
        actor_object_ids=(world_object_id("LEI:OTHERLEI00000000002"),),
        event_ids=(other_event,),
        institutional_setting="GLEIF regime",
        mechanism="registry lifecycle",
        outcome="remained issued",
        outcome_claim_ids=(other_status,),
        claim_ids=(other_status,),
    )
    theme = create_theme(
        ctx,
        title="Acme registry standing",
        supporting_claim_ids=(
            claims[("LEI:ACMELEI000000000001", "entity_status")],),
        event_ids=(next(
            activity["activity_id"]
            for activity in ctx.store.records_of("activity")
            if ACME_OBJECT in activity["subject_ids"]),),
        provenance_kind="RULE",
    )
    analogue = retrieve_analogues(
        ctx, query_kind="analytic_theme", query_id=theme["theme_id"])[0]
    assert not can_view(analogue["marking"], CTX_B)
    transitions = [
        transition for transition in ctx.store.transitions_for(
            analogue["analogue_id"])
        if transition["transition_type"] == "RETRIEVED"
    ]
    assert transitions
    assert secret not in transitions[0]["detail"]
    assert episode["episode_id"] in transitions[0]["detail"]
    assert episode["episode_id"] in transitions[0]["evidence_refs"]
    assert not can_view(transitions[0]["marking"], CTX_B)
