"""Bounded synthetic ingestion and replay harness.

It drives a mix of valid, duplicate, late, corrected and malformed records
through the real pipeline so the store, projection, export and replay run at
volume, then checks that replay reproduces the same projection. The timings
are fixture-scale, not a performance claim.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .access import AccessContext, Marking
from .pipelines import PipelineExecutor, build_connector
from .projection import Projection, projection_hash
from .schema_registry import SchemaRegistry
from .store import MissionDataStore

STRESS_T0 = datetime(2026, 4, 1, tzinfo=timezone.utc)

STRESS_SCHEMA = {
    "schema_id": "stress-reading", "version": "1.0", "media_type": "application/json",
    "payload_kind": "document",
    "fields": {"reading_id": {"type": "string", "required": True},
               "asset_ref": {"type": "string", "required": True},
               "value": {"type": "number", "required": True},
               "reported_status": {"type": "string", "required": True},
               "observed_time": {"type": "string", "required": True, "format": "iso-datetime"}},
}
STRESS_MAPPING = {
    "mapping_id": "stress-map", "version": "1.0", "input_schema_id": "stress-reading",
    "input_schema_version": "1.0", "output_object_type": "ASSET",
    "entries": [{"source_field": "reading_id", "target": "external_id"},
                {"source_field": "asset_ref", "target": "attributes.asset_ref"},
                {"source_field": "value", "target": "attributes.value", "transform": "to_number"},
                {"source_field": "reported_status", "target": "attributes.reported_status"},
                {"source_field": "observed_time", "target": "valid_from", "transform": "iso_time"},
                {"source_field": "observed_time", "target": "source_time", "transform": "iso_time"}],
}
STRESS_PIPELINE = {
    "pipeline_id": "stress", "version": "1.0", "connector_id": "conn-stress", "connector_kind": "json",
    "event_id_field": "reading_id", "schema_id": "stress-reading", "schema_version": "1.0",
    "mode": "direct_state", "unit": "document",
    "object": {"object_type": "ASSET", "id_prefix": "asset-", "external_system": "STRESSNET",
               "external_id_field": "reading_id", "epistemic_default": "REPORTED"},
    "mappings": [{"mapping_id": "stress-map", "version": "1.0"}],
    "marking": Marking("STRESS-AUTH", releasability=("STRESS",)).to_record(),
}


def _at(seconds: int) -> str:
    return (STRESS_T0 + timedelta(seconds=seconds)).isoformat()


def run_stress(store_root: str | Path, *, n: int = 10_000, families: int = 8) -> dict[str, Any]:
    store_root = Path(store_root)
    store = MissionDataStore.create(store_root, "stress-store", _at(0))
    registry = SchemaRegistry(store)
    registry.register_schema(STRESS_SCHEMA, recorded_time=_at(0), actor="stress")
    registry.register_mapping(STRESS_MAPPING, recorded_time=_at(0), actor="stress")
    executor = PipelineExecutor(store, registry, {"conn-stress": build_connector("json", "conn-stress", "stress-reading", "reading_id")})
    executor.register_pipeline(STRESS_PIPELINE, recorded_time=_at(0), actor="stress")
    markings = [Marking("STRESS-AUTH", releasability=("STRESS",)),
                Marking("STRESS-AUTH", compartments=("STRESS-SENSITIVE",), releasability=("STRESS",), min_role="ANALYST")]

    counts = {"valid": 0, "duplicate": 0, "late": 0, "corrected": 0, "malformed": 0}
    ingest_started = time.monotonic()
    for i in range(n):
        family = i % families
        source = f"stress-src-{family}"
        recorded = _at(100 + i)
        marking = markings[i % 2]
        observed_seconds = 100 + i
        if i % 97 == 0 and i > 0:  # malformed body
            body = b'{"reading_id": "broken", '
            counts["malformed"] += 1
        elif i % 53 == 0 and i > 0:  # resend of a prior reading
            body = json.dumps({"reading_id": f"r-{family}-{i-1}", "asset_ref": f"asset-{family}",
                               "value": 1.0, "reported_status": "NOMINAL", "observed_time": _at(100 + i - 1)}).encode()
            counts["duplicate"] += 1
        else:
            late = i % 37 == 0 and i > families
            if late:
                observed_seconds = 100 + i - 30
                counts["late"] += 1
            reported = "DEGRADED" if i % 41 == 0 else "NOMINAL"
            if reported == "DEGRADED":
                counts["corrected"] += 1
            body = json.dumps({"reading_id": f"r-{family}-{i}", "asset_ref": f"asset-{family}",
                               "value": float(i % 500), "reported_status": reported,
                               "observed_time": _at(observed_seconds)}).encode()
            counts["valid"] += 1
        executor.run("stress", body, source_id=source, source_time=_at(observed_seconds), received_time=recorded,
                     recorded_time=recorded, actor="stress", marking_override=marking)
    ingest_s = time.monotonic() - ingest_started

    context = AccessContext("stress-ctx", "stress-analyst", "HUMAN", ("ANALYST",), ("STRESS-SENSITIVE",),
                            ("STRESS",), "STRESS-AUTH")
    projection_started = time.monotonic()
    projection = Projection(store, snapshot_time=_at(100 + n))
    view = projection.view(context)
    projection_s = time.monotonic() - projection_started
    original_hash = projection_hash(view)

    history_started = time.monotonic()
    Projection(store, as_of_seq=store.head()["event_count"] // 2, snapshot_time=_at(100 + n))
    history_s = time.monotonic() - history_started

    export_started = time.monotonic()
    store.export_to(store_root.parent / "stress_export")
    export_s = time.monotonic() - export_started

    replay_started = time.monotonic()
    replayed = MissionDataStore.import_from(store_root.parent / "stress_export", store_root.parent / "stress_replay")
    replay_view = Projection(replayed, snapshot_time=_at(100 + n)).view(context)
    replay_s = time.monotonic() - replay_started

    events_bytes = (store_root / "events.jsonl").stat().st_size
    return {
        "records_ingested": n, "families": families, "counts": counts,
        "store_events": store.head()["event_count"], "objects": view["counts"]["objects_total"],
        "quarantined_ingestions": sum(1 for r in store.records_of("ingestion") if r["quarantined"]),
        "duplicate_ingestions": sum(1 for r in store.records_of("ingestion") if r["validation"] == "DUPLICATE"),
        "late_ingestions": sum(1 for r in store.records_of("ingestion") if r["late"]),
        "determinism": {"original_projection_hash": original_hash,
                        "replay_projection_hash": projection_hash(replay_view),
                        "projection_equal": original_hash == projection_hash(replay_view),
                        "chain_valid": replayed.verify_chain()["valid"]},
        "performance": {
            "ingest_s": round(ingest_s, 3), "ingest_per_1k_s": round(ingest_s / (n / 1000), 4),
            "projection_s": round(projection_s, 3), "historical_query_s": round(history_s, 3),
            "export_s": round(export_s, 3), "replay_s": round(replay_s, 3),
            "events_bytes": events_bytes, "bytes_per_event": round(events_bytes / max(store.head()["event_count"], 1)),
            "environment": f"python {sys.version.split()[0]}, single process, local disk",
            "note": "fixture-scale synthetic stress; NOT a production performance claim",
        },
    }
