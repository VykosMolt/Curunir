"""Process-isolated CLI for the V3 node, sync and projection call paths."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..canonical import canonical_line
from .models import (AccessMarkingV3, ActorIdentity, NodeIdentity, RevocationRecord,
                     SyncReceipt, SyncRequest)
from .node import DistributedNode
from .strategic import render_strategic_html, strategic_view
from .sync import (acknowledge_receipt, build_bundle, import_bundle, make_offer, make_request,
                   read_bundle, write_bundle)


def _load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_line(value) + "\n", encoding="utf-8")


def _emit(value: Any) -> None:
    payload = {"pid": os.getpid(), **value} if isinstance(value, dict) else {"pid": os.getpid(), "result": value}
    print(json.dumps(payload, indent=2, sort_keys=True))


def _request(record: dict[str, Any]) -> SyncRequest:
    record = dict(record)
    for key in ("requested_scope", "supported_schemas"):
        record[key] = tuple(record[key])
    return SyncRequest(**record)


def _receipt(record: dict[str, Any]) -> SyncReceipt:
    record = dict(record)
    for key in ("accepted_events", "duplicates", "quarantines", "causal_gaps", "conflicts"):
        record[key] = tuple(record.get(key, ()))
    return SyncReceipt(**record)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curunir-v3", description="Distributed research-shadow node CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init-node"); p.add_argument("--store", required=True); p.add_argument("--config", required=True)
    p = sub.add_parser("append-event"); p.add_argument("--store", required=True); p.add_argument("--spec", required=True)
    p = sub.add_parser("offer"); p.add_argument("--store", required=True); p.add_argument("--destination", required=True); p.add_argument("--access", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("request"); p.add_argument("--store", required=True); p.add_argument("--source", required=True); p.add_argument("--access", required=True); p.add_argument("--scope", required=True); p.add_argument("--out", required=True); p.add_argument("--max-events", type=int, default=100000); p.add_argument("--max-bytes", type=int, default=256000000)
    p = sub.add_parser("build-bundle"); p.add_argument("--store", required=True); p.add_argument("--request", required=True); p.add_argument("--out", required=True); p.add_argument("--time", required=True); p.add_argument("--expiry"); p.add_argument("--reverse", action="store_true")
    p = sub.add_parser("import-bundle"); p.add_argument("--store", required=True); p.add_argument("--bundle", required=True); p.add_argument("--time", required=True); p.add_argument("--receipt-out", required=True)
    p = sub.add_parser("ack-receipt"); p.add_argument("--store", required=True); p.add_argument("--bundle", required=True); p.add_argument("--receipt", required=True)
    p = sub.add_parser("project"); p.add_argument("--store", required=True); p.add_argument("--access", required=True); p.add_argument("--out", required=True); p.add_argument("--valid-at"); p.add_argument("--known-at"); p.add_argument("--audit", action="store_true")
    p = sub.add_parser("resolve-conflict"); p.add_argument("--store", required=True); p.add_argument("--conflict", required=True); p.add_argument("--actor", required=True); p.add_argument("--resolution", required=True); p.add_argument("--marking", required=True); p.add_argument("--time", required=True); p.add_argument("--nonce", required=True)
    p = sub.add_parser("revoke"); p.add_argument("--store", required=True); p.add_argument("--record", required=True)
    p = sub.add_parser("export"); p.add_argument("--store", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("import"); p.add_argument("--source", required=True); p.add_argument("--store", required=True)
    p = sub.add_parser("verify"); p.add_argument("--store", required=True)
    p = sub.add_parser("render-strategic"); p.add_argument("--store", required=True); p.add_argument("--access", required=True); p.add_argument("--json-out", required=True); p.add_argument("--html-out", required=True)
    args = parser.parse_args(argv)

    if args.command == "init-node":
        config = _load(args.config)
        identity = NodeIdentity(**{**config["identity"],
                                   "sharing_scopes": tuple(config["identity"]["sharing_scopes"]),
                                   "supported_protocol_versions": tuple(config["identity"].get("supported_protocol_versions", ("curunir-distributed-sync-v3",)))})
        actors = tuple(ActorIdentity(**{**record, "roles": tuple(record["roles"]),
                                         "compartments": tuple(record.get("compartments", ())),
                                         "mission_scopes": tuple(record.get("mission_scopes", ())),
                                         "authority_scopes": tuple(record.get("authority_scopes", ()))})
                       for record in config["actors"])
        peers = {key: NodeIdentity(**{**record, "sharing_scopes": tuple(record["sharing_scopes"]),
                                      "supported_protocol_versions": tuple(record.get("supported_protocol_versions", ("curunir-distributed-sync-v3",)))})
                 for key, record in config["peers"].items()}
        node = DistributedNode.create(args.store, identity, actors, config["keys"], peers)
        _emit({"status": "INITIALIZED", "manifest": node.manifest.to_record()}); return 0

    if args.command == "import":
        node = DistributedNode.import_open(args.source, args.store)
        _emit({"status": "IMPORTED", "integrity": node.verify_integrity()}); return 0

    node = DistributedNode(args.store)
    if args.command == "append-event":
        spec = _load(args.spec)
        spec["marking"] = AccessMarkingV3(**{**spec["marking"],
                                             "compartments": tuple(spec["marking"].get("compartments", ())),
                                             "releasability": tuple(spec["marking"].get("releasability", ())),
                                             "mission_scopes": tuple(spec["marking"].get("mission_scopes", ())),
                                             "originator_controls": tuple(spec["marking"].get("originator_controls", ()))})
        spec["parent_event_ids"] = tuple(spec.get("parent_event_ids", ()))
        event = node.append_action(**spec)
        _emit({"status": "APPENDED", "event_id": event.event_id,
               "opaque_state_token": node.opaque_state_token()}); return 0
    if args.command == "offer":
        offer = make_offer(node, args.destination, _load(args.access)); _write(args.out, offer.to_record())
        _emit({"status": "OFFERED", "offer": offer.to_record()}); return 0
    if args.command == "request":
        request = make_request(node, args.source, _load(args.access),
                               requested_scope=tuple(part for part in args.scope.split(",") if part),
                               max_events=args.max_events, max_bytes=args.max_bytes)
        _write(args.out, request.to_record()); _emit({"status": "REQUESTED", "out": args.out}); return 0
    if args.command == "build-bundle":
        request = _request(_load(args.request))
        bundle = build_bundle(node, request, creation_time=args.time, expiry=args.expiry,
                              reverse_delivery_order=args.reverse)
        result = write_bundle(bundle, args.out); _emit({"status": "BUNDLE_WRITTEN", **result}); return 0
    if args.command == "import-bundle":
        bundle = read_bundle(args.bundle); receipt = import_bundle(node, bundle, admitted_time=args.time)
        _write(args.receipt_out, receipt.to_record())
        _emit({"status": receipt.verification_state, "receipt_path": args.receipt_out,
               "public_summary": receipt.public_summary}); return 0 if receipt.verification_state in ("ACCEPTED", "DUPLICATE") else 3
    if args.command == "ack-receipt":
        bundle = read_bundle(args.bundle); receipt = _receipt(_load(args.receipt))
        acknowledge_receipt(node, bundle, receipt); _emit({"status": "RECEIPT_ACKNOWLEDGED"}); return 0
    if args.command == "project":
        projection = node.projection(_load(args.access), valid_at=args.valid_at, known_at=args.known_at,
                                     privileged_audit=args.audit)
        _write(args.out, projection); _emit({"status": "PROJECTED", "out": args.out,
                                             "opaque_state_token": projection["opaque_state_token"]}); return 0
    if args.command == "resolve-conflict":
        marking_data = _load(args.marking)
        marking = AccessMarkingV3(**{**marking_data,
                                     "compartments": tuple(marking_data.get("compartments", ())),
                                     "releasability": tuple(marking_data.get("releasability", ())),
                                     "mission_scopes": tuple(marking_data.get("mission_scopes", ())),
                                     "originator_controls": tuple(marking_data.get("originator_controls", ()))})
        event = node.resolve_conflict(args.conflict, actor_id=args.actor, resolution=_load(args.resolution),
                                      recorded_time=args.time, nonce=args.nonce, marking=marking)
        _emit({"status": "RESOLUTION_EVENT_APPENDED", "event_id": event.event_id}); return 0
    if args.command == "revoke":
        data = _load(args.record); record = RevocationRecord(**data); node.revoke(record)
        _emit({"status": "REVOKED", "subject_type": record.subject_type,
               "subject_id": record.subject_id}); return 0
    if args.command == "export":
        _emit({"status": "EXPORTED", "manifest": node.export_open(args.out)}); return 0
    if args.command == "verify":
        result = node.verify_integrity(); _emit(result); return 0 if result["valid"] else 3
    if args.command == "render-strategic":
        view = strategic_view(node.projection(_load(args.access)))
        _write(args.json_out, view); Path(args.html_out).write_text(render_strategic_html(view), encoding="utf-8")
        _emit({"status": "RENDERED", "json": args.json_out, "html": args.html_out,
               "integrity_hash": view["integrity_hash"]}); return 0
    return 2


def entry() -> int:
    try:
        return main()
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        # Deliberately category-only: no hidden ids, counts, sequence values or
        # compartment names are reflected into a requester-visible error.
        print(json.dumps({"status": "REJECTED", "error_category": type(exc).__name__,
                          "detail": "request failed bounded validation or authorization"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entry())
