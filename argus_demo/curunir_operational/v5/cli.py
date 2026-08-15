"""Command line entry points for reproducible V5 operations."""
from __future__ import annotations

import argparse
import json
import os

from .adjudication import build_frozen_corpus, execute_blind_reviews
from .campaign import execute_assurance, execute_stage1, execute_stage2, finalize_sanitized_model_panel
from .epistemic_repairs import build_final_repair_packets, build_post_comparison_repair_packets, build_verification_packets
from .finalize import finalize
from .operations import comprehensive_v4_replay


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="curunir-v5")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("stage1", "stage2"):
        command = commands.add_parser(name); command.add_argument("--v4-root", required=True)
        command.add_argument("--artifact-root", required=True)
    command = commands.add_parser("assurance"); command.add_argument("--artifact-root", required=True)
    command = commands.add_parser("replay-v4"); command.add_argument("--v4-root", required=True)
    command.add_argument("--output-root", required=True)
    command = commands.add_parser("freeze-corpus"); command.add_argument("--v4-root", required=True)
    command.add_argument("--output-root", required=True)
    command = commands.add_parser("review"); command.add_argument("--packet-file", required=True)
    command.add_argument("--output-root", required=True); command.add_argument("--include-held-out", action="store_true")
    command = commands.add_parser("verification-packets")
    command.add_argument("--v4-root", required=True); command.add_argument("--artifact-root", required=True)
    command.add_argument("--blind-review-root", required=True); command.add_argument("--output-root", required=True)
    command = commands.add_parser("finalize-panel")
    command.add_argument("--artifact-root", required=True); command.add_argument("--comparison-root", required=True)
    command = commands.add_parser("repair-verification-packets")
    command.add_argument("--v4-root", required=True); command.add_argument("--artifact-root", required=True)
    command.add_argument("--panel-root", required=True)
    command = commands.add_parser("final-repair-packets")
    command.add_argument("--v4-root", required=True); command.add_argument("--artifact-root", required=True)
    command.add_argument("--panel-root", required=True)
    command = commands.add_parser("finalize")
    command.add_argument("--repo-root", required=True); command.add_argument("--artifact-root", required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "stage1": result = execute_stage1(args.v4_root, args.artifact_root)
        elif args.command == "stage2": result = execute_stage2(args.v4_root, args.artifact_root)
        elif args.command == "assurance": result = execute_assurance(args.artifact_root)
        elif args.command == "replay-v4": result = comprehensive_v4_replay(v4_root=args.v4_root, output_root=args.output_root)
        elif args.command == "freeze-corpus": result = build_frozen_corpus(args.v4_root, args.output_root)
        elif args.command == "review": result = execute_blind_reviews(args.packet_file, args.output_root,
                                                                        include_held_out=args.include_held_out)
        elif args.command == "verification-packets":
            result = build_verification_packets(args.v4_root, args.artifact_root,
                                                args.blind_review_root, args.output_root)
        elif args.command == "finalize-panel":
            result = finalize_sanitized_model_panel(args.artifact_root, args.comparison_root)
        elif args.command == "finalize":
            result = finalize(args.repo_root, args.artifact_root)
        elif args.command == "repair-verification-packets":
            result = build_post_comparison_repair_packets(args.v4_root, args.artifact_root, args.panel_root)
        else: result = build_final_repair_packets(args.v4_root, args.artifact_root, args.panel_root)
        print(json.dumps({"status": "PASS", "pid": os.getpid(), "result": result}, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "category": type(exc).__name__}, sort_keys=True))
        raise


if __name__ == "__main__":
    main()
