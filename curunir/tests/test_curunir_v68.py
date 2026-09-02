from __future__ import annotations

import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from curunir_workbench.store import WorkbenchStore
from tools import curunir_v68 as v68
from tools.curunir_v68 import (
    APPROVER_ACTOR,
    CONTRACT_PATH,
    M3_OPERATIONAL_REVIEW_PATHS,
    MISSION_IDS,
    PILOT_LOG,
    PRIMARY_ACTOR,
    PROTOCOL_PATH,
    PUBLIC_ACTOR,
    REPAIR_CONTRACT_PATH,
    V68Error,
    _append_pilot_event,
    _licensed_wrapper_around,
    _measurement,
    _offline_replay_guard,
    _repository_identity,
    _session_analysis,
    _sha256_bytes,
    _supported_sentence_binding,
    _tree_manifest,
    _accepted_authorities,
    _current_authority,
    _validate_frozen_authority_files,
    _v67_report_findings,
    _write_json,
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


def test_repair_contract_freezes_protocol_and_corrected_starting_states():
    qualification = json.loads(CONTRACT_PATH.read_text())
    repair = json.loads(REPAIR_CONTRACT_PATH.read_text())
    missions = json.loads(v68.MISSIONS_PATH.read_text())["missions"]
    assert qualification["contract_state"] \
        == "ACCEPTANCE_FROZEN_BEFORE_REPAIR; CAMPAIGN_ROOT_ADVANCED_BEFORE_HUMAN_PILOT"
    assert qualification["operator_protocol"]["sha256"] \
        == v68._sha256_file(PROTOCOL_PATH)
    assert qualification["base_authority"]["repair_contract"]["sha256"] \
        == v68._sha256_file(REPAIR_CONTRACT_PATH)
    assert repair["reviewed_executable_sha"] \
        == "83df706af0664e6c1ee31a6458f730ff005f3179"  # Curunír id; Saulot 5bb32e92 per CURUNIR_COMMIT_MAP_SAULOT.txt
    assert "baseline live captures" in missions[0]["starting_state"]
    assert "no preparation-authored analytical conclusion" in missions[1]["starting_state"]
    _validate_frozen_authority_files()


def test_repository_identity_refuses_dirty_checkout(monkeypatch: pytest.MonkeyPatch):
    class Result:
        def __init__(self, output: str):
            self.stdout = output

    def fake_run(command, **_kwargs):
        if "rev-parse" in command:
            return Result("a" * 40 + "\n")
        if "status" in command:
            return Result(" M curunir/tools/curunir_v68.py\n")
        raise AssertionError(command)

    monkeypatch.setattr(v68.subprocess, "run", fake_run)
    with pytest.raises(V68Error, match="clean executable checkout"):
        _repository_identity(require_clean=True)


def test_frozen_protocol_hash_rejects_substitution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    replacement = tmp_path / PROTOCOL_PATH.name
    replacement.write_text("substituted protocol\n")
    monkeypatch.setattr(v68, "PROTOCOL_PATH", replacement)
    with pytest.raises(V68Error, match="protocol identity mismatch"):
        _validate_frozen_authority_files()


@pytest.mark.parametrize("mission_id", MISSION_IDS[1:])
def test_deterministic_mission_preparation_uses_valid_chain_and_refuses_overwrite(
        tmp_path: Path, mission_id: str):
    root = tmp_path / mission_id
    prepared = prepare_mission(mission_id, root)
    assert prepared["status"] == "READY_FOR_GENUINE_HUMAN_PILOT"
    assert prepared["human_participation_recorded"] is False
    assert prepared["preparation"]["chain"]["valid"] is True
    assert prepared["executable_identity"] == _repository_identity(require_clean=True)
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
    prepared = json.loads((root / "preparation.json").read_text())["preparation"]
    states = {item["claim_id"]: item for item in store.records_of("semantic_claim_state")}
    derivative = states[prepared["dependent_translation_claim_id"]]
    assert derivative["state"] == "SUPERSEDED"
    assert derivative["superseded_by"] == prepared["current_facility_claim_id"]
    translated_id = prepared["manifestations"][2]
    execution = next(item for item in store.records_of("fabric_execution")
                     if translated_id in item["manifestation_ids"])
    queries = [item for plan in store.records_of("fabric_discovery_plan")
               for item in plan["queries"]]
    query = next(item for item in queries
                 if item["query_id"] == execution["query_id"])
    assert prepared["manifestations"][1] in query["derived_from"]
    assert not store.records_of("analytic_narrative")
    faithfulness = verify_evidence_faithfulness(root)
    result = assess_mission(root, faithfulness, replay)
    codes = {item["code"] for item in result["findings"]}
    assert "FIXTURE_MANIFEST_MISMATCH" not in codes
    assert "M2_DERIVATIVE_STATE_INVALID" not in codes
    assert "FIXTURE_EVIDENCE_NOT_REVIEWED" in codes
    assert "M2_REPORT_CORRECTION_SEMANTICS_INCOMPLETE" in codes


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
    brief = client.get(
        "/v68/pilot/brief",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert brief.status_code == 200
    assert "SOURCE_CORRECTED" in brief.json()["operator_gate_notes"][
        "M2_SOURCE_CORRECTED_REVIEWS"]
    assert verify_pilot_log(root / PILOT_LOG)["valid"] is True
    raw = (root / PILOT_LOG).read_text(encoding="utf-8")
    assert token not in raw
    assert "authorization" not in raw.lower()
    assert "PRIMARY_OPERATOR" in raw


def test_session_pairing_requires_order_and_bounds_measurement(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    path = root / PILOT_LOG
    _append_pilot_event(path, {
        "event_kind": "SESSION_ENDED", "event_time": "2026-08-21T12:00:00+00:00",
        "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": PRIMARY_ACTOR,
        "actor_kind": "HUMAN", "participant_role": "PRIMARY_OPERATOR", "note": "",
    })
    _append_pilot_event(path, {
        "event_kind": "SESSION_STARTED", "event_time": "2026-08-21T12:01:00+00:00",
        "mission_id": "M2_REGULATORY_CORRECTION", "actor_id": PRIMARY_ACTOR,
        "actor_kind": "HUMAN", "participant_role": "PRIMARY_OPERATOR", "note": "",
    })
    analysis = _session_analysis(v68._pilot_events(path))
    assert analysis["valid"] is False
    assert {item["code"] for item in analysis["findings"]} \
        == {"SESSION_END_WITHOUT_START", "SESSION_START_WITHOUT_END"}
    measurement = _measurement(
        root, {"lineage_complete_conclusions": 0,
               "lineage_required_conclusions": 0}, {"status": "PASS"})
    assert measurement["measured_value"]["operator_elapsed_seconds"] == 0
    assert measurement["measured_value"]["evidence_items_inspected"] == 0


def test_control_routes_enforce_actor_role_and_order(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    primary = _token(root, PRIMARY_ACTOR)
    client = TestClient(create_instrumented_app(root))
    headers = {"Authorization": f"Bearer {primary}"}
    assert client.post("/v68/pilot/end", headers=headers,
                       json={"participant_role": "PRIMARY_OPERATOR"}).status_code == 409
    assert client.post("/v68/pilot/start", headers=headers,
                       json={"participant_role": "APPROVER"}).status_code == 403
    assert client.post("/v68/pilot/start", headers=headers,
                       json={"participant_role": "PRIMARY_OPERATOR"}).status_code == 200
    assert client.post("/v68/pilot/start", headers=headers,
                       json={"participant_role": "PRIMARY_OPERATOR"}).status_code == 409
    assert client.get("/v68/pilot/brief", headers=headers).status_code == 200
    assert client.post("/v68/pilot/end", headers=headers,
                       json={"participant_role": "PRIMARY_OPERATOR"}).status_code == 200
    assert _session_analysis(v68._pilot_events(root / PILOT_LOG))["valid"] is True


def test_pilot_log_hashes_evidence_id_instead_of_retaining_it(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    token = _token(root, PRIMARY_ACTOR)
    client = TestClient(create_instrumented_app(root))
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/v68/pilot/start", headers=headers,
                json={"participant_role": "PRIMARY_OPERATOR"})
    manifestation_id = WorkbenchStore(root / "store").records_of(
        "fabric_manifestation")[0]["manifestation_id"]
    assert client.get(f"/api/evidence/{manifestation_id}", headers=headers).status_code == 200
    client.post("/v68/pilot/end", headers=headers,
                json={"participant_role": "PRIMARY_OPERATOR"})
    raw = (root / PILOT_LOG).read_text()
    assert manifestation_id not in raw
    evidence = next(item for item in v68._pilot_events(root / PILOT_LOG)
                    if item.get("category") == "EVIDENCE_OPENED")
    assert evidence["path"] == "/api/evidence/{id}"
    assert evidence["resource_id_sha256"] == _sha256_bytes(manifestation_id.encode())


def test_false_supported_sentence_fails_content_binding(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    client = TestClient(create_instrumented_app(root))
    primary_headers = {"Authorization": f"Bearer {_token(root, PRIMARY_ACTOR)}"}
    approver_headers = {"Authorization": f"Bearer {_token(root, APPROVER_ACTOR)}"}
    claim = next(item for item in WorkbenchStore(root / "store").current_claims().values()
                 if item.get("predicate") == "fixture_status")
    created = client.post("/api/commands/reports", headers=primary_headers, json={
        "title": "Adversarial false quote", "question": "Is the claim supported?",
        "sections": [{"kind": "key_judgments", "title": "Judgment", "sentences": [{
            "text": "The moon is made of green cheese.", "status": "SUPPORTED",
            "basis_refs": [claim["claim_id"]],
        }]}],
    })
    assert created.status_code == 200
    report = created.json()
    submitted = client.post(
        f"/api/commands/reports/{report['report_id']}/submit",
        headers=primary_headers, json={"expected_version": report["version"]})
    assert submitted.status_code == 200
    approved = client.post(
        f"/api/commands/reports/{report['report_id']}/approve",
        headers=approver_headers, json={"expected_version": submitted.json()["version"]})
    assert approved.status_code == 200
    result = verify_evidence_faithfulness(root)
    assert result["status"] == "FAIL"
    assert "SUPPORTED_TEXT_NOT_CONTENT_BOUND" in {
        item["code"] for item in result["findings"]}


class _ClaimProjection:
    def __init__(self, claim: dict):
        self._claim = claim

    def get(self, family: str, ref: str):
        if family == "semantic_claim" and ref == self._claim["claim_id"]:
            return self._claim
        return None

    def family(self, _family: str):
        return []


def test_cited_value_cannot_launder_unrelated_prose():
    claim = {
        "claim_id": "claim-fixture",
        "statement": "fixture_status = NOTIONAL PUBLIC EVIDENCE",
        "object_or_value": "NOTIONAL PUBLIC EVIDENCE",
    }
    projection = _ClaimProjection(claim)
    wrapped = _supported_sentence_binding(projection, {
        "text": "The current status is NOTIONAL PUBLIC EVIDENCE.",
        "basis_refs": ["claim-fixture"],
    })
    laundered = _supported_sentence_binding(projection, {
        "text": "The moon is made of green cheese. NOTIONAL PUBLIC EVIDENCE.",
        "basis_refs": ["claim-fixture"],
    })
    negated = _supported_sentence_binding(projection, {
        "text": "The notice does not report NOTIONAL PUBLIC EVIDENCE.",
        "basis_refs": ["claim-fixture"],
    })
    assert wrapped["content_bound"] is True
    assert laundered["content_bound"] is False
    assert negated["content_bound"] is False
    assert _licensed_wrapper_around(
        "the current affected facility is bridge n-9", "bridge n-9") is True
    assert _licensed_wrapper_around(
        "the moon is made of green cheese bridge n-9", "bridge n-9") is False


def test_required_m3_review_paths_exist_on_existing_workbench(tmp_path: Path):
    root = tmp_path / "m3"
    prepare_mission("M3_RELIEF_COLLABORATION", root)
    client = TestClient(create_instrumented_app(root))
    headers = {"Authorization": f"Bearer {_token(root, PRIMARY_ACTOR)}"}
    assert client.post("/v68/pilot/start", headers=headers,
                       json={"participant_role": "PRIMARY_OPERATOR"}).status_code == 200
    brief = client.get("/v68/pilot/brief", headers=headers)
    assert brief.status_code == 200
    assert "GET /api/overview" in brief.json()["operator_gate_notes"][
        "M3_OPERATIONAL_REVIEW_PATHS"]
    for path in M3_OPERATIONAL_REVIEW_PATHS:
        response = client.get(path, headers=headers)
        assert response.status_code == 200, path
    assert client.get("/api/family/object_version", headers=headers).status_code == 404
    assert client.get("/api/family/recommendation", headers=headers).status_code == 404


def test_every_present_custody_copy_must_match(tmp_path: Path):
    root = tmp_path / "m2"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    store = WorkbenchStore(root / "store")
    manifestation = store.records_of("fabric_manifestation")[0]
    digest = manifestation["content_sha256"]
    custody = root / "custody" / "sha256" / digest[:2] / digest[2:4] / digest
    assert store.put_payload(custody.read_bytes()) == digest
    custody.write_bytes(b"corrupt one of two retained copies")
    result = verify_evidence_faithfulness(root)
    assert "CUSTODY_COPY_HASH_MISMATCH" in {
        item["code"] for item in result["findings"]}


def test_public_actor_is_read_only_and_truncated_hidden_id_is_detected(tmp_path: Path):
    from curunir_operational.access import Marking
    from curunir_workbench.annotations import create_annotation
    from curunir_workbench.auth import ActorRegistry
    from curunir_workbench.projections import MissionProjection

    root = tmp_path / "m3"
    prepare_mission("M3_RELIEF_COLLABORATION", root)
    public_headers = {"Authorization": f"Bearer {_token(root, PUBLIC_ACTOR)}"}
    client = TestClient(create_instrumented_app(root))
    assert client.post("/v68/pilot/start", headers=public_headers,
                       json={"participant_role": "PUBLIC_ACCESS_CHECK"}).status_code == 200
    assert client.post("/api/commands/annotate", headers=public_headers, json={
        "target_kind": "recommendation", "target_id": "anything",
        "text": "write attempt",
    }).status_code == 403
    assert client.post("/v68/pilot/end", headers=public_headers,
                       json={"participant_role": "PUBLIC_ACCESS_CHECK"}).status_code == 200

    store = WorkbenchStore(root / "store")
    context = ActorRegistry(root / "actors.json").context_for_actor(PUBLIC_ACTOR)
    projection = MissionProjection(store, context)
    hidden = next(identifier for identifier in sorted(projection.hidden_ids)
                  if "-" in identifier)
    prefix, suffix = hidden.split("-", 1)
    truncated = f"{prefix}-{suffix[:6]}"
    target = projection.base_view["recommendations"][0]
    create_annotation(
        store, projection, actor="adversarial-direct-writer",
        marking=Marking(owning_authority="M3_RELIEF_COLLABORATION",
                        releasability=("PUBLIC",)),
        now=datetime.now(timezone.utc).isoformat(), target_kind="recommendation",
        target_id=target["recommendation_id"], kind="NOTE",
        text=f"truncated restricted token {truncated}")
    result = verify_evidence_faithfulness(root)
    assert result["restricted_projection_check"]["leaked_ids"]
    assert "RESTRICTED_IDENTIFIER_LEAK" in {
        item["code"] for item in result["findings"]}


def test_replay_external_call_guard_actively_denies_network():
    counters = None
    with pytest.raises(V68Error, match="network access is denied"):
        with _offline_replay_guard() as counters:
            socket.create_connection(("127.0.0.1", 9))
    assert counters is not None
    assert counters["network_attempts_blocked"] == 1
    assert counters["network_calls_completed"] == 0
    with pytest.raises(V68Error, match="network access is denied"):
        with _offline_replay_guard() as counters:
            subprocess.run(["true"], check=False)
    assert counters["network_attempts_blocked"] == 1


def _valid_v67_report(executable_sha: str) -> dict:
    baseline = json.loads(
        (CONTRACT_PATH.parent / "CURUNIR_V6_7_BASELINE_NONPASSING.json").read_text())
    node = baseline["allowed_nonpassing_nodeids"][0]
    outcome = "error" if node in baseline["allowed_error_nodeids"] else "failure"
    failures = int(outcome == "failure")
    errors = int(outcome == "error")
    return {
        "format": "curunir-v6.7-terminal-validation-v1",
        "status": "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS",
        "commit": executable_sha,
        "clean_reconstruction": {
            "status": "RECONSTRUCTION_OK", "commit": executable_sha,
            "kernel_tree_sha256": "4c173df7412952b8318b7838a06ee991638fd9144eee2be36232c64aecfb7906",
            "kernel_python_file_count": 140,
            "requirements_sha256": baseline["harness"]["requirements_sha256"],
            "smoke": {"status": "RECONSTRUCTION_OK"},
        },
        "focused_v67": {"status": "PASSED", "tests": 130, "passed": 130,
                         "skipped": 0, "failed": 0, "errors": 0},
        "curunir_product_planes": {
            "status": "PASSED", "tests": 630, "passed": 624,
            "skipped": 6, "failed": 0, "errors": 0,
            "deselected_accepted_baseline_nodes": [
                "tests/test_operational_v2_integration.py::test_live_vs_synthetic_distinct"],
        },
        "full_repository": {
            "status": "PASS_WITH_ACCEPTED_BASELINE_RESIDUALS",
            "tests": 5676, "passed": 5676 - 261 - failures - errors,
            "skipped": 261, "failed": failures, "errors": errors,
            "nonpassing_nodeids": [node], "nonpassing_outcomes": {node: outcome},
            "nonpassing_node_set_sha256": _sha256_bytes((node + "\n").encode()),
            "rewrite_only_nonpassing": [], "outcome_kind_changes": [],
            "accepted_baseline_nonpassing_fixed":
                len(baseline["allowed_nonpassing_nodeids"]) - 1,
        },
    }


def test_v67_report_gate_rechecks_kernel_residuals_and_clean_pass():
    executable_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True).stdout.strip()
    report = _valid_v67_report(executable_sha)
    assert _v67_report_findings(report, executable_sha) == []
    forged = json.loads(json.dumps(report))
    forged["clean_reconstruction"]["kernel_tree_sha256"] = "deadbeef"
    forged["full_repository"]["rewrite_only_nonpassing"] = ["invented::node"]
    codes = {item["code"] for item in _v67_report_findings(forged, executable_sha)}
    assert {"V67_KERNEL_IDENTITY_MISMATCH",
            "V67_REPORTED_REGRESSION_FIELDS_NONEMPTY"}.issubset(codes)

    clean = _valid_v67_report(executable_sha)
    full = clean["full_repository"]
    full.update({
        "status": "PASSED", "passed": full["tests"] - full["skipped"],
        "failed": 0, "errors": 0, "nonpassing_nodeids": [],
        "nonpassing_outcomes": {},
        "nonpassing_node_set_sha256": _sha256_bytes(b"\n"),
        "accepted_baseline_nonpassing_fixed":
            len(json.loads((CONTRACT_PATH.parent /
                "CURUNIR_V6_7_BASELINE_NONPASSING.json").read_text())[
                    "allowed_nonpassing_nodeids"]),
    })
    clean["status"] = "PASS"
    assert _v67_report_findings(clean, executable_sha) == []


def test_terminal_rederives_and_rejects_forged_prehuman_pass(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    campaign = tmp_path / "campaign"
    root = campaign / "M2_REGULATORY_CORRECTION"
    prepare_mission("M2_REGULATORY_CORRECTION", root)
    finalize_mission(root)
    artifacts = root / "artifacts"
    result = json.loads((artifacts / "mission_result.json").read_text())
    result["status"] = "PASS"
    result["manual_waiver"] = False
    result["coverage"] = {
        capability: {
            "mission": "M2_REGULATORY_CORRECTION",
            "exact_exercised_path": "forged package row",
            "artifact": "mission_result.json", "measured_result": True,
            "status": "EXERCISED",
        } for capability in json.loads(CONTRACT_PATH.read_text())["required_coverage"]
    }
    _write_json(artifacts / "mission_result.json", result)
    package = json.loads((artifacts / "mission_package.json").read_text())
    package["status"] = "PASS"
    _write_json(artifacts / "mission_package.json", package)
    _write_json(artifacts / "artifact_manifest.json", {
        "format": "curunir-v6.8-artifact-manifest-v1",
        "mission_id": "M2_REGULATORY_CORRECTION",
        "entries": _tree_manifest(artifacts, exclude={"artifact_manifest.json"}),
    })
    executable_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True).stdout.strip()
    v67_path = tmp_path / "v67.json"
    _write_json(v67_path, _valid_v67_report(executable_sha))
    monkeypatch.setattr(v68, "MISSION_IDS", ("M2_REGULATORY_CORRECTION",))
    monkeypatch.setattr(v68, "_focused_v68_validation", lambda: {
        "status": "PASS", "tests": 1, "passed": 1, "skipped": 0,
        "failures": 0, "errors": 0, "exit_code": 0})
    terminal = v68.validate_campaign(campaign, v67_path)
    assert terminal["status"] == "NOT_ACHIEVED"
    codes = {item["code"] for item in terminal["findings"]}
    assert {"PACKAGED_DERIVATION_MISMATCH", "MISSION_NOT_ACHIEVED"}.issubset(codes)


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


def test_superseded_authority_is_accepted_only_when_ledgered_in_the_contract(monkeypatch: pytest.MonkeyPatch):
    """A campaign root prepared under a superseded authority set is recognised
    only through the frozen contract's ``authority_supersession`` ledger; an
    unledgered set is refused, and an incomplete ledger entry is an error."""
    current = _current_authority()
    contract = json.loads(v68.CONTRACT_PATH.read_text(encoding="utf-8"))
    ledgered = contract["authority_supersession"]
    assert ledgered, "the 2026-09-02 revision must be ledgered"
    accepted = _accepted_authorities()
    assert accepted[0] == current
    for entry in ledgered:
        superseded = {**current, **{key: entry[key] for key in (
            "contract_sha256", "missions_sha256", "repository_truth_sha256",
            "repair_contract_sha256", "pilot_protocol_sha256")}}
        assert superseded in accepted
        forged = dict(superseded, pilot_protocol_sha256="f" * 64)
        assert forged not in accepted
    # the canonical human pilot root still verifies through the ledger
    pilot = v68.REPO_ROOT / "curunir_v68_runs" / "V68_TERMINAL_004" / "M1_CORPORATE_REGISTRY_CAPSTONE"
    if pilot.is_dir():
        recorded = json.loads((pilot / "qualification_authority.json").read_text(encoding="utf-8"))
        assert recorded in accepted

    broken = dict(contract)
    broken["authority_supersession"] = [{"contract_sha256": "0" * 64}]
    monkeypatch.setattr(v68, "_read_json", lambda path: broken if path == v68.CONTRACT_PATH else json.loads(Path(path).read_text(encoding="utf-8")))
    with pytest.raises(V68Error, match="incomplete"):
        _accepted_authorities()

