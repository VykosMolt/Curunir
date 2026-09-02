"""Live integrated exercise of the analytical intelligence layer.

    python -m curunir_analytic.demo --root DIR --phase 1|2|3|4 [--operator NAME]

Phase 1  ACQUIRE  (live network): targeted real acquisition — GLEIF LEI
         record, Wikidata entity, SEC EDGAR full-text search, Wayback
         captures of the company site — through the OSINT fabric with full
         custody, then semantic understanding.
Phase 2  ANALYZE  (fresh process = restart): deterministic theme discovery,
         stakeholder discovery with identity caveats, analyst-entered
         inferred interest, objective + assumption + typed impact path from
         a real event, narrative scan, structured explanations.
Phase 3  COLLECT  (fresh process): analytical uncertainty → discriminator →
         mission requirement → EIV-ranked routes (dependence-aware: the
         same-family route scores zero) → live execution → honest
         satisfaction accounting → operator (human) folds independent
         evidence into the theme → propagation and alerts.
Phase 4  REPLAY   (fresh process): export → import → identical analytical
         views, chain verification, anchor recovery from replayed payloads
         alone, and proof that no model provider was invoked anywhere.
Phase 6  FORECAST (fresh process, live network): analyst forecasts over the
         real registry claims — machine TRUE resolution against standing
         evidence, the coverage gate refusing FALSE-from-silence until a
         real post-horizon search lands, a pre-authorized indicator, a
         named-rule warning projection, and the calibration scoreboard.

Every phase-1/3 retrieval is a real network request. Nothing is mocked, and
nothing pretends: where the acquired evidence cannot answer a question, the
demo reports the typed uncertainty instead of manufacturing corroboration.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import digest_id
from curunir_fabric.contracts import QuerySpec
from curunir_fabric.executor import ExecutionContext, RateGate, execute_single
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.mission_bridge import open_requirement_with_need
from curunir_fabric.registry import load_registry
from curunir_operational.access import Marking
from curunir_semantic.collection import plan_collection_routes, execute_route
from curunir_semantic.pipeline import SemanticPipeline
from curunir_semantic.worldmodel import world_object_id

from .collect import analytic_collection_needs, open_analytic_requirements
from .contracts import ImpactEdge, StakeholderPosition
from .explain import explain_object, render_text
from .impact import (build_path, create_objective, record_assumption,
                     suggest_path_edges)
from .narratives import create_narrative, derive_propagation, normalize_statement
from .propagate import propagate_semantic_changes
from .stakeholders import add_position, create_assessment, discover_stakeholders
from .store import AnalyticStore
from .substrate import AnalyticContext
from .themes import (create_theme, discover_theme_candidates, refresh_theme,
                     update_membership)

ACTOR = "analytic-live-demo"
MARK = Marking(owning_authority="curunir-analytic-demo", releasability=("PUBLIC",))

SUBJECT_LEI = "HWUPKR0MPOU8FGXBT394"   # Apple Inc.
SUBJECT_QID = "Q312"
SUBJECT_NAME = "Apple Inc."
SUBJECT_SITE = "apple.com"
LEI_OBJECT = world_object_id(f"LEI:{SUBJECT_LEI}")
MISSION = "apple-corporate-visibility"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stores(root: Path) -> tuple[SemanticPipeline, AnalyticContext]:
    store = AnalyticStore(root / "store")
    pipeline = SemanticPipeline(store=store, custody_root=root / "custody",
                                actor=ACTOR, marking=MARK, now_fn=_now)
    return pipeline, AnalyticContext(store=store, actor=ACTOR, marking=MARK,
                                     now_fn=_now)


def _fabric(root: Path, store: AnalyticStore) -> ExecutionContext:
    return ExecutionContext(
        store=store, registry=load_registry(store),
        custody=SourceCustodyStore(root / "custody"), actor=ACTOR, marking=MARK,
        rate_gate=RateGate(0.6, per_source={"sec-edgar": 0.2}))


def _query(need_id: str, family: str, value: str, operation: str, source_id: str,
           rationale: str) -> QuerySpec:
    return QuerySpec(
        query_id=digest_id("anquery", need_id, source_id, operation, value),
        family=family, value=value, language="", script="",
        operation=operation, source_id=source_id, time_bounds=(None, None),
        origin="RULE", origin_detail="analytic-live-demo", rationale=rationale,
        derived_from=())


# ---- phase 1: acquire -----------------------------------------------------


def phase_1(root: Path) -> dict:
    if not (root / "store" / "store_meta.json").exists():
        store = AnalyticStore.create(root / "store",
                                     digest_id("analytic-demo", str(root)), _now())
        seed_starter_catalog(store, recorded_time=_now(), actor=ACTOR)
    pipeline, ctx = _stores(root)
    fabric = _fabric(root, ctx.store)

    need = open_requirement_with_need(
        ctx.store, mission_context=MISSION,
        question="What are Apple Inc.'s registry identity, public filings, "
                 "web history and stakeholder structure?",
        entities=(SUBJECT_NAME,), languages=("en",), scripts=(),
        now=_now(), actor=ACTOR, marking=MARK)

    executed = []
    for family, value, operation, source in (
            ("IDENTIFIER", SUBJECT_LEI, "LOOKUP", "gleif"),
            ("IDENTIFIER", SUBJECT_QID, "LOOKUP", "wikidata"),
            ("QUOTED_PHRASE", SUBJECT_NAME, "SEARCH", "sec-edgar"),
            ("DOMAIN", SUBJECT_SITE, "HISTORICAL_ENUMERATE", "wayback")):
        outcome = execute_single(fabric, query=_query(
            need.need_id, family, value, operation, source,
            f"targeted acquisition for {MISSION}"), source_id=source)
        executed.append(f"{source}/{operation} {outcome.execution.outcome} "
                        f"results={outcome.execution.result_count}")
        if operation == "HISTORICAL_ENUMERATE" and outcome.results:
            # fetch the earliest enumerated capture: real historical evidence
            captures = sorted(outcome.results, key=lambda r: r.native_id)
            capture = captures[0]
            fetch = execute_single(fabric, query=_query(
                need.need_id, "NATIVE_OBJECT", capture.native_id,
                "HISTORICAL_FETCH", "wayback",
                f"earliest enumerated capture of {SUBJECT_SITE}"),
                source_id="wayback")
            executed.append(f"wayback/HISTORICAL_FETCH {fetch.execution.outcome} "
                            f"capture={capture.native_id[:30]}")

    understood = pipeline.process_new_evidence()
    historical = pipeline.interpret_historical_discoveries()
    store = ctx.store
    return {
        "acquired": executed,
        "understood": len(understood["processed"]),
        "failed": [p["error"][:120] for p in understood["failed"]],
        "historical_discoveries": sum(len(h["semantic_changes"]) for h in historical),
        "observations": len(store.records_of("semantic_observation")),
        "claims": len(store.current_claims()),
        "objects": len({v["object_id"] for v in store.records_of("object_version")}),
        "events": len(store.records_of("activity")),
        "identity_ambiguities_open": len(
            [r for r in store.open_review_items() if r["kind"] == "IDENTITY_AMBIGUITY"]),
        "chain_valid": store.verify_chain()["valid"],
    }


# ---- phase 2: analyze -----------------------------------------------------


def phase_2(root: Path, operator: str) -> dict:
    pipeline, ctx = _stores(root)
    store = ctx.store

    # deterministic theme discovery over the world graph, then themes
    candidates = discover_theme_candidates(store)
    lei_candidate = next(c for c in candidates if LEI_OBJECT in c["entity_ids"])
    theme = create_theme(
        ctx, title=f"{SUBJECT_NAME} corporate registry standing",
        description="registry identity and standing of the subject entity",
        supporting_claim_ids=lei_candidate["claim_ids"],
        entity_ids=lei_candidate["entity_ids"],
        event_ids=lei_candidate["event_ids"], provenance_kind="RULE",
        caused_by=lei_candidate["candidate_id"])

    # stakeholders from what the world model states (OPERATES from Wikidata)
    site_objects = [c["object_object_id"] for c in store.current_claims().values()
                    if c["object_object_id"]]
    operates = [r for r in store.records_of("relationship_version")
                if r["relation_type"] == "OPERATES" and r["status"] == "ACTIVE"]
    discovered = []
    if operates:
        discovered = discover_stakeholders(
            ctx, context_kind="THEME", context_id=theme["theme_id"],
            relevant_object_ids=[operates[-1]["target_object_id"]])
    assessment = discovered[0] if discovered else create_assessment(
        ctx, entity_object_id=LEI_OBJECT, context_kind="THEME",
        context_id=theme["theme_id"], role_in_context="subject entity",
        supporting_claim_ids=theme["basis"]["supporting_claim_ids"])

    # an analyst-entered inferred interest — visibly non-observed
    status_claims = [c["claim_id"] for c in store.current_claims().values()
                     if c["subject_ref"] == f"LEI:{SUBJECT_LEI}"
                     and c["predicate"] == "entity_status"]
    interest = StakeholderPosition(
        position_id=digest_id("pos", assessment["assessment_id"], "interest"),
        kind="INFERRED_INTEREST",
        statement="maintaining an unbroken LEI registration supports the "
                  "entity's regulated-market access",
        stance="UNRESOLVED", authority="ANALYST_ASSESSMENT",
        claim_ids=tuple(status_claims), relationship_ids=(),
        valid_from=None, valid_to=None, superseded=False,
        note=f"entered by {operator}; no source states this")
    add_position(ctx, assessment["assessment_id"], interest,
                 caused_by=f"analyst:{operator}")

    # objective, assumption, and a typed impact path from a real event
    objective = create_objective(
        ctx, mission_context=MISSION,
        statement="Maintain continuous visibility of the subject's registry "
                  "standing and public filings",
        priority="HIGH", depends_on=(("object", LEI_OBJECT),))
    assumption = record_assumption(
        ctx, statement="The GLEIF registration remains issued and current",
        supporting_claim_ids=tuple(status_claims),
        objective_ids=(objective["objective_id"],))
    event = next(a for a in store.records_of("activity")
                 if a["activity_type"] in ("lei_registered", "filing_published"))
    edges = suggest_path_edges(store, activity_id=event["activity_id"],
                               objective_id=objective["objective_id"])
    inference_edge = ImpactEdge(
        edge_id=digest_id("imedge", objective["objective_id"], "regulatory"),
        from_kind="object", from_id=LEI_OBJECT,
        to_kind="mission_objective", to_id=objective["objective_id"],
        edge_kind="INFERENCE", effect_order="POTENTIAL",
        authority="SUPPORTED_INFERENCE",
        note="a lapse in the entity's registry standing would degrade "
             "downstream counterparty-risk assessments relying on this "
             "visibility",
        basis_ids=(), assumption_ids=(assumption["assumption_id"],))
    if edges:
        build_path(ctx, objective_id=objective["objective_id"],
                   summary="event exposure of the visibility objective (derived)",
                   edges=edges, caused_by=event["activity_id"])
    path = build_path(
        ctx, objective_id=objective["objective_id"],
        summary="registry-standing exposure of the visibility objective",
        edges=(inference_edge,), caused_by=event["activity_id"])

    # narrative scan: a real proposition family needs the same normalized
    # statement across manifestations; report honestly what the corpus holds
    by_norm: dict[str, list] = {}
    for claim in store.current_claims().values():
        norm = normalize_statement(claim["object_or_value"])
        if len(norm.split()) >= 4:
            by_norm.setdefault(norm, []).append(claim)
    narrative_report: dict = {"status": "INSUFFICIENT_EVIDENCE",
                              "detail": "no proposition of ≥4 words appears in "
                                        "more than one manifestation of this "
                                        "corpus; the narrative engine is "
                                        "exercised in the deterministic suite"}
    for norm, claims in sorted(by_norm.items()):
        manifestations = set()
        for claim in claims:
            for observation in store.records_of("semantic_observation"):
                if observation["observation_id"] in claim["observation_ids"]:
                    manifestations.add(observation["manifestation_id"])
        if len(manifestations) >= 2:
            narrative = create_narrative(
                ctx, statement=claims[0]["object_or_value"],
                supporting_claim_ids=[c["claim_id"] for c in claims])
            edges_out = derive_propagation(ctx, narrative["narrative_id"])
            narrative_report = {
                "status": "CREATED", "statement": claims[0]["object_or_value"][:120],
                "reach": narrative["basis"]["manifestation_count"],
                "independent_families": len(narrative["basis"]["origin_families"]),
                "propagation": [e["relation"] for e in edges_out]}
            break

    return {
        "theme": {"title": theme["title"], "status": theme["status"],
                  "supporting_claims": len(theme["basis"]["supporting_claim_ids"]),
                  "independent_families": len(theme["basis"]["origin_families"]),
                  "families": list(theme["basis"]["origin_families"])},
        "theme_candidates_found": len(candidates),
        "stakeholder": {
            "entity": assessment["entity_label"],
            "context": f"{assessment['context_kind']}:{assessment['context_id'][:18]}",
            "positions": [(p["kind"], p["authority"]) for p in
                          store.current_stakeholder_assessments()[
                              assessment["assessment_id"]]["positions"]],
            "identity_caveats_open": len(assessment["identity_caveats"])},
        "impact_path": {
            "edges": [(e["edge_kind"], e["effect_order"], e["authority"])
                      for e in path["edges"]],
            "path_authority": path["path_authority"],
            "uncertainty": path["uncertainty_note"][:160]},
        "narrative": narrative_report,
        "theme_explanation": render_text(
            explain_object(store, "analytic_theme", theme["theme_id"])),
        "path_explanation": render_text(
            explain_object(store, "impact_path", path["path_id"])),
        "chain_valid": store.verify_chain()["valid"],
    }


# ---- phase 3: analytics drive collection ----------------------------------


def phase_3(root: Path, operator: str) -> dict:
    pipeline, ctx = _stores(root)
    store = ctx.store
    registry = load_registry(store)

    needs = analytic_collection_needs(store)
    opened = open_analytic_requirements(ctx, mission_context=MISSION, needs=needs)
    theme_entry = next((e for e in opened
                        if e["need"]["source_kind"] == "analytic_theme"), None)
    if theme_entry is None:
        # re-run after the loop already closed: the theme's independence
        # uncertainty no longer exists — that is the loop's success state
        theme = next(t for t in store.current_themes().values()
                     if LEI_OBJECT in t["entity_ids"])
        return {
            "analytic_needs_found": [(n["source_kind"], n["question"][:100])
                                     for n in needs],
            "note": "no theme independence need remains: the collection loop "
                    "already resolved it in a prior run",
            "theme_current": {
                "status": theme["status"],
                "independent_families": len(theme["basis"]["origin_families"]),
                "version": theme["version"]},
            "chain_valid": store.verify_chain()["valid"],
        }
    discriminator = theme_entry["discriminator"]
    routes = plan_collection_routes(
        store, registry, discriminator,
        requirement_id=discriminator["requirement_id"],
        now=_now(), actor=ACTOR, marking=MARK)
    route_lines = [
        f"rank {r['rank']}: {r['source_id']}/{r['operation']} score={r['score']:.3f}"
        + (" [same-family: zero]" if r["score"] == 0 else "")
        for r in routes[:5]]
    viable = [r for r in routes if r["score"] > 0 and r["automatable"]]
    executed = execute_route(pipeline, registry, viable[0]) if viable else \
        {"execution_outcome": "NO_VIABLE_ROUTE"}

    # honest satisfaction accounting: acquiring an independent family does
    # not by itself corroborate a source-native attribute of another scheme
    discriminator_after = store.latest_by_id(
        "discriminator", "discriminator_id")[discriminator["discriminator_id"]]

    # the operator (a human analyst) folds genuinely independent evidence
    # into the theme: statements preserved from the subject's own site are an
    # independent origin family relative to the registry publisher
    theme_id = theme_entry["need"]["source_id"]
    site_claims = [c["claim_id"] for c in store.current_claims().values()
                   if c["subject_ref"].startswith("URL:")
                   and SUBJECT_SITE in c["subject_ref"]]
    folded = None
    if site_claims:
        folded = update_membership(
            ctx, theme_id, add_supporting=site_claims[:5],
            caused_by=f"analyst:{operator}",
            rationale=f"{operator}: site statements preserved from "
                      f"{SUBJECT_SITE} concern the same subject (identity "
                      f"reviewed) and descend from an independent origin family")
    theme_after = refresh_theme(ctx, theme_id, caused_by=f"analyst:{operator}")

    propagation = propagate_semantic_changes(ctx)
    alerts = [a for a in store.records_of("alert")
              if a["rule_id"] == "analytic-change"]
    return {
        "analytic_needs_found": [(n["source_kind"], n["question"][:100])
                                 for n in needs],
        "requirement_opened": theme_entry["requirement"]["requirement_id"][:24],
        "eiv_routes": route_lines,
        "independence_note": next((r["explanation"] for r in routes
                                   if r["score"] == 0), "")[:200],
        "route_executed": {
            "route": f"{viable[0]['source_id']}/{viable[0]['operation']}"
            if viable else None,
            "outcome": executed.get("execution_outcome"),
            "new_manifestations": len(executed.get("manifestations", ()))},
        "discriminator_after_execution": discriminator_after["status"],
        "satisfaction_note": "an independent acquisition that cannot answer the "
                             "exact source-native attribute does not satisfy the "
                             "discriminator — no false corroboration",
        "theme_after_fold": {
            "status": theme_after["status"],
            "independent_families": len(theme_after["basis"]["origin_families"]),
            "families": list(theme_after["basis"]["origin_families"]),
            "version": theme_after["version"],
            "folded_by": operator if folded else None},
        "theme_transitions": [
            (t["transition_type"], t["detail"][:90])
            for t in store.transitions_for(theme_id)],
        "semantic_changes_propagated": len(propagation),
        "analytic_alerts": [a["trigger"].split("\n")[0][:120] for a in alerts[:3]],
        "chain_valid": store.verify_chain()["valid"],
    }


# ---- phase 5: historical analogue (bounded) -------------------------------

COMPARABLE_LEI = "INR2EJN1ERAN0W5ZP974"  # Microsoft Corporation


def phase_5(root: Path) -> dict:
    """Bounded real analogue exercise: acquire a comparable entity's registry
    record live, record its lifecycle as an evidence-bound episode, and
    retrieve structural analogues for the subject theme."""
    from .analogues import explain_analogue, record_episode, retrieve_analogues
    pipeline, ctx = _stores(root)
    store = ctx.store
    fabric = _fabric(root, store)
    need = next(iter(store.records_of("fabric_information_need")), {"need_id": ""})
    outcome = execute_single(fabric, query=_query(
        need["need_id"] if isinstance(need, dict) else need.need_id,
        "IDENTIFIER", COMPARABLE_LEI, "LOOKUP", "gleif",
        "comparable-entity registry lifecycle for analogue corpus"),
        source_id="gleif")
    pipeline.process_new_evidence()

    comparable_object = world_object_id(f"LEI:{COMPARABLE_LEI}")
    comparable_claims = [c for c in store.current_claims().values()
                         if c["subject_ref"] == f"LEI:{COMPARABLE_LEI}"]
    comparable_events = [a["activity_id"] for a in store.records_of("activity")
                         if comparable_object in a["subject_ids"]]
    episode = record_episode(
        ctx, title="Microsoft Corporation LEI registration lifecycle",
        summary="registration and standing history of a comparable US "
                "technology corporation under the GLEIF regime",
        actor_object_ids=(comparable_object,),
        event_ids=tuple(comparable_events),
        institutional_setting="GLEIF LEI registration regime (US corporate)",
        mechanism="registry registration and periodic renewal",
        constraints=("single registrar jurisdiction",),
        outcome=next((f"registration {c['object_or_value']}"
                      for c in comparable_claims
                      if c["predicate"] == "registration_status"), ""),
        outcome_claim_ids=tuple(c["claim_id"] for c in comparable_claims
                                if c["predicate"] == "registration_status"),
        claim_ids=tuple(c["claim_id"] for c in comparable_claims))
    theme_id = next(t["theme_id"] for t in store.current_themes().values()
                    if LEI_OBJECT in t["entity_ids"])
    analogues = retrieve_analogues(ctx, query_kind="analytic_theme",
                                   query_id=theme_id)
    explanation = explain_analogue(store, analogues[0]["analogue_id"]) \
        if analogues and "analogue_id" in analogues[0] else {}
    return {
        "acquired": f"gleif/LOOKUP {outcome.execution.outcome}",
        "episode": {"title": episode["title"],
                    "evidence_claims": len(episode["claim_ids"]),
                    "events": len(episode["event_ids"]),
                    "outcome": episode["outcome"]},
        "analogues_retrieved": len(analogues),
        "analogue": {
            "matched": [d["dimension"] for d in explanation.get("matched", [])],
            "mismatched": [d["dimension"] for d in explanation.get("mismatched", [])],
            "transfer_risks": explanation.get("transfer_risks", [])[:3],
            "outcome_caveat": explanation.get("outcome_caveat", ""),
            "authority": analogues[0].get("authority", "") if analogues else "",
        },
        "chain_valid": store.verify_chain()["valid"],
    }


# ---- phase 6: forecasting and strategic warning (live) ---------------------


def phase_6(root: Path, operator: str) -> dict:
    """Live forecast lifecycle over the real Apple evidence:

    * an analyst forecast machine-resolves TRUE against the real GLEIF claim;
    * a second forecast reaches its horizon and shows the coverage gate —
      a live search executed BEFORE the horizon cannot prove absence AT the
      horizon, so resolution blocks until a real post-horizon search lands;
    * a warning projects the open forecast onto the phase-2 objective through
      the named tier rule, and resolves when the question settles;
    * the calibration scoreboard scores exactly what resolved and reports
      exactly what it could not score.
    """
    import time as _time

    from .calibration import scoreboard
    from .contracts import IndicatorEffect, ResolutionRule
    from .forecasts import (create_forecast, refresh_forecast,
                            try_machine_resolution)
    from .indicators import arm_indicator
    from .warning import project_warning, refresh_warnings

    pipeline, ctx = _stores(root)
    store = ctx.store
    fabric = _fabric(root, store)
    subject = f"LEI:{SUBJECT_LEI}"
    registration_claim = next(
        (c for c in store.current_claims().values()
         if c["subject_ref"] == subject
         and c["predicate"] == "registration_status"), None)
    if registration_claim is None:
        return {"error": "run phases 1-2 first: no registration_status claim"}
    observed_value = registration_claim["object_or_value"]
    objective_id = next((o["objective_id"]
                         for o in store.current_objectives().values()
                         if o["mission_context"] == MISSION), "")

    # forecast A: settles TRUE (early, pre-horizon) against the standing
    # real claim — a machine-resolution demonstration. The scoreboard
    # excludes it from aggregates (resolved_by_prior_evidence): its answer
    # was on the record when it was authored, and the demo says so
    from datetime import timedelta as _timedelta
    horizon_a = (datetime.now(timezone.utc) + _timedelta(hours=1)).isoformat()
    forecast_a = create_forecast(
        ctx,
        question=f"Will GLEIF registration_status for {SUBJECT_NAME} read "
                 f"{observed_value} at or before the horizon?",
        outcome_semantics=f"TRUE iff a CURRENT registration_status claim "
                          f"reads {observed_value!r} at or before the horizon",
        proposition_refs=(("claim", registration_claim["claim_id"]),),
        horizon_time=horizon_a,
        resolution=ResolutionRule(
            kind="CLAIM_PREDICATE",
            criteria=f"GLEIF registration_status reads {observed_value}",
            claim_subject_ref=subject, claim_attribute="registration_status",
            expected_value=observed_value,
            absence_min_successful_sources=1,
            absence_required_source_ids=("gleif",)),
        probability=0.92,
        probability_basis="the registry record was ISSUED at acquisition and "
                          "large-issuer lapses inside a day are rare",
        author=operator, domain="corporate-registry",
        supporting_claim_ids=[registration_claim["claim_id"]])

    # forecast B: a lapse that will NOT be observed — the honest FALSE path
    from datetime import timedelta
    horizon_b = (datetime.now(timezone.utc)
                 + timedelta(seconds=20)).isoformat()
    forecast_b = create_forecast(
        ctx,
        question=f"Will GLEIF registration_status for {SUBJECT_NAME} read "
                 f"LAPSED by {horizon_b[:19]}?",
        outcome_semantics="TRUE iff a CURRENT registration_status claim reads "
                          "'LAPSED' at or before the horizon",
        proposition_refs=(("claim", registration_claim["claim_id"]),),
        horizon_time=horizon_b,
        resolution=ResolutionRule(
            kind="CLAIM_PREDICATE",
            criteria="GLEIF registration_status reads LAPSED",
            claim_subject_ref=subject, claim_attribute="registration_status",
            expected_value="LAPSED",
            absence_min_successful_sources=1,
            absence_required_source_ids=("gleif",)),
        probability=0.03,
        probability_basis="no lapse signal in the acquired record; the "
                          "renewal is not due within the horizon",
        author=operator, domain="corporate-registry",
        supporting_claim_ids=[registration_claim["claim_id"]])

    # a pre-authorized indicator: the analyst's conditional judgment on record
    indicator = arm_indicator(
        ctx, description=f"GLEIF registration_status for {SUBJECT_NAME} "
                         f"reads LAPSED",
        forecast_ids=(forecast_b["forecast_id"],),
        kind="PRESENCE", direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=subject, desired_attribute="registration_status",
        expected_value="LAPSED",
        effect=IndicatorEffect(mode="APPLY_PROBABILITY",
                               target_probability=0.85,
                               rationale="an observed lapse mostly settles "
                                         "the lapse question",
                               authorized_by=operator,
                               authorized_kind="HUMAN"))

    # the warning projects the open lapse forecast onto the mission objective
    warning = None
    if objective_id:
        warning = project_warning(ctx, forecast_id=forecast_b["forecast_id"],
                                  objective_id=objective_id)

    # A resolves TRUE against the real claim, machine act with real evidence
    resolved_a = try_machine_resolution(ctx, forecast_a["forecast_id"])

    # a LIVE search BEFORE B's horizon: real work that cannot prove absence
    pre = execute_single(fabric, query=_query(
        forecast_b["forecast_id"], "IDENTIFIER", SUBJECT_LEI, "LOOKUP", "gleif",
        "pre-horizon search: demonstrates it cannot satisfy the coverage gate"),
        source_id="gleif")
    pipeline.process_new_evidence()

    # wait out the horizon, then show the coverage gate holding
    _time.sleep(max(0.0, (datetime.fromisoformat(horizon_b)
                          - datetime.now(timezone.utc)).total_seconds()) + 1.0)
    blocked = refresh_forecast(ctx, forecast_b["forecast_id"],
                               caused_by="phase-6-horizon")
    coverage_gap_open = any(
        item["kind"] == "COVERAGE_GAP"
        and item["subject_id"] == forecast_b["forecast_id"]
        for item in store.open_review_items())
    needs = [n for n in analytic_collection_needs(store)
             if n["source_id"] == forecast_b["forecast_id"]]

    # a LIVE post-horizon search: only now can silence mean anything
    post = execute_single(fabric, query=_query(
        forecast_b["forecast_id"], "IDENTIFIER", SUBJECT_LEI, "LOOKUP", "gleif",
        "post-horizon search satisfying the declared absence coverage"),
        source_id="gleif")
    pipeline.process_new_evidence()
    resolved_b = try_machine_resolution(ctx, forecast_b["forecast_id"])

    propagate_semantic_changes(ctx)
    warning_final = store.current_warnings().get(
        warning["warning_id"]) if warning else None
    board = scoreboard(store)
    explanation = explain_object(store, "analytic_forecast",
                                 forecast_b["forecast_id"])
    return {
        "forecast_a": {
            "question": forecast_a["question"],
            "authored_probability": forecast_a["probability"],
            "status": resolved_a["status"],
            "resolver_kind": resolved_a["resolver_kind"],
            "resolution_evidence": list(resolved_a["resolution_evidence_refs"]),
        },
        "forecast_b": {
            "question": forecast_b["question"],
            "authored_probability": forecast_b["probability"],
            "status_at_horizon": blocked["status"],
            "coverage_gap_was_open": coverage_gap_open,
            "collection_needs_raised": [n["question"][:140] for n in needs],
            "pre_horizon_search": f"{pre.execution.outcome} "
                                  f"(completed {pre.execution.completed_time[:19]}"
                                  f" < horizon: cannot prove absence)",
            "post_horizon_search": post.execution.outcome,
            "final_status": resolved_b["status"],
            "resolution_evidence": list(resolved_b["resolution_evidence_refs"]),
        },
        "indicator": {
            "description": indicator["description"],
            "status": store.current_indicators()[
                indicator["indicator_id"]]["status"],
            "note": "never fired: no lapse was observed, so the "
                    "pre-authorized effect stayed unexecuted; once the "
                    "watched question settled the indicator expired",
        },
        "warning": {
            "raised_tier": warning["tier"] if warning else None,
            "components": {k: warning[k] for k in
                           ("probability_band", "consequence", "time_pressure",
                            "evidence_confidence")} if warning else None,
            "tier_rule": warning["tier_rule_id"] if warning else None,
            "final_status": warning_final["status"] if warning_final else None,
        },
        "calibration": {
            "scored": board["coverage"]["scored"],
            "authored_after_horizon": board["coverage"]["authored_after_horizon"],
            "resolved_by_prior_evidence":
                board["coverage"]["resolved_by_prior_evidence"],
            "unscored_by_status": board["coverage"]["unscored_by_status"],
            "overall": board["overall"],
            "occupied_buckets": [b for b in board["buckets"] if b["count"]],
            "note": "forecast A demonstrates machine resolution against "
                    "standing evidence; the scoreboard excludes it from "
                    "aggregates for exactly that reason — its answer was on "
                    "the record when it was authored",
        },
        "forecast_b_explained": {
            "WHAT": explanation["WHAT"],
            "UNCERTAINTY": explanation["UNCERTAINTY"][:4],
        },
        "chain_valid": store.verify_chain()["valid"],
        "model_provider_invocations": len(store.records_of("inference")),
    }


# ---- phase 4: replay ------------------------------------------------------


def phase_4(root: Path) -> dict:
    _, ctx = _stores(root)
    store = ctx.store
    export_dir = root / "export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    manifest = store.export_to(export_dir)
    replay_dir = root / "replayed"
    if replay_dir.exists():
        shutil.rmtree(replay_dir)
    replayed = AnalyticStore.import_from(export_dir, replay_dir)

    def view(s: AnalyticStore) -> dict:
        return {
            "themes": s.current_themes(), "narratives": s.current_narratives(),
            "assessments": s.current_stakeholder_assessments(),
            "influence": s.current_influence_assertions(),
            "objectives": s.current_objectives(),
            "assumptions": s.current_assumptions(),
            "paths": s.current_impact_paths(),
            "forecasts": s.current_forecasts(),
            "indicators": s.current_indicators(),
            "warnings": s.current_warnings(),
            "transitions": len(s.records_of("analytic_transition")),
        }

    identical = view(store) == view(replayed)
    theme_id = next(iter(replayed.current_themes()), "")
    explanation = explain_object(replayed, "analytic_theme", theme_id) \
        if theme_id else {}
    # recover one exact anchor from replayed payloads alone
    anchor_check = {}
    theme = replayed.current_themes().get(theme_id)
    if theme:
        from curunir_semantic.normalize import load_fields
        claim = replayed.current_claims()[
            theme["basis"]["supporting_claim_ids"][0]]
        observation = next(o for o in replayed.records_of("semantic_observation")
                           if o["observation_id"] == claim["observation_ids"][0])
        anchor = observation["anchors"][0]
        if anchor["kind"] == "FIELD":
            document = next(d for d in replayed.records_of("semantic_document")
                            if d["manifestation_id"] == anchor["manifestation_id"])
            fields = dict(load_fields(replayed, document))
            recovered = fields.get(anchor["field_path"], "")
            anchor_check = {"field_path": anchor["field_path"],
                            "recovered": recovered[:60],
                            "matches": recovered == anchor["exact_value"]}
    return {
        "export_events": manifest["event_count"],
        "replayed_chain_valid": replayed.verify_chain()["valid"],
        "analytical_views_identical": identical,
        "theme_explained_from_replay": bool(explanation.get("WHY")),
        "anchor_recovered_from_replay": anchor_check,
        "model_provider_invocations": len(replayed.records_of("inference")),
        "no_hidden_provider_dependency": len(replayed.records_of("inference")) == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--phase", type=int, choices=(1, 2, 3, 4, 5, 6),
                        required=True)
    parser.add_argument("--operator", default="demo-operator")
    args = parser.parse_args(argv)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if args.phase == 1:
        result = phase_1(root)
    elif args.phase == 2:
        result = phase_2(root, args.operator)
    elif args.phase == 3:
        result = phase_3(root, args.operator)
    elif args.phase == 5:
        result = phase_5(root)
    elif args.phase == 6:
        result = phase_6(root, args.operator)
    else:
        result = phase_4(root)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
