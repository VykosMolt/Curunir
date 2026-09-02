"""Shared fixtures for curunir_operational tests (no DB, no network)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from curunir_operational.access import AccessContext, Marking
from curunir_operational.contracts import EvidenceRef, ObjectVersion, ProvenanceSummary, RelationshipVersion
from curunir_operational.geometry import Geometry
from curunir_operational.projection import Projection
from curunir_operational.store import MissionDataStore

T0 = "2026-03-01T06:00:00+00:00"

BASE_MARKING = Marking(owning_authority="CIVDEF-AUTH", releasability=("CORRIDOR-OPS",))
RESTRICTED_MARKING = Marking(owning_authority="CIVDEF-AUTH", compartments=("SENSITIVE-INFRA",),
                             releasability=("CORRIDOR-OPS",), min_role="ANALYST")

HIGH_CONTEXT = AccessContext("ctx-high", "analyst-vale", "HUMAN", ("ANALYST", "SUPERVISOR"),
                             ("SENSITIVE-INFRA",), ("CORRIDOR-OPS",), "CIVDEF-AUTH")
LOW_CONTEXT = AccessContext("ctx-low", "observer-brenn", "HUMAN", ("OBSERVER",),
                            (), ("CORRIDOR-OPS",), "PARTNER-RELIEF-ORG")
SERVICE_CONTEXT = AccessContext("ctx-svc", "rule-engine", "SERVICE", ("ANALYST",),
                                ("SENSITIVE-INFRA",), ("CORRIDOR-OPS",), "CIVDEF-AUTH")


def t(hours: float) -> str:
    base = datetime.fromisoformat(T0)
    return (base + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()


def make_store(tmp_path, name: str = "store", store_id: str = "test-store") -> MissionDataStore:
    return MissionDataStore.create(tmp_path / name, store_id, T0)


def fake_sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",))


def obj(object_id, object_type, *, version=1, hours=0.0, geometry=None, attributes=None, marking=BASE_MARKING,
        epistemic="REPORTED", evidence=(), labels=None):
    provenance = PROV if not evidence else ProvenanceSummary(mode="EVIDENTIARY", source_ids=("src-argus",),
                                                             ingestion_ids=("ing-e",), evidence=tuple(evidence))
    return ObjectVersion(object_id=object_id, version=version, object_type=object_type, lifecycle="ACTIVE",
                         labels=tuple(labels or (object_id,)), external_refs=(), valid_from=t(hours), valid_to=None,
                         source_time=t(hours), time_precision="HOUR", recorded_time=t(max(hours, 0.0)),
                         geometry=geometry, attributes=attributes or {}, quality={},
                         epistemic_state=epistemic, marking=marking, provenance=provenance)


def rel(relation_type, source, target, *, marking=BASE_MARKING, hours=0.0):
    relationship_id = f"rel-{relation_type.lower()}-{source}-{target}"
    return RelationshipVersion(relationship_id, 1, relation_type, source, target, t(hours), None, t(hours),
                               ("ing-1",), "MAPPING", "UNKNOWN", "ACTIVE", marking, PROV)


def evidence_ref(source_object, group=None):
    return EvidenceRef(source_object, f"doc-{source_object}", fake_sha(source_object), f"asrt-{source_object}",
                       "basis-bridge-strike", "IDENTITY_PROVISIONAL", "REPUTABLE_SECONDARY_REPORT",
                       "UNRESOLVED", "SINGLE_BASIS", "UNREVIEWED", "EXACT", group, ("COMMON_ORIGIN_REVIEW",))


def build_scene(tmp_path, *, restrict_first_observation=False):
    store = make_store(tmp_path)
    line = Geometry("LINESTRING", ((-30.30, 45.10), (-30.10, 45.20), (-29.90, 45.30)))
    alt_line = Geometry("LINESTRING", ((-30.30, 45.10), (-30.05, 45.05), (-29.90, 45.30)))
    group = "evgroup-shared-basis"
    records = [
        obj("infra-BR-7", "INFRASTRUCTURE", geometry=Geometry("POINT", (-30.10, 45.20)),
            attributes={"status": "OPERATIONAL"}),
        obj("route-R1", "ROUTE", geometry=line),
        obj("route-R2", "ROUTE", geometry=alt_line),
        obj("mv-relief-1", "MOVEMENT", epistemic="PLANNED", attributes={"cargo": "medical"}),
        obj("obs-sensor-1", "OBSERVATION", hours=24.0, attributes={"reported_status": "OPERATIONAL"},
            marking=RESTRICTED_MARKING if restrict_first_observation else BASE_MARKING),
        obj("obs-report-1", "OBSERVATION", hours=25.0, attributes={"reported_status": "DAMAGED"},
            evidence=[evidence_ref("argus-src-1", group)]),
        obj("obs-report-2", "OBSERVATION", hours=25.5, attributes={"reported_status": "DAMAGED"},
            evidence=[evidence_ref("argus-src-2", group)]),
        obj("stock-fuel-alden", "RESOURCE_STOCK", hours=-30.0, attributes={"commodity": "fuel"}),
    ]
    for record in records:
        store.append("OBJECT_VERSION_APPENDED", record, recorded_time=t(max(record.version, 26.0)), actor="fixture")
    relationships = [
        rel("REPORTS_ON", "obs-sensor-1", "infra-BR-7",
            marking=RESTRICTED_MARKING if restrict_first_observation else BASE_MARKING, hours=24.0),
        rel("REPORTS_ON", "obs-report-1", "infra-BR-7", hours=25.0),
        rel("REPORTS_ON", "obs-report-2", "infra-BR-7", hours=25.5),
        rel("DEPENDS_ON", "route-R1", "infra-BR-7"),
        rel("PLANNED_FOR", "mv-relief-1", "route-R1"),
        rel("ALTERNATE_OF", "route-R1", "route-R2"),
    ]
    for relationship in relationships:
        store.append("RELATIONSHIP_VERSION_APPENDED", relationship, recorded_time=t(26.0), actor="fixture")
    projection = Projection(store, snapshot_time=t(26.0), staleness_hours={"RESOURCE_STOCK": 24.0})
    return store, projection
