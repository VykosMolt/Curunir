"""Synthetic feed payloads for scenarios B and C. All invented.

Scenario B is an infrastructure cascade: a hazard degrades a substation and a
bridge, pulling in a communications dependency, a restricted assessment,
conflicting reports and a route consequence in the other workbench.

Scenario C is false corroboration and schema drift: several reports off one
basis, one genuinely independent source, a schema version change, a delayed
correction, a retraction, a duplicate publication and an ambiguous object.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LIVE_FIXTURES = Path(__file__).resolve().parent / "live_fixtures"

# Corridor geometry reused from the first scenario.
CORRIDOR = {
    "ALDEN": [-30.42, 45.06], "BRUSKA": [-29.88, 45.31], "BR-7": [-30.10, 45.20], "SUB-4": [-30.05, 45.12],
    "COMMS-2": [-30.08, 45.16],
    "R1": [[-30.42, 45.06], [-30.10, 45.20], [-29.88, 45.31]],
    "R2": [[-30.42, 45.06], [-30.20, 45.00], [-29.95, 45.12], [-29.88, 45.31]],
    "HAZARD_CENTER": [-30.09, 45.19],  # Close to BR-7 and SUB-4.
}


def load_gdacs_capture() -> dict:
    return json.loads((LIVE_FIXTURES / "gdacs_capture_20260720.json").read_text())


def load_evidence_bundle() -> dict:
    return json.loads((LIVE_FIXTURES / "gdacs_report_evidence_bundle_20260720.json").read_text())


def hazard_over_corridor(from_utc_naive: str) -> bytes:
    """One hazard feature over the corridor, borrowing the shape of a captured
    live feed. The geometry is synthetic corridor coordinates, not the feed's."""
    capture = load_gdacs_capture()
    template = next(f["properties"] for f in capture["features"] if f["properties"]["eventtype"] == "EQ")
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": CORRIDOR["HAZARD_CENTER"]},
        "properties": {"eventid": 9900001, "eventtype": template["eventtype"], "name": "Synthetic seismic event (corridor)",
                       "alertlevel": "Orange", "fromdate": from_utc_naive, "country": "Vessia (synthetic)"},
    }
    return json.dumps({"type": "FeatureCollection", "features": [feature]}).encode()


def corridor_infrastructure(effective: str) -> bytes:
    def feature(infra_id, name, kind, coords):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
                "properties": {"infra_id": infra_id, "name": name, "kind": kind,
                               "status": "OPERATIONAL", "effective_time": effective}}
    return json.dumps({"type": "FeatureCollection", "features": [
        feature("ALDEN", "Alden Relief Depot", "depot", CORRIDOR["ALDEN"]),
        feature("BRUSKA", "Bruska Forward Depot", "depot", CORRIDOR["BRUSKA"]),
        feature("BR-7", "Vessia Crossing Seven", "bridge", CORRIDOR["BR-7"]),
        feature("SUB-4", "Substation Four", "substation", CORRIDOR["SUB-4"]),
        feature("COMMS-2", "Relay Mast Two", "substation", CORRIDOR["COMMS-2"]),
    ]}).encode()


def corridor_routes(effective: str) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": CORRIDOR["R1"]},
         "properties": {"route_id": "R1", "name": "North Crossing", "crosses_ref": "BR-7",
                        "alternate_ref": "R2", "effective_time": effective}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": CORRIDOR["R2"]},
         "properties": {"route_id": "R2", "name": "Coastal Loop", "effective_time": effective}},
    ]}).encode()


def stock_csv(effective: str) -> bytes:
    header = "stock_id,depot_ref,commodity,quantity,unit,report_time"
    rows = [("ALD-REP", "ALDEN", "repair", 40, "units", effective),
            ("ALD-MED", "ALDEN", "medical", 300, "kits", effective),
            ("BRK-FUEL", "BRUSKA", "fuel", 8000, "l", effective)]
    return ("\n".join([header] + [",".join(str(v) for v in row) for row in rows]) + "\n").encode()


def engineering_assessment_v1(assessment_id, asset, condition, assessed, inspector=None) -> bytes:
    payload = {"assessment_id": assessment_id, "asset": asset, "condition": condition, "assessed_time": assessed}
    if inspector:
        payload["inspector"] = inspector
    return json.dumps(payload).encode()


def engineering_assessment_v2(assessment_id, asset_ref, condition, assessed, inspector=None) -> bytes:
    payload = {"assessment_id": assessment_id, "asset_ref": asset_ref, "condition": condition,
               "schema_version": "2.0", "assessed_time": assessed}
    if inspector:
        payload["inspector"] = inspector
    return json.dumps(payload).encode()


def field_observation(report_id, subject_ref, subject_kind, status, observed, detail="", corrects=None,
                      lon=None, lat=None) -> bytes:
    payload = {"report_id": report_id, "subject_ref": subject_ref, "subject_kind": subject_kind,
               "reported_status": status, "observed_time": observed}
    if detail:
        payload["detail"] = detail
    if corrects:
        payload["corrects_report"] = corrects
    if lon is not None:
        payload["lon"] = lon
        payload["lat"] = lat
    return json.dumps(payload).encode()


# ---- scenario C: false corroboration and schema drift ----

def argus_bundle(source_key, assertion_id, publisher, basis, subject, status, report_time, *,
                 dependents=(), review_state="UNREVIEWED", retraction=False) -> bytes:
    rels = [{"relationship_type": "SYNDICATION", "source_object_a": source_key, "source_object_b": other}
            for other in dependents]
    assertion = {"assertion_id": assertion_id, "evidence_basis_id": basis, "subject_ref": subject,
                 "reported_status": status, "summary": f"{publisher} reports {status} for {subject}",
                 "report_time": report_time, "review_state": review_state, "mapping_status": "APPROXIMATE"}
    if retraction:
        assertion["reported_status"] = "RETRACTED"
        assertion["summary"] = f"{publisher} retracts prior report for {subject}"
    return json.dumps({
        "source_object": {"source_object_id": source_key, "content_sha256": hashlib.sha256(source_key.encode()).hexdigest()},
        "document": {"document_id": f"doc-{assertion_id}", "authority_state": "REPUTABLE_SECONDARY_REPORT"},
        "assertion": assertion,
        "admission": {"independence_status": "UNRESOLVED", "claim_basis_status": "SINGLE_BASIS",
                      "dependencies": ["COMMON_ORIGIN_REVIEW"]},
        "identity": {"status": "IDENTITY_PROVISIONAL"},
        "source_relationships": rels}).encode()
