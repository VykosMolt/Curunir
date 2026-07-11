"""V5E batch-3 annotation tests: marker reachability, split cohesion,
exhaustive policy, diverse relation labels, heldout freeze."""
from __future__ import annotations

import pytest

from argus_neural.public_doc_label_feedback import (
    all_true_public_docs,
    leakage_audit,
    rows_by_split,
)
from argus_neural.true_public_annotations_v5b import true_public_gold_docs
from argus_neural.true_public_annotations_v5e import (
    EXHAUSTIVE_KEYS_V5E,
    SPLIT_ASSIGNMENT_V5E,
    true_public_gold_docs_v5e,
)

FROZEN_HELDOUT_KEYS = {
    # V5B heldout (frozen-4)
    "zuckerberg_letter", "llama31_eval_details", "cjeu_schufa_excerpt",
    "se_imy_2019_excerpt",
    # V5C heldout additions (new-3)
    "imy_school_pr", "vodafone_hellenic_pr", "galluppi_school_pr",
}


@pytest.fixture(scope="module")
def v5e_docs():
    docs = true_public_gold_docs_v5e()
    assert docs, "corpus files missing"
    return {d["v5b_doc_key"]: d for d in docs}


@pytest.mark.no_db
def test_v5e_docs_build_and_validate(v5e_docs):
    # builders run validate_v3_row; reaching here means every marker matched
    # and every span is exact — assert the full batch is present
    assert set(v5e_docs) == set(SPLIT_ASSIGNMENT_V5E)


@pytest.mark.no_db
def test_v5e_adds_no_heldout_docs(v5e_docs):
    assert all(d["split"] in ("train", "dev") for d in v5e_docs.values())
    heldout_keys = {r["v5b_doc_key"] for r in all_true_public_docs()
                    if r["split"] == "heldout"}
    assert heldout_keys == FROZEN_HELDOUT_KEYS


@pytest.mark.no_db
def test_v5e_split_cohesion_and_leakage_clean(v5e_docs):
    # same-case cohesion: the Hellenic press page sits with the HDPA decision
    # excerpt in dev; every other batch-3 doc is train
    assert v5e_docs["hellenic_clearview_pr"]["split"] == "dev"
    audit = leakage_audit(rows_by_split())
    assert audit["clean"], audit


@pytest.mark.no_db
def test_toc_excerpt_is_exhaustive_zero_claim_negative_region(v5e_docs):
    toc = v5e_docs["nl_ap_toc_excerpt"]
    assert toc["annotation_completeness"] == "exhaustive"
    assert toc["claims"] == [] and toc["relations"] == []
    assert toc["abstentions"]
    # the region actually contains the dot-leader shape the batch targets
    assert toc["source_text"].count("......") > 10


@pytest.mark.no_db
def test_train_side_supersedes_present(v5e_docs):
    ftt = v5e_docs["uk_ftt_reasons_excerpt"]
    assert ftt["split"] == "train"
    rels = {r["relation_type"] for r in ftt["relations"]}
    assert "supersedes" in rels
    sup = next(r for r in ftt["relations"] if r["relation_type"] == "supersedes")
    left = ftt["claims"][sup["left_claim_index"]]
    right = ftt["claims"][sup["right_claim_index"]]
    # direction: the tribunal finding (left) supersedes the issued notices (right)
    assert "not in accordance" in left["claim_text"]
    assert "issued two notices" in right["claim_text"]
    train_supersedes = sum(1 for r in rows_by_split()["train"]
                           for rel in r["relations"]
                           if rel["relation_type"] == "supersedes")
    assert train_supersedes >= 1


@pytest.mark.no_db
def test_handoff_label_present_in_press_doc(v5e_docs):
    imy = v5e_docs["imy_police_pr"]
    handoffs = [c for c in imy["claims"] if c["claim_type"] == "handoff"]
    assert len(handoffs) == 2
    assert all(c["subject_text"] == "Elena Mazzotti Pallard" for c in handoffs)


@pytest.mark.no_db
def test_exhaustive_flags_match_declared_sets(v5e_docs):
    for key, doc in v5e_docs.items():
        expected = "exhaustive" if key in EXHAUSTIVE_KEYS_V5E else "partial"
        assert doc["annotation_completeness"] == expected, key


@pytest.mark.no_db
def test_claim_spans_unique_within_each_doc(v5e_docs):
    for key, doc in v5e_docs.items():
        spans = [(c["start_char"], c["end_char"]) for c in doc["claims"]]
        assert len(spans) == len(set(spans)), key


@pytest.mark.no_db
def test_nl_ap_completion_pass_is_exhaustive_and_full_sentence():
    nl = next(r for r in true_public_gold_docs()
              if r["v5b_doc_key"] == "nl_ap_2024_excerpt")
    assert nl["annotation_completeness"] == "exhaustive"
    assert len(nl["claims"]) == 11
    # the wrapped-sentence spans must be whole sentences, not line fragments
    for c in nl["claims"]:
        text = c["claim_text"].rstrip()
        assert text.endswith((".", ":")), text[-60:]
    values = {c["value"] for c in nl["claims"]}
    assert {"EUR 30,500,000", "no EU representative", "four orders"} <= values


@pytest.mark.no_db
def test_v5e_rows_carry_provenance_label(v5e_docs):
    assert all(d["label_provenance"] == "v5e_batch3" for d in v5e_docs.values())
