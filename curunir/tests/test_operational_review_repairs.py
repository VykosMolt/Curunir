"""Repairs found in the operational-package review: marking joins, report and
explanation filtering, strict feed parsing, chain reporting, append-time state
checks, PACE path safety, and the record index behind reference resolution."""
from __future__ import annotations

import hashlib
import os

import pytest

from curunir_operational import association, delta, sovereignty
from curunir_operational.access import Marking, can_view, most_restrictive
from curunir_operational.analytics import DeterministicRuleProvider
from curunir_operational.argus_adapter import ArgusEvidenceConnector
from curunir_operational.canonical import canonical_line, sha256
from curunir_operational.connectors import JsonFeedConnector
from curunir_operational.contracts import (EvidenceRef, ObjectVersion, ProvenanceSummary,
                                           RelationshipVersion)
from curunir_operational.geometry import Geometry
from curunir_operational.explain import explain, explain_markdown
from curunir_operational.missions import MissionWorkflow, MissionWorkflowError
from curunir_operational.pipelines import PipelineExecutor
from curunir_operational.projection import Projection
from curunir_operational.schema_registry import SchemaRegistry
from curunir_operational.security import PRIMARY_ID_FIELDS, _latest
from curunir_operational.sitrep import build_situation_report, render_markdown, render_text
from curunir_operational.store import CHAIN_GENESIS, MissionDataStore, StoreError, _entry_hash

from operational_support import T0, fake_sha, make_store, t

pytestmark = pytest.mark.no_db

AUTH = "CIVDEF-AUTH"
REL = ("CORRIDOR-OPS",)
PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=(), ingestion_ids=(), transformation_ids=())


def marking(*compartments: str, min_role: str = "OBSERVER") -> Marking:
    return Marking(owning_authority=AUTH, compartments=compartments, releasability=REL, min_role=min_role)


def context(context_id: str, *compartments: str, roles=("ANALYST",)):
    from curunir_operational.access import AccessContext
    return AccessContext(context_id, f"actor-{context_id}", "HUMAN", roles, compartments, REL, AUTH)


def obj(object_id, version, *, object_type, mark, hours=0.0, attributes=None, geometry=None,
        labels=None, provenance=PROV, epistemic_state="REPORTED", lifecycle="ACTIVE"):
    return ObjectVersion(
        object_id=object_id, version=version, object_type=object_type, lifecycle=lifecycle,
        labels=tuple(labels or (object_id,)), external_refs=(), valid_from=t(hours), valid_to=None,
        source_time=t(hours), time_precision="HOUR", recorded_time=t(hours), geometry=geometry,
        attributes=dict(attributes or {}), quality={}, epistemic_state=epistemic_state,
        marking=mark, provenance=provenance,
    )


# ---- B1: the analytics join unions compartments -----------------------------

def test_b1_rule_marking_joins_every_contributing_compartment(tmp_path):
    """A rule's inference is not viewable by a context blind to one of its inputs."""
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED",
                 obj("obs-A", 1, object_type="OBSERVATION", mark=marking("X", "Z", min_role="ANALYST"),
                     attributes={"reported_status": "DAMAGED"}),
                 recorded_time=t(0), actor="fixture")
    store.append("OBJECT_VERSION_APPENDED",
                 obj("infra-B", 1, object_type="INFRASTRUCTURE", mark=marking("Y", min_role="ANALYST")),
                 recorded_time=t(0), actor="fixture")
    store.append("RELATIONSHIP_VERSION_APPENDED",
                 RelationshipVersion("rel-1", 1, "REPORTS_ON", "obs-A", "infra-B", t(0), None, t(0),
                                     (), "MAPPING", "UNKNOWN", "ACTIVE", marking("X", "Y", "Z", min_role="ANALYST"),
                                     PROV),
                 recorded_time=t(0), actor="fixture")
    projection = Projection(store, snapshot_time=t(1))
    DeterministicRuleProvider(store).run(projection, context("all", "X", "Y", "Z"), recorded_time=t(1))

    inference = next(r for r in store.records_of("inference")
                     if r["output"].get("rule_id") == "rule-reported-disruption")
    assert inference["output"]["target"] == "infra-B"
    assert sorted(inference["marking"]["compartments"]) == ["X", "Y", "Z"]
    assert not can_view(inference["marking"], context("no-Y", "X", "Z"))


# ---- B2: a situation report never prints an id the reader cannot view -------

def _route_exposure_store(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED",
                 obj("route-R1", 1, object_type="ROUTE", mark=marking(),
                     geometry=Geometry("LINESTRING", ((24.0, 60.0), (24.02, 60.0)), None, "WGS84")),
                 recorded_time=t(0), actor="fixture")
    store.append("OBJECT_VERSION_APPENDED",
                 obj("hazard-SECRET", 1, object_type="OPERATIONAL_CONCERN",
                     mark=marking("SENSITIVE-INFRA", min_role="ANALYST"),
                     geometry=Geometry("POINT", (24.01, 60.001), None, "WGS84"),
                     attributes={"hazard_kind": "flood"}),
                 recorded_time=t(0), actor="fixture")
    projection = Projection(store, snapshot_time=t(1))
    DeterministicRuleProvider(store).run(projection, context("high", "SENSITIVE-INFRA"), recorded_time=t(1))
    return store


def test_b2_situation_report_omits_unviewable_disruption_ids(tmp_path):
    store = _route_exposure_store(tmp_path)
    projection = Projection(store, snapshot_time=t(2))
    observer = context("observer", roles=("OBSERVER",))
    report = build_situation_report(store, projection, observer, operational_context="corridor")
    assert [o["object_id"] for o in projection.view(observer)["objects"]] == ["route-R1"]
    for rendered in (canonical_line(report), render_text(report), render_markdown(report)):
        assert "hazard-SECRET" not in rendered


# ---- B3: explanations do not list hidden dependence-group members -----------

def _dependence_store(tmp_path):
    store = make_store(tmp_path)

    def provenance(group):
        return ProvenanceSummary(
            mode="OPERATIONAL",
            evidence=(EvidenceRef(source_object_id="ext-src", document_id="doc-1",
                                  content_sha256=fake_sha("doc-1"), assertion_id="assert-1",
                                  evidence_basis_id="basis-1", identity_status="IDENTITY_UNKNOWN",
                                  authority_state="AUTHORITY_NOT_ASSESSED", independence_status="UNRESOLVED",
                                  claim_basis_status="UNKNOWN_BASIS", review_state="UNREVIEWED",
                                  mapping_status="UNKNOWN", dependence_group_id=group),))

    store.append("OBJECT_VERSION_APPENDED",
                 obj("obs-PUBLIC", 1, object_type="OBSERVATION", mark=marking(),
                     provenance=provenance("GRP-1")),
                 recorded_time=t(0), actor="fixture")
    store.append("OBJECT_VERSION_APPENDED",
                 obj("obs-HIDDEN", 1, object_type="OBSERVATION",
                     mark=marking("SENSITIVE-INFRA", min_role="ANALYST"), provenance=provenance("GRP-1")),
                 recorded_time=t(0), actor="fixture")
    return store


def test_b3_explain_filters_dependence_group_members(tmp_path):
    store = _dependence_store(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    low = context("low", roles=("OBSERVER",))
    assert projection.dependence_groups == {"GRP-1": ["obs-HIDDEN", "obs-PUBLIC"]}
    explanation = explain(projection, "obs-PUBLIC", low)
    listed = [m for group in explanation["dependent_sources"] for m in group["member_object_ids"]]
    assert "obs-HIDDEN" not in listed
    assert "obs-HIDDEN" not in explain_markdown(explanation)
    high = context("high", "SENSITIVE-INFRA")
    high_groups = explain(projection, "obs-PUBLIC", high)["dependent_sources"]
    assert high_groups == [{"group_id": "GRP-1", "member_object_ids": ["obs-HIDDEN", "obs-PUBLIC"]}]


# ---- B4/B5: feeds are parsed and validated at the boundary ------------------

FEED_SCHEMA = {
    "schema_id": "feed-doc", "version": "1.0", "media_type": "application/json",
    "payload_kind": "document",
    "fields": {"id": {"type": "string", "required": True}, "v": {"type": "number"}},
}
FEED_MAPPING = {
    "mapping_id": "feed-to-infra", "version": "1.0",
    "input_schema_id": "feed-doc", "input_schema_version": "1.0",
    "output_object_type": "INFRASTRUCTURE",
    "entries": [{"source_field": "id", "target": "attributes.external"},
                {"source_field": "v", "target": "attributes.value"}],
}
FEED_PIPELINE = {
    "pipeline_id": "feed", "version": "1.0", "connector_id": "conn-json",
    "schema_id": "feed-doc", "schema_version": "1.0", "mode": "direct_state", "unit": "document",
    "object": {"object_type": "INFRASTRUCTURE", "id_prefix": "infra-", "external_id_field": "id"},
    "mappings": [{"mapping_id": "feed-to-infra", "version": "1.0"}],
    "marking": Marking(owning_authority=AUTH, releasability=REL).to_record(),
}

ROWS_SCHEMA = {
    "schema_id": "feed-rows", "version": "1.0", "media_type": "application/json",
    "payload_kind": "rows", "fields": {"id": {"type": "string", "required": True}},
}


def _feed_executor(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema(FEED_SCHEMA, recorded_time=T0, actor="fixture")
    registry.register_mapping(FEED_MAPPING, recorded_time=T0, actor="fixture")
    executor = PipelineExecutor(store, registry, {"conn-json": JsonFeedConnector("conn-json", "feed-doc")})
    executor.register_pipeline(FEED_PIPELINE, recorded_time=T0, actor="fixture")
    return store, executor


@pytest.mark.parametrize("body", [b'{"id":"x1","v":1e999}', b'{"id":"x1","id":"x2"}'])
def test_b4_non_canonical_feed_json_is_quarantined(tmp_path, body):
    store, executor = _feed_executor(tmp_path)
    result = executor.run("feed", body, source_id="src-feed", source_time=t(1),
                          received_time=t(1), recorded_time=t(1), actor="pipe")
    assert result["status"] == "QUARANTINED"
    ingestion = store.records_of("ingestion")[-1]
    assert ingestion["quarantined"] and ingestion["validation"] == "INVALID"
    assert any("MALFORMED_PAYLOAD" in reason for reason in ingestion["quarantine_reasons"])
    assert store.records_of("transformation") == []
    assert store.records_of("object_version") == []


def test_b4_argus_connector_rejects_non_canonical_json():
    with pytest.raises(ValueError):
        ArgusEvidenceConnector("conn-argus", "argus-evidence").parse(b'{"a":1,"a":2}')


def test_b5_non_object_row_yields_an_invalid_ingestion(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema(ROWS_SCHEMA, recorded_time=T0, actor="fixture")
    assert registry.validate_payload("feed-rows", "1.0", {"rows": [1]})["status"] == "INVALID"
    connector = JsonFeedConnector("conn-rows", "feed-rows")
    outcome = connector.ingest(store, registry, b'{"rows":[1]}', source_id="src-rows",
                               received_time=t(1), recorded_time=t(1), actor="pipe",
                               marking=Marking(owning_authority=AUTH, releasability=REL))
    assert outcome.status == "QUARANTINED"
    assert store.records_of("ingestion")[-1]["quarantined"]


def test_b5_non_object_geojson_feature_yields_an_invalid_ingestion(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema({**ROWS_SCHEMA, "schema_id": "feed-geo", "payload_kind": "geojson",
                              "media_type": "application/geo+json"},
                             recorded_time=T0, actor="fixture")
    result = registry.validate_payload("feed-geo", "1.0",
                                       {"type": "FeatureCollection", "features": [1]})
    assert result["status"] == "INVALID"


# ---- B6: verify_chain reports damage instead of raising --------------------

def test_b6_verify_chain_reports_a_record_missing_its_index_field(tmp_path):
    store = make_store(tmp_path)
    record = {"record_type": "alert"}
    entry_hash = _entry_hash(1, "ALERT_RAISED", T0, "tamper", record, CHAIN_GENESIS)
    event = {"seq": 1, "event_id": f"evt-000001-{entry_hash[:8]}", "event_type": "ALERT_RAISED",
             "recorded_time": T0, "actor": "tamper", "record": record,
             "prev_hash": CHAIN_GENESIS, "entry_hash": entry_hash}
    store.events_path.write_text(canonical_line(event) + "\n", encoding="utf-8")
    result = store.verify_chain()
    assert result["valid"] is False and "dedup_key" in result["reason"]
    with pytest.raises(StoreError):
        MissionDataStore(store.root)


# ---- B7: state checks repeat under the append lock -------------------------

def test_b7_requirement_transition_rechecks_status_under_the_lock(tmp_path):
    first = make_store(tmp_path)
    mark = Marking(owning_authority=AUTH, releasability=REL)
    workflow = MissionWorkflow(first)
    requirement = workflow.open_requirement(
        mission_context="corridor", question="is the bridge usable?", affected_ids=(),
        priority="MEDIUM", rationale="test", required_evidence_type="REPORT",
        owning_role="ANALYST", closure_criteria="a report arrives", due_time=None,
        recorded_time=t(0), marking=mark, actor="analyst-a")
    requirement_id = requirement["requirement_id"]

    second = MissionDataStore(first.root)
    MissionWorkflow(second).transition("requirement", requirement_id, "ANSWERED",
                                       actor_id="analyst-b", actor_kind="HUMAN",
                                       evidence_refs=("ev-1",), note="answered",
                                       recorded_time=t(1), marking=mark)
    # `first` still believes the requirement is OPEN; the append must refuse.
    with pytest.raises(MissionWorkflowError):
        MissionWorkflow(first).transition("requirement", requirement_id, "ANSWERED",
                                          actor_id="analyst-a", actor_kind="HUMAN",
                                          evidence_refs=("ev-2",), note="answered again",
                                          recorded_time=t(2), marking=mark)
    assert len(MissionDataStore(first.root).records_of("workflow_transition")) == 1


def test_b7_alert_dedup_rechecks_under_the_lock(tmp_path):
    from curunir_operational.workflow import WorkflowEngine
    first = make_store(tmp_path)
    mark = Marking(owning_authority=AUTH, releasability=REL)
    content = {"rule_id": "r", "rule_version": "1.0", "trigger": "t", "affected_ids": [],
               "evidence_refs": ["infra-1"], "severity": "WARNING", "severity_rationale": "why",
               "dedup_key": "dk-1", "expiry_condition": ""}
    second = MissionDataStore(first.root)
    WorkflowEngine(second).raise_alert(content, marking=mark, recorded_time=t(0), actor="svc")
    alert_id, created = WorkflowEngine(first).raise_alert(content, marking=mark,
                                                          recorded_time=t(1), actor="svc")
    assert created is False
    assert len(MissionDataStore(first.root).records_of("alert")) == 1


# ---- B8: PACE bundles are written and verified through safe paths ----------

def _pace_store(tmp_path):
    store = make_store(tmp_path, name="pace-store")
    store.append("OBJECT_VERSION_APPENDED",
                 obj("infra-1", 1, object_type="INFRASTRUCTURE", mark=marking()),
                 recorded_time=t(0), actor="fixture")
    return store


def test_b8_verify_rejects_a_member_that_escapes_the_bundle(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    members = {"../outside.txt": hashlib.sha256(b"secret\n").hexdigest()}
    manifest = {"bundle_type": "OperationalPACEBundle", "bundle_format": "curunir-operational-pace-v1",
                "members": members, "combined_sha256": sha256(members)}
    (bundle / "bundle_manifest.json").write_text(canonical_line(manifest) + "\n", encoding="utf-8")
    result = sovereignty.verify_pace_bundle(bundle)
    assert result["valid"] is False
    assert any("../outside.txt" in failure for failure in result["failures"])


def test_b8_verify_reports_a_manifest_without_members(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "bundle_manifest.json").write_text(canonical_line({"bundle_format": "x"}) + "\n",
                                                 encoding="utf-8")
    result = sovereignty.verify_pace_bundle(bundle)
    assert result["valid"] is False and result["failures"]


def test_b8_build_refuses_a_planted_output_path(tmp_path):
    store = _pace_store(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    victim = tmp_path / "victim.txt"
    victim.write_text("original\n", encoding="utf-8")
    out_dir = tmp_path / "pace"
    out_dir.mkdir()
    (out_dir / "sitrep.txt").symlink_to(victim)
    with pytest.raises(StoreError):
        sovereignty.build_pace_bundle(store, projection, context("high"), out_dir,
                                      operational_context="corridor")
    assert victim.read_text() == "original\n"


def test_b8_build_still_publishes_a_verifiable_bundle(tmp_path):
    store = _pace_store(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    manifest = sovereignty.build_pace_bundle(store, projection, context("high"), tmp_path / "pace",
                                             operational_context="corridor")
    assert sovereignty.verify_pace_bundle(tmp_path / "pace")["valid"]
    assert manifest["combined_sha256"] == sha256(manifest["members"])


# ---- B9: an unknown role can never rank below a known one ------------------

@pytest.mark.parametrize("records", [
    [{"owning_authority": AUTH, "min_role": "OBSERVER"}, {"owning_authority": AUTH, "min_role": "ROOT"}],
    [{"owning_authority": AUTH, "min_role": "ROOT"}, {"owning_authority": AUTH, "min_role": "OBSERVER"}],
])
def test_b9_unknown_min_role_refuses_in_either_order(records):
    with pytest.raises(ValueError):
        most_restrictive(records)


# ---- B10: an unassessed mapping confidence dominates -----------------------

def test_b10_unknown_mapping_confidence_dominates():
    left = {"object_type": "INFRASTRUCTURE", "quality": {"mapping_confidence": "UNKNOWN"}}
    right = {"object_type": "INFRASTRUCTURE", "quality": {"mapping_confidence": "HIGH"}}
    assert association.compute_features(left, right)["mapping_confidence"] == "UNKNOWN"
    assert association.compute_features(right, left)["mapping_confidence"] == "UNKNOWN"


# ---- B11: bundle invariants refuse rather than assert ----------------------

def test_b11_full_bundle_invariant_raises_store_error(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(delta, "build_delta_bundle",
                        lambda *a, **kw: {"base_entry_hash": "f" * 64})
    with pytest.raises(StoreError):
        delta.build_full_bundle(store, tmp_path / "bundle")


# ---- B12: an unseal is recorded before the store becomes readable ----------

def test_b12_unseal_is_registered_before_the_chmod(tmp_path, monkeypatch):
    from curunir_operational import partition_custody as custody
    store_path = tmp_path / "store.jsonl"
    store_path.write_text('{"unit_id": "unit-1"}\n', encoding="utf-8")
    croot = str(tmp_path / "custody")
    custody.seal_store("TEST_STORE", root=croot, relative_path=str(store_path), reason="test")

    registered_at_chmod: list[bool] = []
    real_chmod = os.chmod

    def spy(path, mode, *args, **kwargs):
        registered_at_chmod.append("TEST_STORE" in custody._UNSEALED)
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(custody.os, "chmod", spy)
    with custody.open_sealed_partition("TEST_STORE", purpose="test", root=croot,
                                       prerequisites={"ready": True}):
        pass
    assert registered_at_chmod and registered_at_chmod[0] is True


# ---- P2: the record index answers exactly what a scan would ----------------

def _scan_latest(store, record_type, id_field, record_id):
    version = None
    base_id = record_id
    if "@v" in record_id:
        candidate, separator, suffix = record_id.rpartition("@v")
        if separator and suffix.isdigit():
            base_id = candidate
            version = int(suffix)
    matches = [r for r in store.records_of(record_type)
               if r.get(id_field) == base_id and (version is None or r.get("version") == version)]
    if not matches:
        return None
    return max(matches, key=lambda record: record.get("version", 1))


def test_p2_latest_matches_a_full_scan(tmp_path):
    store = make_store(tmp_path)
    mark = Marking(owning_authority=AUTH, releasability=REL)
    for version in (1, 2, 3):
        store.append("OBJECT_VERSION_APPENDED",
                     obj("infra-1", version, object_type="INFRASTRUCTURE", mark=mark, hours=version),
                     recorded_time=t(version), actor="fixture")
    store.append("SOURCE_REGISTERED",
                 {"record_type": "source", "source_id": "src-1", "source_type": "SYSTEM",
                  "source_system": "SYS", "system_instance": "one", "custodian": "me",
                  "owning_authority": AUTH, "reliability": {}, "marking": mark.to_record(),
                  "status": "ACTIVE", "notes": "", "registered_time": T0},
                 recorded_time=t(4), actor="fixture")

    for record_type, id_field in (("object_version", "object_id"), ("source", "source_id")):
        for probe in ("infra-1", "infra-1@v2", "src-1", "absent", "absent@v9"):
            assert _latest(store, record_type, id_field, probe) \
                == _scan_latest(store, record_type, id_field, probe)
    assert PRIMARY_ID_FIELDS["object_version"] == "object_id"
