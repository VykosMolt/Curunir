"""V5.1 clean held-out corpus, isolation, and scoring tests."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v4.io import append_jsonl, read_jsonl
from curunir_operational.v5_1.heldout import (
    assign_reviewers, build_heldout, consensus_and_score, freeze_reviews,
)

from test_operational_v5_1_campaign import (  # noqa: F401  (fixture reuse)
    _definition, _fake_acquire, _leads, campaign_run,
)
from curunir_operational.v5_1.campaign import build_reports

pytestmark = pytest.mark.no_db

_PROMPT = ("Judge only from packet content; do not guess. Decisions: CORRECT, "
           "INCORRECT, PARTIALLY_CORRECT, INSUFFICIENT_INFORMATION, AMBIGUOUS, "
           "CANNOT_ADJUDICATE, EPISTEMICALLY_UNRESOLVABLE, PACKET_DEFECT. "
           "Choose EPISTEMICALLY_UNRESOLVABLE only when the packet demonstrates "
           "the public evidence itself cannot settle the question.")
_PROMPTS = {f"SURFACE_{index}": _PROMPT + f" Surface {index} guidance sentence."
            for index in range(1, 7)}


@pytest.fixture()
def heldout_corpus(campaign_run, tmp_path):
    definition, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    report_root = run_tmp / "report"
    if not (report_root / "proposition_evidence_ledger.jsonl").exists():
        build_reports({"case_id": "case-D", "campaign_key": "D",
                       "title": "Fictional campaign report",
                       "research_question": definition["research_question"]},
                      analysis, report_root)
    out = tmp_path / "heldout"
    summary = build_heldout(
        campaigns=[{"campaign_key": "D", "analysis_root": analysis,
                    "custody_root": custody, "report_root": report_root}],
        output_root=out, reviewer_prompts=_PROMPTS,
        minimums={key: 2 for key in _PROMPTS})
    return out, summary


def test_build_freezes_corpus_with_shortfalls_and_coverage(heldout_corpus):
    out, summary = heldout_corpus
    manifest = summary["manifest"]
    assert manifest["frozen"] is True
    assert manifest["packet_count"] > 0
    assert (out / "frozen_packets.jsonl").exists()
    assert (out / "sealed_answers.json").exists()
    assert isinstance(summary["shortfalls"], dict)
    coverage = summary["class_coverage"]
    assert coverage["dependence_states"], "dependence states must be covered"
    assert coverage["candidate_types"], "candidate types must be covered"


def test_reserves_meet_ratio_where_possible(heldout_corpus):
    _out, summary = heldout_corpus
    manifest = summary["manifest"]
    for surface, active in manifest["surface_counts"].items():
        if active > 1:
            assert manifest["reserve_ratio_by_surface"][surface] >= 0.20


def test_reviewer_prompts_are_required(campaign_run, tmp_path):
    definition, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    with pytest.raises(ValueError, match="reviewer prompt"):
        build_heldout(
            campaigns=[{"campaign_key": "D", "analysis_root": analysis,
                        "custody_root": custody, "report_root": run_tmp / "report"}],
            output_root=tmp_path / "x", reviewer_prompts={"SURFACE_1": "short"})


def test_assignments_are_isolated_and_deterministic(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    first = assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                             output_root=tmp_path / "assign1")
    second = assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                              output_root=tmp_path / "assign2")
    orders = {reviewer: entry["order_hash"]
              for reviewer, entry in first["reviewers"].items()}
    assert len(set(orders.values())) == 3, "reviewer orders must differ"
    assert orders == {reviewer: entry["order_hash"]
                      for reviewer, entry in second["reviewers"].items()}
    payload = json.loads((tmp_path / "assign1" / "assignment_A.json").read_text())
    assert "answer" not in json.dumps(payload).casefold()
    assert payload["isolation"]["curunir_verdicts_provided"] is False


def _reviewer_outputs(out, assign_root, tmp_path, *, mutate=None):
    sealed = json.loads((out / "sealed_answers.json").read_text())["answers"]
    files = {}
    for reviewer in ("A", "B", "C"):
        assignment = json.loads(
            (assign_root / f"assignment_{reviewer}.json").read_text())
        records = []
        for packet_id in assignment["packet_order"]:
            records.append({"packet_id": packet_id, "decision": "LABELED",
                            "label": sealed[packet_id],
                            "reasoning": "synthetic reviewer fixture",
                            "confidence": 0.8, "reviewer": reviewer,
                            "other_reviews_seen": False, "human_review": False})
        if mutate:
            records = mutate(reviewer, records)
        path = tmp_path / f"review_{reviewer}.jsonl"
        path.unlink(missing_ok=True)
        append_jsonl(path, records)
        files[reviewer] = path
    return files


def test_freeze_reviews_and_scoring_roundtrip(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    assign_root = tmp_path / "assign"
    assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                     output_root=assign_root)
    files = _reviewer_outputs(out, assign_root, tmp_path)
    manifest = freeze_reviews(review_files=files, assignment_root=assign_root,
                              output_path=tmp_path / "completion.json")
    assert manifest["all_reviewers_complete"] is True
    scores = consensus_and_score(
        corpus_root=out, completion_manifest=tmp_path / "completion.json",
        review_files=files, output_root=tmp_path / "scores")
    for surface, report in scores["surface_reports"].items():
        assert report["metrics"]["packet_defect_rate"] == 0.0
        # Reviewers agreeing with sealed answers on every adjudicable packet
        # must yield hardened-gate-satisfying correctness.
        assert report["gates"]["packet_defect_rate_below_2"], surface
    assert scores["critical_failures"] == []


def test_scoring_refused_without_completion_manifest(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    with pytest.raises(ValueError, match="completion manifest"):
        consensus_and_score(
            corpus_root=out, completion_manifest=tmp_path / "missing.json",
            review_files={}, output_root=tmp_path / "scores2")


def test_freeze_reviews_rejects_incomplete_and_violations(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    assign_root = tmp_path / "assign"
    assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                     output_root=assign_root)

    files = _reviewer_outputs(out, assign_root, tmp_path,
                              mutate=lambda reviewer, records:
                              records[:-1] if reviewer == "B" else records)
    with pytest.raises(ValueError, match="incomplete"):
        freeze_reviews(review_files=files, assignment_root=assign_root,
                       output_path=tmp_path / "c1.json")

    files = _reviewer_outputs(out, assign_root, tmp_path,
                              mutate=lambda reviewer, records:
                              [{**records[0], "decision": "MAYBE"}] + records[1:]
                              if reviewer == "A" else records)
    with pytest.raises(ValueError, match="unknown decision"):
        freeze_reviews(review_files=files, assignment_root=assign_root,
                       output_path=tmp_path / "c2.json")

    files = _reviewer_outputs(out, assign_root, tmp_path,
                              mutate=lambda reviewer, records:
                              [{**records[0], "other_reviews_seen": True}] + records[1:]
                              if reviewer == "C" else records)
    with pytest.raises(ValueError, match="isolation violation"):
        freeze_reviews(review_files=files, assignment_root=assign_root,
                       output_path=tmp_path / "c3.json")


def test_tampered_reviewer_output_detected_at_scoring(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    assign_root = tmp_path / "assign"
    assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                     output_root=assign_root)
    files = _reviewer_outputs(out, assign_root, tmp_path)
    freeze_reviews(review_files=files, assignment_root=assign_root,
                   output_path=tmp_path / "completion.json")
    rows = read_jsonl(files["A"])
    rows[0]["decision"] = "INCORRECT"
    files["A"].write_text("".join(json.dumps(row) + "\n" for row in rows),
                          encoding="utf-8")
    with pytest.raises(ValueError, match="changed after completion freeze"):
        consensus_and_score(
            corpus_root=out, completion_manifest=tmp_path / "completion.json",
            review_files=files, output_root=tmp_path / "scores3")


def test_critical_failure_inputs_block(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    assign_root = tmp_path / "assign"
    assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                     output_root=assign_root)
    files = _reviewer_outputs(out, assign_root, tmp_path)
    freeze_reviews(review_files=files, assignment_root=assign_root,
                   output_path=tmp_path / "completion.json")
    scores = consensus_and_score(
        corpus_root=out, completion_manifest=tmp_path / "completion.json",
        review_files=files, output_root=tmp_path / "scores4",
        campaign_support_audits=[{"zero_tolerance": {"wrong_polarity_accepted": 1}}])
    assert scores["critical_failures"] == ["wrong_polarity_accepted=1"]
    for report in scores["surface_reports"].values():
        assert report["verdict"] == "PARTIAL"


def test_failed_freeze_verification_blocks(heldout_corpus, tmp_path):
    out, _summary = heldout_corpus
    assign_root = tmp_path / "assign"
    assign_reviewers(corpus_root=out, reviewer_ids=("A", "B", "C"),
                     output_root=assign_root)
    files = _reviewer_outputs(out, assign_root, tmp_path)
    freeze_reviews(review_files=files, assignment_root=assign_root,
                   output_path=tmp_path / "completion.json")
    with pytest.raises(ValueError, match="INVALID"):
        consensus_and_score(
            corpus_root=out, completion_manifest=tmp_path / "completion.json",
            review_files=files, output_root=tmp_path / "scores5",
            freeze_verification={"verdict": "INVALID"})
