"""Mission data store: append-only history, corrections, as-of reads, idempotency,
chain integrity, and export and import that reproduce the same store."""
from __future__ import annotations

import pytest

from curunir_operational.contracts import IngestionEvent, ObjectVersion, ProvenanceSummary, SourceRecord
from curunir_operational.store import MissionDataStore, StoreError

from operational_support import BASE_MARKING, T0, fake_sha, make_store, t

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-logsys",), ingestion_ids=("ing-1",))


def source_record(created=T0):
    return SourceRecord("src-logsys", "SYSTEM", "LOGSYS", "logsys-01", "Corridor Logistics Office",
                        "CIVDEF-AUTH", {"track_record": "UNKNOWN"}, BASE_MARKING, "ACTIVE", "", created)


def object_version(version=1, recorded=None, status="OPERATIONAL", **overrides):
    values = dict(object_id="depot-alden", version=version, object_type="INFRASTRUCTURE", lifecycle="ACTIVE",
                  labels=("Alden Depot",), external_refs=(), valid_from=t(version - 1), valid_to=None,
                  source_time=t(version - 1), time_precision="HOUR", recorded_time=recorded or t(version - 1),
                  geometry=None, attributes={"status": status}, quality={}, epistemic_state="REPORTED",
                  marking=BASE_MARKING, provenance=PROV)
    values.update(overrides)
    return ObjectVersion(**values)


def test_create_append_reopen_and_chain(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED", source_record(), recorded_time=T0, actor="fixture")
    store.append("OBJECT_VERSION_APPENDED", object_version(1), recorded_time=t(0), actor="fixture")
    head = store.head()
    reopened = MissionDataStore(tmp_path / "store")
    assert reopened.head() == head
    assert reopened.verify_chain()["valid"]


def test_recorded_time_must_not_decrease(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED", source_record(t(2)), recorded_time=t(2), actor="fixture")
    with pytest.raises(StoreError):
        store.append("OBJECT_VERSION_APPENDED", object_version(1, recorded=t(1)), recorded_time=t(1), actor="fixture")


def test_version_continuity_enforced(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", object_version(1), recorded_time=t(0), actor="fixture")
    with pytest.raises(StoreError):
        store.append("OBJECT_VERSION_APPENDED", object_version(3, recorded=t(2)), recorded_time=t(2), actor="fixture")
    assert store.next_object_version("depot-alden") == 2


def test_correction_preserves_history_and_asof(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", object_version(1, status="OPERATIONAL"), recorded_time=t(0), actor="fixture")
    seq_before = store.head()["event_count"]
    store.append("OBJECT_VERSION_APPENDED",
                 object_version(2, recorded=t(5), status="DEGRADED", correction_of="depot-alden@v1",
                                correction_reason="unit error in original report", epistemic_state="CORRECTED"),
                 recorded_time=t(5), actor="fixture")
    versions = store.records_of("object_version")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["attributes"]["status"] == "OPERATIONAL"
    as_of = store.records_of("object_version", until_seq=seq_before)
    assert [v["version"] for v in as_of] == [1]


def test_wrong_record_type_and_unknown_event_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(StoreError):
        store.append("OBJECT_VERSION_APPENDED", source_record(), recorded_time=T0, actor="fixture")
    with pytest.raises(StoreError):
        store.append("SOMETHING_ELSE", source_record(), recorded_time=T0, actor="fixture")


def ingestion_event(key="k1", validation="VALID", duplicate_of=None, received=None):
    return IngestionEvent(f"ing-{key}-{validation}", "conn-json", "1.0", "src-logsys", t(0), received or t(0),
                          fake_sha("payload"), "schema-x", "1.0", key, validation,
                          False, (), duplicate_of, False, fake_sha("payload"), BASE_MARKING)


def test_idempotency_guard(tmp_path):
    store = make_store(tmp_path)
    store.append("INGESTION_RECORDED", ingestion_event(), recorded_time=t(0), actor="conn")
    with pytest.raises(StoreError):
        store.append("INGESTION_RECORDED", ingestion_event(received=t(1)), recorded_time=t(1), actor="conn")
    store.append("INGESTION_RECORDED",
                 ingestion_event(validation="DUPLICATE", duplicate_of="ing-k1-VALID", received=t(1)),
                 recorded_time=t(1), actor="conn")
    assert store.find_ingestion_by_key("k1") == "ing-k1-VALID"


def test_payload_roundtrip(tmp_path):
    store = make_store(tmp_path)
    digest = store.put_payload(b'{"x": 1}')
    assert store.get_payload(digest) == b'{"x": 1}'


def test_export_import_and_tamper_detection(tmp_path):
    store = make_store(tmp_path)
    store.put_payload(b"body-1")
    store.append("SOURCE_REGISTERED", source_record(), recorded_time=T0, actor="fixture")
    store.append("OBJECT_VERSION_APPENDED", object_version(1), recorded_time=t(0), actor="fixture")
    manifest = store.export_to(tmp_path / "export")
    imported = MissionDataStore.import_from(tmp_path / "export", tmp_path / "fresh")
    assert imported.head()["head_hash"] == store.head()["head_hash"]
    assert imported.head()["event_count"] == manifest["event_count"]
    events = (tmp_path / "export" / "events.jsonl").read_text()
    (tmp_path / "export" / "events.jsonl").write_text(events.replace("OPERATIONAL", "DESTROYED"))
    with pytest.raises(StoreError):
        MissionDataStore.import_from(tmp_path / "export", tmp_path / "fresh2")


def test_deterministic_reconstruction(tmp_path):
    def build(name):
        store = make_store(tmp_path, name)
        store.append("SOURCE_REGISTERED", source_record(), recorded_time=T0, actor="fixture")
        store.append("OBJECT_VERSION_APPENDED", object_version(1), recorded_time=t(0), actor="fixture")
        return store.head()["head_hash"]

    assert build("a") == build("b")


def test_refresh_sees_another_instance_s_append_without_reopening(tmp_path):
    from operational_support import obj
    writer = make_store(tmp_path)
    reader = MissionDataStore(writer.root)
    writer.append("OBJECT_VERSION_APPENDED", obj("infra-1", "INFRASTRUCTURE"), recorded_time=t(1.0), actor="w")
    assert reader.records_of("object_version") == []
    head = reader.refresh()
    assert head == writer.head()["head_hash"]
    assert [r["object_id"] for r in reader.records_of("object_version")] == ["infra-1"]
    assert reader.refresh() == head
