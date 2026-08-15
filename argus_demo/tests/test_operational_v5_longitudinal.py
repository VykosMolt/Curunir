from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.v4.models import sha256
from curunir_operational.v5 import longitudinal
from curunir_operational.v5.longitudinal import (
    controlled_mechanics_scenario, cross_case_memory, derive_watch_deltas, execute_live_watch,
    standing_definitions,
)
from curunir_operational.v5.models import SourceVersion
from curunir_operational.v5.campaign import revalidate_persisted_stage2

pytestmark = pytest.mark.no_db

V4 = Path("artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722")


def test_two_standing_investigations_are_cli_driven(tmp_path):
    values = standing_definitions(V4, tmp_path)
    assert {x["standing_id"] for x in values} == {"EUROPEAN_SOVEREIGN_DEFENCE_AI_WATCH_V5",
        "IBERIAN_BLACKOUT_OFFICIAL_RECORD_WATCH_V5"}
    assert all(not x["recapture_policy"]["scheduler_active"] for x in values)


def test_source_version_cannot_be_mutable():
    with pytest.raises(ValueError, match="immutable"):
        SourceVersion("v", "s", None, "r", None, sha256({}), None, "PUBLIC", "UNAVAILABLE",
            False, False, None, "2026-07-22T00:00:00+00:00", "run", False)


def test_cosmetic_change_is_not_material():
    assert longitudinal._semantic_text(b"<nav>A</nav><p>Evidence text</p>") == \
           longitudinal._semantic_text(b"<nav>B</nav><p>Evidence   text</p>")


def test_recapture_records_start_before_transport_and_no_change(monkeypatch, tmp_path):
    body = b"<html><p>Official text.</p></html>"; digest = sha256(body)
    capture = tmp_path / "capture"; path = capture / "custody/content/sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True); path.write_bytes(body)
    source = {"source_object_id": "source-1", "content_hash": digest, "final_urls": ["https://example.invalid/x"],
        "requested_urls": ["https://example.invalid/x"], "publication_time": None}
    definition = {"standing_id": "watch", "watched_urls": ["https://example.invalid/x"]}
    monkeypatch.setattr(longitudinal, "retrieve_public_bytes_v4", lambda **kwargs: {
        "body": body, "status": 200, "final_url": kwargs["url"], "headers": {}, "redirects": (),
        "error": None, "truncated": False})
    result = execute_live_watch(definition=definition, sources=[source], capture_root=capture,
                                output_root=tmp_path / "out")
    events = [json.loads(line) for line in (tmp_path / "out/recapture_events.jsonl").read_text().splitlines()]
    assert [x["event"] for x in events] == ["ATTEMPT_STARTED", "ATTEMPT_COMPLETED"]
    assert result["live_change_result"] == "NO_MATERIAL_CHANGE"


def test_controlled_correction_invalidates_proposal_and_preserves_versions(tmp_path):
    result = controlled_mechanics_scenario(tmp_path)
    deltas = json.loads((tmp_path / "controlled_deltas.json").read_text())
    assert result["old_versions_preserved"] and result["proposal_invalidated"]
    assert any(x["kind"] == "KERNEL_PROPOSAL" and x["state"] == "INVALIDATED" for x in deltas)


def test_no_change_produces_no_alert(tmp_path):
    definition = {"standing_id": "watch"}
    versions = [{"source_version_id": "v2", "source_object_id": "s", "previous_version_id": "v1",
        "semantic_changed": False, "change_state": "NO_CHANGE"}]
    (tmp_path / "update").mkdir(); (tmp_path / "update/source_versions.json").write_text(json.dumps(versions))
    result = derive_watch_deltas(definition, tmp_path / "update", tmp_path / "delta")
    assert result["alerts"] == 0 and result["proposal_change"] == "NO_EFFECT"


def test_material_change_produces_review_alert_not_direct_mutation(tmp_path):
    definition = {"standing_id": "watch"}
    versions = [{"source_version_id": "v2", "source_object_id": "s", "previous_version_id": "v1",
        "semantic_changed": True, "change_state": "CONTENT_CHANGED"}]
    (tmp_path / "update").mkdir(); (tmp_path / "update/source_versions.json").write_text(json.dumps(versions))
    result = derive_watch_deltas(definition, tmp_path / "update", tmp_path / "delta")
    alerts = json.loads((tmp_path / "delta/alerts.json").read_text())
    assert result["alerts"] == 1 and alerts[0]["review_state"] == "HUMAN_REVIEW_REQUIRED"
    assert alerts[0]["requested_action"] == "REPORT_UPDATE_AND_PROPOSAL_REVALIDATION"


def test_cross_case_memory_preserves_applicability(tmp_path):
    result = cross_case_memory(V4, tmp_path)
    assert result["case_specific_applicability_preserved"] and not result["global_truth_graph_created"]
    assert result["access_leakage_findings"] == 0


def test_stage2_post_repair_regression_reuses_capture_without_network(tmp_path):
    stage = tmp_path / "15_longitudinal_architecture"; stage.mkdir()
    overlays = tmp_path / "10_errors_and_repairs/semantic_overlays"; overlays.mkdir(parents=True)
    watch = {"live": {"live_requests": 2, "live_change_result": "NO_MATERIAL_CHANGE"},
             "handoff": {"prior_handoff_preserved": True},
             "proposal": {"status": "HUMAN_REVIEW_REQUIRED"}}
    # The persisted replay figure must now be LABELLED as measured: the
    # regression's canonical_writes_zero check refuses a bare zero, because a
    # bare zero is what the unmeasured emitter used to supply.
    (stage / "stage_2_summary.json").write_text(json.dumps({"watches": {"a": watch, "b": watch},
        "replay": {"network_requests": 0, "provider_reinvocations": 0, "canonical_writes": 0,
                   "canonical_writes_measurement": "MEASURED_DRIVER_LAYER_STATEMENT_OBSERVATION"}}))
    (overlays / "semantic_overlay_report.json").write_text(json.dumps({"verdict": "PASS_HARDENED",
                                                                         "integrity_hash": "h"}))
    result = revalidate_persisted_stage2(tmp_path)
    assert result["all_pass"] and result["additional_network_requests"] == 0
