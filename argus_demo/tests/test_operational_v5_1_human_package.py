"""V5.1 human-review package tests (contract Section 27)."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1.human_package import (
    build_human_packets, package_manifest, plain_language_lint, review_modes,
    study_design, validate_study_design,
)

from test_operational_v5_1_campaign import (  # noqa: F401  (fixture reuse)
    _definition, _fake_acquire, _leads, campaign_run,
)
from test_operational_v5_1_heldout import _PROMPTS, heldout_corpus  # noqa: F401

pytestmark = pytest.mark.no_db


@pytest.fixture()
def human_build(heldout_corpus, tmp_path):
    corpus_root, _summary = heldout_corpus
    out = tmp_path / "human"
    summary = build_human_packets(corpus_root / "manifest.json", {}, out, minimum=5)
    return out, summary


def test_packets_built_with_explicit_shortfall(human_build):
    out, summary = human_build
    assert summary["packet_count"] >= 5
    assert summary["mode"] == "INDEPENDENT_LABEL"
    assert summary["human_review_state"].startswith("HUMAN_REVIEW_PENDING")
    if summary["missing_surfaces"]:
        shortfall = json.loads((out / "shortfall_record.json").read_text())
        assert shortfall["explicit"] is True
        assert "SURFACE_COVERAGE_SHORTFALL" in shortfall["reasons"]


def test_answers_hidden_in_independent_mode(human_build):
    out, summary = human_build
    assert summary["curunir_answers_hidden"] is True
    assert summary["sealed_answers_copied"] is False
    payload = (out / "human_packets.jsonl").read_text(encoding="utf-8").casefold()
    for marker in ("sealed_answer", "system_label", "answer_key",
                   "expected_label", "curunir_decision"):
        assert marker not in payload, marker


def test_plain_language_lint_blocks_jargon():
    hits = plain_language_lint(
        "Judge the sampling stratum and the adjudication boundary of this "
        "epistemic surface.")
    assert hits, "jargon must be flagged"
    assert plain_language_lint(
        "Does the highlighted passage really say what the record claims?") == ()


def test_packet_instructions_pass_lint(human_build):
    out, _summary = human_build
    for line in (out / "human_packets.jsonl").read_text(encoding="utf-8").splitlines():
        packet = json.loads(line)
        assert plain_language_lint(packet["question"]) == ()
        assert plain_language_lint(packet["instructions"]) == ()


def test_review_modes_document_all_four(tmp_path):
    modes = review_modes(tmp_path / "modes.json")
    names = set(modes["modes"]) if isinstance(modes.get("modes"), (list, tuple)) \
        else set(modes["modes"].keys())
    assert {"INDEPENDENT_LABEL", "CURUNIR_OUTPUT_VERIFICATION",
            "DISAGREEMENT_ADJUDICATION", "DOMAIN_EXPERT_ESCALATION"} <= names


def test_study_design_refuses_fake_results(tmp_path):
    design = study_design(tmp_path / "design.json")
    assert validate_study_design(design) is True
    poisoned = dict(design)
    poisoned["results"] = {"accuracy": 0.99}
    with pytest.raises(ValueError):
        validate_study_design(poisoned)


def test_package_manifest_states_pending(human_build, tmp_path):
    out, _summary = human_build
    review_modes(out / "review_modes.json")
    study_design(out / "study_design.json")
    manifest = package_manifest(out)
    assert manifest["human_review_state"].startswith("HUMAN_REVIEW_PENDING")
    assert manifest["human_results_claimed"] is False
    assert manifest["no_human_result_files_verified"] is True
    assert manifest["package_hash"]


def test_build_refuses_existing_package(human_build):
    out, _summary = human_build
    with pytest.raises(ValueError, match="append-only"):
        build_human_packets(out / "does-not-matter.json", {}, out, minimum=1)
