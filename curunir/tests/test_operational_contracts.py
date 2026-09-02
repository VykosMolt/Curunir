"""Contracts: identifiers, timestamps, geometry, epistemic states, quality,
markings, stable serialization and hashes."""
from __future__ import annotations

import pytest

from curunir_operational.access import AccessContext, Marking, can_view
from curunir_operational.canonical import sha256
from curunir_operational.contracts import (Alert, ModelPackage, ObjectVersion, ProvenanceSummary,
                                           Recommendation, model_integrity_hash)
from curunir_operational.geometry import Geometry, geometry_from_geojson, haversine_m, point_to_linestring_m

from operational_support import BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, T0, fake_sha, t

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-1",), ingestion_ids=("ing-1",))


def make_object(**overrides):
    values = dict(object_id="depot-alden", version=1, object_type="INFRASTRUCTURE", lifecycle="ACTIVE",
                  labels=("Alden Depot",), external_refs=(), valid_from=T0, valid_to=None,
                  source_time=T0, time_precision="EXACT", recorded_time=T0, geometry=None,
                  attributes={"status": "OPERATIONAL"}, quality={"completeness": 1.0},
                  epistemic_state="REPORTED", marking=BASE_MARKING, provenance=PROV)
    values.update(overrides)
    return ObjectVersion(**values)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        make_object(recorded_time="2026-03-01T06:00:00")


def test_unknown_time_stays_valid():
    version = make_object(source_time=None, valid_from=None, time_precision="UNKNOWN")
    assert version.source_time is None and version.time_precision == "UNKNOWN"


def test_invalid_epistemic_state_rejected():
    with pytest.raises(ValueError):
        make_object(epistemic_state="TRUE_FACT")


def test_quality_dimensions_validated():
    with pytest.raises(ValueError):
        make_object(quality={"truth_score": 0.9})
    with pytest.raises(ValueError):
        make_object(quality={"completeness": None})
    version = make_object(quality={"completeness": "UNKNOWN", "source_reliability": "UNKNOWN"})
    assert version.quality["completeness"] == "UNKNOWN"


def test_correction_requires_reason():
    with pytest.raises(ValueError):
        make_object(version=2, correction_of="depot-alden@v1")


def test_geometry_validation():
    with pytest.raises(ValueError):
        Geometry("POINT", (-200.0, 10.0))
    with pytest.raises(ValueError):
        Geometry("POLYGON", ((-30.0, 45.0), (-30.1, 45.0), (-30.1, 45.1)))  # unclosed / too short
    point = Geometry("POINT", (-30.25, 45.5, 12.0), uncertainty_m=250.0)
    assert point.to_geojson() == {"type": "Point", "coordinates": [-30.25, 45.5, 12.0]}
    with pytest.raises(ValueError):
        geometry_from_geojson({"type": "MultiPolygon", "coordinates": []})


def test_distances_deterministic_and_sane():
    a, b = (-30.0, 45.0), (-30.0, 46.0)
    d = haversine_m(a, b)
    assert 110_000 < d < 112_500
    assert d == haversine_m(a, b)
    line = ((-30.2, 45.0), (-29.8, 45.0))
    assert point_to_linestring_m((-30.0, 45.05), line) < 6_000


def test_stable_serialization_and_hash():
    first, second = make_object(), make_object()
    assert first.to_record() == second.to_record()
    assert sha256(first.to_record()) == sha256(second.to_record())


def test_marking_fail_closed_matrix():
    assert can_view(BASE_MARKING, HIGH_CONTEXT) and can_view(BASE_MARKING, LOW_CONTEXT)
    compartmented = Marking("CIVDEF-AUTH", compartments=("SENSITIVE-INFRA",), releasability=("CORRIDOR-OPS",), min_role="ANALYST")
    assert can_view(compartmented, HIGH_CONTEXT) and not can_view(compartmented, LOW_CONTEXT)
    owner_only = Marking("CIVDEF-AUTH")  # empty releasability: owning organisation only
    assert can_view(owner_only, HIGH_CONTEXT) and not can_view(owner_only, LOW_CONTEXT)
    assert not can_view(None, HIGH_CONTEXT)
    assert not can_view({"min_role": "WIZARD", "owning_authority": "CIVDEF-AUTH"}, HIGH_CONTEXT)
    with pytest.raises(ValueError):
        Marking("")
    with pytest.raises(ValueError):
        AccessContext("c", "a", "ROBOT", ("ANALYST",))


def test_alert_and_recommendation_must_be_evidence_bound():
    with pytest.raises(ValueError):
        Alert("al-1", "rule-x", "1.0", "trigger", ("obj",), (), "", "WARNING", "because", "k", "", T0, BASE_MARKING)
    with pytest.raises(ValueError):
        Recommendation("rec-1", ("al-1",), "ROUTE_CHANGE", "use alternate", "why", (), (), (),
                       fake_sha("snap"), "", "", "", None, "SUPERVISOR", "prov", t(1), BASE_MARKING)
    with pytest.raises(ValueError):  # unknown role also fails closed
        Recommendation("rec-1", ("al-1",), "ROUTE_CHANGE", "use alternate", "why", (), (), ("ev-1",),
                       fake_sha("snap"), "", "", "", None, "COMMANDER", "prov", t(1), BASE_MARKING)


def test_model_package_integrity_hash():
    package = ModelPackage("mock-damage", "1.0", "curunir-internal", "assessment", "s-in", "s-out",
                           "synthetic only", "not evaluated", ("fixture-scale",), ("research",),
                           ("operational targeting",), "instant", "cpu", "internal", "SYNTHETIC_EVALUATION_ONLY")
    assert package.integrity_hash == model_integrity_hash(package)
    with pytest.raises(ValueError):
        ModelPackage("mock-damage", "1.0", "curunir-internal", "assessment", "s-in", "s-out",
                     "synthetic only", "not evaluated", ("fixture-scale",), ("research",),
                     ("operational targeting",), "instant", "cpu", "internal", "SYNTHETIC_EVALUATION_ONLY",
                     integrity_hash=fake_sha("tampered"))
    with pytest.raises(ValueError):
        ModelPackage("mock-damage", "1.0", "curunir-internal", "assessment", "s-in", "s-out",
                     "synthetic only", "not evaluated", (), (), (), "instant", "cpu", "internal",
                     accreditation_state="FULLY_ACCREDITED")
