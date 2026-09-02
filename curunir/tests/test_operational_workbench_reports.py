"""Workshop definitions, workbench rendering, the COP page, and situation
reports."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from curunir_operational.contracts import ObjectVersion, ProvenanceSummary, SourceRecord
from curunir_operational.geometry import Geometry
from curunir_operational.projection import Projection
from curunir_operational.schema_registry import SchemaError
from curunir_operational.sitrep import build_situation_report, render_markdown, render_text
from curunir_operational.workbench import (WorkbenchAccessError, WorkbenchRenderer, render_cop_html,
                                           validate_workshop_definition)

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, RESTRICTED_MARKING, T0,
                                 make_store, t)

pytestmark = pytest.mark.no_db

WORKSHOP = {
    "workshop_id": "test-workbench", "version": "1.0", "purpose": "test visibility",
    "object_types": ["INFRASTRUCTURE", "RESOURCE_STOCK", "OBSERVATION"],
    "relationship_types": ["REPORTS_ON"],
    "tables": [{"table_id": "stock", "title": "Stock", "object_type": "RESOURCE_STOCK",
                "columns": [{"header": "id", "path": "object_id"},
                            {"header": "qty", "path": "attributes.quantity"},
                            {"header": "fresh", "path": "freshness.state"}],
                "sort_by": "object_id"}],
    "map_layers": [{"layer_id": "infra", "title": "Infra", "object_types": ["INFRASTRUCTURE", "OBSERVATION"],
                    "geometry_kinds": ["POINT"]}],
    "timeline": {"include": ["object_version", "alert"]},
    "actions": ["ACKNOWLEDGE", "ANNOTATE"],
    "access": {"min_role": "OBSERVER"},
}


def test_workshop_validation_strict():
    checked = validate_workshop_definition(WORKSHOP)
    assert checked["definition_sha256"]
    with pytest.raises(SchemaError):
        validate_workshop_definition({**WORKSHOP, "custom_javascript": "alert(1)"})
    with pytest.raises(SchemaError):
        validate_workshop_definition({**WORKSHOP, "object_types": ["TARGET_LIST"]})
    with pytest.raises(SchemaError):
        validate_workshop_definition({**WORKSHOP, "tables": [{"table_id": "x", "object_type": "RESOURCE_STOCK",
                                                              "columns": [{"header": "a", "path": "b", "extra": 1}]}]})
    with pytest.raises(SchemaError):
        validate_workshop_definition({**WORKSHOP, "actions": ["LAUNCH"]})
    with pytest.raises(SchemaError):
        validate_workshop_definition({**WORKSHOP, "filters": [{"field": "object_id", "op": "regex", "values": []}]})


PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",))


def seeded_projection(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED",
                 SourceRecord("src-a", "SYSTEM", "TESTSYS", "x", "op", "CIVDEF-AUTH", {}, BASE_MARKING,
                              "ACTIVE", "", T0), recorded_time=T0, actor="fixture")

    def add(object_id, object_type, marking=BASE_MARKING, geometry=None, attributes=None, hours=0.0):
        store.append("OBJECT_VERSION_APPENDED",
                     ObjectVersion(object_id, 1, object_type, "ACTIVE", (object_id,), (), t(hours), None,
                                   t(hours), "HOUR", t(max(hours, 0.0)), geometry, attributes or {}, {},
                                   "REPORTED", marking, PROV),
                     recorded_time=t(max(hours, 0.0)), actor="fixture")

    add("infra-A", "INFRASTRUCTURE", geometry=Geometry("POINT", (-30.1, 45.2)), attributes={"status": "OPERATIONAL"})
    add("stock-1", "RESOURCE_STOCK", attributes={"quantity": 100.0}, hours=-40.0)
    add("obs-secret", "OBSERVATION", marking=RESTRICTED_MARKING, geometry=Geometry("POINT", (-30.05, 45.12)),
        attributes={"reported_status": "DEGRADED"})
    return store, Projection(store, snapshot_time=t(4.0), staleness_hours={"RESOURCE_STOCK": 24.0})


def test_workbench_render_and_access(tmp_path):
    store, projection = seeded_projection(tmp_path)
    definition = validate_workshop_definition(WORKSHOP)
    renderer = WorkbenchRenderer(projection)
    high = renderer.render(definition, HIGH_CONTEXT)
    low = renderer.render(definition, LOW_CONTEXT)
    assert high["counts"]["objects"] == 3 and low["counts"]["objects"] == 2
    stock_table = next(t for t in low["tables"] if t["table_id"] == "stock")
    assert stock_table["rows"][0]["fresh"] == "STALE"
    high_features = high["map_layers"][0]["feature_collection"]["features"]
    low_features = low["map_layers"][0]["feature_collection"]["features"]
    assert {f["properties"]["object_id"] for f in high_features} == {"infra-A", "obs-secret"}
    assert {f["properties"]["object_id"] for f in low_features} == {"infra-A"}
    restricted_definition = validate_workshop_definition({**WORKSHOP, "access": {"min_role": "ANALYST"}})
    with pytest.raises(WorkbenchAccessError):
        renderer.render(restricted_definition, LOW_CONTEXT)


def test_cop_html_self_contained_and_leak_free(tmp_path):
    store, projection = seeded_projection(tmp_path)
    definition = validate_workshop_definition(WORKSHOP)
    renderer = WorkbenchRenderer(projection)
    high_html = render_cop_html(renderer.render(definition, HIGH_CONTEXT), title="Test COP")
    low_html = render_cop_html(renderer.render(definition, LOW_CONTEXT), title="Test COP")
    assert "obs-secret" in high_html and "obs-secret" not in low_html
    assert "RESEARCH SHADOW" in low_html
    assert not re.search(r'(src|href)\s*=\s*["\']https?://', high_html)  # nothing loaded off the network
    assert "[REPORTED" in high_html  # the state is written out, not shown by colour alone
    assert '<html lang="en">' in low_html and "<main>" in low_html
    assert '<th scope="col">' in low_html and "<caption>" in low_html
    assert 'aria-labelledby="map-title map-description"' in low_html
    assert '<desc id="map-description">' in low_html


def test_cop_right_edge_labels_are_end_anchored(tmp_path):
    _, projection = seeded_projection(tmp_path)
    view = WorkbenchRenderer(projection).render(validate_workshop_definition(WORKSHOP), HIGH_CONTEXT)
    # Put one point on the right edge of the extent.
    features = view["map_layers"][0]["feature_collection"]["features"]
    features.append({
        "type": "Feature", "geometry": {"type": "Point", "coordinates": [-20.0, 45.2]},
        "properties": {"object_id": "right", "label": "Right edge label", "epistemic_state": "REPORTED",
                       "symbol": "square", "freshness": "CURRENT", "quality_state": "KNOWN",
                       "uncertainty_m": None},
    })
    rendered = render_cop_html(view, title="Edge")
    assert 'text-anchor="end">Right edge label' in rendered


def test_no_scenario_constants_in_core():
    core = Path("curunir_operational")
    scenario_tokens = ("BR-7", "ALDEN", "BRUSKA", "RELIEF-101", "Vessia", "SUB-4", "CONV-A",
                       "CORRIDOR-OPS", "SENSITIVE-INFRA", "CIVDEF")
    for path in core.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in scenario_tokens:
            assert token not in text, f"scenario constant {token!r} leaked into core module {path.name}"


def test_situation_report_language_and_determinism(tmp_path):
    store, projection = seeded_projection(tmp_path)
    high_report = build_situation_report(store, projection, HIGH_CONTEXT, operational_context="test corridor")
    low_report = build_situation_report(store, projection, LOW_CONTEXT, operational_context="test corridor")
    again = build_situation_report(store, projection, HIGH_CONTEXT, operational_context="test corridor")
    assert high_report["integrity_hash"] == again["integrity_hash"]
    assert high_report["integrity_hash"] != low_report["integrity_hash"]
    markdown = render_markdown(high_report)
    text = render_text(high_report)
    assert "**REPORTED**" in markdown
    assert "[REPORTED]" in text
    assert "STALE" in text
    assert "obs-secret" not in render_text(low_report)
    assert max(len(line) for line in text.splitlines()) <= 72
    assert "not independent corroboration" in markdown or "Dependent" in markdown
