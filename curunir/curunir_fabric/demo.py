"""Bounded live demonstration of the OSINT fabric loop against real sources.

    python -m curunir_fabric.demo --root /tmp/fabric-demo [--phase 1|2|3]

Phase 1: need → capable sources → multilingual plan → live execution across
         Wikidata / GLEIF / SEC EDGAR → custody → pivots → coverage → watches.
Phase 2: (separate process = restart) run due watches, baseline observations.
Phase 3: (separate process) run watches again, detect real changes, alert,
         export and replay the lineage.

Every retrieval is a real network request to the registered public endpoint;
nothing is mocked. The store root persists between phases deliberately.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking

from .catalog import seed_starter_catalog
from .contracts import WatchDefinition
from .coverage import assess_coverage, coverage_summary, unsearched_families
from .executor import ExecutionContext, RateGate, execute_plan
from .mission_bridge import alert_from_change, open_requirement_with_need
from .pivots import current_pivots, propose_pivots, queries_from_pivots
from .planner import plan_discovery, record_plan
from .registry import load_registry
from .store import FabricStore
from .watch import register_watch, tick

ACTOR = "fabric-live-demo"
MARK = Marking(owning_authority="curunir-fabric-demo", releasability=("PUBLIC",))
# a real public page whose content genuinely changes between observations
CHANGING_URL = "https://www.federalregister.gov/api/v1/documents.rss"
SUBJECT = "Severstal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _context(root: Path) -> ExecutionContext:
    store = FabricStore(root / "store")
    return ExecutionContext(
        store=store, registry=load_registry(store),
        custody=SourceCustodyStore(root / "custody"), actor=ACTOR, marking=MARK,
        rate_gate=RateGate(0.6, per_source={"sec-edgar": 0.2}),
    )


def phase_1(root: Path) -> dict:
    store = FabricStore.create(root / "store", digest_id("demo-store", str(root)), _now())
    seed_starter_catalog(store, recorded_time=_now(), actor=ACTOR)
    ctx = _context(root)

    need = open_requirement_with_need(
        ctx.store, mission_context="severstal-corporate-identity",
        question="What is the corporate identity, registration and public history of Severstal?",
        entities=(SUBJECT,), languages=("en", "ru"), scripts=("Cyrl",),
        now=_now(), actor=ACTOR, marking=MARK)

    view = load_registry(ctx.store)
    historical = [d.source_id for d in view.capable_sources(historical=True)]

    plan = plan_discovery(need, view, now=_now(), marking=MARK, budget_max_requests=12)
    record_plan(ctx.store, plan, recorded_time=_now(), actor=ACTOR)
    outcomes = execute_plan(ctx, plan, max_requests=8)

    pivots = []
    for outcome in outcomes:
        pivots.extend(propose_pivots(ctx.store, outcome, subject=SUBJECT,
                                     now=_now(), actor=ACTOR, marking=MARK))

    expansion_outcomes = []

    def _expand(generation_label: str, budget: int, families: tuple[str, ...] = ()) -> list[str]:
        pivot_queries = queries_from_pivots(current_pivots(ctx.store), need_id=need.need_id)
        already = {r["query_id"] for r in ctx.store.records_of("fabric_execution")}
        fresh = tuple(q for q in pivot_queries if q.query_id not in already
                      and (not families or q.family in families))
        if not fresh:
            return []
        expansion = plan_discovery(need, view, pivot_queries=fresh[:budget],
                                   generation="PIVOT_EXPANSION", now=_now(), marking=MARK,
                                   budget_max_requests=budget)
        record_plan(ctx.store, expansion, recorded_time=_now(), actor=ACTOR)
        outcomes = execute_plan(ctx, expansion, max_requests=budget)
        expansion_outcomes.extend(outcomes)
        for outcome in outcomes:
            propose_pivots(ctx.store, outcome, subject=SUBJECT,
                           now=_now(), actor=ACTOR, marking=MARK)
        return [f"{o.execution.source_id}/{o.execution.operation}"
                f" {o.execution.outcome} results={o.execution.result_count}"
                f" [{generation_label}]" for o in outcomes]

    summary_2 = _expand("lookup-generation", 5)
    summary_3 = _expand("domain-history-generation", 4, families=("DOMAIN",))

    # retrieve one enumerated capture: a historical manifestation, distinct
    # from every live retrieval of the same URL
    from .contracts import QuerySpec
    from .executor import execute_single
    for outcome in expansion_outcomes:
        if outcome.execution.operation == "HISTORICAL_ENUMERATE" and outcome.results:
            capture = outcome.results[0]
            fetch = execute_single(ctx, query=QuerySpec(
                query_id=digest_id("query", need.need_id, "NATIVE_OBJECT", capture.native_id),
                family="NATIVE_OBJECT", value=capture.native_id, language="", script="",
                operation="HISTORICAL_FETCH", source_id="wayback", time_bounds=(None, None),
                origin="RULE", origin_detail="fabric-live-demo",
                rationale=f"retrieve enumerated capture {capture.native_id}",
                derived_from=tuple(m.manifestation_id for m in outcome.manifestations)),
                source_id="wayback")
            summary_3.append(f"wayback/HISTORICAL_FETCH {fetch.execution.outcome}"
                             f" capture={capture.native_id[:34]}")
            break

    assess_coverage(ctx.store, view, need.need_id, now=_now(), actor=ACTOR, marking=MARK)

    register_watch(ctx.store, WatchDefinition(
        watch_id=digest_id("watch", need.need_id, "federal-register-feed"),
        need_id=need.need_id, target_kind="FEED", target_ref=CHANGING_URL,
        source_id="federal-register-feed", operation="POLL", query_value=CHANGING_URL,
        cadence_seconds=60, active=True, blind_spots=("feed window only",),
        created_by=ACTOR, created_time=_now(), marking=MARK), actor=ACTOR)
    # a live public endpoint whose body genuinely differs between retrievals,
    # so the second tick demonstrates real content-change detection
    trace_url = "https://www.cloudflare.com/cdn-cgi/trace"
    register_watch(ctx.store, WatchDefinition(
        watch_id=digest_id("watch", need.need_id, "live-trace"),
        need_id=need.need_id, target_kind="URL", target_ref=trace_url,
        source_id="live-web", operation="FETCH", query_value=trace_url,
        cadence_seconds=60, active=True, blind_spots=(),
        created_by=ACTOR, created_time=_now(), marking=MARK), actor=ACTOR)

    (root / "need_id.txt").write_text(need.need_id)
    return {
        "need_id": need.need_id, "requirement_id": need.requirement_id,
        "historical_capable_sources": historical,
        "plan_queries": len(plan.queries),
        "query_families": sorted({q.family for q in plan.queries}),
        "executions": [f"{o.execution.source_id}/{o.execution.operation}"
                       f" {o.execution.outcome} results={o.execution.result_count}"
                       for o in outcomes],
        "expansion_executions": summary_2 + summary_3,
        "historical_manifestations": sum(
            1 for r in ctx.store.records_of("fabric_manifestation")
            if r["temporal_status"] == "HISTORICAL"),
        "manifestations": len(ctx.store.records_of("fabric_manifestation")),
        "pivots": len(current_pivots(ctx.store)),
        "coverage": coverage_summary(ctx.store, need.need_id),
        "unsearched_families": unsearched_families(ctx.store, view, need.need_id),
        "chain_valid": ctx.store.verify_chain()["valid"],
    }


def phase_2(root: Path) -> dict:
    ctx = _context(root)
    runs = tick(ctx)
    return {"runs": [{"watch_id": run.watch_id[:20], "outcome": run.outcome,
                      "observed_records": len(run.observed_records),
                      "changes": len(run.change_observation_ids),
                      "next_due": run.next_due_time} for run in runs],
            "chain_valid": ctx.store.verify_chain()["valid"]}


def phase_3(root: Path) -> dict:
    ctx = _context(root)
    runs = tick(ctx)
    changes = ctx.store.records_of("fabric_change")
    alerts = []
    for change in changes:
        if change["change_type"] != "RETRIEVAL_FAILURE":
            alert_id, created = alert_from_change(ctx.store, change, now=_now(),
                                                  actor=ACTOR, marking=MARK)
            if created:
                alerts.append(alert_id)
    export_dir = root / "export"
    manifest = ctx.store.export_to(export_dir)
    replayed = FabricStore.import_from(export_dir, root / "replayed")
    return {
        "runs": [{"watch_id": run.watch_id[:20], "outcome": run.outcome,
                  "changes": len(run.change_observation_ids)} for run in runs],
        "change_types": sorted({c["change_type"] for c in changes}),
        "alerts_raised": len(alerts),
        "export_events": manifest["event_count"],
        "replayed_chain_valid": replayed.verify_chain()["valid"],
        "replayed_head_matches": replayed.head()["head_hash"] == manifest["head_hash"],
        "replayed_manifestations": len(replayed.records_of("fabric_manifestation")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--phase", type=int, choices=(1, 2, 3), required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    result = {1: phase_1, 2: phase_2, 3: phase_3}[args.phase](root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
