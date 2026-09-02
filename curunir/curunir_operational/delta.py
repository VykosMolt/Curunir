"""Full-fidelity incremental synchronization between stores.

A delta is admitted as one transaction, and only once its base, its whole
envelope suffix and every referenced payload have been validated. This format
is unfiltered; filtered dissemination goes through a PACE bundle instead.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .canonical import (
    canonical_line,
    parse_json_strict,
    parse_time,
    require_aware,
    sha256,
    utc_now,
)
from .store import (
    CHAIN_GENESIS,
    MissionDataStore,
    StoreError,
    _entry_hash,
    _exclusive_write,
    _fsync_directory,
    _is_digest,
    _log_lines,
    _read_regular,
    _require_real_directory,
    refuse_output_overlap,
)

DELTA_FORMAT = "curunir-operational-delta-v1"
AUDIENCE = "PRIVILEGED_STORE_SYNC"

# These fields name stored payloads by contract. The scanner below also picks
# up any other string that happens to name a payload the source store holds.
PAYLOAD_FIELDS: dict[str, tuple[str, ...]] = {
    "ingestion": ("payload_ref",),
    "fabric_manifestation": ("content_sha256",),
    "semantic_document": ("content_sha256", "normalized_sha256", "fields_sha256"),
}


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


def _declared_payload_refs(events: Iterable[Mapping[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for event in events:
        record = event.get("record")
        if not isinstance(record, Mapping):
            continue
        for field in PAYLOAD_FIELDS.get(str(record.get("record_type")), ()):
            value = record.get(field)
            if value:
                if not isinstance(value, str) or not _is_digest(value):
                    raise StoreError(
                        f"{record.get('record_type')} names an invalid payload in {field}")
                refs.add(value)
    return refs


def _source_payload_refs(
    store: MissionDataStore,
    events: list[Mapping[str, Any]],
) -> set[str]:
    available = {
        path.name for path in store.payload_dir.iterdir()
        if _is_digest(path.name)
    }
    refs = _declared_payload_refs(events)
    for event in events:
        refs.update(
            value for value in _walk_strings(event.get("record"))
            if value in available
        )
    return refs


# Older name kept for the store-integrity tests.
_referenced_payloads = _source_payload_refs


def _publish_bundle(
    destination: Path,
    *,
    events_bytes: bytes,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> None:
    stage = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.delta.",
        dir=destination.parent,
    ))
    try:
        (stage / "payloads").mkdir(mode=0o700)
        _exclusive_write(stage / "delta_events.jsonl", events_bytes)
        for digest, body in sorted(payloads.items()):
            _exclusive_write(stage / "payloads" / digest, body)
        _exclusive_write(
            stage / "delta_manifest.json",
            (canonical_line(manifest) + "\n").encode("utf-8"),
        )
        _fsync_directory(stage / "payloads")
        _fsync_directory(stage)
        os.rename(stage, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def build_delta_bundle(
    store: MissionDataStore,
    out_dir: str | Path,
    *,
    base_seq: int,
) -> dict[str, Any]:
    if type(base_seq) is not int:
        raise StoreError("base_seq must be an integer")
    destination = refuse_output_overlap(
        out_dir, source_root=store.root, label="delta")
    with store._append_lock():
        store._catch_up()
        head = store.head()
        if not 0 <= base_seq <= head["event_count"]:
            raise StoreError(
                f"base_seq {base_seq} outside store range 0..{head['event_count']}")
        events = [event for event in store.events() if event["seq"] > base_seq]
        events_bytes = b"".join(
            (canonical_line(event) + "\n").encode("utf-8")
            for event in events
        )
        payloads = {
            digest: store.get_payload(digest)
            for digest in sorted(_source_payload_refs(store, events))
        }
        manifest: dict[str, Any] = {
            "bundle_type": "OperationalDeltaBundle",
            "bundle_format": DELTA_FORMAT,
            "audience": AUDIENCE,
            "store_id": store.meta["store_id"],
            "base_seq": base_seq,
            "base_entry_hash": store.entry_hash_at(base_seq),
            "base_state_token": store.state_token_at(base_seq),
            "end_seq": head["event_count"],
            "end_entry_hash": head["head_hash"],
            "event_count": len(events),
            "events_sha256": hashlib.sha256(events_bytes).hexdigest(),
            "payloads": sorted(payloads),
            "omitted_content_declaration": (
                "none omitted: delta bundles are full-fidelity privileged sync "
                "artifacts; access-filtered dissemination uses PACE bundles instead"
            ),
            "integrity_note": (
                "sha256 integrity only; no signatures; "
                "no secure cross-domain transport claimed"
            ),
        }
        manifest["manifest_sha256"] = sha256({
            key: value for key, value in manifest.items()
            if key != "manifest_sha256"
        })
    _publish_bundle(
        destination,
        events_bytes=events_bytes,
        payloads=payloads,
        manifest=manifest,
    )
    return manifest


def _validate_bundle_events(
    raw: bytes,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    expected_seq = manifest["base_seq"] + 1
    expected_prev = manifest["base_entry_hash"]
    last_recorded: str | None = None
    for number, line in enumerate(
        _log_lines(raw, label="delta_events.jsonl"),
        start=1,
    ):
        try:
            event = parse_json_strict(line, label=f"delta event {number}")
        except ValueError as exc:
            raise StoreError(f"delta event {number} is malformed: {exc}") from exc
        if not isinstance(event, dict):
            raise StoreError(f"delta event {number} is not an object")
        required = {
            "seq", "event_id", "event_type", "recorded_time", "actor",
            "record", "prev_hash", "entry_hash",
        }
        if set(event) != required:
            raise StoreError(f"delta event {number} has an invalid envelope")
        if type(event["seq"]) is not int or event["seq"] != expected_seq:
            raise StoreError(f"delta event {number} is out of sequence")
        if not isinstance(event["event_type"], str) \
                or not isinstance(event["record"], dict) \
                or not isinstance(event["actor"], str) or not event["actor"]:
            raise StoreError(f"delta event {number} has invalid typed fields")
        try:
            require_aware(event["recorded_time"])
        except (TypeError, ValueError) as exc:
            raise StoreError(f"delta event {number} has invalid time") from exc
        if last_recorded is not None \
                and parse_time(event["recorded_time"]) < parse_time(last_recorded):
            raise StoreError(f"delta event {number} decreases recorded_time")
        if event["prev_hash"] != expected_prev:
            raise StoreError(f"delta event {number} does not link to its predecessor")
        digest = _entry_hash(
            event["seq"],
            event["event_type"],
            event["recorded_time"],
            event["actor"],
            event["record"],
            event["prev_hash"],
        )
        if event["entry_hash"] != digest:
            raise StoreError(f"delta event {number} fails content-hash verification")
        if event["event_id"] != f"evt-{event['seq']:06d}-{digest[:8]}":
            raise StoreError(f"delta event {number} has an unbound event_id")
        events.append(event)
        expected_seq += 1
        expected_prev = digest
        last_recorded = event["recorded_time"]
    if len(events) != manifest["event_count"]:
        raise StoreError("delta event_count does not match event log")
    if manifest["end_seq"] != manifest["base_seq"] + len(events):
        raise StoreError("delta end_seq does not match its range")
    expected_end = expected_prev if events else manifest["base_entry_hash"]
    if manifest["end_entry_hash"] != expected_end:
        raise StoreError("delta end_entry_hash does not match its event log")
    return events


def _verified_delta(bundle_dir: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_dir)
    _require_real_directory(bundle, label="delta directory")
    _require_real_directory(bundle / "payloads", label="delta payload directory")
    try:
        manifest = parse_json_strict(
            _read_regular(bundle / "delta_manifest.json", label="delta manifest"),
            label="delta manifest",
        )
    except ValueError as exc:
        if isinstance(exc, StoreError):
            raise
        raise StoreError(str(exc)) from exc
    if not isinstance(manifest, dict):
        raise StoreError("delta manifest is not an object")
    if manifest.get("bundle_type") != "OperationalDeltaBundle" \
            or manifest.get("bundle_format") != DELTA_FORMAT \
            or manifest.get("audience") != AUDIENCE:
        raise StoreError("delta manifest type, format, or audience is invalid")
    expected_manifest_hash = sha256({
        key: value for key, value in manifest.items()
        if key != "manifest_sha256"
    })
    if manifest.get("manifest_sha256") != expected_manifest_hash:
        raise StoreError("delta manifest hash mismatch")
    for field in ("base_seq", "end_seq", "event_count"):
        if type(manifest.get(field)) is not int or manifest[field] < 0:
            raise StoreError(f"delta manifest {field} is not a non-negative integer")
    for field in ("store_id", "base_entry_hash", "end_entry_hash", "events_sha256"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise StoreError(f"delta manifest {field} is missing or invalid")
    events_bytes = _read_regular(bundle / "delta_events.jsonl", label="delta event log")
    if hashlib.sha256(events_bytes).hexdigest() != manifest["events_sha256"]:
        raise StoreError("delta events hash mismatch")
    events = _validate_bundle_events(events_bytes, manifest)
    names = manifest.get("payloads")
    if not isinstance(names, list) or len(names) != len(set(names)):
        raise StoreError("delta manifest payloads must be a unique list")
    payloads: dict[str, bytes] = {}
    for name in names:
        if not isinstance(name, str) or not _is_digest(name):
            raise StoreError("delta payload reference is not a content-address digest")
        body = _read_regular(bundle / "payloads" / name, label="delta payload")
        if hashlib.sha256(body).hexdigest() != name:
            raise StoreError(f"delta payload {name} is missing or tampered")
        payloads[name] = body
    missing = _declared_payload_refs(events) - set(payloads)
    if missing:
        raise StoreError(
            f"delta omits declared payloads: {sorted(missing)}")
    return {"manifest": manifest, "events": events, "payloads": payloads}


def verify_delta_bundle(bundle_dir: str | Path) -> dict[str, Any]:
    try:
        verified = _verified_delta(bundle_dir)
    except (StoreError, OSError, ValueError) as exc:
        return {"valid": False, "failures": [str(exc)], "manifest": {}}
    return {
        "valid": True,
        "failures": [],
        "manifest": verified["manifest"],
    }


def _receipt(
    status: str,
    manifest: Mapping[str, Any],
    store: MissionDataStore,
    detail: str,
    applied: int = 0,
) -> dict[str, Any]:
    return {
        "record_type": "OperationalSyncReceipt",
        "status": status,
        "detail": detail,
        "applied_events": applied,
        "bundle_end_seq": manifest.get("end_seq"),
        "store_head_after": store.head(),
        "received_at": utc_now(),
    }


def import_delta_bundle(
    store: MissionDataStore,
    bundle_dir: str | Path,
) -> dict[str, Any]:
    try:
        verified = _verified_delta(bundle_dir)
    except (StoreError, OSError, ValueError) as exc:
        raise StoreError(f"delta bundle failed verification: {exc}") from exc
    manifest = verified["manifest"]
    events = verified["events"]
    payloads = verified["payloads"]
    head = store.head()
    base_seq = manifest["base_seq"]
    if head["event_count"] < base_seq:
        return {
            "record_type": "OperationalConflictRecord",
            "conflict": "MISSING_BASE",
            "detail": (
                f"store head {head['event_count']} is before bundle base {base_seq}; "
                "an earlier delta or full bundle is required first"
            ),
            "store_head": head,
            "bundle_base_seq": base_seq,
            "received_at": utc_now(),
        }
    if store.entry_hash_at(base_seq) != manifest["base_entry_hash"]:
        return {
            "record_type": "OperationalConflictRecord",
            "conflict": "WRONG_BASE",
            "detail": (
                "store history at the bundle base differs from the bundle's "
                "base hash; stores diverged and nothing was applied"
            ),
            "store_head": head,
            "bundle_base_seq": base_seq,
            "received_at": utc_now(),
        }
    suffix: list[dict[str, Any]] = []
    for event in events:
        if event["seq"] <= head["event_count"]:
            if store.entry_hash_at(event["seq"]) != event["entry_hash"]:
                return {
                    "record_type": "OperationalConflictRecord",
                    "conflict": "DIVERGED_OVERLAP",
                    "detail": (
                        f"event {event['seq']} differs from the store; "
                        "nothing was applied"
                    ),
                    "store_head": store.head(),
                    "bundle_base_seq": base_seq,
                    "received_at": utc_now(),
                }
            continue
        suffix.append(event)

    def payloads_for(event: Mapping[str, Any]) -> Iterable[tuple[str, bytes]]:
        referenced = {
            value for value in _walk_strings(event["record"])
            if value in payloads
        }
        return [(digest, payloads[digest]) for digest in sorted(referenced)]

    applied = store.apply_imported_events_locked(suffix, payloads_for)
    if applied == 0:
        return _receipt(
            "DUPLICATE_DELTA",
            manifest,
            store,
            "all bundle events already present; idempotent no-op",
        )
    return _receipt(
        "APPLIED",
        manifest,
        store,
        "delta applied atomically after complete validation",
        applied,
    )


def build_full_bundle(
    store: MissionDataStore,
    out_dir: str | Path,
) -> dict[str, Any]:
    manifest = build_delta_bundle(store, out_dir, base_seq=0)
    if manifest["base_entry_hash"] != CHAIN_GENESIS:
        raise StoreError("a full bundle must start from the chain genesis")
    return manifest
