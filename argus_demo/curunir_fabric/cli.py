"""Thin command layer for the OSINT fabric.

    python -m curunir_fabric.cli create-store --store /path
    python -m curunir_fabric.cli seed-catalog --store /path
    python -m curunir_fabric.cli sources --store /path [--operation SEARCH] [--historical]
    python -m curunir_fabric.cli open-need --store /path --mission M --question Q [--entity E ...]
    python -m curunir_fabric.cli plan --store /path --need NEED_ID
    python -m curunir_fabric.cli execute --store /path --need NEED_ID [--max-requests N]
    python -m curunir_fabric.cli coverage --store /path --need NEED_ID
    python -m curunir_fabric.cli watch-register --store /path --need NEED_ID --source S \
        --target-kind FEED --target-ref URL --operation POLL --cadence 3600
    python -m curunir_fabric.cli tick --store /path
    python -m curunir_fabric.cli verify --store /path
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from curunir_operational.access import Marking

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import digest_id

from .catalog import seed_starter_catalog
from .contracts import WatchDefinition
from .coverage import assess_coverage, coverage_summary, unsearched_families
from .executor import ExecutionContext, execute_plan
from .mission_bridge import open_requirement_with_need
from .pivots import current_pivots, propose_pivots, queries_from_pivots
from .planner import plan_discovery, record_plan
from .registry import load_registry
from .store import FabricStore
from .watch import register_watch, tick

DEFAULT_ACTOR = "fabric-cli"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _marking() -> Marking:
    return Marking(owning_authority="curunir-fabric", releasability=("PUBLIC",))


def _store(args) -> FabricStore:
    return FabricStore(args.store)


def _context(args) -> ExecutionContext:
    store = _store(args)
    custody_root = Path(args.custody or (Path(args.store) / "custody"))
    return ExecutionContext(store=store, registry=load_registry(store),
                            custody=SourceCustodyStore(custody_root),
                            actor=DEFAULT_ACTOR, marking=_marking())


def _need_record(store: FabricStore, need_id: str) -> dict:
    records = [r for r in store.records_of("fabric_information_need") if r["need_id"] == need_id]
    if not records:
        raise SystemExit(f"unknown need: {need_id}")
    return records[-1]


def _need_from_record(record: dict):
    from .contracts import InformationNeed
    from curunir_operational.access import marking_from_record
    data = {k: v for k, v in record.items() if k != "record_type"}
    data["entities"] = tuple(data["entities"])
    data["identifiers"] = tuple((s, v) for s, v in data["identifiers"])
    data["time_bounds"] = tuple(data["time_bounds"])
    for key in ("geography", "languages", "scripts", "hypotheses"):
        data[key] = tuple(data[key])
    data["marking"] = marking_from_record(data["marking"])
    return InformationNeed(**data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curunir_fabric")
    parser.add_argument("command", choices=[
        "create-store", "seed-catalog", "sources", "open-need", "plan", "execute",
        "coverage", "watch-register", "tick", "verify"])
    parser.add_argument("--store", required=True)
    parser.add_argument("--custody", default="")
    parser.add_argument("--mission", default="")
    parser.add_argument("--question", default="")
    parser.add_argument("--entity", action="append", default=[])
    parser.add_argument("--identifier", action="append", default=[],
                        help="SCHEME:VALUE, e.g. LEI:5493001KJTIIGC8Y1R12")
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--need", default="")
    parser.add_argument("--operation", default="")
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--source", default="")
    parser.add_argument("--target-kind", default="URL")
    parser.add_argument("--target-ref", default="")
    parser.add_argument("--cadence", type=int, default=3600)
    args = parser.parse_args(argv)
    now = _now()

    if args.command == "create-store":
        FabricStore.create(args.store, digest_id("fabric-store", args.store, now), now)
        print(json.dumps({"created": args.store}))
        return 0

    if args.command == "seed-catalog":
        registered = seed_starter_catalog(_store(args), recorded_time=now, actor=DEFAULT_ACTOR)
        print(json.dumps({"registered": registered}))
        return 0

    if args.command == "sources":
        view = load_registry(_store(args))
        matches = view.capable_sources(operation=args.operation or None,
                                       historical=True if args.historical else None)
        print(json.dumps([{"source_id": d.source_id, "type": d.source_type,
                           "capabilities": list(d.capabilities)} for d in matches], indent=2))
        return 0

    if args.command == "open-need":
        identifiers = tuple(tuple(item.split(":", 1)) for item in args.identifier)
        need = open_requirement_with_need(
            _store(args), mission_context=args.mission or "fabric-cli-mission",
            question=args.question, entities=tuple(args.entity), identifiers=identifiers,
            languages=tuple(args.language), now=now, actor=DEFAULT_ACTOR, marking=_marking())
        print(json.dumps({"need_id": need.need_id, "requirement_id": need.requirement_id}))
        return 0

    if args.command == "plan":
        store = _store(args)
        need = _need_from_record(_need_record(store, args.need))
        plan = plan_discovery(need, load_registry(store), now=now, marking=_marking(),
                              budget_max_requests=args.max_requests)
        record_plan(store, plan, recorded_time=now, actor=DEFAULT_ACTOR)
        print(json.dumps({"plan_id": plan.plan_id, "queries": len(plan.queries),
                          "considered_sources": list(plan.considered_source_ids),
                          "unmatched": len(plan.unmatched_query_ids)}))
        return 0

    if args.command == "execute":
        ctx = _context(args)
        need = _need_from_record(_need_record(ctx.store, args.need))
        plan = plan_discovery(need, ctx.registry, now=now, marking=_marking(),
                              budget_max_requests=args.max_requests)
        record_plan(ctx.store, plan, recorded_time=now, actor=DEFAULT_ACTOR)
        outcomes = execute_plan(ctx, plan, max_requests=args.max_requests)
        pivots = []
        for outcome in outcomes:
            pivots.extend(propose_pivots(ctx.store, outcome, subject=need.entities[0] if need.entities else need.question,
                                         now=ctx.now_fn(), actor=DEFAULT_ACTOR, marking=_marking()))
        assess_coverage(ctx.store, ctx.registry, need.need_id, now=ctx.now_fn(),
                        actor=DEFAULT_ACTOR, marking=_marking())
        print(json.dumps({
            "plan_id": plan.plan_id,
            "executions": {o.execution.execution_id[:24]: o.execution.outcome for o in outcomes},
            "manifestations": sum(len(o.manifestations) for o in outcomes),
            "pivots_proposed": len(pivots),
            "coverage": coverage_summary(ctx.store, need.need_id),
        }, indent=2))
        return 0

    if args.command == "coverage":
        store = _store(args)
        print(json.dumps({
            "coverage": coverage_summary(store, args.need),
            "unsearched_families": unsearched_families(store, load_registry(store), args.need),
        }, indent=2))
        return 0

    if args.command == "watch-register":
        store = _store(args)
        definition = WatchDefinition(
            watch_id=digest_id("watch", args.need, args.source, args.target_ref),
            need_id=args.need, target_kind=args.target_kind, target_ref=args.target_ref,
            source_id=args.source, operation=args.operation or "POLL",
            query_value=args.target_ref, cadence_seconds=args.cadence, active=True,
            blind_spots=(), created_by=DEFAULT_ACTOR, created_time=now, marking=_marking())
        register_watch(store, definition, actor=DEFAULT_ACTOR)
        print(json.dumps({"watch_id": definition.watch_id}))
        return 0

    if args.command == "tick":
        ctx = _context(args)
        runs = tick(ctx)
        print(json.dumps([{"run_id": run.run_id[:24], "watch_id": run.watch_id[:24],
                           "outcome": run.outcome, "changes": len(run.change_observation_ids),
                           "next_due": run.next_due_time} for run in runs], indent=2))
        return 0

    if args.command == "verify":
        result = _store(args).verify_chain()
        print(json.dumps(result))
        return 0 if result["valid"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
