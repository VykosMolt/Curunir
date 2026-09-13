"""Defects found by review, each held shut by a test: unknown claims carry no
weight, model output needs a person's acceptance, a denial never counts as
support, interrupted writes finish on the next run, and nothing silently
overwrites a version someone else wrote."""
from __future__ import annotations

import pytest

from curunir_analytic.basis import compute_basis
from curunir_analytic.contracts import (ImpactEdge, StakeholderPosition,
                                        weakest_authority)
from curunir_analytic.explain import explain_object
from curunir_analytic.impact import (build_path, create_objective,
                                     invalidate_assumption, record_assumption,
                                     refresh_path, review_response_option,
                                     propose_response_option)
from curunir_analytic.narratives import (add_variant, assert_independent_adoption,
                                         create_narrative, derive_propagation,
                                         explain_narrative)
from curunir_analytic.propagate import propagate_semantic_changes
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_analytic.stakeholders import (add_position, assert_influence,
                                           create_assessment, supersede_influence,
                                           supersede_position)
from curunir_analytic.store import AnalyticStore
from curunir_analytic.substrate import DependencyIndex, resolve_candidate
from curunir_analytic.themes import apply_merge, create_theme, resolve_theme
from curunir_operational.store import StoreError
from curunir_semantic.worldmodel import world_object_id

from analytic_support import (MARK, T0, make_analytic, plant_page, seed_acme,
                              statement_page)
from semantic_support import plant_manifestation

pytestmark = pytest.mark.no_db

ACME_OBJECT = world_object_id("LEI:ACMELEI000000000001")


# A claim nobody has is not evidence


def test_f1_phantom_claims_cannot_found_an_object(tmp_path):
    _, ctx = make_analytic(tmp_path, seeded=False)
    basis = compute_basis(ctx.store, ["claim-that-does-not-exist"])
    assert basis.supporting_claim_ids == ()
    assert basis.unresolved_claim_ids == ("claim-that-does-not-exist",)
    assert "no evidentiary weight" in basis.note
    with pytest.raises(ValueError, match="unsupported title"):
        create_theme(ctx, title="Registry instability",
                     supporting_claim_ids=("claim-that-does-not-exist",))


def test_f1_zero_basis_is_loudest_uncertainty(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"],
                                               "phantom-claim"],
                         provenance_kind="RULE")
    assert "phantom-claim" not in theme["basis"]["supporting_claim_ids"]
    assert "phantom-claim" in theme["basis"]["unresolved_claim_ids"]
    explanation = explain_object(ctx.store, "analytic_theme", theme["theme_id"])
    assert any("no known claim" in u for u in explanation["UNCERTAINTY"])


# Model output becomes state only through a person


def test_f2_f3_model_state_requires_accepted_candidate(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    with pytest.raises(ValueError, match="accepted candidate"):
        create_theme(ctx, title="Model theme",
                     supporting_claim_ids=[by_predicate["entity_status"]],
                     provenance_kind="MODEL", inference_id="inf-fabricated")
    with pytest.raises(ValueError, match="not found in the log"):
        create_theme(ctx, title="Model theme",
                     supporting_claim_ids=[by_predicate["entity_status"]],
                     provenance_kind="MODEL", inference_id="inf-fabricated",
                     proposal_id="prop-fabricated")
    # Accepted model output is an inference, never a derivation.
    assist = AnalyticalAssist(
        package=analytical_assist_package("test", "stub", "1.0"),
        infer_fn=lambda task, payload: {"title": "Registry standing risk",
                                        "supporting_claim_ids":
                                        list(payload["claims"])})
    proposed = assist.propose(ctx, task="t", target_kind="analytic_theme",
                              inputs={"claims": [by_predicate["entity_status"]]},
                              input_refs=(by_predicate["entity_status"],))
    resolved = resolve_candidate(ctx, proposed["proposal"]["proposal_id"],
                                 accept=True, actor_id="jan", actor_kind="HUMAN")
    theme = create_theme(ctx, title="Registry standing risk",
                         supporting_claim_ids=resolved["content"]["supporting_claim_ids"],
                         provenance_kind="MODEL",
                         inference_id=resolved["inference_id"],
                         proposal_id=resolved["proposal_id"])
    assert theme["authority"] == "SUPPORTED_INFERENCE"
    proposed_2 = assist.propose(ctx, task="t2", target_kind="analytic_theme",
                                inputs={"claims": [by_predicate["legal_name"]]},
                                input_refs=(by_predicate["legal_name"],))
    rejected = resolve_candidate(ctx, proposed_2["proposal"]["proposal_id"],
                                 accept=False, actor_id="jan", actor_kind="HUMAN")
    with pytest.raises(ValueError, match="REJECTED"):
        create_theme(ctx, title="Rejected theme",
                     supporting_claim_ids=[by_predicate["legal_name"]],
                     provenance_kind="MODEL",
                     inference_id=proposed_2["inference_id"],
                     proposal_id=rejected["proposal_id"])


# A denial contradicts, it does not corroborate


def test_f4_counter_variant_contradicts_instead_of_supporting(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_page(pipeline, url="https://ministerium.example.de/notice",
               body=statement_page("The ministry will suspend the export licence"),
               retrieval_time=T0)
    plant_page(pipeline, url="https://gegenrede.example.org/reply",
               body=statement_page("The ministry denies any plan to suspend the export licence"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    assertion = next(c for value, c in claims.items() if "will suspend" in value)
    denial = next(c for value, c in claims.items() if "denies" in value)
    narrative = create_narrative(ctx, statement=assertion["object_or_value"],
                                 supporting_claim_ids=[assertion["claim_id"]])
    denial_manifestation = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in denial["observation_ids"])
    updated = add_variant(
        ctx, narrative["narrative_id"], relation="COUNTER_NARRATIVE",
        statement=denial["object_or_value"], claim_ids=(denial["claim_id"],),
        manifestation_ids=(denial_manifestation,),
        authority="ANALYST_ASSESSMENT", mechanism="explicit denial",
        provenance_kind="ANALYST")
    assert denial["claim_id"] in updated["basis"]["contradicting_claim_ids"]
    assert denial["claim_id"] not in updated["basis"]["supporting_claim_ids"]
    assert len(updated["basis"]["origin_families"]) == 1  # still one family
    explanation = explain_narrative(ctx.store, narrative["narrative_id"])
    assert not any("denies" in why for why in explanation["why"])
    assert any("denies" in against for against in explanation["against"])
    # An assertion and its denial share no wording, so neither copies the other.
    edges = derive_propagation(ctx, narrative["narrative_id"])
    assert all(e["relation"] != "LIKELY_DERIVATIVE" for e in edges)


# Revising an adoption keeps the earlier reading


def test_f5_human_adoption_revision_is_versioned(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://nyhet.example.no/a",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://industriwatch.example.org/b",
               body=statement_page(statement),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claim_ids = [c["claim_id"] for c in ctx.store.current_claims().values()
                 if c["predicate"] == "statement"]
    narrative = create_narrative(ctx, statement=statement,
                                 supporting_claim_ids=claim_ids)
    edges = derive_propagation(ctx, narrative["narrative_id"])
    derived = next(e for e in edges if e["relation"] == "LIKELY_DERIVATIVE")
    revised = assert_independent_adoption(
        ctx, narrative["narrative_id"],
        from_manifestation_id=derived["from_manifestation_id"],
        to_manifestation_id=derived["to_manifestation_id"],
        mechanism="site B cites its own on-the-ground reporting",
        actor_id="jan", actor_kind="HUMAN")
    assert revised["version"] == 2
    versions = ctx.store.analytic_versions("propagation_edge", derived["edge_id"])
    assert [v["relation"] for v in versions] == ["LIKELY_DERIVATIVE",
                                                 "INDEPENDENT_ADOPTION"]
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(derived["edge_id"])}
    assert "REVISED" in kinds


# An interrupted marking pass finishes on the next run


def _impact_state(pipeline, ctx):
    by_predicate = seed_acme(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1",
                                 statement="Track Acme standing",
                                 depends_on=(("object", ACME_OBJECT),))
    assumption = record_assumption(ctx, statement="Supplier X remains available",
                                   supporting_claim_ids=(by_predicate["entity_status"],))
    edge = ImpactEdge(
        edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
        note="", basis_ids=(by_predicate["entity_status"],),
        assumption_ids=(assumption["assumption_id"],))
    path = build_path(ctx, objective_id=objective["objective_id"],
                      summary="s", edges=(edge,))
    return by_predicate, objective, assumption, path


def test_f6_interrupted_invalidation_completes_on_rerun(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate, objective, assumption, path = _impact_state(pipeline, ctx)
    real_append = ctx.store.append
    state = {"crashed": False}

    def crashing_append(event_type, record, **kwargs):
        data = record if isinstance(record, dict) else record.to_record()
        if not state["crashed"] and event_type == "ANALYTIC_TRANSITION_RECORDED" \
                and data.get("transition_type") == "INVALIDATED":
            state["crashed"] = True
            raise OSError("crash after the assumption version landed")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = crashing_append
    with pytest.raises(OSError):
        invalidate_assumption(ctx, assumption["assumption_id"],
                              contradicting_claim_ids=(by_predicate["registration_status"],),
                              caused_by="chg-1", reason="supplier ceased operations")
    ctx.store.append = real_append
    # Half done: the assumption is invalid but nothing downstream knows.
    assert ctx.store.current_assumptions()[assumption["assumption_id"]]["status"] \
        == "INVALIDATED"
    assert ctx.store.current_impact_paths()[path["path_id"]]["status"] == "ASSESSED"
    invalidate_assumption(ctx, assumption["assumption_id"],
                          contradicting_claim_ids=(by_predicate["registration_status"],),
                          caused_by="chg-1", reason="supplier ceased operations")
    assert ctx.store.current_impact_paths()[path["path_id"]]["status"] == "STALE"
    assert ctx.store.current_objectives()[objective["objective_id"]]["status"] \
        == "EXPOSED"
    kinds = {t["transition_type"] for t in ctx.store.transitions_for(path["path_id"])}
    assert "ASSUMPTION_INVALIDATED" in kinds
    versions = ctx.store.analytic_versions("analytic_assumption",
                                           assumption["assumption_id"])
    assert [v["status"] for v in versions] == ["HELD", "INVALIDATED"]


def test_f7_interrupted_stale_refresh_completes_on_rerun(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate, objective, assumption, path = _impact_state(pipeline, ctx)
    from curunir_semantic.contracts import ClaimStateRecord
    state_record = ClaimStateRecord(
        state_id="st-1", claim_id=by_predicate["entity_status"], state="RETRACTED",
        reason="source retraction", caused_by="chg-2", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=MARK)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", state_record,
                     recorded_time=state_record.recorded_time, actor="t")
    real_append = ctx.store.append
    state = {"crashed": False}

    def crashing_append(event_type, record, **kwargs):
        data = record if isinstance(record, dict) else record.to_record()
        if not state["crashed"] and event_type == "ANALYTIC_TRANSITION_RECORDED" \
                and data.get("transition_type") == "STALE":
            state["crashed"] = True
            raise OSError("crash after the STALE version landed")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = crashing_append
    with pytest.raises(OSError):
        refresh_path(ctx, path["path_id"], caused_by="chg-2")
    ctx.store.append = real_append
    assert ctx.store.current_impact_paths()[path["path_id"]]["status"] == "STALE"
    assert ctx.store.current_objectives()[objective["objective_id"]]["status"] \
        == "ACTIVE"  # the exposure was lost in the crash
    refresh_path(ctx, path["path_id"], caused_by="chg-2")
    kinds = {t["transition_type"] for t in ctx.store.transitions_for(path["path_id"])}
    assert "STALE" in kinds
    assert ctx.store.current_objectives()[objective["objective_id"]]["status"] \
        == "EXPOSED"


# An orphaned variant is adopted, not left behind


def test_f8_orphaned_variant_reconciles_on_rerun(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    statement = "Acme Industri plans to close its Oslo plant this year"
    plant_page(pipeline, url="https://nyhet.example.no/a",
               body=statement_page(statement), retrieval_time=T0)
    plant_page(pipeline, url="https://annen.example.se/b",
               body=statement_page("Acme Industri considers closing the Oslo plant"),
               retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    claims = {c["object_or_value"]: c for c in ctx.store.current_claims().values()
              if c["predicate"] == "statement"}
    core = next(c for value, c in claims.items() if "plans to close" in value)
    softer = next(c for value, c in claims.items() if "considers" in value)
    narrative = create_narrative(ctx, statement=statement,
                                 supporting_claim_ids=[core["claim_id"]])
    manifestation_id = next(
        o["manifestation_id"] for o in ctx.store.records_of("semantic_observation")
        if o["observation_id"] in softer["observation_ids"])
    variant_kwargs = dict(
        relation="CERTAINTY_SHIFT", statement=softer["object_or_value"],
        claim_ids=(softer["claim_id"],), manifestation_ids=(manifestation_id,),
        authority="ANALYST_ASSESSMENT", mechanism="'plans' weakened to 'considers'",
        provenance_kind="ANALYST")
    real_append = ctx.store.append
    state = {"crashed": False}

    def crashing_append(event_type, record, **kwargs):
        if not state["crashed"] and event_type == "ANALYTIC_NARRATIVE_RECORDED":
            state["crashed"] = True
            raise OSError("crash after the variant record landed")
        return real_append(event_type, record, **kwargs)

    ctx.store.append = crashing_append
    with pytest.raises(OSError):
        add_variant(ctx, narrative["narrative_id"], **variant_kwargs)
    ctx.store.append = real_append
    # Half done: the variant exists but the narrative does not know it.
    assert len(ctx.store.variants_for_narrative(narrative["narrative_id"])) == 1
    assert ctx.store.current_narratives()[narrative["narrative_id"]]["variant_ids"] == []
    updated = add_variant(ctx, narrative["narrative_id"], **variant_kwargs)
    assert len(updated["variant_ids"]) == 1
    assert softer["claim_id"] in updated["basis"]["supporting_claim_ids"]
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(narrative["narrative_id"])}
    assert "VARIANT_ADDED" in kinds


# Running a decision twice decides it once


def test_f9_merge_resolve_supersede_review_rerun_safe(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    left = create_theme(ctx, title="Acme standing",
                        supporting_claim_ids=[by_predicate["entity_status"]],
                        provenance_kind="RULE")
    right = create_theme(ctx, title="Acme naming",
                         supporting_claim_ids=[by_predicate["legal_name"]],
                         provenance_kind="RULE")
    apply_merge(ctx, left["theme_id"], right["theme_id"], actor_id="jan",
                actor_kind="HUMAN", rationale="same issue")
    apply_merge(ctx, left["theme_id"], right["theme_id"], actor_id="jan",
                actor_kind="HUMAN", rationale="same issue")
    survivor = ctx.store.current_themes()[left["theme_id"]]
    assert [tuple(p) for p in survivor["lineage"]].count(
        ("MERGED_FROM", right["theme_id"])) == 1
    assert len(ctx.store.analytic_versions("analytic_theme", left["theme_id"])) == 2
    resolve_theme(ctx, left["theme_id"], actor_id="jan", actor_kind="HUMAN", note="done")
    resolve_theme(ctx, left["theme_id"], actor_id="jan", actor_kind="HUMAN", note="done")
    versions = ctx.store.analytic_versions("analytic_theme", left["theme_id"])
    assert [v["status"] for v in versions].count("RESOLVED") == 1
    influence = assert_influence(
        ctx, source_object_id=ACME_OBJECT,
        target_object_id=world_object_id("LEI:OTHERLEI00000000002"),
        kind="LIKELY_INFLUENCES", mechanism="coordination",
        authority="SUPPORTED_INFERENCE", claim_ids=(by_predicate["entity_status"],))
    supersede_influence(ctx, influence["influence_id"], reason="ended", caused_by="c")
    supersede_influence(ctx, influence["influence_id"], reason="ended", caused_by="c")
    influence_versions = ctx.store.analytic_versions(
        "influence_assertion", influence["influence_id"])
    assert [v["status"] for v in influence_versions] == ["ACTIVE", "SUPERSEDED"]
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="",
                      basis_ids=(by_predicate["entity_status"],), assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                      edges=(edge,))
    option = propose_response_option(ctx, objective_id=objective["objective_id"],
                                     path_id=path["path_id"], description="d")
    review_response_option(ctx, option["option_id"], accept=True, actor_id="jan",
                           actor_kind="HUMAN", note="go")
    review_response_option(ctx, option["option_id"], accept=True, actor_id="jan",
                           actor_kind="HUMAN", note="go")
    option_versions = ctx.store.analytic_versions("response_option",
                                                  option["option_id"])
    assert [v["status"] for v in option_versions] == ["PROPOSED", "ACCEPTED"]
    with pytest.raises(ValueError, match="not re-decided"):
        review_response_option(ctx, option["option_id"], accept=False,
                               actor_id="jan", actor_kind="HUMAN", note="undo")


# A second writer on a stale view is refused


def test_f10_concurrent_version_write_raises(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    from curunir_analytic.substrate import AnalyticContext
    store_2 = AnalyticStore(tmp_path / "store")
    ctx_2 = AnalyticContext(store=store_2, actor="w2", marking=MARK,
                            now_fn=ctx.now_fn)
    from curunir_analytic.themes import update_membership
    update_membership(ctx, theme["theme_id"],
                      add_supporting=[by_predicate["legal_name"]],
                      caused_by="w1", rationale="writer 1 folds a claim")
    # Writer 2 worked from the older view, so its write must fail rather than
    # quietly bury writer 1's.
    with pytest.raises(StoreError, match="next version"):
        update_membership(ctx_2, theme["theme_id"],
                          add_supporting=[by_predicate["jurisdiction"]],
                          caused_by="w2", rationale="writer 2 folds a different claim")


# The dependency index reaches everything that depends


def test_f11_assumption_objective_links_reach_objectives(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1",
                                 statement="Track Acme standing",
                                 depends_on=(("object", ACME_OBJECT),))
    # Only the assumption names the objective; the link must still be found
    # from the other side.
    assumption = record_assumption(ctx, statement="Acme remains active",
                                   supporting_claim_ids=(by_predicate["entity_status"],),
                                   objective_ids=(objective["objective_id"],))
    index = DependencyIndex(ctx.store)
    affected = index.affected_by(assumption_ids=[assumption["assumption_id"]])
    assert ("mission_objective", objective["objective_id"]) in affected
    invalidate_assumption(ctx, assumption["assumption_id"],
                          contradicting_claim_ids=(by_predicate["registration_status"],),
                          caused_by="chg", reason="contradicted")
    assert ctx.store.current_objectives()[objective["objective_id"]]["status"] \
        == "EXPOSED"


def test_f11_claim_dependencies_and_options_and_episodes_indexed(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    claim_id = by_predicate["entity_status"]
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("claim", claim_id),))
    edge = ImpactEdge(edge_id="e1", from_kind="object", from_id=ACME_OBJECT,
                      to_kind="mission_objective", to_id=objective["objective_id"],
                      edge_kind="DEPENDENCY", effect_order="DIRECT",
                      authority="DERIVED", note="", basis_ids=(claim_id,),
                      assumption_ids=())
    path = build_path(ctx, objective_id=objective["objective_id"], summary="s",
                      edges=(edge,))
    option = propose_response_option(ctx, objective_id=objective["objective_id"],
                                     path_id=path["path_id"], description="d",
                                     claim_ids=(claim_id,))
    from curunir_analytic.analogues import record_episode
    event_id = next(a["activity_id"] for a in ctx.store.records_of("activity"))
    episode = record_episode(ctx, title="ep", summary="s",
                             actor_object_ids=(ACME_OBJECT,), event_ids=(event_id,),
                             institutional_setting="x", mechanism="m",
                             claim_ids=(claim_id,))
    index = DependencyIndex(ctx.store)
    affected = index.affected_by(claim_ids=[claim_id])
    assert ("mission_objective", objective["objective_id"]) in affected
    assert ("response_option", option["option_id"]) in affected
    assert ("historical_episode", episode["episode_id"]) in affected
    assert ("historical_episode", episode["episode_id"]) \
        in index.affected_by(object_ids=[ACME_OBJECT])
    assert ("historical_episode", episode["episode_id"]) \
        in index.affected_by(activity_ids=[event_id])
    # A dependency the index cannot follow is refused when the objective is made.
    with pytest.raises(ValueError, match="not trackable"):
        create_objective(ctx, mission_context="m1", statement="bad",
                         depends_on=(("mystery", "x"),))


# Identity doubt raised later still reaches the assessment


def test_f12_late_identity_ambiguity_surfaces(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="THEME",
        context_id=theme["theme_id"], role_in_context="subject",
        supporting_claim_ids=[by_predicate["entity_status"]])
    assert assessment["identity_caveats"] == []
    # A second scheme names the same entity, after the assessment was made.
    from test_analytic_stakeholders import WIKIDATA_ACME
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q77777",
                        body=WIKIDATA_ACME, media_type="application/json",
                        retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()
    ambiguities = [r for r in ctx.store.open_review_items()
                   if r["kind"] == "IDENTITY_AMBIGUITY"]
    assert ambiguities
    outcomes = propagate_semantic_changes(ctx)
    refreshed = ctx.store.current_stakeholder_assessments()[
        assessment["assessment_id"]]
    assert refreshed["identity_caveats"]
    kinds = {t["transition_type"]
             for t in ctx.store.transitions_for(assessment["assessment_id"])}
    assert "IDENTITY_CAVEAT_CHANGED" in kinds
    explanation = explain_object(ctx.store, "stakeholder_assessment",
                                 assessment["assessment_id"])
    assert any("identity ambiguity" in u for u in explanation["UNCERTAINTY"])


# When we fetched a page is not when it became true


def test_f13_retrieval_time_does_not_become_valid_time(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_page(pipeline, url="https://side.example.no/x",
               body=statement_page("A statement page with no stated dates at all"),
               retrieval_time=T0)
    pipeline.process_new_evidence()
    claim = next(c for c in ctx.store.current_claims().values()
                 if c["predicate"] == "statement")
    assert claim["valid_from"] is None
    theme = create_theme(ctx, title="Undated theme",
                         supporting_claim_ids=[claim["claim_id"]],
                         provenance_kind="RULE")
    assert theme["valid_from"] is None, \
        "the moment we fetched a page is not when its content became true"
    assert theme["basis"]["earliest_time"]  # when we learned it is still known


# A backlog of changes is named in full


def test_f14_backlog_changes_all_named(tmp_path):
    from curunir_semantic.changes import interpret_change
    from curunir_semantic.worldmodel import IntegrationContext
    from analytic_support import GLEIF_ACME_SUSPENDED
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    theme = create_theme(ctx, title="Acme standing",
                         supporting_claim_ids=[by_predicate["entity_status"]],
                         provenance_kind="RULE")
    v1 = next(m for m in ctx.store.records_of("fabric_manifestation"))
    v2 = plant_manifestation(pipeline, source_id="gleif",
                             native_id="lei/ACMELEI000000000001",
                             body=GLEIF_ACME_SUSPENDED,
                             media_type="application/json",
                             retrieval_time="2026-08-17T15:00:00+00:00",
                             prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()
    body_3 = GLEIF_ACME_SUSPENDED.replace(b'"INACTIVE"', b'"RETIRED"')
    v3 = plant_manifestation(pipeline, source_id="gleif",
                             native_id="lei/ACMELEI000000000001",
                             body=body_3, media_type="application/json",
                             retrieval_time="2026-08-17T16:00:00+00:00",
                             prior_manifestation_id=v2["manifestation_id"])
    pipeline.process_new_evidence()
    integration = IntegrationContext(store=ctx.store, actor="t", marking=MARK,
                                     now_fn=ctx.now_fn)
    changes_1 = interpret_change(integration, v1["manifestation_id"],
                                 v2["manifestation_id"])
    changes_2 = interpret_change(integration, v2["manifestation_id"],
                                 v3["manifestation_id"])
    outcomes = propagate_semantic_changes(ctx)
    theme_outcome = next(o for o in outcomes
                         if o.get("affected_object") == ("analytic_theme",
                                                         theme["theme_id"]))
    named = set(theme_outcome["change_ids"])
    relevant_1 = {c["change_id"] for c in changes_1
                  if theme["basis"]["supporting_claim_ids"][0]
                  in c["affected_claim_ids"]}
    relevant_2 = {c["change_id"] for c in changes_2
                  if theme["basis"]["supporting_claim_ids"][0]
                  in c["affected_claim_ids"]}
    assert relevant_1 and relevant_2
    assert relevant_1 <= named and relevant_2 <= named, \
        "every pending change is named; none is silently absorbed by the first"


# An inferred interest cannot be relabelled as a stated position


def test_f15_interest_cannot_become_public_position(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    assessment = create_assessment(
        ctx, entity_object_id=ACME_OBJECT, context_kind="ISSUE",
        context_id="rule", role_in_context="party",
        supporting_claim_ids=[by_predicate["entity_status"]])
    interest = StakeholderPosition(
        position_id="int-1", kind="INFERRED_INTEREST",
        statement="benefits from barriers", stance="UNRESOLVED",
        authority="SUPPORTED_INFERENCE",
        claim_ids=(by_predicate["entity_status"],), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False, note="")
    add_position(ctx, assessment["assessment_id"], interest, caused_by="t")
    relabel = StakeholderPosition(
        position_id="pub-1", kind="PUBLIC_POSITION",
        statement="benefits from barriers", stance="UNRESOLVED",
        authority="OBSERVED", claim_ids=(by_predicate["entity_status"],),
        relationship_ids=(), valid_from=None, valid_to=None,
        superseded=False, note="")
    with pytest.raises(ValueError, match="inferred[\\s\\S]*interest"):
        supersede_position(ctx, assessment["assessment_id"], "int-1", relabel,
                           caused_by="t", rationale="relabel")
    with pytest.raises(ValueError, match="inferred[\\s\\S]*interest"):
        add_position(ctx, assessment["assessment_id"], relabel, caused_by="t")


# Asserting something again keeps the earlier history


def test_f16_reassert_influence_keeps_history(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    other = world_object_id("LEI:OTHERLEI00000000002")
    influence = assert_influence(
        ctx, source_object_id=ACME_OBJECT, target_object_id=other,
        kind="LIKELY_INFLUENCES", mechanism="coordination",
        authority="SUPPORTED_INFERENCE", claim_ids=(by_predicate["entity_status"],))
    supersede_influence(ctx, influence["influence_id"], reason="ended", caused_by="c1")
    reasserted = assert_influence(
        ctx, source_object_id=ACME_OBJECT, target_object_id=other,
        kind="LIKELY_INFLUENCES", mechanism="coordination resumed",
        authority="SUPPORTED_INFERENCE", claim_ids=(by_predicate["entity_status"],))
    assert reasserted["version"] == 3
    assert "ASSERTED:ANALYST" in reasserted["history"][0] \
        or reasserted["history"][0].startswith("ASSERTED")
    assert any(h.startswith("SUPERSEDED") for h in reasserted["history"])
    asserted_transitions = [t for t in ctx.store.transitions_for(influence["influence_id"])
                            if t["transition_type"] == "ASSERTED"]
    assert len(asserted_transitions) == 2, \
        "the reassertion is its own recorded transition"


# Every exposed path records its own reason


def test_f18_multiple_paths_each_record_exposure_cause(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    by_predicate = seed_acme(pipeline, ctx)
    objective = create_objective(ctx, mission_context="m1", statement="o",
                                 depends_on=(("object", ACME_OBJECT),))
    assumption = record_assumption(ctx, statement="a",
                                   supporting_claim_ids=(by_predicate["entity_status"],))
    paths = []
    for label, claim in (("p1", "entity_status"), ("p2", "legal_name")):
        edge = ImpactEdge(
            edge_id=f"e-{label}", from_kind="object", from_id=ACME_OBJECT,
            to_kind="mission_objective", to_id=objective["objective_id"],
            edge_kind="DEPENDENCY", effect_order="DIRECT", authority="DERIVED",
            note="", basis_ids=(by_predicate[claim],),
            assumption_ids=(assumption["assumption_id"],))
        paths.append(build_path(ctx, objective_id=objective["objective_id"],
                                summary=label, edges=(edge,)))
    invalidate_assumption(ctx, assumption["assumption_id"],
                          contradicting_claim_ids=(by_predicate["registration_status"],),
                          caused_by="chg", reason="contradicted")
    exposures = [t for t in ctx.store.transitions_for(objective["objective_id"])
                 if t["transition_type"] == "EXPOSED"]
    assert len(exposures) >= 2, "each dependent path's exposure reason is recorded"


# Edges the contract refuses to build


def test_f19_f21_edge_typing_and_empty_authority():
    with pytest.raises(ValueError, match="self-loop"):
        ImpactEdge(edge_id="e", from_kind="object", from_id="a",
                   to_kind="object", to_id="a", edge_kind="INFERENCE",
                   effect_order="POTENTIAL", authority="SUPPORTED_INFERENCE",
                   note="n", basis_ids=(), assumption_ids=())
    with pytest.raises(ValueError, match="endpoint kind"):
        ImpactEdge(edge_id="e", from_kind="mystery", from_id="a",
                   to_kind="object", to_id="b", edge_kind="DEPENDENCY",
                   effect_order="DIRECT", authority="DERIVED", note="",
                   basis_ids=("c",), assumption_ids=())
    with pytest.raises(ValueError, match="empty set"):
        weakest_authority([])
    # A machine inference never outranks a person's judgment.
    assert weakest_authority(["ANALYST_ASSESSMENT", "SUPPORTED_INFERENCE"]) \
        == "SUPPORTED_INFERENCE"
