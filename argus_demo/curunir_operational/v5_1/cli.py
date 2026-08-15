"""Command line entry points for V5.1 operations (contract Section 5.4).

Thin argparse plumbing over the v5_1 package functions — argument handling
only, zero business logic, no UI, no interactive prompts.  Every subcommand
reads JSON from file paths and writes JSON to file paths; ``main`` returns
exit code 0 on success and 1 on any failure.

Dispatch convention: each subcommand calls exactly one package function whose
name is the subcommand with dashes replaced by underscores (fast-path
commands are wired directly; the remaining commands resolve through
``FORWARDED_COMMANDS`` at call time).  A subcommand whose backing module has
not been implemented yet fails loudly with a JSON error record and exit code
1 — never silent degradation.

Research shadow only.  Nothing here claims human validation.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import Any, Mapping

from ..v4.io import read_json, write_json
from .anti_memorization import build_term_manifest, scan_paths, write_audit
from .corpora import partition_v5_material
from .freeze import freeze_production, verify_freeze

# Result verdicts that map to exit code 0.  Any other verdict on a result
# mapping is a failure; results without a verdict field succeed by returning.
_PASSING_VERDICTS = frozenset({"PASS", "PASS_HARDENED"})

# Later-stage subcommands forward to one package function each, resolved at
# call time so the CLI stays importable while sibling modules land.  Keys are
# subcommand names; values are (module, function) inside this package, with
# the function name always the dash-to-underscore form of the subcommand.
FORWARDED_COMMANDS: Mapping[str, tuple[str, str]] = {
    "candidate-scan-record": ("campaign", "candidate_scan_record"),
    "freeze-campaigns": ("campaign", "freeze_campaigns"),
    "acquire": ("campaign", "acquire"),
    "analyze": ("campaign", "analyze"),
    "report": ("campaign", "report"),
    "build-heldout": ("heldout", "build_heldout"),
    "assign-reviewers": ("heldout", "assign_reviewers"),
    "freeze-reviews": ("heldout", "freeze_reviews"),
    "score-heldout": ("heldout", "score_heldout"),
    "kernel-regression": ("kernel_regression", "kernel_regression"),
    "replay": ("replay", "replay"),
    "mutations": ("mutation", "mutations"),
    "stress": ("stress", "stress"),
    "human-package": ("human_package", "human_package"),
}


def _read_spec(path: str) -> dict[str, Any]:
    spec = read_json(path)
    if not isinstance(spec, dict):
        raise ValueError("spec file must contain a single JSON object of keyword arguments")
    return spec


def _write_output(path: str | None, result: Any) -> None:
    """Append-only artifact discipline: refuse to overwrite an existing output."""
    if path:
        write_json(path, result, refuse_existing=True)


def _run_corpora_partition(args: argparse.Namespace) -> Any:
    result = partition_v5_material(**_read_spec(args.spec))
    _write_output(args.output, result)
    return result


def _run_scan_memorization(args: argparse.Namespace) -> Any:
    spec = _read_spec(args.spec)
    if "campaign_roots" not in spec or "target_paths" not in spec:
        raise ValueError("scan-memorization spec requires campaign_roots and target_paths")
    manifest = build_term_manifest(spec["campaign_roots"], spec.get("heldout_roots"))
    result = scan_paths(spec["target_paths"], manifest, spec.get("documented_rules"))
    write_audit(result, args.output)
    return result


def _run_freeze_production(args: argparse.Namespace) -> Any:
    return freeze_production(**_read_spec(args.spec), output_path=args.output)


def _run_verify_freeze(args: argparse.Namespace) -> Any:
    result = verify_freeze(args.manifest)
    _write_output(args.output, result)
    return result


def _run_forwarded(args: argparse.Namespace) -> Any:
    module_name, function_name = FORWARDED_COMMANDS[args.command]
    module = importlib.import_module("." + module_name, __package__)
    result = getattr(module, function_name)(**_read_spec(args.spec))
    _write_output(args.output, result)
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="curunir-v5-1",
        description=(
            "Curunír V5.1 operational entry points (contract Section 5.4). "
            "JSON in and out via file paths; exit code 0 on success, 1 on any "
            "failure or non-passing verdict; no interactive prompts."))
    commands = root.add_subparsers(dest="command", required=True)

    command = commands.add_parser(
        "corpora-partition",
        help="partition frozen V5 material into the Section 19 corpora")
    command.add_argument("--spec", required=True,
                         help="JSON object of partition_v5_material keyword arguments")
    command.add_argument("--output", required=True,
                         help="path for the returned corpora manifest (refuses existing)")
    command.set_defaults(handler=_run_corpora_partition)

    command = commands.add_parser(
        "scan-memorization",
        help="build the sensitive-term manifest and scan targets (Section 20)")
    command.add_argument("--spec", required=True,
                         help=("JSON object with campaign_roots, target_paths and optional "
                               "heldout_roots / documented_rules"))
    command.add_argument("--output", required=True, help="path for the audit report JSON")
    command.set_defaults(handler=_run_scan_memorization)

    command = commands.add_parser(
        "freeze-production",
        help="record the frozen production surface (Section 21)")
    command.add_argument("--spec", required=True,
                         help=("JSON object with scope_paths, thresholds, prompts, "
                               "packet_builder_version and provider_versions"))
    command.add_argument("--output", required=True, help="path for the freeze manifest JSON")
    command.set_defaults(handler=_run_freeze_production)

    command = commands.add_parser(
        "verify-freeze",
        help="re-hash the frozen surface; any drift exits 1 (Section 21)")
    command.add_argument("--manifest", required=True, help="path of the freeze manifest JSON")
    command.add_argument("--output", default=None,
                         help="optional path for the verification result (refuses existing)")
    command.set_defaults(handler=_run_verify_freeze)

    for name in sorted(FORWARDED_COMMANDS):
        module_name, function_name = FORWARDED_COMMANDS[name]
        command = commands.add_parser(
            name, help=f"forward to {module_name}.{function_name} with the spec as keywords")
        command.add_argument("--spec", required=True,
                             help=f"JSON object of {function_name} keyword arguments")
        command.add_argument("--output", default=None,
                             help="optional path for the returned result (refuses existing)")
        command.set_defaults(handler=_run_forwarded)
    return root


def _emit(command: str, result: Any) -> int:
    verdict = result.get("verdict") if isinstance(result, Mapping) else None
    passed = verdict is None or verdict in _PASSING_VERDICTS
    print(json.dumps({"status": "PASS" if passed else "FAIL", "command": command,
                      "verdict": verdict, "result": result},
                     sort_keys=True, ensure_ascii=False))
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = args.handler(args)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "command": args.command,
                          "category": type(exc).__name__, "detail": str(exc)},
                         sort_keys=True, ensure_ascii=False), file=sys.stderr)
        return 1
    return _emit(args.command, result)


if __name__ == "__main__":
    raise SystemExit(main())
