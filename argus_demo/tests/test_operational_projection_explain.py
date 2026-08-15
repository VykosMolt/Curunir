"""Projection: current vs as-of state, late data, freshness, access filtering
without leakage, changes-since, explanations."""
from __future__ import annotations

import json

import pytest

from curunir_operational.canonical import canonical_line
from curunir_operational.contracts import ObjectVersion, ProvenanceSummary, RelationshipVersion, SourceRecord
from curunir_operational.explain import explain, explain_markdown
from curunir_operational.projection import Projection, projection_hash

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, RESTRICTED_MARKING, T0,
                                 make_store, t)

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-logsys",), ingestion_ids=("ing-1",),
                         transformation_ids=("tf-1",))

SECRET_LABEL = "Kestrel Array"  # restricted object label; must never leak into low views


def build_store(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED",
                 SourceRecord("src-logsys", "SYSTEM", "LOGSYS", "logsys-01", "Corridor Logistics Office",
                              "CIVDEF-AUTH", {"track_record": "UNKNOWN"}, BASE_MARKING, "ACTIVE", "", T0),
                 recorded_time=T0, actor="fixture")

    def version(object_id, number, hours, status, **kw):
        return ObjectVersion(object_id=object_id, version=number, object_type=kw.pop("object_type", "RESOURCE_STOCK"),
                             lifecycle="ACTIVE", labels=kw.pop("labels", (object_id,)), external_refs=(),
                             valid_from=t(hours), valid_to=None, source_time=t(hours), time_precision="HOUR",
                             recorded_time=kw.pop("recorded_time", t(max(hours, 0.0))), geometry=None,
                             attributes={"status": status, **kw.pop("attributes", {})},
                             quality=kw.pop("quality", {}), epistemic_state=kw.pop("epistemic_state", "REPORTED"),
                             marking=kw.pop("marking", BASE_MARKING), provenance=PROV, **kw)

    store.append("OBJECT_VERSION_APPENDED", version("stock-fuel", 1, 0.0, "1200l"), recorded_time=t(0), actor="pipe")
    store.append("OBJECT_VERSION_APPENDED", version("stock-fuel", 2, 6.0, "900l"), recorded_time=t(6), actor="pipe")
    # late arrival: older validity recorded later — must not displace current state
    store.append("OBJECT_VERSION_APPENDED", version("stock-fuel", 3, 3.0, "1000l", recorded_time=t(8)),
                 recorded_time=t(8), actor="pipe")
    # correction of the 900l report (same validity, higher version)
    store.append("OBJECT_VERSION_APPENDED",
                 version("stock-fuel", 4, 6.0, "950l", recorded_time=t(9), epistemic_state="CORRECTED",
                         correction_of="stock-fuel@v2", correction_reason="clerical unit error"),
                 recorded_time=t(9), actor="pipe")
    store.append("OBJECT_VERSION_APPENDED",
                 version("obs-kestrel", 1, 7.0, "DEGRADED", object_type="OBSERVATION",
                         labels=(SECRET_LABEL,), marking=RESTRICTED_MARKING,
                         quality={"source_reliability": "UNKNOWN"}),
                 recorded_time=t(9), actor="pipe")
    store.append("RELATIONSHIP_VERSION_APPENDED",
                 RelationshipVersion("rel-reports", 1, "REPORTS_ON", "obs-kestrel", "stock-fuel",
                                     t(7), None, t(9), ("ing-1",), "MAPPING", "UNKNOWN", "ACTIVE",
                                     RESTRICTED_MARKING, PROV),
                 recorded_time=t(9), actor="pipe")
    return store


def test_current_state_late_data_and_correction(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(10))
    current = projection.objects["stock-fuel"]["current"]
    assert current["version"] == 4 and current["attributes"]["status"] == "950l"
    assert projection.objects["stock-fuel"]["history_count"] == 4
    assert projection.objects["stock-fuel"]["corrected_version_ids"] == ["stock-fuel@v2"]


def test_as_of_recorded_time(tmp_path):
    store = build_store(tmp_path)
    seq_after_two = 3  # source + v1 + v2
    projection = Projection(store, as_of_seq=seq_after_two, snapshot_time=t(6))
    assert projection.objects["stock-fuel"]["current"]["version"] == 2
    assert projection.objects["stock-fuel"]["current"]["attributes"]["status"] == "900l"


def test_freshness_and_quality_summary(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(40), staleness_hours={"RESOURCE_STOCK": 24.0})
    entry = projection.objects["stock-fuel"]
    assert entry["freshness"]["state"] == "STALE"
    assert "freshness" in entry["quality_summary"]["weak_or_unknown_dimensions"]
    assert entry["quality_summary"]["state"] == "DEGRADED"


def test_access_views_differ_without_leakage(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(10))
    high = projection.view(HIGH_CONTEXT)
    low = projection.view(LOW_CONTEXT)
    high_ids = {o["object_id"] for o in high["objects"]}
    low_ids = {o["object_id"] for o in low["objects"]}
    assert "obs-kestrel" in high_ids and "obs-kestrel" not in low_ids
    assert low["counts"]["objects_total"] == len(low["objects"])  # counts from filtered set only
    assert high["counts"]["objects_total"] == low["counts"]["objects_total"] + 1
    low_json = canonical_line(low)
    assert "obs-kestrel" not in low_json and SECRET_LABEL not in low_json
    assert not low["relationships"]  # hidden endpoint removes the relationship entirely
    assert any(r["source_object_id"] == "obs-kestrel" for r in high["relationships"])
    assert projection_hash(high) != projection_hash(low)


def test_object_history_forbidden_equals_unknown(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(10))
    assert projection.object_history("obs-kestrel", LOW_CONTEXT) == []
    assert projection.object_history("no-such-object", LOW_CONTEXT) == []
    assert len(projection.object_history("obs-kestrel", HIGH_CONTEXT)) == 1


def test_changes_since_respects_access(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(10))
    high = projection.changes_since(1, HIGH_CONTEXT, store)
    low = projection.changes_since(1, LOW_CONTEXT, store)
    assert "obs-kestrel" in high["changes"]["object_version"]
    assert "obs-kestrel" not in low["changes"].get("object_version", [])
    assert "obs-kestrel" not in json.dumps(low)


def test_explain_and_markdown(tmp_path):
    store = build_store(tmp_path)
    projection = Projection(store, snapshot_time=t(10))
    explanation = explain(projection, "stock-fuel", HIGH_CONTEXT)
    assert explanation["status"] == "AVAILABLE"
    assert explanation["sources"][0]["source_system"] == "LOGSYS"
    history = explanation["history"]
    assert [h["version"] for h in history] == [1, 2, 3, 4]
    assert history[3]["correction_of"] == "stock-fuel@v2"
    markdown = explain_markdown(explanation)
    assert "corrects stock-fuel@v2" in markdown and "LOGSYS" in markdown
    hidden = explain(projection, "obs-kestrel", LOW_CONTEXT)
    missing = explain(projection, "no-such-record", LOW_CONTEXT)
    assert hidden == missing  # forbidden and unknown are indistinguishable
    assert explain(projection, "obs-kestrel", HIGH_CONTEXT)["status"] == "AVAILABLE"
