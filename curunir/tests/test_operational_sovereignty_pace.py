"""Sovereignty manifest, the exit test that exports and reimports a store, and
PACE bundles: building, verifying, detecting tampering, and importing."""
from __future__ import annotations

import json

import pytest

from curunir_operational.contracts import ObjectVersion, ProvenanceSummary, SourceRecord
from curunir_operational.projection import Projection
from curunir_operational.sovereignty import (build_pace_bundle, build_sovereignty_manifest, import_pace_bundle,
                                             run_exit_test, verify_pace_bundle)

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, RESTRICTED_MARKING, T0,
                                 make_store, t)

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",))


def seeded(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED",
                 SourceRecord("src-a", "SYSTEM", "TESTSYS", "x", "op", "AUTH-1", {}, BASE_MARKING,
                              "ACTIVE", "", T0), recorded_time=T0, actor="fixture")
    for object_id, marking in (("stock-1", BASE_MARKING), ("obs-secret", RESTRICTED_MARKING)):
        store.append("OBJECT_VERSION_APPENDED",
                     ObjectVersion(object_id, 1, "RESOURCE_STOCK" if object_id == "stock-1" else "OBSERVATION",
                                   "ACTIVE", (object_id,), (), t(0), None, t(0), "HOUR", t(0), None,
                                   {"value": 1}, {"source_reliability": "UNKNOWN"}, "REPORTED", marking, PROV),
                     recorded_time=t(0), actor="fixture")
    return store


def test_sovereignty_manifest_dependencies(tmp_path):
    store = seeded(tmp_path)
    manifest = build_sovereignty_manifest(store)
    names = [d["dependency"] for d in manifest["dependencies"]]
    assert any("standard library" in n for n in names)
    assert all(d["network_required"] is False for d in manifest["dependencies"])
    assert manifest["engagement_boundary"] == "SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS"
    owner = manifest["data_ownership"]["operational_objects_relations_workflow"]["owner"]
    assert "CIVDEF-AUTH" in owner  # read off the recorded markings, not hard-coded


def test_exit_test_roundtrip(tmp_path):
    store = seeded(tmp_path)
    result = run_exit_test(store, tmp_path / "export", tmp_path / "fresh", HIGH_CONTEXT, snapshot_time=t(1))
    assert result["passed"], result["checks"]
    assert all(result["checks"].values())


def test_pace_bundle_lifecycle(tmp_path):
    store = seeded(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    manifest = build_pace_bundle(store, projection, LOW_CONTEXT, tmp_path / "pace",
                                 operational_context="test corridor")
    assert verify_pace_bundle(tmp_path / "pace")["valid"]
    text = (tmp_path / "pace" / "sitrep.txt").read_text()
    assert "obs-secret" not in text and "SITUATION REPORT" in text
    projection_json = (tmp_path / "pace" / "projection.json").read_text()
    assert "obs-secret" not in projection_json
    assert "omitted" in manifest["omission_policy"]
    briefing = import_pace_bundle(tmp_path / "pace")
    assert briefing["non_authoritative_import"] is True
    assert briefing["projection"]["counts"]["objects_total"] == 1
    # The same inputs must give the same bundle hash.
    again = build_pace_bundle(store, projection, LOW_CONTEXT, tmp_path / "pace2",
                              operational_context="test corridor")
    assert again["combined_sha256"] == manifest["combined_sha256"]


def test_pace_tamper_detection(tmp_path):
    store = seeded(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    build_pace_bundle(store, projection, LOW_CONTEXT, tmp_path / "pace",
                      operational_context="test corridor")
    target = tmp_path / "pace" / "alerts.json"
    target.write_text(target.read_text().replace("[]", '[{"forged": true}]'))
    verification = verify_pace_bundle(tmp_path / "pace")
    assert not verification["valid"] and any("alerts.json" in f for f in verification["failures"])
    with pytest.raises(ValueError):
        import_pace_bundle(tmp_path / "pace")
