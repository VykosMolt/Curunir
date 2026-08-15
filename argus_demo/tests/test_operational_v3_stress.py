from __future__ import annotations

import os

import pytest

from curunir_operational.v3.stress import run_stress

pytestmark = [pytest.mark.no_db, pytest.mark.skipif(
    os.environ.get("CURUNIR_RUN_V3_STRESS") != "1",
    reason="explicit bounded 50k stress execution; run with CURUNIR_RUN_V3_STRESS=1",
)]


def test_50000_events_three_stores_restart_sync_conflict_replay_convergence(tmp_path):
    metrics = run_stress(tmp_path / "stress", total_events=50_000)
    assert metrics["status"] == "PASS"
    assert metrics["completed_origin_events"] == 50_000 and metrics["primary_node_stores"] == 3
    assert metrics["concurrent_or_causally_ambiguous_updates"] >= 1000
    assert metrics["conflict_detection_count"] >= 400
    assert metrics["restart"] and metrics["offline_partition"] and metrics["rejoin"]
    assert metrics["duplicate_delivery"] and metrics["delay_and_partial_bundle"]
    assert metrics["tampering_detected"] and metrics["stale_base"] == "STALE_BASE"
    assert metrics["missing_base"] == "MISSING_BASE"
    assert metrics["schema_mismatch_receipt"] == "PARTIAL"
    assert metrics["revoked_node_receipt"] == "UNAUTHORIZED"
    assert metrics["authorized_convergence"] and metrics["replay_equal"]
    assert metrics["provider_reinvocations"] == 0 and metrics["access_leakage_failures"] == 0
    assert metrics["integrity_and_access_checks_disabled"] is False
