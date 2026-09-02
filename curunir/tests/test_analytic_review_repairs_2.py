"""More defects held shut: a model acceptance is bound to one object and spent
once, a re-call cannot smuggle extra claims in, every merge and subtheme is
recorded, and an imported event cannot overwrite a local version."""
from __future__ import annotations

import pytest

from curunir_analytic.basis import basis_from_record
from curunir_analytic.contracts import (ImpactEdge, NarrativeVariant,
                                        StakeholderPosition)
from curunir_analytic.impact import (build_path, create_objective,
                                     record_assumption, refresh_path)
from curunir_analytic.narratives import add_variant, create_narrative
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.stakeholders import (assert_influence, create_assessment,
                                           add_position)
from curunir_analytic.store import AnalyticStore
from curunir_analytic.substrate import DependencyIndex, resolve_candidate
from curunir_analytic.themes import apply_merge, create_theme
from curunir_operational.store import StoreError
from curunir_semantic.contracts import ClaimStateRecord
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (GLEIF_ACME, MARK, T0, make_analytic, plant_page,
                              seed_acme, statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")
NOW = "2026-08-17T12:00:00+00:00"


def _accepted_theme_proposal(ctx, claim_id, title="Registry standing risk"):
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {"title": title,
                                        "supporting_claim_ids":
                                        list(payload["claims"])})
    proposed = assist.propose(ctx, task="t", target_kind="analytic_theme",
                              inputs={"claims": [claim_id]},
                              input_refs=(claim_id,))
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    return resolved


def test_n1_fold_path_is_not_a_model_side_door(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="ANALYST")
    with pytest.raises(ValueError, match="different proposal"):
        create_theme(ctx, title="Acme standing",
                     supporting_claim_ids=[by_predicate["legal_name"]],
                     provenance_kind="MODEL", inference_id="inf-fabricated",
                     proposal_id="")
    # An analyst may fold a claim in, and the record says who did.
    folded = create_theme(ctx, title="Acme standing",
                          supporting_claim_ids=[by_predicate["legal_name"]],
                          provenance_kind="ANALYST")
    assert by_predicate["legal_name"] in folded["basis"]["supporting_claim_ids"]
    assert "provenance ANALYST" in folded["change_reason"]
    # The narrative path is gated the same way.
    plant_page(pipeline, url="https://n.example.no/a",
               body=statement_page("Acme Industri plans to close its Oslo plant"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    statement_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                           if c["predicate"] == "statement")
    narrative = create_narrative(ctx, statement="Acme Industri plans to close its Oslo plant",
                                 supporting_claim_ids=[statement_claim])
    with pytest.raises(ValueError, match="different proposal"):
        create_narrative(ctx, statement="Acme Industri plans to close its Oslo plant",
                         supporting_claim_ids=[by_predicate["legal_name"]],
                         provenance_kind="MODEL", inference_id="x", proposal_id="y")


def test_n7_acceptance_is_bound_and_consumed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    resolved = _accepted_theme_proposal(ctx, by_predicate["entity_status"])
    # A different title than the one accepted.
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        create_theme(ctx, title="An entirely different theme nobody accepted",
                     supporting_claim_ids=[by_predicate["entity_status"]],
                     provenance_kind="MODEL",
                     inference_id=resolved["inference_id"],
                     proposal_id=resolved["proposal_id"])
    # A different kind of object than the one accepted.
    with pytest.raises(ValueError, match="not transferable across kinds"):
        create_narrative(ctx, statement="Registry standing risk statement here",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    # Exactly what was accepted goes through.
    theme = create_theme(ctx, title="Registry standing risk",
                         supporting_claim_ids=resolved["content"]["supporting_claim_ids"],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    assert theme["authority"] == "SUPPORTED_INFERENCE"
    # One acceptance cannot build two different paths.
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))

    def _edge(basis_claim):
        return ImpactEdge(
            edge_id=f"e-{basis_claim[:8]}", from_kind="object", from_id=ACME_OBJECT,
            to_kind="mission_objective", to_id=objective["objective_id"],
            edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
            note="", basis_ids=(basis_claim,), assumption_ids=())

    from curunir_analytic.impact import edge_chain_fingerprint
    accepted_edge = _edge(by_predicate["entity_status"])
    accepted_chain = edge_chain_fingerprint((accepted_edge,))
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {"objective_id": payload["objective_id"],
                                        "summary": "exposure via dependency",
                                        "edge_chain": accepted_chain})
    proposed = assist.propose(ctx, task="t3", target_kind="impact_path",
                              inputs={"objective_id": objective["objective_id"]},
                              input_refs=())
    resolved_2 = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                   accept=True, actor_id="jan", actor_kind="HUMAN")
    with pytest.raises(ValueError, match="differs from what the human accepted"):
        build_path(ctx, objective_id=objective["objective_id"],
                   summary="exposure via dependency",
                   edges=(_edge(by_predicate["legal_name"]),),
                   provenance_kind="MODEL",
                   inference_id=resolved_2["inference_id"],
                   proposal_id=resolved_2["proposal_id"])
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="exposure via dependency",
                      edges=(accepted_edge,),
                      provenance_kind="MODEL",
                      inference_id=resolved_2["inference_id"],
                      proposal_id=resolved_2["proposal_id"])
    # Repeating the same call after a crash completes the same object.
    again = build_path(ctx, objective_id=objective["objective_id"],
                       summary="exposure via dependency",
                       edges=(accepted_edge,),
                       provenance_kind="MODEL",
                       inference_id=resolved_2["inference_id"],
                       proposal_id=resolved_2["proposal_id"])
    assert again["version"] == path["version"]


def test_n2_variant_recall_cannot_smuggle_claims(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://a.example.no/x",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://b.example.se/y",
               body=statement_page("Acme Industri considers closing the Oslo plant"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    plant_page(pipeline, url="https://c.example.dk/z",
               body=statement_page("Harbour fees will rise sharply next quarter"),
               retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    core = next(c for value, c in claims.items() if "plans to close" in value)
    softer = next(c for value, c in claims.items() if "considers" in value)
    unrelated = next(c for value, c in claims.items() if "Harbour" in value)
    narrative = create_narrative(ctx, statement=statement,
                                 supporting_claim_ids=[core["claim_id"]])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in softer["observation_ids"])
    kwargs = dict(relation="CERTAINTY_SHIFT", statement=softer["object_or_value"],
                  claim_ids=(softer["claim_id"],),
                  manifestation_ids=(manifestation_id,),
                  authority="ANALYST_ASSESSMENT", mechanism="weakened certainty",
                  provenance_kind="ANALYST")
    add_variant(ctx, narrative["narrative_id"], **kwargs)
    before = ctx.store.current_narratives()[narrative["narrative_id"]]
    # An extra claim slipped into the repeat call is refused, not folded in.
    with pytest.raises(ValueError, match="different claim_ids"):
        add_variant(ctx, narrative["narrative_id"],
                    **{**kwargs, "claim_ids": (softer["claim_id"],
                                               unrelated["claim_id"])})
    # The contract is checked on a repeat call too.
    with pytest.raises(ValueError, match="manifestations"):
        add_variant(ctx, narrative["narrative_id"],
                    **{**kwargs, "manifestation_ids": ()})
    after = ctx.store.current_narratives()[narrative["narrative_id"]]
    assert after["basis"]["supporting_claim_ids"] \
        == before["basis"]["supporting_claim_ids"]
    assert unrelated["claim_id"] not in after["basis"]["supporting_claim_ids"]
    add_variant(ctx, narrative["narrative_id"], **kwargs)


def test_n3_n4_subthemes_and_merges_each_recorded(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    parent = create_theme(ctx, title="Acme affairs",
                          supporting_claim_ids=[by_predicate["entity_status"]],
                          provenance_kind="RULE")
    create_theme(ctx, title="Sub one", supporting_claim_ids=[by_predicate["legal_name"]],
                 parent_theme_id=parent["theme_id"], provenance_kind="RULE")
    create_theme(ctx, title="Sub two",
                 supporting_claim_ids=[by_predicate["jurisdiction"]],
                 parent_theme_id=parent["theme_id"], provenance_kind="RULE")
    subthemes = [t for t in ctx.store.transitions_for(parent["theme_id"])
                 if t["transition_type"] == "SUBTHEME_EMERGED"]
    assert len(subthemes) == 2, "every subtheme's emergence is recorded"
    # Two merges into the same survivor must leave two records, not one.
    a = create_theme(ctx, title="A", supporting_claim_ids=[by_predicate["entity_status"]],
                     provenance_kind="RULE")
    b = create_theme(ctx, title="B", supporting_claim_ids=[by_predicate["legal_name"]],
                     provenance_kind="RULE")
    c = create_theme(ctx, title="C",
                     supporting_claim_ids=[by_predicate["jurisdiction"]],
                     provenance_kind="RULE")
    apply_merge(ctx, a["theme_id"], b["theme_id"], actor_id="jan",
                actor_kind="HUMAN", rationale="same")
    apply_merge(ctx, a["theme_id"], c["theme_id"], actor_id="jan",
                actor_kind="HUMAN", rationale="same")
    merged = [t for t in ctx.store.transitions_for(a["theme_id"])
              if t["transition_type"] == "MERGED"]
    assert len(merged) == 2, "each merge is its own recorded transition"


def test_n5_unrelated_change_does_not_refire_stale_finding(tmp_path):
    from analytic_support import GLEIF_ACME_SUSPENDED
    from curunir_semantic.changes import interpret_change
    from curunir_semantic.worldmodel import IntegrationContext
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("claim", by_predicate["entity_status"]),))
    edge = ImpactEdge(edge_id="e1", from_kind="claim",
                      from_id=by_predicate["entity_status"],
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                      edges=(edge,))
    state = ClaimStateRecord(
        state_id="st-1", claim_id=by_predicate["entity_status"], state="RETRACTED",
        reason="retraction", caused_by="chg-0", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    refresh_path(ctx, path["path_id"], caused_by="first")
    stale_before = [t for t in ctx.store.transitions_for(path["path_id"])
                    if t["transition_type"] == "STALE"]
    alerts_before = len([a for a in ctx.store.records_of("alert")])
    # A change to a different attribute arrives, and propagation runs again.
    v1 = next(m for m in ctx.store.records_of("fabric_manifestation"))
    v2 = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
        body=GLEIF_ACME.replace(b'"city": "Oslo"', b'"city": "Bergen"'),
        media_type="application/json",
        retrieval_time="2026-08-17T18:00:00+00:00",
        prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()
    integration = IntegrationContext(store=ctx.store, actor="t", marking=MARK,
                                     now_fn=ctx.now_fn)
    interpret_change(integration, v1["manifestation_id"], v2["manifestation_id"])
    propagate_semantic_changes(ctx)
    stale_after = [t for t in ctx.store.transitions_for(path["path_id"])
                   if t["transition_type"] == "STALE"]
    assert len(stale_after) == len(stale_before), \
        "an unrelated change must not re-fire the same STALE finding"
    analytic_alerts = [a for a in ctx.store.records_of("alert")
                       if a["rule_id"] == "analytic-change"
                       and "STALE on impact_path" in a["trigger"]]
    assert len(analytic_alerts) <= 1


def test_n8_model_cannot_wear_analyst_authority():
    with pytest.raises(ValueError, match="analyst authority"):
        NarrativeVariant(
            variant_id="v1", narrative_id="n1", relation="PARAPHRASE",
            statement="s", claim_ids=("c1",), observation_ids=(),
            manifestation_ids=("m1",), language="",
            authority="ANALYST_ASSESSMENT", mechanism="m",
            provenance_kind="MODEL", inference_id="inf-1",
            recorded_time=NOW, marking=MARK)


def test_n9_influence_basis_degradation_is_recorded(tmp_path):
    from analytic_support import GLEIF_ACME_SUSPENDED
    from curunir_semantic.changes import interpret_change
    from curunir_semantic.worldmodel import IntegrationContext
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    influence = assert_influence(
        ctx, source_object_id=ACME_OBJECT,
        target_object_id=world_object_id("LEI:OTHERLEI00000000002"),
        kind="FORMAL_AUTHORITY_OVER", mechanism="board right",
        authority="ANALYST_ASSESSMENT", claim_ids=(by_predicate["entity_status"],))
    state = ClaimStateRecord(
        state_id="st-1", claim_id=by_predicate["entity_status"], state="RETRACTED",
        reason="retraction", caused_by="chg-0", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state,
                     recorded_time=state.recorded_time, actor="t")
    v1 = next(m for m in ctx.store.records_of("fabric_manifestation"))
    v2 = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
        body=GLEIF_ACME_SUSPENDED, media_type="application/json",
        retrieval_time="2026-08-17T18:00:00+00:00",
        prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()
    integration = IntegrationContext(store=ctx.store, actor="t", marking=MARK,
                                     now_fn=ctx.now_fn)
    interpret_change(integration, v1["manifestation_id"], v2["manifestation_id"])
    propagate_semantic_changes(ctx)
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(influence["influence_id"])}
    assert "WEAKENED" in kinds, \
        "an influence assertion whose basis degraded is not silently untouched"


def test_n10_attribution_bypasses_closed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    plant_page(pipeline, url="https://tredjepart.example.org/kommentar",
               body=statement_page("Acme is ruining the market says a critic"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    critic_claim = next(c["claim_id"] for c in ctx.store.current_claims().values()
                        if "ruining" in c["object_or_value"])
    hostile = StakeholderPosition(
        position_id="p1", kind="PUBLIC_POSITION",
        statement="Acme is ruining the market", stance="UNRESOLVED",
        authority="OBSERVED", claim_ids=(critic_claim,), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    # Creating an assessment with the position is checked like adding one.
    with pytest.raises(ValueError, match="attributable"):
        create_assessment(ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
                          context_id="market", role_in_context="party",
                          positions=(hostile,),
                          supporting_claim_ids=[by_predicate["entity_status"]])
    # Conflicting with a site does not make its words yours.
    from curunir_operational.contracts import (EvidenceRef, ProvenanceSummary,
                                               RelationshipVersion)
    site_object = world_object_id("URL:https://tredjepart.example.org/kommentar")
    observation = next(iter(ctx.store.records_of("semantic_observation")))
    provenance = ProvenanceSummary(
        mode="EVIDENTIARY", source_ids=("gleif",),
        evidence=(EvidenceRef(
            source_object_id=observation["manifestation_id"],
            document_id=observation["document_id"],
            content_sha256=observation["anchors"][0]["content_sha256"],
            assertion_id=observation["observation_id"],
            evidence_basis_id="b" * 8, identity_status="IDENTITY_UNKNOWN",
            authority_state="AUTHORITY_NOT_ASSESSED", independence_status="UNRESOLVED",
            claim_basis_status="DIRECT_FIELD", review_state="UNREVIEWED",
            mapping_status="EXACT_FIELD_PATH"),))
    conflict = RelationshipVersion(
        relationship_id="rel-conflict", version=1, relation_type="CONFLICTS_WITH",
        source_object_id=ACME_OBJECT, target_object_id=site_object,
        valid_from=None, valid_to=None, recorded_time=ctx.now_fn(),
        evidence_refs=(observation["observation_id"],), derivation="EVIDENCE",
        confidence="UNKNOWN", status="ACTIVE", marking=MARK, provenance=provenance,
        rationale="adversarial relation")
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED", conflict,
                     recorded_time=conflict.recorded_time, actor="t")
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="market", role_in_context="party",
        supporting_claim_ids=[by_predicate["entity_status"]])
    with pytest.raises(ValueError, match="attributable"):
        add_position(ctx, assessment["assessment_id"], hostile, caused_by="t")
    # Having once operated the site does not make its words yours now.
    operates_retired = RelationshipVersion(
        relationship_id="rel-op", version=1, relation_type="OPERATES",
        source_object_id=ACME_OBJECT, target_object_id=site_object,
        valid_from=None, valid_to=None, recorded_time=ctx.now_fn(),
        evidence_refs=(observation["observation_id"],), derivation="EVIDENCE",
        confidence="UNKNOWN", status="ACTIVE", marking=MARK, provenance=provenance,
        rationale="was operating")
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED", operates_retired,
                     recorded_time=operates_retired.recorded_time, actor="t")
    retired = RelationshipVersion(
        **{**{k: v for k, v in operates_retired.to_record().items()
              if k != "record_type"},
           "version": 2, "status": "RETIRED",
           "evidence_refs": (observation["observation_id"],),
           "provenance": provenance,
           "recorded_time": ctx.now_fn()})
    ctx.store.append("RELATIONSHIP_VERSION_APPENDED", retired,
                     recorded_time=retired.recorded_time, actor="t")
    with pytest.raises(ValueError, match="attributable"):
        add_position(ctx, assessment["assessment_id"], hostile, caused_by="t")
    # Padding an inferred interest with an extra claim does not make it stated.
    interest = StakeholderPosition(
        position_id="int-1", kind="INFERRED_INTEREST", statement="interest",
        stance="UNRESOLVED", authority="SUPPORTED_INFERENCE",
        claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    add_position(ctx, assessment["assessment_id"], interest, caused_by="t")
    padded = StakeholderPosition(
        position_id="pub-1", kind="PUBLIC_POSITION", statement="interest",
        stance="UNRESOLVED", authority="OBSERVED",
        claim_ids=(by_predicate["entity_status"], critic_claim),
        relationship_ids=(), valid_from=None, valid_to=None,
        superseded=False, note="")
    with pytest.raises(ValueError, match="attributable"):
        add_position(ctx, assessment["assessment_id"], padded, caused_by="t")


def test_n11_second_discovered_role_is_kept(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)

    def _role(position_id, statement, claim):
        return StakeholderPosition(
            position_id=position_id, kind="FORMAL_ROLE", statement=statement,
            stance="UNRESOLVED", authority="OBSERVED",
            claim_ids=(by_predicate[claim],), relationship_ids=(),
            valid_from=None, valid_to=None, superseded=False, note="")

    create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="MISSION",
        context_id="m1", role_in_context="party",
        positions=(_role("role-1", "owns X", "entity_status"),))
    second = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="MISSION",
        context_id="m1", role_in_context="party",
        positions=(_role("role-2", "operates Y", "legal_name"),))
    ids = {p["position_id"] for p in second["positions"]}
    assert ids == {"role-1", "role-2"}, "a second discovered role is folded, not dropped"
    with pytest.raises(ValueError, match="phantom"):
        create_assessment(
            ctx, entity_object_id=ACME_OBJECT, context_kind="MISSION",
            context_id="m2", role_in_context="party",
            positions=(StakeholderPosition(
                position_id="role-3", kind="FORMAL_ROLE", statement="advises Z",
                stance="UNRESOLVED", authority="OBSERVED", claim_ids=(),
                relationship_ids=("rel-that-does-not-exist",),
                valid_from=None, valid_to=None, superseded=False, note=""),))


def test_n12_pre_extension_basis_replays(tmp_path):
    legacy = {"record_type": "analytic_basis",
              "supporting_claim_ids": ["c1"], "contradicting_claim_ids": [],
              "observation_count": 1, "manifestation_count": 1, "source_count": 1,
              "origin_families": ["f1"], "degraded_claim_count": 0,
              "languages": [], "earliest_time": "", "latest_time": "",
              "coverage_notes": [], "note": "t"}
    reconstructed = basis_from_record(legacy)
    assert reconstructed.stated_valid_from == ""
    assert reconstructed.unresolved_claim_ids == ()


def test_n13_import_cannot_shadow_versions(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    # Forge an incoming event that reuses the version already held locally.
    head = ctx.store.head()
    from curunir_operational.store import _entry_hash
    record = {**ctx.store.current_themes()[theme["theme_id"]]}
    envelope_record = dict(record)
    now = ctx.now_fn()
    seq = head["event_count"] + 1
    entry_hash = _entry_hash(seq, "ANALYTIC_THEME_RECORDED", now, "attacker",
                             envelope_record, head["head_hash"])
    envelope = {"seq": seq, "event_id": f"evt-{seq:06d}-{entry_hash[:8]}",
                "event_type": "ANALYTIC_THEME_RECORDED", "recorded_time": now,
                "actor": "attacker", "record": envelope_record,
                "prev_hash": head["head_hash"], "entry_hash": entry_hash}
    with pytest.raises(StoreError, match="may not shadow"):
        ctx.store.append_imported_event(envelope)


def test_n14_claim_endpoints_are_indexed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("claim", by_predicate["entity_status"]),))
    edge = ImpactEdge(edge_id="e1", from_kind="claim",
                      from_id=by_predicate["entity_status"],
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                      edges=(edge,))
    index = DependencyIndex(ctx.store)
    assert ("impact_path", path["path_id"]) \
        in index.affected_by(claim_ids=[by_predicate["entity_status"]])
