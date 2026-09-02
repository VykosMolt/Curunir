"""Synthetic feed payloads for the Vessia Corridor scenario. Data only; every
name, coordinate and event is invented."""
from __future__ import annotations

import hashlib
import json

from .config import at

COORDS = {
    "ALDEN": [-30.42, 45.06], "BRUSKA": [-29.88, 45.31],
    "BR-7": [-30.10, 45.20], "SUB-4": [-30.05, 45.12],
    "R1": [[-30.42, 45.06], [-30.10, 45.20], [-29.88, 45.31]],
    "R2": [[-30.42, 45.06], [-30.20, 45.00], [-29.95, 45.12], [-29.88, 45.31]],
    "OBSTRUCTION": [-30.102, 45.202],
    "CONV_A": [-30.35, 45.08], "CONV_B": [-30.30, 45.10],
}


def _geojson(features):
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def infrastructure_registry() -> bytes:
    def feature(infra_id, name, kind, coordinates):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": {"infra_id": infra_id, "name": name, "kind": kind,
                               "status": "OPERATIONAL", "effective_time": at(-24)}}
    return _geojson([
        feature("ALDEN", "Alden Relief Depot", "depot", COORDS["ALDEN"]),
        feature("BRUSKA", "Bruska Forward Depot", "depot", COORDS["BRUSKA"]),
        feature("BR-7", "Vessia Crossing Seven", "bridge", COORDS["BR-7"]),
        feature("SUB-4", "Substation Four", "substation", COORDS["SUB-4"]),
    ])


def route_registry() -> bytes:
    return _geojson([
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": COORDS["R1"]},
         "properties": {"route_id": "R1", "name": "North Crossing", "crosses_ref": "BR-7",
                        "alternate_ref": "R2", "effective_time": at(-24)}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": COORDS["R2"]},
         "properties": {"route_id": "R2", "name": "Coastal Loop", "effective_time": at(-24)}},
    ])


def stock_csv(rows) -> bytes:
    header = "stock_id,depot_ref,commodity,quantity,unit,report_time"
    return ("\n".join([header] + [",".join(str(v) for v in row) for row in rows]) + "\n").encode()


STOCK_INITIAL = stock_csv([
    ("ALD-FUEL", "ALDEN", "fuel", 12000, "l", at(0)),
    ("ALD-MED", "ALDEN", "medical", 400, "kits", at(0)),
    ("BRK-REP", "BRUSKA", "repair", 60, "units", at(0)),
    ("BRK-FUEL", "BRUSKA", "fuel", 5000, "l", at(-30)),  # Never refreshed, so it goes stale.
])
STOCK_UPDATE = stock_csv([("ALD-FUEL", "ALDEN", "fuel", 11000, "l", at(24))])
STOCK_LATE = stock_csv([("ALD-FUEL", "ALDEN", "fuel", 11500, "l", at(20))])  # Older validity, arriving later.


def movement_plan(status="PLANNED", effective_hours=0.75) -> bytes:
    return json.dumps({"movement_id": "RELIEF-101", "from_ref": "ALDEN", "to_ref": "BRUSKA",
                       "route_ref": "R1", "cargo": "medical", "status": status,
                       "depart_time": at(30), "effective_time": at(effective_hours)}).encode()


BULLETIN_DAMAGE = json.dumps({
    "report_id": "CD-9001", "subject_ref": "BR-7", "subject_kind": "infrastructure",
    "reported_status": "DAMAGED", "detail": "partial span failure reported by district office",
    "observed_time": at(24.5)}).encode()

BULLETIN_CORRECTION = json.dumps({
    "report_id": "CD-9001", "subject_ref": "BR-7", "subject_kind": "infrastructure",
    "reported_status": "DAMAGED", "detail": "correction: one lane may remain passable; assessment pending",
    "corrects_report": "CD-9001", "observed_time": at(26.9)}).encode()

SENSOR_BRIDGE_OK = json.dumps({
    "report_id": "FN-2201", "subject_ref": "BR-7", "subject_kind": "infrastructure",
    "reported_status": "OPERATIONAL", "detail": "load sensor within nominal band",
    "lon": COORDS["BR-7"][0], "lat": COORDS["BR-7"][1], "observed_time": at(25.4)}).encode()

SENSOR_SUBSTATION_RESTRICTED = json.dumps({
    "report_id": "FN-2205", "subject_ref": "SUB-4", "subject_kind": "infrastructure",
    "reported_status": "DEGRADED", "detail": "protected feeder telemetry anomaly",
    "lon": COORDS["SUB-4"][0], "lat": COORDS["SUB-4"][1], "observed_time": at(25.6)}).encode()

SENSOR_ROUTE_OBSTRUCTION = json.dumps({
    "report_id": "FN-2210", "subject_ref": "R1", "subject_kind": "route",
    "reported_status": "OBSTRUCTED", "detail": "debris field on the northern approach",
    "lon": COORDS["OBSTRUCTION"][0], "lat": COORDS["OBSTRUCTION"][1], "observed_time": at(25.9)}).encode()

MALFORMED_FIELDNET = b'{"report_id": "FN-2299", "subject_ref": '


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def argus_bundle(source_key: str, assertion_id: str, publisher: str) -> bytes:
    relationships = [{"relationship_type": "SYNDICATION",
                      "source_object_a": "argus-src-vessia-wire", "source_object_b": "argus-src-corridor-daily"}]
    return json.dumps({
        "source_object": {"source_object_id": source_key, "content_sha256": _sha(source_key)},
        "document": {"document_id": f"doc-{assertion_id}", "authority_state": "REPUTABLE_SECONDARY_REPORT"},
        "assertion": {"assertion_id": assertion_id, "evidence_basis_id": "basis-br7-strike",
                      "subject_ref": "BR-7", "reported_status": "DAMAGED",
                      "summary": f"{publisher} reports damage to Vessia Crossing Seven",
                      "report_time": at(24.8), "review_state": "UNREVIEWED", "mapping_status": "EXACT"},
        "admission": {"independence_status": "UNRESOLVED", "claim_basis_status": "SINGLE_BASIS",
                      "dependencies": ["COMMON_ORIGIN_REVIEW", "EXTRACTION_REVIEW"]},
        "identity": {"status": "IDENTITY_PROVISIONAL"},
        "source_relationships": relationships}).encode()


ARGUS_BUNDLE_A = argus_bundle("argus-src-vessia-wire", "asrt-VW-1", "Vessia Wire")
ARGUS_BUNDLE_B = argus_bundle("argus-src-corridor-daily", "asrt-CD-2", "Corridor Daily")


def sighting(sighting_id: str, coordinates, hours: float, *, movement_ref=None, vehicle_count=None) -> bytes:
    payload = {"sighting_id": sighting_id, "lon": coordinates[0], "lat": coordinates[1],
               "observed_time": at(hours)}
    if movement_ref:
        payload["movement_ref"] = movement_ref
    if vehicle_count:
        payload["vehicle_count"] = vehicle_count
    return json.dumps(payload).encode()


SIGHTING_CONV_A = sighting("CONV-A", COORDS["CONV_A"], 28.0, movement_ref="RELIEF-101", vehicle_count=12)
SIGHTING_CONV_B = sighting("CONV-B", COORDS["CONV_B"], 28.5, vehicle_count=11)
