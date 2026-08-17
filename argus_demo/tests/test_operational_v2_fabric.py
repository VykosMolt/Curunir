"""V2 fabric-level tests: information requirements/tasks, delta synchronization,
schema evolution, hazard-impact rule, cross-workbench decision effect, stress."""
from __future__ import annotations

import json

import pytest

from curunir_operational.contracts import ObjectVersion, ProvenanceSummary, SourceRecord
from curunir_operational.delta import (build_delta_bundle, build_full_bundle, import_delta_bundle,
                                       verify_delta_bundle)
from curunir_operational.geometry import Geometry
from curunir_operational.missions import MissionWorkflow, MissionWorkflowError
from curunir_operational.projection import Projection
from curunir_operational.schema_registry import SchemaError, SchemaRegistry
from curunir_operational.store import MissionDataStore, StoreError
from curunir_operational.stress import run_stress

from operational_support import BASE_MARKING, HIGH_CONTEXT, RESTRICTED_MARKING, SERVICE_CONTEXT, T0, make_store, t

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",))


def obj(object_id, object_type="INFRASTRUCTURE", *, version=1, hours=0.0, marking=BASE_MARKING, attributes=None):
    return ObjectVersion(object_id, version, object_type, "ACTIVE", (object_id,), (), t(hours), None, t(hours),
                         "HOUR", t(max(hours, 0.0)), None, attributes or {"status": "OPERATIONAL"}, {},
                         "REPORTED", marking, PROV)


def seed_store(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED",
                 SourceRecord("src-a", "SYSTEM", "SYS", "x", "op", "CIVDEF-AUTH", {}, BASE_MARKING, "ACTIVE", "", T0),
                 recorded_time=T0, actor="fixture")
    store.append("OBJECT_VERSION_APPENDED", obj("infra-BR-7"), recorded_time=t(0), actor="p")
    return store


# ---- information requirements + analyst tasks -------------------------------

def test_requirement_lifecycle_and_human_only_closure(tmp_path):
    store = seed_store(tmp_path)
    missions = MissionWorkflow(store)
    req = missions.open_requirement(
        mission_context="cascade", question="is BR-7 load-bearing?", affected_ids=("infra-BR-7",),
        priority="HIGH", rationale="conflicting reports", required_evidence_type="ASSESSMENT",
        owning_role="ANALYST", closure_criteria="reviewed assessment exists", due_time=t(48),
        recorded_time=t(1), marking=BASE_MARKING, actor="analyst")
    request = missions.request_evidence(req["requirement_id"], request_kind="UPDATED_INFRASTRUCTURE_REPORT",
                                        detail="inspect BR-7", affected_ids=("infra-BR-7",), due_time=t(24),
                                        recorded_time=t(2), marking=BASE_MARKING, actor="analyst")
    # a service actor / model cannot close a requirement
    with pytest.raises(MissionWorkflowError):
        missions.transition("requirement", req["requirement_id"], "ANSWERED", actor_id="rule-engine",
                            actor_kind="SERVICE", evidence_refs=("eng-1",), note="model says ok",
                            recorded_time=t(3), marking=BASE_MARKING)
    # answering requires evidence references
    with pytest.raises(MissionWorkflowError):
        missions.transition("requirement", req["requirement_id"], "ANSWERED", actor_id="analyst",
                            actor_kind="HUMAN", evidence_refs=(), note="", recorded_time=t(3), marking=BASE_MARKING)
    # human closure with evidence
    missions.transition("requirement", req["requirement_id"], "ANSWERED", actor_id="analyst", actor_kind="HUMAN",
                        evidence_refs=("eng-1",), note="assessment filed", recorded_time=t(4), marking=BASE_MARKING)
    trail = missions.audit_trail("requirement", req["requirement_id"])
    assert [x["to_status"] for x in trail] == ["EVIDENCE_PENDING", "ANSWERED"]
    projection = Projection(store, snapshot_time=t(5)).view(HIGH_CONTEXT)
    assert projection["information_requirements"][0]["status"] == "ANSWERED"
    assert projection["counts"]["open_information_requirements"] == 0


def test_task_completion_requires_assigned_actor(tmp_path):
    store = seed_store(tmp_path)
    missions = MissionWorkflow(store)
    task = missions.assign_task(assigned_role="ANALYST", assigned_actor="eng-ruiz", task_type="ASSESSMENT",
                                affected_ids=("infra-BR-7",), required_action="inspect", due_time=t(10),
                                depends_on=(), recorded_time=t(1), marking=BASE_MARKING, actor="lead")
    missions.transition("analyst_task", task["task_id"], "IN_PROGRESS", actor_id="eng-ruiz", actor_kind="HUMAN",
                        evidence_refs=(), note="", recorded_time=t(2), marking=BASE_MARKING)
    with pytest.raises(MissionWorkflowError):  # someone else cannot complete it
        missions.transition("analyst_task", task["task_id"], "DONE", actor_id="other", actor_kind="HUMAN",
                            evidence_refs=(), note="done", recorded_time=t(3), marking=BASE_MARKING)
    with pytest.raises(MissionWorkflowError):  # abandonment needs a reason
        missions.transition("analyst_task", task["task_id"], "ABANDONED", actor_id="eng-ruiz", actor_kind="HUMAN",
                            evidence_refs=(), note="", recorded_time=t(3), marking=BASE_MARKING)
    missions.transition("analyst_task", task["task_id"], "DONE", actor_id="eng-ruiz", actor_kind="HUMAN",
                        evidence_refs=("eng-1",), note="filed", recorded_time=t(3), marking=BASE_MARKING)
    overdue_view = Projection(store, snapshot_time=t(20)).view(HIGH_CONTEXT)
    assert overdue_view["analyst_tasks"][0]["status"] == "DONE"


# ---- delta synchronization --------------------------------------------------

def build_two_phase_store(tmp_path):
    store = seed_store(tmp_path)
    base_seq = store.head()["event_count"]
    store.append("OBJECT_VERSION_APPENDED", obj("infra-SUB-4", hours=1.0), recorded_time=t(1), actor="p")
    store.append("OBJECT_VERSION_APPENDED", obj("infra-BR-7", version=2, hours=2.0,
                                                attributes={"status": "DEGRADED"}), recorded_time=t(2), actor="p")
    return store, base_seq


def test_delta_apply_duplicate_missingbase_wrongbase_tamper(tmp_path):
    store, base_seq = build_two_phase_store(tmp_path)
    # stale target truncated at base_seq
    store.export_to(tmp_path / "exp")
    lines = (tmp_path / "exp" / "events.jsonl").read_text().splitlines()
    (tmp_path / "target").mkdir(); (tmp_path / "target" / "payloads").mkdir()
    import shutil
    shutil.copyfile(tmp_path / "exp" / "store_meta.json", tmp_path / "target" / "store_meta.json")
    (tmp_path / "target" / "events.jsonl").write_text("\n".join(lines[:base_seq]) + "\n")
    for p in (tmp_path / "exp" / "payloads").iterdir():
        shutil.copyfile(p, tmp_path / "target" / "payloads" / p.name)
    target = MissionDataStore(tmp_path / "target")

    build_delta_bundle(store, tmp_path / "delta", base_seq=base_seq)
    assert verify_delta_bundle(tmp_path / "delta")["valid"]
    receipt = import_delta_bundle(target, tmp_path / "delta")
    assert receipt["status"] == "APPLIED" and receipt["applied_events"] == 2
    assert target.head()["head_hash"] == store.head()["head_hash"]
    # duplicate delta → idempotent
    assert import_delta_bundle(target, tmp_path / "delta")["status"] == "DUPLICATE_DELTA"
    # missing base: a target with only 1 event
    (tmp_path / "mb").mkdir(); (tmp_path / "mb" / "payloads").mkdir()
    shutil.copyfile(tmp_path / "exp" / "store_meta.json", tmp_path / "mb" / "store_meta.json")
    (tmp_path / "mb" / "events.jsonl").write_text(lines[0] + "\n")
    for p in (tmp_path / "exp" / "payloads").iterdir():
        shutil.copyfile(p, tmp_path / "mb" / "payloads" / p.name)
    assert import_delta_bundle(MissionDataStore(tmp_path / "mb"), tmp_path / "delta")["conflict"] == "MISSING_BASE"
    # tamper
    events_file = tmp_path / "delta" / "delta_events.jsonl"
    events_file.write_text(events_file.read_text().replace("DEGRADED", "FORGED"))
    assert not verify_delta_bundle(tmp_path / "delta")["valid"]


def test_delta_import_splits_on_newline_only_not_unicode_separators(tmp_path):
    # review NEW-3: canonical_line writes U+2028 / U+2029 / U+0085 RAW under
    # ensure_ascii=False (a routine artifact of scraped/pasted web text). The
    # bundle import must split on \n ONLY — str.splitlines() also breaks on those
    # separators and would tear an event in half, failing an otherwise byte-valid
    # privileged import (verify_delta_bundle still reports valid, so the operator
    # gets a clean verification then a JSONDecodeError). Every event must apply.
    store = seed_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED",
                 obj("infra-SEP", attributes={"status": "line\u2028sep\u2029para\u0085nel"}),
                 recorded_time=t(1), actor="p")
    build_full_bundle(store, tmp_path / "full")
    assert verify_delta_bundle(tmp_path / "full")["valid"]
    fresh = make_store(tmp_path, name="fresh_sep", store_id="x")
    receipt = import_delta_bundle(fresh, tmp_path / "full")
    assert receipt["status"] == "APPLIED"
    assert fresh.head()["head_hash"] == store.head()["head_hash"]   # every event applied intact


def test_full_bundle_round_trips(tmp_path):
    store, _ = build_two_phase_store(tmp_path)
    manifest = build_full_bundle(store, tmp_path / "full")
    assert manifest["base_seq"] == 0
    fresh = make_store(tmp_path, name="fresh_target", store_id="x")
    # a full bundle applies onto a genesis store
    receipt = import_delta_bundle(fresh, tmp_path / "full")
    assert receipt["status"] == "APPLIED"
    assert fresh.head()["head_hash"] == store.head()["head_hash"]


# ---- schema evolution -------------------------------------------------------

BASE_SCHEMA = {"schema_id": "s", "version": "1.0", "media_type": "application/json", "payload_kind": "document",
               "fields": {"id": {"type": "string", "required": True},
                          "grade": {"type": "string", "required": True, "enum": ["A", "B"]},
                          "when": {"type": "string", "required": True, "format": "iso-datetime"}}}


def test_schema_evolution_paths(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema(BASE_SCHEMA, recorded_time=T0, actor="f")
    v11 = {**BASE_SCHEMA, "version": "1.1", "compatible_with": ["1.0"],
           "fields": {**BASE_SCHEMA["fields"], "note": {"type": "string", "required": False}}}
    registry.register_schema(v11, recorded_time=T0, actor="f")
    v2 = {"schema_id": "s", "version": "2.0", "media_type": "application/json", "payload_kind": "document",
          "compatible_with": [], "fields": {"id": {"type": "string", "required": True},
                                            "ref": {"type": "string", "required": True},
                                            "grade": {"type": "string", "required": True, "enum": ["A", "B", "C"]},
                                            "when": {"type": "string", "required": True, "format": "iso-datetime"}}}
    registry.register_schema(v2, recorded_time=T0, actor="f")
    # old producer, new consumer: v1 payload declaring 1.0 validated under v1.1 (compatible)
    old_payload = {"schema_version": "1.0", "id": "x", "grade": "A", "when": T0}
    assert registry.validate_payload("s", "1.1", old_payload)["status"] == "VALID"
    # missing optional field is fine
    assert registry.validate_payload("s", "1.1", {"id": "x", "grade": "A", "when": T0})["status"] == "VALID"
    # renamed field: v2 shape without version, against v1 → INVALID (missing grade ok, missing nothing... ref unmapped)
    renamed = {"id": "x", "ref": "y", "grade": "C", "when": T0}  # C not in v1 enum
    assert registry.validate_payload("s", "1.0", renamed)["status"] == "INVALID"
    # unsupported enum under own version handled; unsupported future version
    assert registry.validate_payload("s", "1.0", {"schema_version": "9.9", "id": "x", "grade": "A", "when": T0})["status"] \
        == "UNSUPPORTED_SCHEMA_VERSION"
    # original payload preserved: the schema definitions retain source and are exportable
    export = registry.export_definitions()
    assert {d["version"] for d in export["schemas"]} == {"1.0", "1.1", "2.0"}


# ---- stress (smaller n for CI speed; determinism is the assertion) ----------

def test_stress_bounded_deterministic(tmp_path):
    result = run_stress(tmp_path / "stress", n=1200, families=6)
    assert result["store_events"] > result["records_ingested"]  # each valid record fans out
    assert result["quarantined_ingestions"] >= 1
    assert result["duplicate_ingestions"] >= 1
    assert result["late_ingestions"] >= 1
    assert result["determinism"]["projection_equal"] is True
    assert result["determinism"]["chain_valid"] is True


def test_delta_import_refuses_non_finite_atomically(tmp_path):
    # review MINOR-3: a delta bundle whose event carries a non-finite float must be
    # refused with a TYPED StoreError and NOTHING applied (atomic) — not partially
    # applied with an untyped ValueError from canonical_line mid-loop.
    import hashlib, shutil
    from curunir_operational.canonical import canonical_line, sha256
    store, base_seq = build_two_phase_store(tmp_path)
    store.export_to(tmp_path / "exp")
    lines = (tmp_path / "exp" / "events.jsonl").read_text().splitlines()
    (tmp_path / "target").mkdir(); (tmp_path / "target" / "payloads").mkdir()
    shutil.copyfile(tmp_path / "exp" / "store_meta.json", tmp_path / "target" / "store_meta.json")
    (tmp_path / "target" / "events.jsonl").write_text("\n".join(lines[:base_seq]) + "\n")
    for p in (tmp_path / "exp" / "payloads").iterdir():
        shutil.copyfile(p, tmp_path / "target" / "payloads" / p.name)
    target = MissionDataStore(tmp_path / "target")
    head_before = target.head()["head_hash"]

    build_delta_bundle(store, tmp_path / "delta", base_seq=base_seq)
    ev_path = tmp_path / "delta" / "delta_events.jsonl"
    dlines = ev_path.read_bytes().split(b"\n")
    ev = json.loads(dlines[0]); ev["record"]["score"] = float("nan")   # poison the 1st delta event
    dlines[0] = json.dumps(ev, allow_nan=True).encode()
    newb = b"\n".join(dlines); ev_path.write_bytes(newb)
    man = json.loads((tmp_path / "delta" / "delta_manifest.json").read_text())
    man["events_sha256"] = hashlib.sha256(newb).hexdigest()             # re-hash both gates
    man["manifest_sha256"] = sha256({k: v for k, v in man.items() if k != "manifest_sha256"})
    (tmp_path / "delta" / "delta_manifest.json").write_text(canonical_line(man) + "\n")
    assert verify_delta_bundle(tmp_path / "delta")["valid"]             # gates pass; only the value check catches it

    with pytest.raises(StoreError):
        import_delta_bundle(target, tmp_path / "delta")
    assert target.head()["head_hash"] == head_before                   # ATOMIC: nothing applied


def test_delta_import_refuses_malformed_envelope_atomically(tmp_path):
    # review N-3: a JSON-valid but structurally-malformed event — a scalar line, OR
    # a valid envelope whose `record` is a scalar (the apply loop .get()s it) — must
    # be refused with a typed StoreError and NOTHING applied, not crash mid-loop
    # with an untyped TypeError/KeyError after a partial apply.
    import hashlib, shutil
    from curunir_operational.canonical import canonical_line, sha256

    def _run(sub, mutate_first_line):
        store, base_seq = build_two_phase_store(tmp_path / sub)
        store.export_to(tmp_path / sub / "exp")
        lines = (tmp_path / sub / "exp" / "events.jsonl").read_text().splitlines()
        tgt = tmp_path / sub / "target"; (tgt / "payloads").mkdir(parents=True)
        shutil.copyfile(tmp_path / sub / "exp" / "store_meta.json", tgt / "store_meta.json")
        (tgt / "events.jsonl").write_text("\n".join(lines[:base_seq]) + "\n")
        for p in (tmp_path / sub / "exp" / "payloads").iterdir():
            shutil.copyfile(p, tgt / "payloads" / p.name)
        target = MissionDataStore(tgt)
        head_before = target.head()["head_hash"]
        build_delta_bundle(store, tmp_path / sub / "delta", base_seq=base_seq)
        ev_path = tmp_path / sub / "delta" / "delta_events.jsonl"
        dlines = ev_path.read_bytes().split(b"\n")
        dlines[0] = mutate_first_line(dlines[0])
        newb = b"\n".join(dlines); ev_path.write_bytes(newb)
        man = json.loads((tmp_path / sub / "delta" / "delta_manifest.json").read_text())
        man["events_sha256"] = hashlib.sha256(newb).hexdigest()
        man["manifest_sha256"] = sha256({k: v for k, v in man.items() if k != "manifest_sha256"})
        (tmp_path / sub / "delta" / "delta_manifest.json").write_text(canonical_line(man) + "\n")
        assert verify_delta_bundle(tmp_path / sub / "delta")["valid"]   # gates pass; only the shape check catches it
        with pytest.raises(StoreError):
            import_delta_bundle(target, tmp_path / sub / "delta")
        assert target.head()["head_hash"] == head_before               # ATOMIC: nothing applied

    _run("scalar_event", lambda line: b"5")
    # a valid envelope whose record is a scalar (isinstance(record, dict) half)
    def _scalar_record(line):
        ev = json.loads(line); ev["record"] = 5
        return json.dumps(ev).encode()
    _run("scalar_record", _scalar_record)


def test_delta_import_refuses_non_digest_payload_ref_traversal(tmp_path):
    # review B-4/NEW-A3: a payload_ref used as a path must be a 64-hex content
    # address, or a hostile bundle reads an arbitrary host file into the store.
    import hashlib, shutil
    from curunir_operational.canonical import canonical_line, sha256
    store, base_seq = build_two_phase_store(tmp_path)
    store.export_to(tmp_path / "exp")
    lines = (tmp_path / "exp" / "events.jsonl").read_text().splitlines()
    tgt = tmp_path / "target"; (tgt / "payloads").mkdir(parents=True)
    shutil.copyfile(tmp_path / "exp" / "store_meta.json", tgt / "store_meta.json")
    (tgt / "events.jsonl").write_text("\n".join(lines[:base_seq]) + "\n")
    for p in (tmp_path / "exp" / "payloads").iterdir():
        shutil.copyfile(p, tgt / "payloads" / p.name)
    target = MissionDataStore(tgt); head_before = target.head()["head_hash"]
    build_delta_bundle(store, tmp_path / "delta", base_seq=base_seq)
    ev = tmp_path / "delta" / "delta_events.jsonl"; dl = ev.read_bytes().split(b"\n")
    e = json.loads(dl[0]); e["record"]["record_type"] = "ingestion"; e["record"]["payload_ref"] = "../../../etc/passwd"
    dl[0] = json.dumps(e).encode(); nb = b"\n".join(dl); ev.write_bytes(nb)
    man = json.loads((tmp_path / "delta" / "delta_manifest.json").read_text())
    man["events_sha256"] = hashlib.sha256(nb).hexdigest()
    man["manifest_sha256"] = sha256({k: v for k, v in man.items() if k != "manifest_sha256"})
    (tmp_path / "delta" / "delta_manifest.json").write_text(canonical_line(man) + "\n")
    assert verify_delta_bundle(tmp_path / "delta")["valid"]
    with pytest.raises(StoreError):
        import_delta_bundle(target, tmp_path / "delta")
    assert target.head()["head_hash"] == head_before               # nothing applied


def test_verify_delta_bundle_fails_closed_on_malformed_manifest(tmp_path):
    # review NEW-A2: a hostile/malformed manifest must return valid:False, never
    # raise an untyped UnicodeDecodeError/AttributeError/RecursionError.
    store, base_seq = build_two_phase_store(tmp_path)
    build_delta_bundle(store, tmp_path / "delta", base_seq=base_seq)
    mpath = tmp_path / "delta" / "delta_manifest.json"
    for bad in (b"\xff\xff", b"5", b"[1,2]", b'"x"', b"{", b'{"base_seq":"3"}'):
        mpath.write_bytes(bad)
        assert verify_delta_bundle(tmp_path / "delta")["valid"] is False
