"""Incremental degraded-mode synchronization: OperationalDeltaBundle,
OperationalSyncReceipt, OperationalConflictRecord.

Delta bundles are PRIVILEGED store-to-store artifacts: they carry exact event
envelopes over a range and import only into a store whose chain head at the
base matches the bundle's base hash — every appended event is re-verified for
sequence continuity, chain linkage and content hash. Duplicate deltas are
recognized idempotently; a diverged store yields an explicit conflict record
and nothing is applied. Access-filtered dissemination remains the PACE bundle
(non-authoritative projection), never a delta. Integrity is sha256 only — no
signatures, no secure-transport claim.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .canonical import canonical_line, reject_non_finite, sha256, utc_now
from .store import CHAIN_GENESIS, MissionDataStore, StoreError

DELTA_FORMAT = "curunir-operational-delta-v1"
AUDIENCE = "PRIVILEGED_STORE_SYNC"


def build_delta_bundle(store: MissionDataStore, out_dir: str | Path, *, base_seq: int) -> dict[str, Any]:
    head = store.head()
    if not 0 <= base_seq <= head["event_count"]:
        raise StoreError(f"base_seq {base_seq} outside store range 0..{head['event_count']}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events = [e for e in store.events() if e["seq"] > base_seq]
    events_text = "".join(canonical_line(e) + "\n" for e in events)
    (out_dir / "delta_events.jsonl").write_text(events_text, encoding="utf-8")
    payload_dir = out_dir / "payloads"
    payload_dir.mkdir(exist_ok=True)
    payload_hashes = []
    referenced = {e["record"].get("payload_ref") for e in events
                  if e["record"].get("record_type") == "ingestion" and e["record"].get("payload_ref")}
    for digest in sorted(referenced):
        (payload_dir / digest).write_bytes(store.get_payload(digest))
        payload_hashes.append(digest)
    manifest = {
        "bundle_type": "OperationalDeltaBundle", "bundle_format": DELTA_FORMAT, "audience": AUDIENCE,
        "store_id": store.meta["store_id"],
        "base_seq": base_seq, "base_entry_hash": store.entry_hash_at(base_seq),
        "base_state_token": store.state_token_at(base_seq),
        "end_seq": head["event_count"], "end_entry_hash": head["head_hash"],
        "event_count": len(events),
        "events_sha256": hashlib.sha256(events_text.encode("utf-8")).hexdigest(),
        "payloads": payload_hashes,
        "omitted_content_declaration": "none omitted: delta bundles are full-fidelity privileged sync artifacts; "
                                       "access-filtered dissemination uses PACE bundles instead",
        "integrity_note": "sha256 integrity only; no signatures; no secure cross-domain transport claimed",
    }
    manifest["manifest_sha256"] = sha256({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    (out_dir / "delta_manifest.json").write_text(canonical_line(manifest) + "\n", encoding="utf-8")
    return manifest


def verify_delta_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    try:
        manifest = json.loads((bundle_dir / "delta_manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"valid": False, "reason": "delta_manifest.json missing"}
    failures = []
    if sha256({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest.get("manifest_sha256"):
        failures.append("manifest hash mismatch")
    events_path = bundle_dir / "delta_events.jsonl"
    if not events_path.exists():
        failures.append("delta_events.jsonl missing")
    elif hashlib.sha256(events_path.read_bytes()).hexdigest() != manifest.get("events_sha256"):
        failures.append("events hash mismatch")
    for digest in manifest.get("payloads", []):
        path = bundle_dir / "payloads" / digest
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(f"payload {digest[:12]} missing or tampered")
    return {"valid": not failures, "failures": failures, "manifest": manifest}


def _receipt(status: str, manifest: dict[str, Any], store: MissionDataStore, detail: str,
             applied: int = 0) -> dict[str, Any]:
    return {"record_type": "OperationalSyncReceipt", "status": status, "detail": detail,
            "applied_events": applied, "bundle_end_seq": manifest.get("end_seq"),
            "store_head_after": store.head(), "received_at": utc_now()}


def import_delta_bundle(store: MissionDataStore, bundle_dir: str | Path) -> dict[str, Any]:
    verification = verify_delta_bundle(bundle_dir)
    if not verification["valid"]:
        raise StoreError(f"delta bundle failed verification: {verification['failures']}")
    manifest = verification["manifest"]
    bundle_dir = Path(bundle_dir)
    head = store.head()
    base_seq = manifest["base_seq"]
    if head["event_count"] < base_seq:
        return {"record_type": "OperationalConflictRecord", "conflict": "MISSING_BASE",
                "detail": f"store head {head['event_count']} is before bundle base {base_seq}; "
                          "an earlier delta or full bundle is required first",
                "store_head": head, "bundle_base_seq": base_seq, "received_at": utc_now()}
    if store.entry_hash_at(base_seq) != manifest["base_entry_hash"]:
        return {"record_type": "OperationalConflictRecord", "conflict": "WRONG_BASE",
                "detail": "store history at the bundle base differs from the bundle's base hash; "
                          "stores have diverged and nothing was applied",
                "store_head": head, "bundle_base_seq": base_seq, "received_at": utc_now()}
    # split on \n ONLY — NOT str.splitlines(), which also breaks on U+2028 /
    # U+2029 / U+0085. canonical_line writes those RAW under ensure_ascii=False
    # (a routine artifact of scraped/pasted web text — the very content this
    # system ingests), so splitlines() would tear an event in half and fail an
    # otherwise byte-valid privileged bundle import. This is the C-1 doctrine
    # ("all readers split on \n only") applied to the delta path (review NEW-3).
    try:
        raw = (bundle_dir / "delta_events.jsonl").read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:                          # hostile bundle (M9)
        raise StoreError("delta bundle events are not valid UTF-8") from exc
    try:
        events = [json.loads(line) for line in raw.split("\n") if line.strip()]
    except json.JSONDecodeError as exc:                        # hostile bundle (MINOR-3)
        raise StoreError("delta bundle has a malformed event line") from exc
    # validate EVERY event (non-finite by value, incl. 1e400->inf) BEFORE applying
    # any, so a poison bundle is refused atomically rather than partially applied
    # with an untyped ValueError from canonical_line mid-loop (review MINOR-3)
    for event in events:
        try:
            reject_non_finite(event)
        except ValueError as exc:
            raise StoreError("delta bundle contains a non-finite float (NaN/Infinity); refusing") from exc
    applied = 0
    for event in events:
        if event["seq"] <= head["event_count"]:
            existing = store.entry_hash_at(event["seq"])
            if existing != event["entry_hash"]:
                return {"record_type": "OperationalConflictRecord", "conflict": "DIVERGED_OVERLAP",
                        "detail": f"event {event['seq']} in the bundle differs from the store's recorded event; "
                                  "unresolved conflict, nothing further applied",
                        "store_head": store.head(), "bundle_base_seq": base_seq, "received_at": utc_now()}
            continue  # duplicate portion: idempotent skip
        for digest in ([event["record"].get("payload_ref")] if event["record"].get("record_type") == "ingestion"
                       and event["record"].get("payload_ref") else []):
            body_path = bundle_dir / "payloads" / digest
            if body_path.exists():
                store.put_payload(body_path.read_bytes())
        store.append_imported_event(event)
        applied += 1
    if applied == 0:
        return _receipt("DUPLICATE_DELTA", manifest, store, "all bundle events already present; idempotent no-op")
    return _receipt("APPLIED", manifest, store, "delta applied with per-event chain verification", applied)


def build_full_bundle(store: MissionDataStore, out_dir: str | Path) -> dict[str, Any]:
    manifest = build_delta_bundle(store, out_dir, base_seq=0)
    assert manifest["base_entry_hash"] == CHAIN_GENESIS
    return manifest
