"""V5H corpus-growth tests: batch integrity, frozen-heldout expansion
discipline, new-case isolation, relation completeness conventions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_neural.public_doc_label_feedback import (
    all_true_public_docs,
    leakage_audit,
    rows_by_split,
)
from argus_neural.true_public_annotations_v5h import (
    EXHAUSTIVE_KEYS_V5H,
    SPLIT_ASSIGNMENT_V5H,
    true_public_gold_docs_v5h,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def v5h_docs():
    docs = true_public_gold_docs_v5h()
    assert docs, "fetched corpus files missing"
    return {d["v5b_doc_key"]: d for d in docs}


@pytest.mark.no_db
def test_v5h_batch_builds_completely(v5h_docs):
    assert set(v5h_docs) == set(SPLIT_ASSIGNMENT_V5H)
    for key, doc in v5h_docs.items():
        assert doc["annotation_completeness"] == (
            "exhaustive" if key in EXHAUSTIVE_KEYS_V5H else "partial")
        assert doc["label_provenance"] == "v5h_corpus_growth"


@pytest.mark.no_db
def test_v5h_heldout_expansion_is_additive_and_clean(v5h_docs):
    heldout = {r["v5b_doc_key"] for r in all_true_public_docs()
               if r["split"] == "heldout"}
    new4 = {k for k, s in SPLIT_ASSIGNMENT_V5H.items() if s == "heldout"}
    assert len(new4) == 4 and new4 <= heldout
    assert len(heldout) == 11
    audit = leakage_audit(rows_by_split())
    assert audit["clean"], audit


@pytest.mark.no_db
def test_v5h_fetch_manifest_covers_every_source_file():
    manifest = json.loads(
        (ROOT / "demo_corpus" / "v5h_fetch_manifest.json").read_text())
    files = {f["file"] for f in manifest["files"]}
    assert len(files) == 6
    for f in files:
        assert (ROOT / "demo_corpus" / "raw" / f).is_file(), f
        assert f.startswith("edpb_2026_fetch_")
    assert all(f["url"].startswith("https://www.edpb.europa.eu/")
               for f in manifest["files"])


@pytest.mark.no_db
def test_v5h_third_train_supersedes_with_convention_direction(v5h_docs):
    rep = v5h_docs["replika_chatbot_pr"]
    assert rep["split"] == "train"
    sup = [r for r in rep["relations"] if r["relation_type"] == "supersedes"]
    assert len(sup) == 1
    left = rep["claims"][sup[0]["left_claim_index"]]
    right = rep["claims"][sup[0]["right_claim_index"]]
    assert left["value"] == "EUR 5 million"          # later enforcement step
    assert "blocking" in right["value"]              # earlier measure
    train_sup = sum(1 for r in rows_by_split()["train"]
                    for rel in r["relations"] if rel["relation_type"] == "supersedes")
    assert train_sup >= 3


@pytest.mark.no_db
def test_v5h_claims_are_sentence_shaped_and_unique(v5h_docs):
    for key, doc in v5h_docs.items():
        spans = [(c["start_char"], c["end_char"]) for c in doc["claims"]]
        assert len(spans) == len(set(spans)), key
        for c in doc["claims"]:
            assert len(c["claim_text"]) > 25, (key, c["claim_text"])


@pytest.mark.no_db
def test_v5h_every_doc_has_complete_relation_annotation_marker(v5h_docs):
    # the V5E lesson: every exhaustive doc must carry relations (no doc with
    # multiple findings and a sanction may leave all pairs as implicit 'none')
    for key, doc in v5h_docs.items():
        if len(doc["claims"]) >= 3:
            assert doc["relations"], f"{key} has claims but no relations"
