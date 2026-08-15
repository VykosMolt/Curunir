"""Regression locks for the third-round adversarial findings (R1–R10)."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import ImpactEdge, StakeholderPosition
from curunir_analytic.impact import build_path, create_objective
from curunir_analytic.narratives import add_variant, create_narrative
from curunir_analytic.propagate import _ALERTABLE
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.stakeholders import assert_influence, create_assessment
from curunir_analytic.substrate import (AnalyticContext, record_candidate,
                                        resolve_candidate)
from curunir_analytic.themes import create_theme
from curunir_operational.contracts import AnalyticalProposal
from curunir_operational.store import StoreError, _entry_hash
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")


def _seed(pipeline, ctx):
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=GLEIF_ACME, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == "LEI:ACMELEI000000000001"}


def _accepted(ctx, target_kind, content_fn, inputs):
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=content_fn)
    proposed = assist.propose(ctx, task="t", target_kind=target_kind,
                              inputs=inputs, input_refs=())
    return resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                             accept=True, actor_id="jan", actor_kind="HUMAN")


def test_r1_model_fold_cannot_exceed_accepted_content(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    resolved = _accepted(
        ctx, "analytic_theme",
        lambda task, payload: {"title": "Acme standing",
                               "supporting_claim_ids": payload["claims"]},
        {"claims": [by_predicate["entity_status"]]})
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    # completing the same materialization is allowed…
    again = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    assert again["version"] == theme["version"]
    # …but folding claims the human never accepted is not
    with pytest.raises(ValueError, match="beyond the[\\s\\S]*accepted"):
        create_theme(ctx, title="Acme standing",
                     supporting_claim_ids=[by_predicate["entity_status"],
                                           by_predicate["legal_name"],
                                           by_predicate["jurisdiction"]],
                     provenance_kind="MODEL",
                     inference_id=resolved["inference_id"],
                     proposal_id=resolved["proposal_id"])
    current = ctx.store.current_themes()[theme["theme_id"]]
    assert by_predicate["legal_name"] not in current["basis"]["supporting_claim_ids"]


def test_r2_candidates_must_declare_binding_fields(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    # a model choosing its own key names cannot produce an acceptable candidate
    with pytest.raises(ValueError, match="binding[\\s\\S]*fields"):
        record_candidate(ctx, target_kind="analytic_theme",
                         content={"proposed_title": "Modest registry note",
                                  "evidence": ["c1"]},
                         inference_id="inf-1")
    with pytest.raises(ValueError, match="binding[\\s\\S]*fields"):
        record_candidate(ctx, target_kind="impact_path",
                         content={"headline": "x"}, inference_id="inf-1")


def test_r3_operational_acceptance_is_not_analytic_currency(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    # an accepted OPERATIONAL proposal (different proposal_type, no
    # target_kind) must be worthless at the analytical gate
    from curunir_operational.canonical import sha256
    from curunir_operational.contracts import InferenceRecord
    now = ctx.now_fn()
    inference = InferenceRecord(
        inference_id="inf-op-1", model_id="rules", model_version="1",
        input_refs=(), input_hash=sha256({}), output={"x": 1},
        output_hash=sha256({"x": 1}), started=now, completed=now,
        parameters={}, errors=(), validation="VALID", marking=MARK)
    ctx.store.append("INFERENCE_RECORDED", inference, recorded_time=now, actor="t")
    operational = AnalyticalProposal(
        proposal_id="prop-op-1", inference_id="inf-op-1",
        proposal_type="RECOMMENDATION_CANDIDATE",
        content={"proposed_action": "inspect"}, status="ACCEPTED",
        recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("ANALYTICAL_PROPOSAL_RECORDED", operational,
                     recorded_time=operational.recorded_time, actor="jan")
    with pytest.raises(ValueError, match="not an analytical[\\s\\S]*candidate"):
        create_theme(ctx, title="Cross-plane theme",
                     supporting_claim_ids=[by_predicate["entity_status"]],
                     provenance_kind="MODEL", inference_id="inf-op-1",
                     proposal_id="prop-op-1")


def test_r4_variant_acceptance_is_consumed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://a.example.no/x",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://b.example.se/y",
               body=statement_page("Acme Industri considers closing the Oslo plant"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    core = next(c for value, c in claims.items() if "plans to close" in value)
    softer = next(c for value, c in claims.items() if "considers" in value)
    narrative_1 = create_narrative(ctx, statement=statement,
                                   supporting_claim_ids=[core["claim_id"]])
    narrative_2 = create_narrative(ctx, statement="A second, distinct narrative "
                                                  "about the same plant",
                                   supporting_claim_ids=[core["claim_id"]])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in softer["observation_ids"])
    resolved = _accepted(
        ctx, "narrative_variant",
        lambda task, payload: {"statement": payload["statement"],
                               "relation": "CERTAINTY_SHIFT",
                               "claim_ids": payload["claims"]},
        {"statement": softer["object_or_value"], "claims": [softer["claim_id"]]})
    kwargs = dict(relation="CERTAINTY_SHIFT", statement=softer["object_or_value"],
                  claim_ids=(softer["claim_id"],),
                  manifestation_ids=(manifestation_id,),
                  authority="SUPPORTED_INFERENCE", mechanism="model judgment",
                  provenance_kind="MODEL",
                  inference_id=resolved["inference_id"],
                  proposal_id=resolved["proposal_id"])
    add_variant(ctx, narrative_1["narrative_id"], **kwargs)
    # the same acceptance cannot mint a second variant on another narrative
    with pytest.raises(ValueError, match="consumed|already"):
        add_variant(ctx, narrative_2["narrative_id"], **kwargs)


def test_r5_model_recall_completes_instead_of_raising(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    other = world_object_id("LEI:OTHERLEI00000000002")
    resolved = _accepted(
        ctx, "influence_assertion",
        lambda task, payload: {"source_object_id": ACME_OBJECT,
                               "target_object_id": other,
                               "kind": "LIKELY_INFLUENCES"},
        {})
    kwargs = dict(source_object_id=ACME_OBJECT, target_object_id=other,
                  kind="LIKELY_INFLUENCES", mechanism="coordination",
                  authority="SUPPORTED_INFERENCE",
                  claim_ids=(by_predicate["entity_status"],),
                  provenance_kind="MODEL",
                  inference_id=resolved["inference_id"],
                  proposal_id=resolved["proposal_id"])
    first = assert_influence(ctx, **kwargs)
    # the crash-recovery re-call completes idempotently — it must NOT raise
    # "acceptance consumed" against the object it itself materialized
    second = assert_influence(ctx, **kwargs)
    assert second["version"] == first["version"]
    # but a different proposal cannot touch it
    with pytest.raises(ValueError, match="different proposal"):
        assert_influence(ctx, **{**kwargs, "proposal_id": "prop-other",
                                 "inference_id": "inf-other"})


def test_r6_position_order_cannot_bypass_guard(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    interest = StakeholderPosition(
        position_id="int-1", kind="INFERRED_INTEREST", statement="interest",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    public = StakeholderPosition(
        position_id="pub-1", kind="PUBLIC_POSITION", statement="interest",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    for ordering in ((interest, public), (public, interest)):
        with pytest.raises(ValueError, match="inferred[\\s\\S]*interest"):
            create_assessment(
                ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
                context_id=f"issue-{ordering[0].position_id}",
                role_in_context="party", positions=ordering,
                supporting_claim_ids=[by_predicate["entity_status"]])


def test_r7_verbatim_requires_text_identity(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://a.example.no/x",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://b.example.se/y",
               body=statement_page("Harbour fees will rise sharply next quarter"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    core = next(c for value, c in claims.items() if "plans to close" in value)
    unrelated = next(c for value, c in claims.items() if "Harbour" in value)
    narrative = create_narrative(ctx, statement=statement,
                                 supporting_claim_ids=[core["claim_id"]])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in unrelated["observation_ids"])
    with pytest.raises(ValueError, match="verbatim"):
        add_variant(ctx, narrative["narrative_id"], relation="VERBATIM",
                    statement=unrelated["object_or_value"],
                    claim_ids=(unrelated["claim_id"],),
                    manifestation_ids=(manifestation_id,),
                    authority="DERIVED", provenance_kind="RULE")
    after = ctx.store.current_narratives()[narrative["narrative_id"]]
    assert unrelated["claim_id"] not in after["basis"]["supporting_claim_ids"]


def test_r8_assessment_fold_is_typed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = _seed(pipeline, ctx)
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="x", role_in_context="party",
        supporting_claim_ids=[by_predicate["entity_status"]])
    create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="x", role_in_context="party",
        supporting_claim_ids=[by_predicate["legal_name"]])
    kinds = [t["transition_type"]
             for t in ctx.store.transitions_for(assessment["assessment_id"])]
    assert "EVIDENCE_UPDATED" in kinds


def test_r9_analogue_degradation_is_alertable():
    assert ("historical_analogue", "EVIDENCE_DEGRADED") in _ALERTABLE


def test_r10_forged_object_version_import_rejected(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _seed(pipeline, ctx)
    version = next(v for v in ctx.store.records_of("object_version"))
    head = ctx.store.head()
    now = ctx.now_fn()
    seq = head["event_count"] + 1
    entry_hash = _entry_hash(seq, "OBJECT_VERSION_APPENDED", now, "attacker",
                             version, head["head_hash"])
    envelope = {"seq": seq, "event_id": f"evt-{seq:06d}-{entry_hash[:8]}",
                "event_type": "OBJECT_VERSION_APPENDED", "recorded_time": now,
                "actor": "attacker", "record": version,
                "prev_hash": head["head_hash"], "entry_hash": entry_hash}
    with pytest.raises(StoreError, match="may not shadow"):
        ctx.store.append_imported_event(envelope)
