"""Focused tests: V5.1 Section 19 corpus partitioning."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1.corpora import partition_v5_material

pytestmark = pytest.mark.no_db


def _mini_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    packets = [
        {"packet_id": f"packet-{index}", "surface": "SURFACE_X",
         "review_material": {"value": index}}
        for index in range(4)
    ]
    (corpus / "frozen_packets.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in packets), encoding="utf-8")
    consensus = [
        {"blind_packet_id": "packet-0", "surface": "SURFACE_X",
         "consensus_state": "UNANIMOUS", "decision_counts": {"CORRECT": 3}},
        {"blind_packet_id": "packet-1", "surface": "SURFACE_X",
         "consensus_state": "MAJORITY",
         "decision_counts": {"PARTIALLY_CORRECT": 2, "CORRECT": 1}},
        {"blind_packet_id": "packet-2", "surface": "SURFACE_X",
         "consensus_state": "MAJORITY",
         "decision_counts": {"PACKET_DEFECT": 2, "CORRECT": 1}},
        {"blind_packet_id": "packet-3", "surface": "SURFACE_X",
         "consensus_state": "MAJORITY",
         "decision_counts": {"INCORRECT": 2, "CORRECT": 1}},
    ]
    consensus_path = tmp_path / "consensus.json"
    consensus_path.write_text(json.dumps(consensus), encoding="utf-8")
    errors = [{"error_id": "error-1", "packet_id": "packet-3"}]
    errors_path = tmp_path / "errors.json"
    errors_path.write_text(json.dumps(errors), encoding="utf-8")
    return corpus, consensus_path, errors_path


def test_partition_membership_and_counts(tmp_path):
    corpus, consensus_path, errors_path = _mini_corpus(tmp_path)
    out = tmp_path / "out"
    manifest = partition_v5_material(
        corpus_root=corpus, consensus_path=consensus_path,
        error_records_paths=[errors_path], output_root=out)
    assert manifest["counts"]["V5_1_REPAIR_DEVELOPMENT_CORPUS"] == 3
    assert manifest["counts"]["V5_1_REGRESSION_CORPUS"] == 1
    assert manifest["counts"]["V5_1_REPAIR_VERIFICATION_CORPUS"] == 3
    assert manifest["unmatched_consensus_rows"] == 0
    assert manifest["clean_generalization_evidence"] is False
    development = [json.loads(line) for line in
                   (out / "11_repair_development/repair_development_corpus.jsonl")
                   .read_text(encoding="utf-8").splitlines()]
    reasons = {item["packet_id"]: item["membership_reason"] for item in development}
    assert reasons == {"packet-1": "PARTIAL", "packet-2": "PACKET_DEFECT",
                       "packet-3": "INCORRECT"}
    linked = {item["packet_id"]: item["linked_error_records"] for item in development}
    assert linked["packet-3"] == ["error-1"]


def test_partition_never_marks_clean_generalization(tmp_path):
    corpus, consensus_path, errors_path = _mini_corpus(tmp_path)
    manifest = partition_v5_material(
        corpus_root=corpus, consensus_path=consensus_path,
        error_records_paths=[errors_path], output_root=tmp_path / "out2")
    assert manifest["source_material"] == "PRE_FREEZE_V4_V5_ONLY"
    assert manifest["labels_visible_in_development"] is True
