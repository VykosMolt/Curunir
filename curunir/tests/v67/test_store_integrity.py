"""V6.7 historical store exploits, classified by invariant.

These tests attack the shared durability/admission mechanisms rather than the
old round-by-round call sites.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

import curunir_operational.store as store_module
from curunir_operational.canonical import canonical_line, sha256
from curunir_operational.contracts import ObjectVersion, ProvenanceSummary
from curunir_operational.delta import (
    _referenced_payloads,
    build_delta_bundle,
    import_delta_bundle,
    verify_delta_bundle,
)
from curunir_operational.store import MissionDataStore, StoreError

from operational_support import BASE_MARKING, RESTRICTED_MARKING, T0, make_store, t

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src",), ingestion_ids=("ing",))


def _version(number: int, marking=BASE_MARKING) -> ObjectVersion:
    return ObjectVersion(
        object_id="obj-1",
        version=number,
        object_type="OBSERVATION",
        lifecycle="ACTIVE",
        labels=("object",),
        external_refs=(),
        valid_from=t(number),
        valid_to=None,
        source_time=t(number),
        time_precision="HOUR",
        recorded_time=t(number),
        geometry=None,
        attributes={"version": number},
        quality={},
        epistemic_state="REPORTED",
        marking=marking,
        provenance=PROV,
    )


def _append_versions(store: MissionDataStore, count: int) -> None:
    for number in range(1, count + 1):
        store.append(
            "OBJECT_VERSION_APPENDED",
            _version(number),
            recorded_time=t(number),
            actor="fixture",
        )


def test_store_admission_is_the_causal_marking_floor(tmp_path, monkeypatch):
    protected = make_store(tmp_path, "protected")
    protected.append(
        "OBJECT_VERSION_APPENDED",
        _version(1, RESTRICTED_MARKING),
        recorded_time=t(1),
        actor="fixture",
    )
    event = protected.append(
        "OBJECT_VERSION_APPENDED",
        _version(2, BASE_MARKING),
        recorded_time=t(2),
        actor="fixture",
    )
    assert event["record"]["marking"]["compartments"] == ["SENSITIVE-INFRA"]
    assert event["record"]["marking"]["min_role"] == "ANALYST"

    # Negative control: sever exactly the claimed dependency.  The same weak
    # input then remains weak, proving the preceding result came from the
    # canonical admission primitive rather than the fixture or schema.
    monkeypatch.setattr(store_module, "admit_marking", lambda _store, record: dict(record))
    severed = make_store(tmp_path, "severed")
    severed.append(
        "OBJECT_VERSION_APPENDED",
        _version(1, RESTRICTED_MARKING),
        recorded_time=t(1),
        actor="fixture",
    )
    weak = severed.append(
        "OBJECT_VERSION_APPENDED",
        _version(2, BASE_MARKING),
        recorded_time=t(2),
        actor="fixture",
    )
    assert weak["record"]["marking"]["compartments"] == []
    assert weak["record"]["marking"]["min_role"] == "OBSERVER"


def test_torn_tail_recovery_preserves_committed_prefix_and_forensics(tmp_path):
    store = make_store(tmp_path)
    _append_versions(store, 2)
    before = store.head()
    events = store.events_path
    with events.open("ab") as handle:
        handle.write(b'{"seq":3,"recorded_time":"cut-\xc3')
    with pytest.raises(StoreError, match="torn"):
        MissionDataStore(store.root)
    result = MissionDataStore.recover_torn_tail(store.root)
    assert result["recovered"] is True
    assert MissionDataStore(store.root).head() == before
    assert events.with_name("events.jsonl.torn").read_bytes().endswith(b"cut-\xc3")


def test_recovery_refuses_committed_or_prefix_corruption_without_mutation(tmp_path):
    store = make_store(tmp_path)
    _append_versions(store, 2)
    lines = store.events_path.read_bytes().splitlines()
    victim = json.loads(lines[0])
    victim["record"]["attributes"]["version"] = 999
    lines[0] = json.dumps(victim, separators=(",", ":")).encode()
    corrupt = b"\n".join(lines) + b'\n{"torn"'
    store.events_path.write_bytes(corrupt)
    with pytest.raises(StoreError, match="earlier committed history"):
        MissionDataStore.recover_torn_tail(store.root)
    assert store.events_path.read_bytes() == corrupt
    assert not list(store.root.glob("events.jsonl.torn*"))


def test_unterminated_complete_event_is_uncommitted(tmp_path):
    store = make_store(tmp_path)
    _append_versions(store, 2)
    raw = store.events_path.read_bytes()
    store.events_path.write_bytes(raw[:-1])
    with pytest.raises(StoreError, match="torn"):
        MissionDataStore(store.root)
    MissionDataStore.recover_torn_tail(store.root)
    assert MissionDataStore(store.root).head()["event_count"] == 1


def test_payload_corruption_is_loud_and_content_addressed_repair_is_safe(tmp_path):
    store = make_store(tmp_path)
    body = b"authenticated evidence bytes"
    digest = store.put_payload(body)
    (store.payload_dir / digest).write_bytes(b"torn")
    with pytest.raises(StoreError, match="corrupt"):
        store.get_payload(digest)
    assert store.put_payload(body) == digest
    assert store.get_payload(digest) == body


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_payload_reads_refuse_links_and_special_files(tmp_path, kind):
    store = make_store(tmp_path)
    digest = hashlib.sha256(b"body").hexdigest()
    slot = store.payload_dir / digest
    outside = tmp_path / f"outside-{kind}"
    outside.write_bytes(b"body")
    if kind == "symlink":
        slot.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, slot)
    else:
        slot.unlink(missing_ok=True)
        os.mkfifo(slot)
    with pytest.raises(StoreError, match="unsafe|single-link"):
        store.get_payload(digest)


def test_export_refuses_corrupt_payload_before_publishing_backup(tmp_path):
    store = make_store(tmp_path)
    digest = store.put_payload(b"body")
    (store.payload_dir / digest).write_bytes(b"torn")
    destination = tmp_path / "backup"
    with pytest.raises(StoreError, match="corrupt"):
        store.export_to(destination)
    assert not destination.exists()


def test_restore_validates_every_member_before_installation(tmp_path):
    store = make_store(tmp_path)
    digest = store.put_payload(b"body")
    _append_versions(store, 1)
    backup = tmp_path / "backup"
    store.export_to(backup)
    (backup / "payloads" / digest).write_bytes(b"tampered")
    destination = tmp_path / "restored"
    with pytest.raises(StoreError, match="tampered"):
        MissionDataStore.import_from(backup, destination)
    assert not destination.exists()


def test_restore_refuses_manifest_path_traversal(tmp_path):
    store = make_store(tmp_path)
    _append_versions(store, 1)
    backup = tmp_path / "backup"
    store.export_to(backup)
    manifest_path = backup / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["payloads"] = {"../events.jsonl": "../events.jsonl"}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(StoreError, match="invalid payload manifest"):
        MissionDataStore.import_from(backup, tmp_path / "restored")


def test_delta_preflight_prevents_good_prefix_partial_commit(tmp_path):
    source = make_store(tmp_path, "source", store_id="same")
    target = make_store(tmp_path, "target", store_id="same")
    _append_versions(source, 3)
    _append_versions(target, 1)
    suffix = source.events()[1:]
    suffix[-1] = json.loads(json.dumps(suffix[-1]))
    suffix[-1]["entry_hash"] = "de" * 32
    before = target.head()
    with pytest.raises(StoreError, match="hash chain broken"):
        target.apply_imported_events_locked(suffix)
    assert target.head() == before
    assert MissionDataStore(target.root).head() == before


def test_delta_destination_cannot_overlap_source_store(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_delta_bundle(store, store.root, base_seq=0)
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_delta_bundle(store, store.payload_dir, base_seq=0)


def test_delta_payload_discovery_is_record_family_agnostic(tmp_path):
    store = make_store(tmp_path)
    digest = store.put_payload(b"semantic evidence")
    events = [
        {"record": {"record_type": "semantic_document", "normalized_sha256": digest}},
        {"record": {"record_type": "object_version", "attributes": {"nested": [digest]}}},
    ]
    assert _referenced_payloads(store, events) == {digest}
    assert _referenced_payloads(
        store, [{"record": {"evidence_snapshot_hash": "de" * 32}}]
    ) == set()


def test_delta_verifier_rejects_rehashed_nonfinite_before_apply(tmp_path):
    source = make_store(tmp_path, "source")
    _append_versions(source, 2)
    bundle = tmp_path / "delta"
    build_delta_bundle(source, bundle, base_seq=0)
    events_path = bundle / "delta_events.jsonl"
    lines = events_path.read_bytes().split(b"\n")
    event = json.loads(lines[0])
    event["record"]["score"] = float("nan")
    lines[0] = json.dumps(event, allow_nan=True).encode()
    events_bytes = b"\n".join(lines)
    events_path.write_bytes(events_bytes)
    manifest_path = bundle / "delta_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["events_sha256"] = hashlib.sha256(events_bytes).hexdigest()
    manifest["manifest_sha256"] = sha256({
        key: value for key, value in manifest.items()
        if key != "manifest_sha256"
    })
    manifest_path.write_text(canonical_line(manifest) + "\n")
    assert verify_delta_bundle(bundle)["valid"] is False
    target = make_store(tmp_path, "target")
    before = target.head()
    with pytest.raises(StoreError, match="failed verification"):
        import_delta_bundle(target, bundle)
    assert target.head() == before


def test_delta_verifier_refuses_manifest_traversal_and_malformed_shapes(tmp_path):
    source = make_store(tmp_path, "source")
    _append_versions(source, 1)
    bundle = tmp_path / "delta"
    build_delta_bundle(source, bundle, base_seq=0)
    manifest_path = bundle / "delta_manifest.json"
    original = json.loads(manifest_path.read_text())
    malicious = {**original, "payloads": ["../../../etc/passwd"]}
    malicious["manifest_sha256"] = sha256({
        key: value for key, value in malicious.items()
        if key != "manifest_sha256"
    })
    manifest_path.write_text(canonical_line(malicious) + "\n")
    assert verify_delta_bundle(bundle)["valid"] is False
    for malformed in (b"\xff", b"5", b"[1,2]", b"{"):
        manifest_path.write_bytes(malformed)
        assert verify_delta_bundle(bundle)["valid"] is False


def test_backup_restore_replay_is_byte_and_state_deterministic(tmp_path):
    store = make_store(tmp_path)
    store.put_payload(b"body")
    _append_versions(store, 2)
    backup = tmp_path / "backup"
    manifest = store.export_to(backup)
    restored = MissionDataStore.import_from(backup, tmp_path / "restored")
    assert restored.head() == store.head()
    assert restored.events() == store.events()
    assert hashlib.sha256(restored.events_path.read_bytes()).hexdigest() == manifest["events_sha256"]
    second = tmp_path / "second"
    assert restored.export_to(second) == manifest
