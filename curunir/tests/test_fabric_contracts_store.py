"""Fabric contracts and store: validation invariants and durable replay."""
from __future__ import annotations

import pytest

from curunir_fabric import ABSENCE_SEMANTICS
from curunir_fabric.contracts import (ChangeObservation, ExecutionRecord, ManifestationRecord,
                                      WatchDefinition)
from curunir_fabric.store import FabricStore
from curunir_operational.access import Marking
from curunir_operational.store import MissionDataStore, StoreError

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
MARK = Marking(owning_authority="test-fabric", releasability=("PUBLIC",))
SHA = "a" * 64


def _execution(**overrides):
    base = dict(
        execution_id="exec-1", plan_id="plan-1", query_id="q-1", source_id="wikidata",
        connector_id="wikidata-v1", connector_version="1.0", operation="SEARCH",
        outcome="EXECUTED_EMPTY", result_count=0, request_url="https://example",
        http_status=200, policy_decision="ELIGIBLE_PUBLIC_API", error_class=None,
        error_detail="", manifestation_ids=(), started_time=NOW, completed_time=NOW,
        absence_semantics=ABSENCE_SEMANTICS, marking=MARK,
    )
    base.update(overrides)
    return ExecutionRecord(**base)


def test_execution_absence_semantics_is_mandatory():
    with pytest.raises(ValueError, match="absence semantics"):
        _execution(absence_semantics="WHO_KNOWS")


def test_executed_empty_requires_zero_results_and_results_require_count():
    with pytest.raises(ValueError):
        _execution(outcome="EXECUTED_EMPTY", result_count=3)
    with pytest.raises(ValueError):
        _execution(outcome="EXECUTED_WITH_RESULTS", result_count=0)
    _execution(outcome="EXECUTED_WITH_RESULTS", result_count=2)  # valid


def _manifestation(**overrides):
    base = dict(
        manifestation_id="man-1", source_id="wayback", connector_id="wayback-machine-v1",
        connector_version="1.0", native_id="20240301000000/https://example.com/",
        request_url="https://web.archive.org/...", final_url="https://web.archive.org/...",
        content_sha256=SHA, content_store_path="sha256/aa/bb/x", media_type="text/html",
        temporal_status="HISTORICAL", source_time=None,
        archive_capture_time="2024-03-01T00:00:00+00:00", retrieval_time=NOW,
        http_status=200, redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id="ret-1", custody_ingestion_id="ing-1", source_object_id="so-1",
        execution_id="exec-1", prior_manifestation_id=None, marking=MARK,
    )
    base.update(overrides)
    return ManifestationRecord(**base)


def test_historical_manifestation_requires_capture_time():
    with pytest.raises(ValueError, match="archive capture time"):
        _manifestation(archive_capture_time=None)
    _manifestation(temporal_status="LIVE", archive_capture_time=None)  # valid


def test_watch_cadence_floor_and_change_evidence():
    with pytest.raises(ValueError, match="cadence"):
        WatchDefinition(watch_id="w1", need_id="n1", target_kind="URL", target_ref="https://x",
                        source_id="live-web", operation="FETCH", query_value="https://x",
                        cadence_seconds=5, active=True, blind_spots=(),
                        created_by="t", created_time=NOW, marking=MARK)
    with pytest.raises(ValueError, match="evidence"):
        ChangeObservation(change_id="c1", watch_id="w1", run_id="r1",
                          change_type="CONTENT_CHANGED", detail="", prior_ref="", current_ref="",
                          evidence_manifestation_ids=(), observed_time=NOW, marking=MARK)


def test_fabric_store_accepts_fabric_and_mission_events(tmp_path):
    store = FabricStore.create(tmp_path / "store", "fabric-test", NOW)
    store.append("FABRIC_EXECUTION_RECORDED", _execution(), recorded_time=NOW, actor="t")
    store.append("FABRIC_MANIFESTATION_RECORDED", _manifestation(), recorded_time=NOW, actor="t")
    # mission event types still work on the same chain
    from curunir_operational.contracts import InformationRequirement
    requirement = InformationRequirement(
        requirement_id="req-1", mission_context="m", question="q?", affected_ids=(),
        priority="MEDIUM", rationale="r", required_evidence_type="ANY", owning_role="ANALYST",
        created_time=NOW, due_time=None, status="OPEN", closure_criteria="c", marking=MARK)
    store.append("REQUIREMENT_RECORDED", requirement, recorded_time=NOW, actor="t")
    assert store.verify_chain()["valid"]
    assert len(store.records_of("fabric_execution")) == 1
    assert len(store.records_of("information_requirement")) == 1


def test_fabric_events_replay_after_reopen(tmp_path):
    root = tmp_path / "store"
    store = FabricStore.create(root, "fabric-test", NOW)
    store.append("FABRIC_EXECUTION_RECORDED", _execution(), recorded_time=NOW, actor="t")
    head = store.head()["head_hash"]
    reopened = FabricStore(root)
    assert reopened.head()["head_hash"] == head
    assert reopened.records_of("fabric_execution")[0]["outcome"] == "EXECUTED_EMPTY"
    assert reopened.verify_chain()["valid"]


def test_plain_mission_store_still_rejects_fabric_events(tmp_path):
    store = MissionDataStore.create(tmp_path / "plain", "plain-test", NOW)
    with pytest.raises(StoreError, match="unknown event type"):
        store.append("FABRIC_EXECUTION_RECORDED", _execution(), recorded_time=NOW, actor="t")


def test_latest_by_id_supersedes_on_replay(tmp_path):
    store = FabricStore.create(tmp_path / "store", "fabric-test", NOW)
    active = WatchDefinition(watch_id="w1", need_id="n1", target_kind="URL",
                             target_ref="https://x", source_id="live-web", operation="FETCH",
                             query_value="https://x", cadence_seconds=3600, active=True,
                             blind_spots=(), created_by="t", created_time=NOW, marking=MARK)
    retired = WatchDefinition(watch_id="w1", need_id="n1", target_kind="URL",
                              target_ref="https://x", source_id="live-web", operation="FETCH",
                              query_value="https://x", cadence_seconds=3600, active=False,
                              blind_spots=(), created_by="t",
                              created_time="2026-08-15T13:00:00+00:00", marking=MARK)
    store.append("FABRIC_WATCH_RECORDED", active, recorded_time=NOW, actor="t")
    store.append("FABRIC_WATCH_RECORDED", retired,
                 recorded_time="2026-08-15T13:00:00+00:00", actor="t")
    latest = store.latest_by_id("fabric_watch", "watch_id")
    assert latest["w1"]["active"] is False
    # both events remain in the log — nothing was rewritten
    assert len(store.records_of("fabric_watch")) == 2
