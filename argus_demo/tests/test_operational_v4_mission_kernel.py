from __future__ import annotations

import json
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from curunir_operational.v4.kernel import (
    ZeroWriteMonitor, ZeroWriteViolation, build_proposal, inspect_frozen_kernel,
    shadow_dry_run, static_zero_write_scan, validate_proposal, zero_write_guard,
)
from curunir_operational.v4.mission import (
    build_handoff, initialize_review_nodes, receive_review_packet, serialize_review_packet,
)

pytestmark = pytest.mark.no_db


def proposal(**updates):
    values = dict(
        case_id="CASE", proposed_concept="Public programme record", target_table_or_action="entities",
        proposed_field_values={"entity_type": "programme", "status": "shadow"},
        source_object_ids=("source-1",), claim_ids=("claim-1",), evidence_basis_ids=("basis-1",),
        source_independence_state="INDEPENDENT", identity_state="SAME_ENTITY_ACCEPTED",
        contradiction_state="NONE", correction_retraction_state="CURRENT",
        mapping_precision="EXACT_CHARACTER", dependencies=(), provider="SOL_AI_SECONDARY_REVIEW",
        creator_actor_id="strategic-analyst", access_marking={"releasability": ["PUBLIC"]},
    ); values.update(updates); return build_proposal(**values)


def test_frozen_kernel_inspection_only_hashes_and_tables():
    root = Path(__file__).parents[1]
    result = inspect_frozen_kernel(root)
    assert result["protected_hashes_match"] and result["canonical_table_count"] == 16
    assert result["database_connection_opened"] is False and result["action_module_imported"] is False
    assert "entities" in result["tables"] and result["action_signatures"]


def test_admission_human_gate_and_self_approval_denied():
    root = Path(__file__).parents[1]; inspection = inspect_frozen_kernel(root); item = proposal()
    valid = validate_proposal(item, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                              reviewer_actor_id="kernel-reviewer", known_claim_ids={"claim-1"},
                              known_basis_ids={"basis-1"}, known_source_ids={"source-1"}, known_dependencies=set())
    assert valid.resulting_status == "HUMAN_REVIEW_REQUIRED" and valid.separation_of_duties_valid
    self_review = validate_proposal(item, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                    reviewer_actor_id=item.creator_actor_id, known_claim_ids={"claim-1"},
                                    known_basis_ids={"basis-1"}, known_source_ids={"source-1"}, known_dependencies=set())
    assert self_review.resulting_status == "APPROVAL_BLOCKED" and not self_review.separation_of_duties_valid


def test_incomplete_identity_contradiction_dependency_and_target_are_blocked():
    root = Path(__file__).parents[1]; inspection = inspect_frozen_kernel(root)
    cases = ((proposal(target_table_or_action="imaginary"), "APPROVAL_BLOCKED"),
             (proposal(source_object_ids=()), "EVIDENCE_INCOMPLETE"),
             (proposal(identity_state="AMBIGUOUS"), "IDENTITY_UNRESOLVED"),
             (proposal(contradiction_state="UNRESOLVED"), "CONTRADICTION_UNRESOLVED"),
             (proposal(dependencies=("missing",)), "APPROVAL_BLOCKED"))
    for item, status in cases:
        validation = validate_proposal(item, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                       reviewer_actor_id="reviewer", known_claim_ids={"claim-1"},
                                       known_basis_ids={"basis-1"}, known_source_ids={"source-1"},
                                       known_dependencies=set())
        assert validation.resulting_status == status


def test_dry_run_is_deterministic_blocked_and_does_not_mutate_fixture():
    root = Path(__file__).parents[1]; inspection = inspect_frozen_kernel(root); item = proposal()
    validation = validate_proposal(item, inspection, validator_node_id="KERNEL_REVIEW_NODE",
                                   reviewer_actor_id="reviewer", known_claim_ids={"claim-1"},
                                   known_basis_ids={"basis-1"}, known_source_ids={"source-1"}, known_dependencies=set())
    fixture = {"entities": [{"id": "existing"}]}; snapshot = json.loads(json.dumps(fixture))
    first = shadow_dry_run((item,), (validation,), fixture, inspection)
    second = shadow_dry_run((item,), (validation,), fixture, inspection)
    assert fixture == snapshot and first.integrity_hash == second.integrity_hash
    assert first.blocked_operation_ids and first.canonical_write_attempts == first.canonical_writes == 0


def test_zero_write_static_and_runtime_monitor_refuses_postgres_and_psql(monkeypatch):
    root = Path(__file__).parents[1]
    assert static_zero_write_scan(root / "curunir_operational" / "v4")["verdict"] == "PASS"
    monitor = ZeroWriteMonitor(root)
    with zero_write_guard(monitor):
        with pytest.raises(ZeroWriteViolation):
            socket.socket().connect(("127.0.0.1", 5432))
        with pytest.raises(ZeroWriteViolation):
            subprocess.run(["psql", "--version"])
    assert monitor.attempts == 2 and monitor.verify()["canonical_writes"] == 0


def test_handoff_is_proposal_only_and_integrity_bound():
    handoff = build_handoff(case_id="CASE", claim_or_hypothesis_ids=("claim-1",),
                            evidence_basis_ids=("basis-1",), source_dependence_summary="one source family",
                            correction_retraction_state="CURRENT", uncertainty="Human review pending",
                            proposed_implication="Open a monitoring requirement", recipient="LOGISTICS_WORKBENCH",
                            action_kind="INFORMATION_REQUIREMENT", access_marking={"releasability": ["PUBLIC"]},
                            expires_at="2026-08-01T00:00:00+00:00")
    assert not handoff.direct_operational_mutation and len(handoff.integrity_hash) == 64


def test_separate_review_nodes_serialized_process_packet(tmp_path):
    manifests = initialize_review_nodes(tmp_path / "nodes")
    assert manifests["STRATEGIC_EVIDENCE_NODE"]["browse"]
    assert manifests["KERNEL_REVIEW_NODE"]["browse"] is False
    handoff = build_handoff(case_id="CASE", claim_or_hypothesis_ids=("claim-1",),
                            evidence_basis_ids=("basis-1",), source_dependence_summary="bounded",
                            correction_retraction_state="CURRENT", uncertainty="pending",
                            proposed_implication="monitor", recipient="LOGISTICS_WORKBENCH",
                            action_kind="MONITORING_TASK", access_marking={"releasability": ["PUBLIC"]},
                            expires_at="2026-08-01T00:00:00+00:00")
    item = proposal(); packet_path = tmp_path / "packet.json"; receipt_path = tmp_path / "receipt.json"
    serialize_review_packet(strategic_node_root=tmp_path / "nodes" / "STRATEGIC_EVIDENCE_NODE",
                            destination_node_id="KERNEL_REVIEW_NODE", case_id="CASE",
                            handoffs=[handoff.to_record()], proposals=[item.to_record()],
                            access_context={"releasability": ["PUBLIC"]}, output_path=packet_path)
    completed = subprocess.run([sys.executable, "-m", "curunir_operational.v4.cli", "review-receive",
                                "--node-root", str(tmp_path / "nodes" / "KERNEL_REVIEW_NODE"),
                                "--packet", str(packet_path), "--receipt", str(receipt_path)],
                               text=True, capture_output=True, check=True)
    output = json.loads(completed.stdout); receipt = json.loads(receipt_path.read_text())
    assert output["status"] == "VALID" and output["pid"] != __import__("os").getpid()
    assert receipt["browsing_attempts"] == receipt["canonical_write_attempts"] == 0


def test_tampered_review_packet_is_rejected(tmp_path):
    initialize_review_nodes(tmp_path / "nodes"); packet_path = tmp_path / "packet.json"
    item = proposal()
    serialize_review_packet(strategic_node_root=tmp_path / "nodes" / "STRATEGIC_EVIDENCE_NODE",
                            destination_node_id="KERNEL_REVIEW_NODE", case_id="CASE", handoffs=[],
                            proposals=[item.to_record()], access_context={"releasability": ["PUBLIC"]},
                            output_path=packet_path)
    packet = json.loads(packet_path.read_text()); packet["case_id"] = "TAMPERED"
    packet_path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="integrity"):
        receive_review_packet(kernel_node_root=tmp_path / "nodes" / "KERNEL_REVIEW_NODE",
                              packet_path=packet_path, receipt_path=tmp_path / "receipt.json")


def test_review_packet_filters_restricted_records_before_serialization_without_count_leak(tmp_path):
    initialize_review_nodes(tmp_path / "nodes")
    public = proposal(); restricted = proposal(proposed_concept="Restricted concept",
                                               access_marking={"releasability": ["RESTRICTED"]})
    packet = serialize_review_packet(
        strategic_node_root=tmp_path / "nodes" / "STRATEGIC_EVIDENCE_NODE",
        destination_node_id="KERNEL_REVIEW_NODE", case_id="CASE", handoffs=[],
        proposals=[public.to_record(), restricted.to_record()],
        access_context={"releasability": ["PUBLIC"]}, output_path=tmp_path / "packet.json")
    serialized = (tmp_path / "packet.json").read_text()
    assert [item["proposal_id"] for item in packet["proposals"]] == [public.proposal_id]
    assert restricted.proposal_id not in serialized and "Restricted concept" not in serialized
    assert packet["omissions_declaration"] == "Policy may omit records or attributes; no hidden counts or identifiers are disclosed."
