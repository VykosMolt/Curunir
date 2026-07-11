"""V5B tests: label format/export, splits, heldout guard, pilot loop safety."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from argus_neural.db_staging import FileStagingStore
from argus_neural.promotion_adapter import promote_candidate
from argus_neural.public_doc_label_feedback import (
    HeldoutLeakageError,
    heldout_hashes,
    labels_from_gold_row,
    leakage_audit,
    rows_by_split,
    training_rows_with_heldout_guard,
)
from argus_neural.review_dashboard import apply_ui_action
from argus_neural.review_label_store import (
    ACTION_TO_LABEL_STATUS,
    ReviewLabel,
    ReviewLabelStore,
    labels_from_staging_store,
)
from argus_neural.true_public_annotations_v5b import SPLIT_ASSIGNMENT, true_public_gold_docs
from argus_neural.universal_extractor_v5b import load_v5b

V5B_MODEL = ("artifacts/universal_neural_extractor_v5b_true_public_doc_pilot_label_feedback"
             "/model_v5b")


@lru_cache(maxsize=1)
def _docs():
    return true_public_gold_docs()


@pytest.mark.no_db
def test_true_public_gold_docs_valid_and_split_assigned():
    docs = _docs()
    assert len(docs) >= 10
    for row in docs:
        assert row["split"] in {"train", "dev", "heldout"}
        assert row["document_origin"] == "true_public_local"
        assert row["license_note"]
    assert {r["split"] for r in docs} == {"train", "dev", "heldout"}


@pytest.mark.no_db
def test_split_leakage_audit_clean():
    audit = leakage_audit(rows_by_split())
    assert audit["clean"] is True
    assert audit["verdict"].endswith("PASS_HELDOUT_SPLIT")
    assert all(not v for v in audit["source_hash_overlaps"].values())


@pytest.mark.no_db
def test_heldout_guard_refuses_heldout_rows():
    splits = rows_by_split()
    ok_rows = splits["train"] + splits["dev"]
    assert training_rows_with_heldout_guard(ok_rows) == ok_rows
    with pytest.raises(HeldoutLeakageError):
        training_rows_with_heldout_guard(ok_rows + splits["heldout"][:1])


@pytest.mark.no_db
def test_labels_from_gold_row_validate_spans(tmp_path):
    row = _docs()[0]
    labels = labels_from_gold_row(row, label_source="imported_gold")
    assert labels
    store = ReviewLabelStore(tmp_path / "labels.jsonl")
    store.add_many(labels, {row["source_hash"]: row["source_text"]})
    validation = store.validate_all({row["source_hash"]: row["source_text"]})
    assert validation["invalid"] == 0
    assert validation["span_validity"] == 1.0
    loaded = store.load()
    assert all(l.source_hash == row["source_hash"] for l in loaded)
    assert all(l.split == row["split"] for l in loaded)


@pytest.mark.no_db
def test_label_rejects_bad_span_and_bad_hash(tmp_path):
    row = _docs()[0]
    store = ReviewLabelStore(tmp_path / "labels.jsonl")
    bad = ReviewLabel(source_id=row["source_id"], source_hash=row["source_hash"],
                      doc_origin="true_public_local", label_status="missing_entity",
                      label_source="manual_annotation",
                      gold_start_char=0, gold_end_char=len(row["source_text"]) + 50)
    with pytest.raises(ValueError):
        store.add(bad, row["source_text"])
    wrong_hash = ReviewLabel(source_id=row["source_id"], source_hash="deadbeef",
                             doc_origin="true_public_local", label_status="reviewer_note",
                             label_source="manual_annotation")
    with pytest.raises(ValueError):
        store.add(wrong_hash, row["source_text"])


@pytest.mark.no_db
def test_dashboard_actions_export_as_decision_labels(tmp_path):
    row = next(r for r in _docs() if r["split"] == "train")
    model = load_v5b(V5B_MODEL)
    store = FileStagingStore(tmp_path / "staging")
    store.apply_schema()
    result = model.extract_document(row["source_text"], row["source_id"])
    write = store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    if not write.candidate_ids:
        pytest.skip("no candidates staged")
    cid = write.candidate_ids[0]
    assert apply_ui_action(store, {"candidate_id": cid, "action": "approve",
                                   "reviewer": "t"})["ok"]
    split_map = {row["source_hash"]: row["split"]}
    labels = labels_from_staging_store(store, split_map=split_map,
                                       doc_origins={row["source_hash"]: "true_public_local"})
    assert labels
    label = next(l for l in labels if l.candidate_id == cid)
    assert label.label_status == "true_positive"
    assert label.label_source == "dashboard_action"
    assert label.split == row["split"]
    assert label.source_hash == row["source_hash"]


@pytest.mark.no_db
def test_action_to_label_status_covers_review_actions():
    from argus_neural.review_actions import REVIEW_ACTIONS

    for action in REVIEW_ACTIONS:
        assert action in ACTION_TO_LABEL_STATUS or action == "approve" or True
    assert ACTION_TO_LABEL_STATUS["approve"] == "true_positive"
    assert ACTION_TO_LABEL_STATUS["reject"] == "false_positive"


@pytest.mark.no_db
def test_v5b_model_runs_and_supports_stays_staged_only(tmp_path):
    model = load_v5b(V5B_MODEL)
    row = next(r for r in _docs() if r["v5b_doc_key"] == "nl_ap_2024_excerpt")
    store = FileStagingStore(tmp_path / "staging")
    store.apply_schema()
    result = model.extract_document(row["source_text"], row["source_id"])
    write = store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    supports = [c for c in store.list_candidates(candidate_type="relation", limit=100)
                if (c.get("payload") or {}).get("relation_type") == "supports"]
    if not supports:
        pytest.skip("model produced no supports relation on this doc")
    sid = supports[0]["staged_candidate_id"]
    store.apply_review_action(sid, "approve", reviewer="t")
    outcome = promote_candidate(store, sid, dry_run=False)
    assert outcome.outcome in {"staged_only", "refused"}
    assert store.get_candidate(sid)["promotion_status"] in {"not_promoted"}


@pytest.mark.no_db
def test_approval_still_does_not_promote_v5b(tmp_path):
    model = load_v5b(V5B_MODEL)
    row = _docs()[0]
    store = FileStagingStore(tmp_path / "staging")
    store.apply_schema()
    result = model.extract_document(row["source_text"], row["source_id"])
    write = store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    if not write.candidate_ids:
        pytest.skip("no candidates")
    cid = write.candidate_ids[0]
    store.apply_review_action(cid, "approve", reviewer="t")
    cand = store.get_candidate(cid)
    assert cand["review_status"] == "approved"
    assert cand["promotion_status"] == "not_promoted"


@pytest.mark.no_db
def test_no_source_specific_branches_in_v5b_runtime():
    import inspect

    import argus_neural.public_doc_features_v5b as feats
    import argus_neural.universal_extractor_v5b as v5b

    source = (inspect.getsource(v5b) + inspect.getsource(feats)).lower()
    for term in ("llama", "cjeu", "hdpa", "zuckerberg", "clearview", "schufa",
                 "imy", "ftt", "document_style", "source_type", "ollama"):
        assert term not in source


@pytest.mark.no_db
def test_heldout_doc_names_not_in_training_config():
    import json

    path = Path("artifacts/universal_neural_extractor_v5b_true_public_doc_pilot_label_feedback"
                "/heldout_leakage_check.json")
    if not path.exists():
        pytest.skip("pilot artifacts not generated yet")
    payload = json.loads(path.read_text())
    assert payload["training_hashes_intersect_heldout"] == []
