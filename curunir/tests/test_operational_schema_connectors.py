"""Schema registry and mappings, the JSON, CSV and GeoJSON connectors, the
evidence adapter, and pipelines built from configuration."""
from __future__ import annotations

import json

import pytest

from curunir_operational.argus_adapter import ArgusEvidenceConnector, dependence_group_id, evidence_ref_from_bundle
from curunir_operational.connectors import CsvFeedConnector, GeoJsonConnector, JsonFeedConnector, source_watermark
from curunir_operational.pipelines import PipelineExecutor, validate_pipeline_definition
from curunir_operational.schema_registry import SchemaError, SchemaRegistry, apply_mapping, validate_mapping_definition

from operational_support import BASE_MARKING, RESTRICTED_MARKING, T0, fake_sha, make_store, t

pytestmark = pytest.mark.no_db

STOCK_SCHEMA = {
    "schema_id": "stock-report", "version": "1.0", "media_type": "text/csv", "payload_kind": "rows",
    "fields": {
        "depot_id": {"type": "string", "required": True},
        "commodity": {"type": "string", "required": True, "enum": ["fuel", "medical", "repair"]},
        "quantity": {"type": "number", "required": True},
        "unit": {"type": "string", "required": True},
        "report_time": {"type": "string", "required": True, "format": "iso-datetime"},
    },
}

STOCK_MAPPING = {
    "mapping_id": "stock-to-resource", "version": "1.0",
    "input_schema_id": "stock-report", "input_schema_version": "1.0",
    "output_object_type": "RESOURCE_STOCK",
    "entries": [
        {"source_field": "depot_id", "target": "attributes.depot_ref"},
        {"source_field": "commodity", "target": "attributes.commodity"},
        {"source_field": "quantity", "target": "attributes.quantity", "transform": "to_number"},
        {"source_field": "unit", "target": "attributes.unit"},
        {"source_field": "report_time", "target": "valid_from", "transform": "iso_time"},
        {"source_field": "report_time", "target": "source_time", "transform": "iso_time"},
    ],
    "lossy_operations": ["quantity rounded to reporting unit by origin system"],
    "quality_effects": {"mapping_confidence": 0.9},
}


def registry_with_stock(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema(STOCK_SCHEMA, recorded_time=T0, actor="fixture")
    registry.register_mapping(STOCK_MAPPING, recorded_time=T0, actor="fixture")
    return store, registry


def test_schema_registration_and_version_conflict(tmp_path):
    store, registry = registry_with_stock(tmp_path)
    registry.register_schema(STOCK_SCHEMA, recorded_time=t(1), actor="fixture")  # re-registering the same schema is a no-op
    changed = {**STOCK_SCHEMA, "fields": {**STOCK_SCHEMA["fields"], "extra": {"type": "string"}}}
    with pytest.raises(SchemaError):
        registry.register_schema(changed, recorded_time=t(1), actor="fixture")
    assert registry.schema_versions("stock-report") == ["1.0"]
    with pytest.raises(SchemaError):
        registry.register_schema({**STOCK_SCHEMA, "surprise": True}, recorded_time=t(1), actor="fixture")


def test_payload_validation_paths(tmp_path):
    _, registry = registry_with_stock(tmp_path)
    good = {"rows": [{"depot_id": "ALD", "commodity": "fuel", "quantity": 100.0, "unit": "l",
                      "report_time": T0}]}
    assert registry.validate_payload("stock-report", "1.0", good)["status"] == "VALID"
    missing = {"rows": [{"commodity": "fuel", "quantity": 1.0, "unit": "l", "report_time": T0}]}
    result = registry.validate_payload("stock-report", "1.0", missing)
    assert result["status"] == "INVALID" and "depot_id" in result["errors"][0]
    bad_enum = {"rows": [{"depot_id": "ALD", "commodity": "gold", "quantity": 1.0, "unit": "l", "report_time": T0}]}
    assert registry.validate_payload("stock-report", "1.0", bad_enum)["status"] == "INVALID"
    future = {"schema_version": "9.9", "rows": []}
    assert registry.validate_payload("stock-report", "1.0", future)["status"] == "UNSUPPORTED_SCHEMA_VERSION"


def test_compatible_version_accepted(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    v11 = {**STOCK_SCHEMA, "version": "1.1", "compatible_with": ["1.0"]}
    registry.register_schema(v11, recorded_time=T0, actor="fixture")
    payload = {"schema_version": "1.0",
               "rows": [{"depot_id": "ALD", "commodity": "fuel", "quantity": 1.0, "unit": "l", "report_time": T0}]}
    result = registry.validate_payload("stock-report", "1.1", payload)
    assert result["status"] == "VALID" and result["schema_version"] == "1.1"


def test_mapping_hygiene():
    with pytest.raises(SchemaError):
        validate_mapping_definition({**STOCK_MAPPING, "entries": [{"source_field": "x", "target": "attributes.x", "sneaky": 1}]})
    with pytest.raises(SchemaError):
        validate_mapping_definition({**STOCK_MAPPING, "entries": [{"source_field": "x", "target": "os.system"}]})
    with pytest.raises(SchemaError):
        validate_mapping_definition({**STOCK_MAPPING, "entries": [{"source_field": "x", "target": "attributes.x", "transform": "eval"}]})
    checked = validate_mapping_definition(STOCK_MAPPING)
    assert checked["lossy_operations"] and checked["definition_sha256"]


def test_apply_mapping_defaults_and_unknown():
    mapping = validate_mapping_definition({
        **STOCK_MAPPING,
        "entries": [{"source_field": "depot_id", "target": "attributes.depot_ref"},
                    {"source_field": "grade", "target": "attributes.grade", "default": "standard"},
                    {"source_field": "inspector", "target": "attributes.inspector", "on_missing": "UNKNOWN"}],
    })
    out = apply_mapping(mapping, {"depot_id": "ALD", "surprise_field": 1})
    assert out["attributes"] == {"depot_ref": "ALD", "grade": "standard", "inspector": "UNKNOWN"}
    assert any("declared default applied" in w for w in out["warnings"])
    assert any("unmapped fields dropped" in w for w in out["warnings"])


CSV_BODY = b"depot_id,commodity,quantity,unit,report_time\nALD,fuel,1200,l,2026-03-01T06:00:00+00:00\n"


def test_csv_connector_accepts_and_types(tmp_path):
    store, registry = registry_with_stock(tmp_path)
    connector = CsvFeedConnector("conn-csv", "stock-report")
    outcome = connector.ingest(store, registry, CSV_BODY, source_id="src-logsys", received_time=t(1),
                               recorded_time=t(1), actor="conn", marking=BASE_MARKING, source_time=t(0))
    assert outcome.status == "ACCEPTED"
    assert outcome.payload["rows"][0]["quantity"] == 1200.0
    assert outcome.ingestion["connector_version"] == "1.0"


def test_malformed_payloads_quarantined_with_custody(tmp_path):
    store, registry = registry_with_stock(tmp_path)
    json_connector = JsonFeedConnector("conn-json", "stock-report")
    outcome = json_connector.ingest(store, registry, b"{not json", source_id="src-logsys", received_time=t(1),
                                    recorded_time=t(1), actor="conn", marking=BASE_MARKING)
    assert outcome.status == "QUARANTINED"
    assert outcome.ingestion["quarantine_reasons"]
    assert store.get_payload(outcome.ingestion["payload_ref"]) == b"{not json"  # the bytes are kept before anything parses them
    csv_connector = CsvFeedConnector("conn-csv", "stock-report")
    ragged = b"depot_id,commodity\nALD,fuel,EXTRA\n"
    outcome = csv_connector.ingest(store, registry, ragged, source_id="src-logsys", received_time=t(2),
                                   recorded_time=t(2), actor="conn", marking=BASE_MARKING)
    assert outcome.status == "QUARANTINED"


def test_duplicate_and_late_detection(tmp_path):
    store, registry = registry_with_stock(tmp_path)
    connector = CsvFeedConnector("conn-csv", "stock-report")
    first = connector.ingest(store, registry, CSV_BODY, source_id="src-logsys", received_time=t(1),
                             recorded_time=t(1), actor="conn", marking=BASE_MARKING, source_time=t(0))
    second = connector.ingest(store, registry, CSV_BODY, source_id="src-logsys", received_time=t(2),
                              recorded_time=t(2), actor="conn", marking=BASE_MARKING, source_time=t(0))
    assert second.status == "DUPLICATE"
    assert second.ingestion["duplicate_of"] == first.ingestion["ingestion_id"]
    watermark = source_watermark(store, "src-logsys")
    late_body = CSV_BODY.replace(b"1200", b"900")
    late = connector.ingest(store, registry, late_body, source_id="src-logsys", received_time=t(3),
                            recorded_time=t(3), actor="conn", marking=BASE_MARKING,
                            source_time=t(-4), watermark=watermark)
    assert late.status == "ACCEPTED" and late.ingestion["late"] is True


def test_geojson_connector_validates_geometry(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema({"schema_id": "infra-geo", "version": "1.0", "media_type": "application/geo+json",
                              "payload_kind": "geojson",
                              "fields": {"infra_id": {"type": "string", "required": True}}},
                             recorded_time=T0, actor="fixture")
    connector = GeoJsonConnector("conn-geo", "infra-geo")
    good = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-30.2, 45.1]},
         "properties": {"infra_id": "BR-7"}}]}).encode()
    assert connector.ingest(store, registry, good, source_id="src-geo", received_time=t(1), recorded_time=t(1),
                            actor="conn", marking=BASE_MARKING).status == "ACCEPTED"
    bad = json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-300.0, 45.1]},
         "properties": {"infra_id": "BR-7"}}]}).encode()
    assert connector.ingest(store, registry, bad, source_id="src-geo", received_time=t(2), recorded_time=t(2),
                            actor="conn", marking=BASE_MARKING).status == "QUARANTINED"


def evidence_bundle(source_id="argus-src-1", relationships=()):
    return {
        "source_object": {"source_object_id": source_id, "content_sha256": fake_sha(source_id)},
        "document": {"document_id": f"doc-{source_id}", "authority_state": "REPUTABLE_SECONDARY_REPORT"},
        "assertion": {"assertion_id": f"asrt-{source_id}", "evidence_basis_id": "basis-bridge-strike",
                      "subject_ref": "BR-7", "reported_status": "DAMAGED", "review_state": "UNREVIEWED",
                      "mapping_status": "EXACT", "report_time": t(24)},
        "admission": {"independence_status": "UNRESOLVED", "claim_basis_status": "SINGLE_BASIS",
                      "dependencies": ["COMMON_ORIGIN_REVIEW"]},
        "identity": {"status": "IDENTITY_PROVISIONAL"},
        "source_relationships": list(relationships),
    }


def test_evidence_ref_preserves_argus_semantics():
    bundle = evidence_bundle(relationships=[{"relationship_type": "SYNDICATION",
                                             "source_object_a": "argus-src-1", "source_object_b": "argus-src-2"}])
    ref = evidence_ref_from_bundle(bundle)
    assert ref.source_object_id == "argus-src-1" and ref.evidence_basis_id == "basis-bridge-strike"
    assert ref.identity_status == "IDENTITY_PROVISIONAL" and ref.independence_status == "UNRESOLVED"
    assert ref.mapping_status == "EXACT" and ref.review_state == "UNREVIEWED"
    assert ref.unresolved == ("COMMON_ORIGIN_REVIEW",)
    assert ref.dependence_group_id is not None
    minimal = evidence_bundle()
    minimal.pop("admission"); minimal.pop("identity")
    bare = evidence_ref_from_bundle(minimal)
    assert bare.identity_status == "IDENTITY_UNKNOWN" and bare.independence_status == "UNRESOLVED"
    assert bare.dependence_group_id is None  # missing stays unknown rather than invented


def test_dependence_group_is_order_independent():
    a = evidence_bundle("argus-src-1", [{"relationship_type": "SYNDICATION", "source_object_a": "argus-src-1",
                                         "source_object_b": "argus-src-2"}])
    b = evidence_bundle("argus-src-2", [{"relationship_type": "SYNDICATION", "source_object_a": "argus-src-1",
                                         "source_object_b": "argus-src-2"}])
    assert dependence_group_id(a) == dependence_group_id(b)


EVIDENCE_SCHEMA = {"schema_id": "argus-evidence", "version": "1.0",
                   "media_type": "application/vnd.curunir.argus-evidence+json", "payload_kind": "document",
                   "fields": {"source_object": {"type": "object", "required": True},
                              "document": {"type": "object", "required": True},
                              "assertion": {"type": "object", "required": True}}}

OBSERVATION_MAPPING = {
    "mapping_id": "evidence-to-observation", "version": "1.0",
    "input_schema_id": "argus-evidence", "input_schema_version": "1.0",
    "output_object_type": "OBSERVATION",
    "entries": [
        {"source_field": "assertion_id", "target": "external_id"},
        {"source_field": "reported_status", "target": "attributes.reported_status"},
        {"source_field": "subject_ref", "target": "attributes.subject_ref"},
        {"source_field": "report_time", "target": "valid_from", "transform": "iso_time"},
    ],
}

EVIDENCE_PIPELINE = {
    "pipeline_id": "evidence-observation", "version": "1.0", "connector_id": "conn-argus",
    "schema_id": "argus-evidence", "schema_version": "1.0", "mode": "evidence_observation", "unit": "document",
    "object": {"object_type": "OBSERVATION", "id_prefix": "obs-", "external_system": "ARGUS-SI",
               "external_id_field": "assertion_id", "epistemic_default": "EXTRACTED"},
    "mappings": [{"mapping_id": "evidence-to-observation", "version": "1.0"}],
    "observation": {"target_field": "subject_ref", "target_prefix": "infra-"},
    "marking": BASE_MARKING.to_record(),
}


def test_pipeline_validation_is_strict(tmp_path):
    with pytest.raises(SchemaError):
        validate_pipeline_definition({**EVIDENCE_PIPELINE, "shell_hook": "rm -rf"})
    with pytest.raises(SchemaError):
        validate_pipeline_definition({**EVIDENCE_PIPELINE, "object": {**EVIDENCE_PIPELINE["object"], "object_type": "TARGET"}})
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    executor = PipelineExecutor(store, registry, {"conn-argus": ArgusEvidenceConnector("conn-argus", "argus-evidence")})
    with pytest.raises(SchemaError):  # the schema is not registered yet
        executor.register_pipeline(EVIDENCE_PIPELINE, recorded_time=T0, actor="fixture")


def test_evidence_pipeline_end_to_end(tmp_path):
    store = make_store(tmp_path)
    registry = SchemaRegistry(store)
    registry.register_schema(EVIDENCE_SCHEMA, recorded_time=T0, actor="fixture")
    registry.register_mapping(OBSERVATION_MAPPING, recorded_time=T0, actor="fixture")
    executor = PipelineExecutor(store, registry, {"conn-argus": ArgusEvidenceConnector("conn-argus", "argus-evidence")})
    executor.register_pipeline(EVIDENCE_PIPELINE, recorded_time=T0, actor="fixture")
    body = json.dumps(evidence_bundle()).encode()
    result = executor.run("evidence-observation", body, source_id="src-argus", source_time=t(24),
                          received_time=t(25), recorded_time=t(25), actor="adapter",
                          marking_override=RESTRICTED_MARKING)
    assert result["status"] == "ACCEPTED" and result["objects"]
    version = store.records_of("object_version")[-1]
    assert version["object_type"] == "OBSERVATION"
    assert version["provenance"]["mode"] == "EVIDENTIARY"
    evidence = version["provenance"]["evidence"][0]
    assert evidence["evidence_basis_id"] == "basis-bridge-strike"
    assert version["marking"]["compartments"] == ["SENSITIVE-INFRA"]
    relations = store.records_of("relationship_version")
    assert any(r["relation_type"] == "REPORTS_ON" and r["target_object_id"] == "infra-BR-7" for r in relations)
    transformations = store.records_of("transformation")
    assert transformations and transformations[0]["input_refs"][0]["kind"] == "ingestion"
