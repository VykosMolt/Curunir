from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.v5.adjudication import (
    SURFACES, build_frozen_corpus, compare_and_repair, execute_blind_reviews,
)
from curunir_operational.v5.models import AdjudicationDecision, freeze_packet

pytestmark = pytest.mark.no_db

V4 = Path("artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722")


@pytest.fixture()
def corpus(tmp_path):
    root = tmp_path / "corpus"; manifest = build_frozen_corpus(V4, root)
    return root, manifest


def test_corpus_is_frozen_and_stratified(corpus):
    root, manifest = corpus
    assert manifest["frozen"] and manifest["packet_count"] >= 100
    assert set(manifest["surface_counts"]) == set(SURFACES)
    assert {"REPAIR_DEVELOPMENT_SET", "REGRESSION_SET", "FINAL_HELD_OUT_SET",
            "ADVERSARIAL_CHALLENGE_SET", "RESERVE_POOL"} <= set(manifest["split_counts"])
    assert manifest["packet_index_hash"] and manifest["sampling_plan_hash"]


def test_blind_packet_contains_no_system_answer(corpus):
    packets = [json.loads(line) for line in (corpus[0] / "frozen_packets.jsonl").read_text().splitlines()]
    forbidden = {"curunir_answer", "system_label", "expected_answer", "final_verdict"}
    assert all(not forbidden.intersection(item["review_material"]) for item in packets)
    assert all(item["answer_key_visibility"] == "HIDDEN_FROM_INDEPENDENT_REVIEWERS" for item in packets)


def test_packet_hash_rejects_replacement():
    packet = freeze_packet(surface=SURFACES[0], source_artifact_ids=("source",), version=1,
        stratum="ordinary", split="REGRESSION_SET", material={"source_excerpt": "A states B."}, ordinal=1)
    value = packet.public_payload(); value["review_material"]["source_excerpt"] = "replaced"
    with pytest.raises(ValueError, match="hash"):
        type(packet)(**value)


def test_answer_leak_rejected():
    with pytest.raises(ValueError, match="leaks"):
        freeze_packet(surface=SURFACES[0], source_artifact_ids=("source",), version=1,
            stratum="bad", split="REGRESSION_SET", material={"system_label": "CORRECT"}, ordinal=2)


def test_human_slot_cannot_emit_model_decision():
    with pytest.raises(ValueError, match="human"):
        AdjudicationDecision("d", "p", "human", "HUMAN_REVIEWER_SLOT", "none", "none", "h",
            "CORRECT", "X", "", (), .5, (), "2026-07-22T00:00:00+00:00")


def test_isolated_reviewers_have_distinct_order_and_no_answer(corpus, tmp_path):
    metrics = execute_blind_reviews(corpus[0] / "frozen_packets.jsonl", tmp_path / "reviews")
    assignments = json.loads((tmp_path / "reviews/reviewer_assignments.json").read_text())
    assert metrics["reviewer_slots"] == 3 and metrics["human_reviewer_records"] == 0
    assert not any(item["answer_key_visible"] for item in assignments)
    by_reviewer = {}
    for item in assignments: by_reviewer.setdefault(item["reviewer_id"], []).append(item)
    orders = [tuple(x["packet_id"] for x in sorted(v, key=lambda y: y["packet_order"])) for v in by_reviewer.values()]
    assert len(set(orders)) == 3


def test_all_decision_states_contract():
    allowed = {"CORRECT", "INCORRECT", "PARTIALLY_CORRECT", "INSUFFICIENT_INFORMATION",
               "AMBIGUOUS", "OUT_OF_SCOPE", "CANNOT_ADJUDICATE", "PACKET_DEFECT"}
    for state in allowed:
        value = AdjudicationDecision(f"d-{state}", "p", "r", "DETERMINISTIC_VALIDATOR", "rules", "1", "h",
            state, "label", "reason", (), .5, (), "2026-07-22T00:00:00+00:00",
            "DETERMINISTIC_VALIDATOR")
        assert value.decision == state


def test_surface_review_finds_general_error_classes(corpus, tmp_path):
    review = tmp_path / "review"; errors = tmp_path / "errors"
    execute_blind_reviews(corpus[0] / "frozen_packets.jsonl", review)
    summary = compare_and_repair(corpus[0], review, errors)
    assert summary["high_unrepaired"] == 0
    assert {"EXTRACTION", "SOURCE_ORIGIN", "DEPENDENCE", "REPORTING"} <= set(summary["by_taxonomy"])
    repairs = json.loads((errors / "repair_records.json").read_text())
    assert repairs and all(not item["case_specific"] for item in repairs)
