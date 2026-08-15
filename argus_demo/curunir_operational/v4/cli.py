"""Process-safe CLI for V4 preregistration and later campaign/review execution."""
from __future__ import annotations

import argparse
import json
import os

from .investigation import deterministic_plan, preregister_case
from .io import read_json, write_json
from .mission import initialize_review_nodes, receive_review_packet, serialize_review_packet
from .campaign import load_case, renormalize_capture, retry_captured_lead, run_live_capture
from .pipeline import assemble_campaign, replay_campaign
from .stress import run_corpus_stress
from .mutation import run_mutation_suite, write_security_review


def preregister(args: argparse.Namespace) -> dict[str, object]:
    spec = read_json(args.spec)
    subquestions = spec.pop("subquestions")
    case = preregister_case(**spec)
    plan = deterministic_plan(case, subquestions)
    write_json(args.case_out, case.to_record(), refuse_existing=True)
    write_json(args.plan_out, plan, refuse_existing=True)
    return {"status": "PREREGISTERED", "case_id": case.case_id,
            "integrity_hash": case.integrity_hash, "pid": os.getpid()}


def review_receive(args: argparse.Namespace) -> dict[str, object]:
    receipt = receive_review_packet(kernel_node_root=args.node_root, packet_path=args.packet,
                                    receipt_path=args.receipt)
    return {"status": receipt["verification_state"], "packet_id": receipt["packet_id"],
            "receipt": args.receipt, "pid": os.getpid()}


def review_init(args: argparse.Namespace) -> dict[str, object]:
    manifests = initialize_review_nodes(args.nodes_root)
    return {"status": "REVIEW_NODES_INITIALIZED", "manifests": manifests, "pid": os.getpid()}


def review_send(args: argparse.Namespace) -> dict[str, object]:
    packet = serialize_review_packet(
        strategic_node_root=os.path.join(args.nodes_root, "STRATEGIC_EVIDENCE_NODE"),
        destination_node_id="KERNEL_REVIEW_NODE", case_id=args.case_id,
        handoffs=read_json(args.handoffs), proposals=read_json(args.proposals),
        access_context={"releasability": ["PUBLIC"]}, output_path=args.packet)
    return {"status": "REVIEW_PACKET_SERIALIZED", "packet_id": packet["packet_id"],
            "payload_hash": packet["payload_hash"], "pid": os.getpid()}


def live_capture(args: argparse.Namespace) -> dict[str, object]:
    metrics = run_live_capture(case=load_case(read_json(args.case)),
                               discovery_spec=read_json(args.discovery_spec), output_root=args.output_root)
    return {"status": "LIVE_CAPTURE_COMPLETE", **metrics, "pid": os.getpid()}


def assemble(args: argparse.Namespace) -> dict[str, object]:
    case = read_json(args.case)
    metrics = assemble_campaign(case=case, capture_root=args.capture_root,
                                analysis_spec=read_json(args.analysis_spec),
                                analysis_output=args.analysis_output, report_output=args.report_output,
                                admission_output=args.admission_output, repository_root=args.repository_root)
    return {"status": "ANALYSIS_COMPLETE_REVIEW_PENDING", **metrics, "pid": os.getpid()}


def replay(args: argparse.Namespace) -> dict[str, object]:
    result = replay_campaign(case=read_json(args.case), capture_root=args.capture_root,
                             live_analysis_root=args.live_analysis_root,
                             live_report_root=args.live_report_root, replay_root=args.replay_root)
    return {"status": "OFFLINE_REPLAY_COMPLETE", **result, "pid": os.getpid()}


def renormalize(args: argparse.Namespace) -> dict[str, object]:
    return {"status": "RENORMALIZED_FROM_CUSTODY", **renormalize_capture(args.capture_root), "pid": os.getpid()}


def retry(args: argparse.Namespace) -> dict[str, object]:
    result = retry_captured_lead(case=load_case(read_json(args.case)), capture_root=args.capture_root,
                                 url=args.url, maximum_bytes=args.maximum_bytes,
                                 timeout_seconds=args.timeout_seconds)
    return {"status": "BOUNDED_RETRY_COMPLETE", **result, "pid": os.getpid()}


def stress(args: argparse.Namespace) -> dict[str, object]:
    return {"status": "STRESS_COMPLETE", **run_corpus_stress(args.output_root, args.repository_root),
            "pid": os.getpid()}


def mutate(args: argparse.Namespace) -> dict[str, object]:
    return {"status": "MUTATION_COMPLETE", **run_mutation_suite(args.output, args.repository_root),
            "pid": os.getpid()}


def security(args: argparse.Namespace) -> dict[str, object]:
    return {"status": "SECURITY_THREAT_MODEL_WRITTEN", **write_security_review(args.output), "pid": os.getpid()}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="curunir-v4")
    commands = value.add_subparsers(dest="command", required=True)
    command = commands.add_parser("preregister")
    command.add_argument("--spec", required=True); command.add_argument("--case-out", required=True)
    command.add_argument("--plan-out", required=True); command.set_defaults(handler=preregister)
    command = commands.add_parser("review-receive")
    command.add_argument("--node-root", required=True); command.add_argument("--packet", required=True)
    command.add_argument("--receipt", required=True); command.set_defaults(handler=review_receive)
    command = commands.add_parser("review-init")
    command.add_argument("--nodes-root", required=True); command.set_defaults(handler=review_init)
    command = commands.add_parser("review-send")
    command.add_argument("--nodes-root", required=True); command.add_argument("--case-id", required=True)
    command.add_argument("--handoffs", required=True); command.add_argument("--proposals", required=True)
    command.add_argument("--packet", required=True); command.set_defaults(handler=review_send)
    command = commands.add_parser("live-capture")
    command.add_argument("--case", required=True); command.add_argument("--discovery-spec", required=True)
    command.add_argument("--output-root", required=True); command.set_defaults(handler=live_capture)
    command = commands.add_parser("assemble")
    command.add_argument("--case", required=True); command.add_argument("--capture-root", required=True)
    command.add_argument("--analysis-spec", required=True); command.add_argument("--analysis-output", required=True)
    command.add_argument("--report-output", required=True); command.add_argument("--admission-output", required=True)
    command.add_argument("--repository-root", required=True); command.set_defaults(handler=assemble)
    command = commands.add_parser("replay")
    command.add_argument("--case", required=True); command.add_argument("--capture-root", required=True)
    command.add_argument("--live-analysis-root", required=True); command.add_argument("--live-report-root", required=True)
    command.add_argument("--replay-root", required=True); command.set_defaults(handler=replay)
    command = commands.add_parser("renormalize")
    command.add_argument("--capture-root", required=True); command.set_defaults(handler=renormalize)
    command = commands.add_parser("retry")
    command.add_argument("--case", required=True); command.add_argument("--capture-root", required=True)
    command.add_argument("--url", required=True); command.add_argument("--maximum-bytes", type=int, required=True)
    command.add_argument("--timeout-seconds", type=float, default=60.0); command.set_defaults(handler=retry)
    command = commands.add_parser("stress")
    command.add_argument("--output-root", required=True); command.add_argument("--repository-root", required=True)
    command.set_defaults(handler=stress)
    command = commands.add_parser("mutate")
    command.add_argument("--output", required=True); command.add_argument("--repository-root", required=True)
    command.set_defaults(handler=mutate)
    command = commands.add_parser("security")
    command.add_argument("--output", required=True); command.set_defaults(handler=security)
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        print(json.dumps(args.handler(args), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "category": type(exc).__name__}, sort_keys=True))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
