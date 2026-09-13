"""Hardening checks: every kind of chain tampering is caught, a torn log line
fails clearly, valid time and knowledge time stay independent, views expose a
state token instead of a sequence number, and the CLI behaves."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from curunir_operational.canonical import canonical_line
from curunir_operational.contracts import (EvidenceRef, ObjectVersion, ProvenanceSummary, SourceRecord)
from curunir_operational.explain import explain
from curunir_operational.projection import Projection
from curunir_operational.store import MissionDataStore, StoreError
from curunir_operational.workbench import WorkbenchRenderer, render_cop_html, validate_workshop_definition

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, RESTRICTED_MARKING, T0,
                                 fake_sha, make_store, t)

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parent.parent

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",),
                         transformation_ids=("tf-1",))


def version(object_id, number, hours, status, recorded=None, **kw):
    return ObjectVersion(object_id=object_id, version=number, object_type=kw.pop("object_type", "RESOURCE_STOCK"),
                         lifecycle="ACTIVE", labels=(object_id,), external_refs=(), valid_from=t(hours),
                         valid_to=None, source_time=t(hours), time_precision="HOUR",
                         recorded_time=recorded or t(max(hours, 0.0)), geometry=kw.pop("geometry", None),
                         attributes={"status": status, **kw.pop("attributes", {})}, quality=kw.pop("quality", {}),
                         epistemic_state=kw.pop("epistemic_state", "REPORTED"),
                         marking=kw.pop("marking", BASE_MARKING), provenance=kw.pop("provenance", PROV), **kw)


# The hash chain catches a mutated record

def test_chain_detects_every_direct_field_mutation(tmp_path):
    store = make_store(tmp_path)
    store.append("SOURCE_REGISTERED",
                 SourceRecord("src-a", "SYSTEM", "TESTSYS", "x", "op", "AUTH-1", {}, BASE_MARKING, "ACTIVE", "", T0),
                 recorded_time=T0, actor="fixture")
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 1, 0.0, "100l"), recorded_time=t(0), actor="pipe")
    assert store.verify_chain()["valid"]
    original = (tmp_path / "store" / "events.jsonl").read_text().splitlines()

    mutations = [
        ("record attribute", lambda e: e["record"]["attributes"].__setitem__("status", "999l")),
        ("event timestamp", lambda e: e.__setitem__("recorded_time", t(5))),
        ("actor", lambda e: e.__setitem__("actor", "impostor")),
        ("provenance identifier", lambda e: e["record"]["provenance"]["source_ids"].__setitem__(0, "src-evil")),
        ("access marking", lambda e: e["record"]["marking"].__setitem__("compartments", ["INJECTED"])),
        ("record type", lambda e: e["record"].__setitem__("record_type", "decision")),
    ]
    for label, mutate in mutations:
        event = json.loads(original[1])
        mutate(event)
        tampered = [original[0], canonical_line(event)]
        (tmp_path / "store" / "events.jsonl").write_text("\n".join(tampered) + "\n")
        # A tampered chain refuses to open at all.
        with pytest.raises(StoreError, match="hash chain broken"):
            MissionDataStore(tmp_path / "store")
    (tmp_path / "store" / "events.jsonl").write_text("\n".join(original) + "\n")
    assert MissionDataStore(tmp_path / "store").verify_chain()["valid"]


def test_chain_detects_decision_and_relationship_mutation(tmp_path):
    # A small store with the record shapes the mutations target.
    from curunir_operational.contracts import DecisionRecord, RelationshipVersion
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", version("a", 1, 0.0, "x"), recorded_time=t(0), actor="p")
    store.append("OBJECT_VERSION_APPENDED", version("b", 1, 0.0, "x"), recorded_time=t(0), actor="p")
    store.append("RELATIONSHIP_VERSION_APPENDED",
                 RelationshipVersion("rel-1", 1, "DEPENDS_ON", "a", "b", t(0), None, t(0), ("ing-1",),
                                     "MAPPING", "UNKNOWN", "ACTIVE", BASE_MARKING, PROV),
                 recorded_time=t(0), actor="p")
    store.append("DECISION_RECORDED",
                 DecisionRecord("dec-1", "rec-1", "analyst-vale", "SUPERVISOR", "ACCEPTED", "", "why",
                                fake_sha("snapshot"), t(1), BASE_MARKING),
                 recorded_time=t(1), actor="analyst-vale")
    lines = (tmp_path / "store" / "events.jsonl").read_text().splitlines()
    for index, field_path in ((2, ("record", "target_object_id", "c")),
                              (3, ("record", "evidence_snapshot_hash", fake_sha("forged")))):
        event = json.loads(lines[index])
        event[field_path[0]][field_path[1]] = field_path[2]
        tampered = list(lines)
        tampered[index] = canonical_line(event)
        (tmp_path / "store" / "events.jsonl").write_text("\n".join(tampered) + "\n")
        with pytest.raises(StoreError, match="hash chain broken"):
            MissionDataStore(tmp_path / "store")
    (tmp_path / "store" / "events.jsonl").write_text("\n".join(lines) + "\n")


# A late arrival on its own

def test_late_arrival_alone_does_not_displace_current(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 1, 6.0, "900l"), recorded_time=t(6), actor="p")
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 2, 3.0, "1000l", recorded=t(8)),
                 recorded_time=t(8), actor="p")  # recorded last, but valid earlier
    projection = Projection(store, snapshot_time=t(9))
    assert projection.objects["stock-1"]["current"]["attributes"]["status"] == "900l"
    assert projection.objects["stock-1"]["history_count"] == 2


# A torn final line

def test_torn_final_line_fails_clearly(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 1, 0.0, "100l"), recorded_time=t(0), actor="p")
    with (tmp_path / "store" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "event_type": "OBJECT_VER')  # as if the process died mid-append
    with pytest.raises(StoreError, match="torn line"):
        MissionDataStore(tmp_path / "store")


# Valid time and knowledge time

def test_valid_at_and_knowledge_as_of_are_independent(tmp_path):
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 1, 0.0, "1200l"), recorded_time=t(0), actor="p")
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 2, 6.0, "900l"), recorded_time=t(6), actor="p")
    seq_before_correction = store.head()["event_count"]
    store.append("OBJECT_VERSION_APPENDED",
                 version("stock-1", 3, 6.0, "950l", recorded=t(9), epistemic_state="CORRECTED",
                         correction_of="stock-1@v2", correction_reason="unit error"),
                 recorded_time=t(9), actor="p")
    # What was true in the world at t(3)?
    valid_projection = Projection(store, snapshot_time=t(9), valid_at=t(3.0))
    assert valid_projection.objects["stock-1"]["current"]["version"] == 1
    # What did we believe just before the correction arrived?
    knowledge = Projection(store, as_of_seq=seq_before_correction, snapshot_time=t(9))
    assert knowledge.objects["stock-1"]["current"]["version"] == 2
    assert knowledge.objects["stock-1"]["current"]["attributes"]["status"] == "900l"
    # Both questions at once.
    combined = Projection(store, as_of_seq=seq_before_correction, snapshot_time=t(9), valid_at=t(3.0))
    assert combined.objects["stock-1"]["current"]["version"] == 1
    # A later correction must not show up in an earlier knowledge view.
    assert not knowledge.objects["stock-1"]["corrected_version_ids"]
    # An object with no validity that early is absent, not empty.
    early = Projection(store, snapshot_time=t(9), valid_at=t(-5.0))
    assert "stock-1" not in early.objects
    assert early.view(HIGH_CONTEXT)["counts"]["objects_total"] == 0


# Provenance round-trips

def test_provenance_roundtrip_variants(tmp_path):
    store = make_store(tmp_path)
    evidence = EvidenceRef("argus-src-1", "doc-1", fake_sha("doc"), "asrt-1", "basis-1",
                           "IDENTITY_PROVISIONAL", "REPUTABLE_SECONDARY_REPORT", "UNRESOLVED",
                           "SINGLE_BASIS", "UNREVIEWED", "APPROXIMATE", "evgroup-1", ("COMMON_ORIGIN_REVIEW",))
    dependent = EvidenceRef("argus-src-2", "doc-2", fake_sha("doc2"), "asrt-2", "basis-1",
                            "IDENTITY_UNKNOWN", "AUTHORITY_NOT_ASSESSED", "UNRESOLVED",
                            "UNKNOWN_BASIS", "UNREVIEWED", "UNKNOWN", "evgroup-1", ())
    cases = [
        version("obs-direct", 1, 0.0, "OPERATIONAL"),
        version("obs-evidence", 1, 1.0, "DAMAGED",
                provenance=ProvenanceSummary(mode="EVIDENTIARY", source_ids=("src-argus",),
                                             ingestion_ids=("ing-e",), evidence=(evidence,)),
                quality={"mapping_confidence": "UNKNOWN"}, epistemic_state="EXTRACTED",
                object_type="OBSERVATION"),
        version("obs-dependent", 1, 1.5, "DAMAGED",
                provenance=ProvenanceSummary(mode="EVIDENTIARY", source_ids=("src-argus",),
                                             ingestion_ids=("ing-e2",), evidence=(dependent,)),
                epistemic_state="EXTRACTED", object_type="OBSERVATION"),
        version("obs-corrected", 1, 2.0, "DAMAGED", object_type="OBSERVATION"),
        version("obs-restricted", 1, 2.5, "DEGRADED", marking=RESTRICTED_MARKING, object_type="OBSERVATION"),
    ]
    for record in cases:
        store.append("OBJECT_VERSION_APPENDED", record, recorded_time=t(3), actor="p")
    store.append("OBJECT_VERSION_APPENDED",
                 version("obs-corrected", 2, 2.0, "PARTIALLY_DAMAGED", recorded=t(4),
                         epistemic_state="CORRECTED", correction_of="obs-corrected@v1",
                         correction_reason="official correction", object_type="OBSERVATION"),
                 recorded_time=t(4), actor="p")
    store.export_to(tmp_path / "export")
    imported = MissionDataStore.import_from(tmp_path / "export", tmp_path / "fresh")
    projection = Projection(imported, snapshot_time=t(5))
    ev = projection.objects["obs-evidence"]["current"]["provenance"]["evidence"][0]
    assert ev["evidence_basis_id"] == "basis-1" and ev["mapping_status"] == "APPROXIMATE"
    assert ev["identity_status"] == "IDENTITY_PROVISIONAL" and ev["review_state"] == "UNREVIEWED"
    dep = projection.objects["obs-dependent"]["current"]["provenance"]["evidence"][0]
    assert dep["independence_status"] == "UNRESOLVED" and dep["claim_basis_status"] == "UNKNOWN_BASIS"
    assert projection.dependence_groups["evgroup-1"] == ["obs-dependent", "obs-evidence"]
    corrected = projection.objects["obs-corrected"]
    assert corrected["current"]["epistemic_state"] == "CORRECTED"
    assert corrected["corrected_version_ids"] == ["obs-corrected@v1"]
    assert projection.objects["obs-restricted"]["current"]["marking"]["compartments"] == ["SENSITIVE-INFRA"]
    explained = explain(projection, "obs-evidence", HIGH_CONTEXT)
    assert explained["evidence"][0]["assertion_id"] == "asrt-1"
    assert explain(projection, "obs-restricted", LOW_CONTEXT) == explain(projection, "no-such", LOW_CONTEXT)


# State token instead of sequence number

def test_no_sequence_side_channel_in_views_reports_bundles(tmp_path):
    from curunir_operational.sitrep import build_situation_report, render_text
    from curunir_operational.sovereignty import build_pace_bundle
    store = make_store(tmp_path)
    store.append("OBJECT_VERSION_APPENDED", version("stock-1", 1, 0.0, "100l"), recorded_time=t(0), actor="p")
    store.append("OBJECT_VERSION_APPENDED",
                 version("obs-secret", 1, 1.0, "DEGRADED", marking=RESTRICTED_MARKING, object_type="OBSERVATION"),
                 recorded_time=t(1), actor="p")
    projection = Projection(store, snapshot_time=t(2))
    low_view = projection.view(LOW_CONTEXT)
    assert "as_of_seq" not in json.dumps(low_view)
    assert re.fullmatch(r"[0-9a-f]{16}", low_view["meta"]["state_token"])
    report = build_situation_report(store, projection, LOW_CONTEXT, operational_context="test")
    assert "as_of_seq" not in json.dumps(report)
    assert "STATE" in render_text(report) and "SEQ " not in render_text(report)
    manifest = build_pace_bundle(store, projection, LOW_CONTEXT, tmp_path / "pace", operational_context="test")
    assert "as_of_seq" not in json.dumps(manifest) and "since_seq" not in json.dumps(manifest)
    changes = projection.changes_since(1, LOW_CONTEXT, store)
    assert "since_seq" not in json.dumps(changes) and re.fullmatch(r"[0-9a-f]{16}", changes["until_state"])


# COP page structure

def test_cop_structure_responsive_and_collision_free(tmp_path):
    store = make_store(tmp_path)
    from curunir_operational.geometry import Geometry
    # Two points almost on top of each other; their labels must not overlap.
    store.append("OBJECT_VERSION_APPENDED",
                 version("infra-A", 1, 0.0, "OPERATIONAL", object_type="INFRASTRUCTURE",
                         geometry=Geometry("POINT", (-30.100, 45.200))), recorded_time=t(0), actor="p")
    store.append("OBJECT_VERSION_APPENDED",
                 version("obs-near", 1, 0.5, "OBSTRUCTED", object_type="OBSERVATION",
                         geometry=Geometry("POINT", (-30.102, 45.202))), recorded_time=t(1), actor="p")
    definition = validate_workshop_definition({
        "workshop_id": "w", "version": "1.0", "purpose": "p",
        "object_types": ["INFRASTRUCTURE", "OBSERVATION"], "tables": [],
        "map_layers": [{"layer_id": "m", "object_types": ["INFRASTRUCTURE", "OBSERVATION"],
                        "geometry_kinds": ["POINT"]}],
        "access": {"min_role": "OBSERVER"}})
    html_out = render_cop_html(WorkbenchRenderer(Projection(store, snapshot_time=t(2)))
                               .render(definition, HIGH_CONTEXT), title="T")
    assert '<meta name="viewport"' in html_out
    assert "height:auto" in html_out  # the map scales with the page
    assert "<title>" in html_out and 'aria-label' in html_out
    anchors = [(float(m.group(1)), float(m.group(2)))
               for m in re.finditer(r'<text x="([0-9.]+)" y="(-?[0-9.]+)" class="lbl"', html_out)]
    assert len(anchors) == 2
    (x1, y1), (x2, y2) = anchors
    assert abs(x1 - x2) >= 110 or abs(y1 - y2) >= 13  # pushed apart


# The CLI

def cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "curunir_operational.cli", *args],
                          capture_output=True, text=True, cwd=cwd)


def test_cli_battery(tmp_path):
    context_file = tmp_path / "ctx.json"
    context_file.write_text(json.dumps({"context_id": "c", "actor_id": "a", "actor_kind": "HUMAN",
                                        "roles": ["SUPERVISOR"], "compartments": [],
                                        "releasability": ["CORRIDOR-OPS"], "organisation": "CIVDEF-AUTH"}))
    helps = cli("--help")
    assert helps.returncode == 0
    for command in ("init", "ingest", "project", "workbench", "explain", "alerts", "recommendations",
                    "decide", "report", "export", "import", "replay", "pace", "verify", "sovereignty"):
        assert command in helps.stdout, f"CLI help missing {command}"
    store_dir = tmp_path / "store"
    made = cli("init", "--store", str(store_dir), "--store-id", "cli-test", "--time", T0)
    assert made.returncode == 0 and "head_hash" in made.stdout
    assert cli("init", "--store", str(store_dir), "--store-id", "cli-test", "--time", T0).returncode == 2  # re-init would destroy the store
    missing = cli("project", "--store", str(tmp_path / "nope"), "--context", str(context_file))
    assert missing.returncode == 2 and "error:" in missing.stderr
    project = cli("project", "--store", str(store_dir), "--context", str(context_file))
    assert project.returncode == 0 and "projection_hash" in project.stdout
    verify = cli("verify", "--store", str(store_dir))
    assert verify.returncode == 0 and '"valid": true' in verify.stdout
    unknown_workshop = cli("workbench", "--store", str(store_dir), "--context", str(context_file),
                           "--workshop", "nope")
    assert unknown_workshop.returncode == 2
    bad_decide = cli("decide", "--store", str(store_dir), "--context", str(context_file),
                     "--recommendation", "rec-x", "--state", "ACCEPTED", "--rationale", "r",
                     "--marking", str(context_file), "--time", t(1))
    assert bad_decide.returncode == 2 and "error:" in bad_decide.stderr
    bad_pace = cli("verify-pace", "--bundle", str(tmp_path / "nothing"))
    assert bad_pace.returncode == 1
