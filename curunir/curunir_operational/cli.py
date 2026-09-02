"""Operational CLI. Thin argparse layer over the library modules — no business
logic here, no network, no destructive defaults, explicit store path and
access context everywhere. Run as:

    python -m curunir_operational.cli <command> --store PATH ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .access import AccessContext, Marking, context_from_record, marking_from_record
from .canonical import utc_now
from .explain import explain, explain_markdown
from .pipelines import PipelineExecutor, executor_with_registered_connectors
from .projection import Projection, projection_hash
from .schema_registry import SchemaRegistry
from .sitrep import build_situation_report, render_markdown, render_text
from .sovereignty import build_pace_bundle, build_sovereignty_manifest, run_exit_test, verify_pace_bundle
from .store import MissionDataStore
from .workbench import WorkbenchRenderer, render_cop_html


def _print(value, as_json=True):
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(value)


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _context(args) -> AccessContext:
    return context_from_record(_load_json(args.context))


def _projection(store, args) -> Projection:
    staleness = _load_json(args.staleness) if getattr(args, "staleness", None) else None
    return Projection(store, as_of_seq=getattr(args, "as_of_seq", None),
                      snapshot_time=getattr(args, "snapshot_time", None), staleness_hours=staleness)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curunir-operational",
                                     description="Curunír mission-data fabric (research shadow; synthetic data only)")
    sub = parser.add_subparsers(dest="command", required=True)

    def cmd(name, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--store", required=name not in ("verify-pace",), help="mission data store path")
        return p

    p = cmd("init"); p.add_argument("--store-id", required=True); p.add_argument("--time", default=None)
    p = cmd("register-schema"); p.add_argument("--file", required=True); p.add_argument("--time", default=None)
    p = cmd("register-mapping"); p.add_argument("--file", required=True); p.add_argument("--time", default=None)
    p = cmd("register-pipeline"); p.add_argument("--file", required=True); p.add_argument("--time", default=None)
    p = cmd("ingest")
    p.add_argument("--pipeline", required=True); p.add_argument("--file", required=True)
    p.add_argument("--source", required=True); p.add_argument("--source-time", default=None)
    p.add_argument("--received-time", default=None); p.add_argument("--marking", default=None)
    p = cmd("run-scenario"); p.add_argument("--out", required=True)
    p = cmd("project")
    p.add_argument("--context", required=True); p.add_argument("--as-of-seq", type=int, default=None)
    p.add_argument("--snapshot-time", default=None); p.add_argument("--staleness", default=None)
    p = cmd("workbench")
    p.add_argument("--context", required=True); p.add_argument("--workshop", required=True)
    p.add_argument("--out", default=None); p.add_argument("--snapshot-time", default=None)
    p.add_argument("--staleness", default=None)
    p = cmd("explain")
    p.add_argument("--context", required=True); p.add_argument("--id", required=True)
    p.add_argument("--markdown", action="store_true"); p.add_argument("--snapshot-time", default=None)
    p = cmd("alerts"); p.add_argument("--context", required=True); p.add_argument("--snapshot-time", default=None)
    p = cmd("recommendations"); p.add_argument("--context", required=True); p.add_argument("--snapshot-time", default=None)
    p = cmd("decide")
    p.add_argument("--context", required=True); p.add_argument("--recommendation", required=True)
    p.add_argument("--state", required=True); p.add_argument("--rationale", required=True)
    p.add_argument("--modification", default=""); p.add_argument("--time", default=None)
    p.add_argument("--marking", required=True)
    p = cmd("report")
    p.add_argument("--context", required=True); p.add_argument("--format", choices=("json", "markdown", "text"),
                                                               default="text")
    p.add_argument("--operational-context", default="operational snapshot"); p.add_argument("--out", default=None)
    p.add_argument("--snapshot-time", default=None); p.add_argument("--staleness", default=None)
    p.add_argument("--since-seq", type=int, default=0)
    p = cmd("export"); p.add_argument("--out", required=True)
    p = cmd("import"); p.add_argument("--from", dest="source", required=True)
    p = cmd("replay"); p.add_argument("--export", dest="export_dir", required=True)
    p.add_argument("--context", required=True); p.add_argument("--snapshot-time", required=True)
    p.add_argument("--staleness", default=None); p.add_argument("--fresh", required=True)
    p = cmd("pace")
    p.add_argument("--context", required=True); p.add_argument("--out", required=True)
    p.add_argument("--operational-context", default="operational snapshot")
    p.add_argument("--snapshot-time", default=None); p.add_argument("--staleness", default=None)
    p.add_argument("--since-seq", type=int, default=0)
    p = cmd("verify"); p.add_argument("--pace", default=None)
    p = sub.add_parser("verify-pace"); p.add_argument("--bundle", required=True)
    p = cmd("sovereignty")

    args = parser.parse_args(argv)
    now = getattr(args, "time", None) or utc_now()

    if args.command == "init":
        store = MissionDataStore.create(args.store, args.store_id, getattr(args, "time", None) or utc_now())
        _print(store.head()); return 0
    if args.command == "verify-pace":
        result = verify_pace_bundle(args.bundle)
        _print(result); return 0 if result["valid"] else 1
    if args.command == "import":
        imported = MissionDataStore.import_from(args.source, args.store)
        _print(imported.head()); return 0
    if args.command == "run-scenario":
        from .scenario.runner import run_scenario
        _print(run_scenario(Path(args.store), Path(args.out))["summary"]); return 0

    store = MissionDataStore(args.store)
    registry = SchemaRegistry(store)

    if args.command == "register-schema":
        _print(registry.register_schema(_load_json(args.file), recorded_time=now, actor="cli"))
    elif args.command == "register-mapping":
        _print(registry.register_mapping(_load_json(args.file), recorded_time=now, actor="cli"))
    elif args.command == "register-pipeline":
        executor = executor_with_registered_connectors(store, registry)
        definition = _load_json(args.file)
        from .pipelines import build_connector
        executor.connectors[definition["connector_id"]] = build_connector(
            definition.get("connector_kind", "json"), definition["connector_id"], definition["schema_id"],
            definition.get("event_id_field"))
        _print(executor.register_pipeline(definition, recorded_time=now, actor="cli"))
    elif args.command == "ingest":
        executor = executor_with_registered_connectors(store, registry)
        marking = marking_from_record(_load_json(args.marking)) if args.marking else None
        result = executor.run(args.pipeline, Path(args.file).read_bytes(), source_id=args.source,
                              source_time=args.source_time, received_time=args.received_time or utc_now(),
                              recorded_time=utc_now(), actor="cli", marking_override=marking)
        _print(result)
    elif args.command == "project":
        view = _projection(store, args).view(_context(args))
        _print({"view": view, "projection_hash": projection_hash(view)})
    elif args.command == "workbench":
        definitions = store.records_of("workshop_definition")
        chosen = [d for d in definitions if d["workshop_id"] == args.workshop]
        if not chosen:
            print(f"error: workshop not registered: {args.workshop}", file=sys.stderr); return 2
        view = WorkbenchRenderer(_projection(store, args)).render(chosen[-1], _context(args))
        if args.out:
            Path(args.out).write_text(render_cop_html(view, title=chosen[-1]["purpose"]), encoding="utf-8")
            _print({"written": args.out, "counts": view["counts"]})
        else:
            _print(view)
    elif args.command == "explain":
        explanation = explain(_projection(store, args), args.id, _context(args))
        _print(explain_markdown(explanation) if args.markdown else explanation, as_json=not args.markdown)
    elif args.command == "alerts":
        _print(_projection(store, args).view(_context(args))["alerts"])
    elif args.command == "recommendations":
        _print(_projection(store, args).view(_context(args))["recommendations"])
    elif args.command == "decide":
        from .workflow import WorkflowEngine
        decision = WorkflowEngine(store).decide(
            args.recommendation, context=_context(args), state=args.state, rationale=args.rationale,
            modification=args.modification, recorded_time=now, marking=marking_from_record(_load_json(args.marking)))
        _print(decision)
    elif args.command == "report":
        report = build_situation_report(store, _projection(store, args), _context(args),
                                        operational_context=args.operational_context, since_seq=args.since_seq)
        rendered = {"json": lambda: json.dumps(report, indent=2, sort_keys=True),
                    "markdown": lambda: render_markdown(report),
                    "text": lambda: render_text(report)}[args.format]()
        if args.out:
            Path(args.out).write_text(rendered, encoding="utf-8"); _print({"written": args.out,
                                                                           "integrity_hash": report["integrity_hash"]})
        else:
            print(rendered)
    elif args.command == "export":
        _print(store.export_to(args.out))
    elif args.command == "replay":
        result = run_exit_test(store, args.export_dir, args.fresh, _context(args),
                               snapshot_time=args.snapshot_time,
                               staleness_hours=_load_json(args.staleness) if args.staleness else None)
        _print(result); return 0 if result["passed"] else 1
    elif args.command == "pace":
        _print(build_pace_bundle(store, _projection(store, args), _context(args), args.out,
                                 operational_context=args.operational_context, since_seq=args.since_seq))
    elif args.command == "verify":
        result = store.verify_chain()
        if args.pace:
            result = {"chain": result, "pace": verify_pace_bundle(args.pace)}
        _print(result)
    elif args.command == "sovereignty":
        _print(build_sovereignty_manifest(store))
    return 0


def entry() -> int:
    try:
        return main()
    except KeyError as exc:
        print(f"error: malformed input file, missing field {exc}", file=sys.stderr)
        return 2
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entry())
