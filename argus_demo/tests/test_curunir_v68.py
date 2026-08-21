from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curunir_workbench.store import WorkbenchStore
from tools.curunir_v68 import (
    MISSION_IDS,
    PILOT_LOG,
    PRIMARY_ACTOR,
    V68Error,
    _append_pilot_event,
    assess_mission,
    create_instrumented_app,
    finalize_mission,
    prepare_mission,
    replay_mission,
    verify_evidence_faithfulness,
    verify_package,
    verify_pilot_log,
)

pytestmark = pytest.mark.no_db


def _token(root: Path, actor_id: str) -> str:
    actors = json.loads((root / "actors.json").read_text(encoding="utf-8"))["actors"]
    return next(item["token"] for item in actors if item["actor_id"] == actor_id)


def test_frozen_mission_set_is_heterogeneous_and_capstone_is_exactly_one():
    package = Path(__file__).resolve().parents[1]
    missions = json.loads((package / "CURUNIR_V6_8_MISSIONS.json").read_text())["missions"]
    assert tuple(item["mission_id"] for item in missions) == MISSION_IDS
    assert sum(item["kind"] == "CONTINUOUS_CAPSTONE" for item in missions) == 1
    assert len({tuple(item["permitted_sources"]) for item in missions}) == 3


@pytest.mark.parametrize("mission_id", MISSION_IDS[1:])
def test_deterministic_mission_preparation_uses_valid_chain_and_refuses_overwrite(
        tmp_path: Path, mission_id: str):
    root = tmp_path / mission_id
    prepared = prepare_mission(mission_id, root)
    assert prepared["status"] == "READY_FOR_GENUINE_HUMAN_PILOT"
    assert prepared["human_participation_recorded"] is False
    assert prepared["preparation"]["chain"]["valid"] is True
    assert WorkbenchStore(root / "store").verify_chain()["valid"] is True
    with pytest.raises(V68Error, match="refusing overwrite"):
        prepare_mission(mission_id, root)


def test_m2_fixture_carries_real_correction_and_hash_verified_replay(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    store = WorkbenchStore(root / "store")
    changes = store.records_of("semantic_change")
    assert any(item["attribute"] == "affected_facility"
               and item["prior_value"] == "Bridge N-4"
               and item["current_value"] == "Bridge N-9"
               and item["change_class"] == "SOURCE_CORRECTION"
               for item in changes)
    replay = replay_mission(root, tmp_path / "replay")
    assert replay["status"] == "PASS"
    assert replay["network_used"] is False
    assert replay["original_projection_fingerprints"] \
        == replay["replay_projection_fingerprints"]


def test_m3_public_projection_contains_no_restricted_identifier(tmp_path: Path):
    root = tmp_path / "m3"
    prepare_mission("M3_RELIEF_COLLABORATION", root)
    report = verify_evidence_faithfulness(root)
    assert report["restricted_projection_check"] == {
        "applicable": True,
        "leaked_ids": [],
    }
    assert [item["code"] for item in report["findings"]] == ["FINAL_ARTIFACT_ABSENT"]


def test_pilot_log_is_hash_chained_and_tamper_evident(tmp_path: Path):
    path = tmp_path / PILOT_LOG
    _append_pilot_event(path, {
        "event_kind": "SESSION_STARTED", "event_time": "2026-08-21T12:00:00+00:00",
        "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": PRIMARY_ACTOR,
        "actor_kind": "HUMAN", "participant_role": "PRIMARY_OPERATOR", "note": "",
    })
    _append_pilot_event(path, {
        "event_kind": "SESSION_ENDED", "event_time": "2026-08-21T12:01:00+00:00",
        "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": PRIMARY_ACTOR,
        "actor_kind": "HUMAN", "participant_role": "PRIMARY_OPERATOR", "note": "",
    })
    assert verify_pilot_log(path)["valid"] is True
    body = path.read_bytes().replace(b"SESSION_ENDED", b"SESSION_FAKED", 1)
    path.write_bytes(body)
    assert verify_pilot_log(path)["valid"] is False


def test_instrumented_app_records_bounded_actions_without_bearer(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    token = _token(root, PRIMARY_ACTOR)
    client = TestClient(create_instrumented_app(root))
    response = client.post(
        "/v68/pilot/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"participant_role": "PRIMARY_OPERATOR", "note": "begin"},
    )
    assert response.status_code == 200
    assert client.get(
        "/v68/pilot/brief",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200
    assert verify_pilot_log(root / PILOT_LOG)["valid"] is True
    raw = (root / PILOT_LOG).read_text(encoding="utf-8")
    assert token not in raw
    assert "authorization" not in raw.lower()
    assert "PRIMARY_OPERATOR" in raw


def test_prehuman_state_is_not_achieved_while_replay_remains_honest(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    replay = replay_mission(root, tmp_path / "replay")
    faithfulness = verify_evidence_faithfulness(root)
    result = assess_mission(root, faithfulness, replay)
    assert replay["status"] == "PASS"
    assert result["status"] == "NOT_ACHIEVED"
    assert result["human_participation_recorded"] is False
    codes = {item["code"] for item in result["findings"]}
    assert {"PILOT_LOG_INVALID", "PRIMARY_STORE_ACTION_ABSENT",
            "APPROVED_FINAL_ARTIFACT_ABSENT"}.issubset(codes)


def test_failed_pilot_package_is_retained_without_credentials_or_waiver(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    package = finalize_mission(root)
    assert package["status"] == "NOT_ACHIEVED"
    assert not (root / "artifacts" / "actors.json").exists()
    result = json.loads((root / "artifacts" / "mission_result.json").read_text())
    assert result["manual_waiver"] is False
    assert verify_package(root)["status"] == "PASS"
    with pytest.raises(V68Error, match="refusing to overwrite"):
        finalize_mission(root)
