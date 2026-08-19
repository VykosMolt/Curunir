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
from .store import (CHAIN_GENESIS, MissionDataStore, StoreError,
                    _export_write_bytes, _no_duplicate_keys,
                    _refuse_dest_store_overlap)

DELTA_FORMAT = "curunir-operational-delta-v1"
AUDIENCE = "PRIVILEGED_STORE_SYNC"


def _scan_digests(value: Any, available: set, out: set) -> None:
    """Collect every string in `value` — dict KEYS and values, nested — that names
    a payload in `available`. Family/field-AGNOSTIC: a payload is content-addressed,
    so any field/key that equals a payload file name is a reference (the V6.7
    `semantic_document` normalized_sha256/fields_sha256, the V2 `ingestion`
    payload_ref, any future family), without binding to a record type. Keys are
    scanned too (review A-F1 / B-5)."""
    if isinstance(value, str):
        if value in available:
            out.add(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_digests(k, available, out)
            _scan_digests(v, available, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan_digests(item, available, out)


def _referenced_payloads(store: MissionDataStore, events: list) -> set:
    """Every payload digest referenced by these events (the old
    `record_type == "ingestion"` predicate omitted 100% of a V6.7 mission's
    evidence while declaring "none omitted" — review A-F1)."""
    available = {p.name for p in store.payload_dir.iterdir()}
    referenced: set = set()
    for event in events:
        _scan_digests(event.get("record"), available, referenced)
    return referenced


def _is_digest(value: Any) -> bool:
    """True iff `value` is a 64-hex sha256 content address — safe to use as a
    payload file name. A payload_ref from an untrusted bundle used as a path
    otherwise reads an arbitrary host file into the store (the import_from M4
    guard, applied to the delta path; review B-4 / NEW-A3)."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def build_delta_bundle(store: MissionDataStore, out_dir: str | Path, *, base_seq: int) -> dict[str, Any]:
    head = store.head()
    if not 0 <= base_seq <= head["event_count"]:
        raise StoreError(f"base_seq {base_seq} outside store range 0..{head['event_count']}")
    out_dir = Path(out_dir)
    _refuse_dest_store_overlap(out_dir, source_root=store.root, replace_dest=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    events = [e for e in store.events() if e["seq"] > base_seq]
    events_text = "".join(canonical_line(e) + "\n" for e in events)
    _export_write_bytes(out_dir / "delta_events.jsonl", events_text.encode("utf-8"))
    payload_dir = out_dir / "payloads"
    if payload_dir.exists() and (payload_dir.is_symlink() or not payload_dir.is_dir()):
        raise StoreError("delta dest payloads is not a regular directory; refusing")
    payload_dir.mkdir(exist_ok=True)
    payload_hashes = []
    referenced = _referenced_payloads(store, events)      # family-agnostic (A-F1)
    for digest in sorted(referenced):
        _export_write_bytes(payload_dir / digest, store.get_payload(digest))
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
    _export_write_bytes(out_dir / "delta_manifest.json",
                        (canonical_line(manifest) + "\n").encode("utf-8"))
    return manifest


def verify_delta_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    bundle_dir = Path(bundle_dir)
    # the manifest is untrusted: parse it defensively and fail CLOSED (valid:False)
    # on any malformed/hostile shape, rather than raising an untyped
    # UnicodeDecodeError / AttributeError / RecursionError from a later .items() /
    # index (review NEW-A2 / B-4)
    try:
        manifest = json.loads((bundle_dir / "delta_manifest.json").read_bytes().decode("utf-8"),
                              object_pairs_hook=_no_duplicate_keys)
    except FileNotFoundError:
        return {"valid": False, "reason": "delta_manifest.json missing"}
    except (ValueError, UnicodeDecodeError, RecursionError, OSError):   # incl. IsADirectoryError (A-F5)
        return {"valid": False, "reason": "delta_manifest.json is not valid JSON", "manifest": {}}
    if not isinstance(manifest, dict):
        return {"valid": False, "reason": "delta_manifest.json is not an object", "manifest": {}}
    failures = []
    if sha256({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest.get("manifest_sha256"):
        failures.append("manifest hash mismatch")
    if not isinstance(manifest.get("base_seq"), int) or isinstance(manifest.get("base_seq"), bool):
        failures.append("base_seq is not an integer")   # import_delta_bundle compares it numerically
    if not isinstance(manifest.get("base_entry_hash"), str):
        failures.append("base_entry_hash is missing or not a string")   # import indexes it (A-F4)
    events_path = bundle_dir / "delta_events.jsonl"
    try:
        if events_path.is_symlink() or events_path.is_dir() or not events_path.is_file():
            events_bytes = None
        else:
            events_bytes = events_path.read_bytes()
    except OSError:                                      # a directory in its place (A-F5)
        events_bytes = None
    if events_bytes is None:
        failures.append("delta_events.jsonl missing or unreadable")
    elif hashlib.sha256(events_bytes).hexdigest() != manifest.get("events_sha256"):
        failures.append("events hash mismatch")
    payloads = manifest.get("payloads", [])
    if not isinstance(payloads, list):                  # a non-list is a FAILURE, not a silent skip (A-F6)
        failures.append("manifest payloads is not a list")
        payloads = []
    for digest in payloads:
        if not _is_digest(digest):        # never use an untrusted ref as a path (traversal)
            failures.append("payload ref is not a content-address digest")
            continue
        path = bundle_dir / "payloads" / digest
        try:
            if path.is_symlink() or path.is_dir() or not path.is_file():
                ok = False
            else:
                ok = hashlib.sha256(path.read_bytes()).hexdigest() == digest
        except OSError:                                 # a directory named like a digest (A-F5)
            ok = False
        if not ok:
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
        # verify's early returns carry "reason" (not "failures") — read both, or a
        # malformed manifest re-raises an untyped KeyError, the exact unswept half
        # of the round-22 NEW-A2 repair (review A-F3)
        raise StoreError("delta bundle failed verification: "
                         f"{verification.get('failures') or [verification.get('reason', 'invalid')]}")
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
        events = [json.loads(line, object_pairs_hook=_no_duplicate_keys)
                  for line in raw.split("\n") if line.strip()]
    except json.JSONDecodeError as exc:                        # hostile bundle (MINOR-3)
        raise StoreError("delta bundle has a malformed event line") from exc
    except ValueError as exc:                                  # duplicate key (R25B-4)
        raise StoreError("delta bundle has a malformed event line") from exc
    # validate EVERY event (shape + non-finite by value, incl. 1e400->inf) BEFORE
    # applying any, so a poison/malformed bundle is refused atomically with a
    # typed StoreError rather than partially applied then crashing mid-loop on an
    # untyped TypeError/KeyError/ValueError while indexing event[...] (review
    # MINOR-3 / N-3)
    for event in events:
        if not (isinstance(event, dict) and isinstance(event.get("seq"), int)
                and not isinstance(event.get("seq"), bool)
                and isinstance(event.get("entry_hash"), str)
                and isinstance(event.get("event_type"), str)      # _entry_hash / append index these
                and isinstance(event.get("recorded_time"), str)
                and isinstance(event.get("actor"), str)
                and isinstance(event.get("prev_hash"), str)
                and isinstance(event.get("record"), dict)):       # record must be a dict — the apply loop .get()s it
            raise StoreError("delta bundle has a malformed event envelope; refusing")
        ref = event["record"].get("payload_ref") if event["record"].get("record_type") == "ingestion" else None
        if ref is not None and not _is_digest(ref):
            raise StoreError("delta bundle names a non-digest payload_ref; refusing")
        try:
            reject_non_finite(event)
        except ValueError as exc:
            raise StoreError("delta bundle contains a non-finite float (NaN/Infinity); refusing") from exc
    manifest_payloads = set(manifest.get("payloads", []))    # verified present+hashed by verify
    to_apply = []
    for event in events:
        if event["seq"] <= head["event_count"]:
            existing = store.entry_hash_at(event["seq"])
            if existing != event["entry_hash"]:
                return {"record_type": "OperationalConflictRecord", "conflict": "DIVERGED_OVERLAP",
                        "detail": f"event {event['seq']} in the bundle differs from the store's recorded event; "
                                  "unresolved conflict, nothing further applied",
                        "store_head": store.head(), "bundle_base_seq": base_seq, "received_at": utc_now()}
            continue  # duplicate portion: idempotent skip
        to_apply.append(event)
    # refuse a hostile suffix BEFORE planting payloads or applying a prefix
    # (review R26B-3). DIVERGED_OVERLAP above already returned without writes.
    def _payloads_for(event):
        refs: set = set()
        _scan_digests(event.get("record"), manifest_payloads, refs)
        return [(bundle_dir / "payloads" / digest).read_bytes()
                for digest in sorted(refs)]

    applied = store.apply_imported_events_locked(to_apply, _payloads_for)
    if applied == 0:
        return _receipt("DUPLICATE_DELTA", manifest, store, "all bundle events already present; idempotent no-op")
    return _receipt("APPLIED", manifest, store, "delta applied with per-event chain verification", applied)


def build_full_bundle(store: MissionDataStore, out_dir: str | Path) -> dict[str, Any]:
    manifest = build_delta_bundle(store, out_dir, base_seq=0)
    assert manifest["base_entry_hash"] == CHAIN_GENESIS
    return manifest
