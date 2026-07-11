"""V4 DB staging / review / promotion tests.

Two groups:
- no_db tests: file-backed store, transition model, promotion guards. These
  never need Postgres.
- DB tests (no marker): run against the conftest-managed argus_test database
  and skip cleanly when Postgres is unavailable.
"""
from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from argus_neural.db_staging import FileStagingStore, PostgresStagingStore
from argus_neural.promotion_adapter import promote_candidate, rollback_promotion
from argus_neural.review_actions import (
    InvalidTransition,
    PROMOTION_STATUSES,
    REVIEW_ACTIONS,
    REVIEW_STATUSES,
    apply_promotion_action,
    apply_review_action,
    transition_table,
)
from argus_neural.segmentation_dataset import generate_v3_segmentation_dataset
from argus_neural.universal_extractor_v3 import ArgusUniversalNeuralExtractorV3

V3_MODEL_DIR = "artifacts/universal_neural_extractor_v3_segmentation_threading/model"


@lru_cache(maxsize=1)
def _v3_extractor():
    return ArgusUniversalNeuralExtractorV3.load(V3_MODEL_DIR)


@lru_cache(maxsize=1)
def _v3_rows():
    ds = generate_v3_segmentation_dataset()
    return {
        "markdown": ds["markdown_thread_test"][0],
        "negative": ds["structure_negative_test"][0],
        "quote_reply": ds["quote_reply_test"][0],
    }


def _staged_file_store(tmp_path, doc_key="markdown"):
    row = _v3_rows()[doc_key]
    extractor = _v3_extractor()
    result = extractor.extract_document(row["source_text"], row["source_id"])
    store = FileStagingStore(tmp_path / "staging")
    store.apply_schema()
    write = store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    return store, write, row


# ---------------------------------------------------------------------------
# transition model (pure logic)
# ---------------------------------------------------------------------------

@pytest.mark.no_db
def test_review_transition_defaults_and_basic_flow():
    t = apply_review_action("pending", "not_promoted", "approve")
    assert (t.new_review_status, t.new_promotion_status) == ("approved", "not_promoted")
    t = apply_review_action("approved", "not_promoted", "reject")
    assert t.new_review_status == "rejected"
    t = apply_review_action("pending", "not_promoted", "defer")
    assert t.new_review_status == "deferred"


@pytest.mark.no_db
def test_review_transition_idempotent_and_invalid():
    t = apply_review_action("approved", "not_promoted", "approve")
    assert t.idempotent_noop
    with pytest.raises(InvalidTransition):
        apply_review_action("rejected", "not_promoted", "approve")
    with pytest.raises(InvalidTransition):
        apply_review_action("pending", "promoted", "reject")  # frozen while promoted
    with pytest.raises(InvalidTransition):
        apply_review_action("pending", "not_promoted", "nonsense_action")


@pytest.mark.no_db
def test_promotion_transitions_require_approval_and_order():
    with pytest.raises(InvalidTransition):
        apply_promotion_action("pending", "not_promoted", "mark_promotion_ready")
    t = apply_promotion_action("approved", "not_promoted", "mark_promotion_ready")
    assert t.new_promotion_status == "promotion_ready"
    with pytest.raises(InvalidTransition):
        apply_promotion_action("approved", "not_promoted", "request_rollback")
    t = apply_promotion_action("approved", "promoted", "request_rollback")
    assert t.new_promotion_status == "rollback_requested"
    t = apply_promotion_action("approved", "rollback_requested", "record_rolled_back")
    assert t.new_promotion_status == "rolled_back"


@pytest.mark.no_db
def test_transition_table_is_total_and_states_known():
    rows = transition_table()
    assert rows
    for row in rows:
        assert row["review_status"] in REVIEW_STATUSES
        assert row["promotion_status"] in PROMOTION_STATUSES
        assert row["outcome"] in {"ok", "idempotent_noop", "invalid"}


# ---------------------------------------------------------------------------
# file-backed store (no DB required)
# ---------------------------------------------------------------------------

@pytest.mark.no_db
def test_file_staging_validates_and_preserves_provenance(tmp_path):
    store, write, row = _staged_file_store(tmp_path)
    assert write.staged_candidates > 0
    assert write.rejected_invalid == 0
    assert write.staged_segments > 0
    assert write.staged_thread_edges > 0
    text = row["source_text"]
    for cand in store.list_candidates(limit=1000):
        assert cand["source_hash"] == row["source_hash"]
        assert cand["review_status"] == "pending"
        assert cand["promotion_status"] == "not_promoted"
        assert 0.0 <= cand["confidence"] <= 1.0
        if cand["start_char"] is not None:
            assert text[cand["start_char"]:cand["end_char"]].strip()
        assert text[cand["evidence_start_char"]:cand["evidence_end_char"]].strip()
    segments = store.list_segments(write.staged_source_id)
    assert segments and all("segment_type" in seg for seg in segments)
    edges = store.list_thread_edges(write.staged_source_id)
    assert edges and all("edge_type" in edge for edge in edges)


@pytest.mark.no_db
def test_file_staging_duplicate_restage_is_skipped(tmp_path):
    store, write, row = _staged_file_store(tmp_path)
    extractor = _v3_extractor()
    result = extractor.extract_document(row["source_text"], row["source_id"])
    second = store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    assert second.staged_candidates == 0
    assert second.skipped_duplicates == write.staged_candidates


@pytest.mark.no_db
def test_file_review_actions_record_events_and_do_not_auto_promote(tmp_path):
    store, write, _ = _staged_file_store(tmp_path)
    cid = write.candidate_ids[0]
    event = store.apply_review_action(cid, "approve", reviewer="t", reason="ok")
    assert event["old_review_status"] == "pending"
    assert event["new_review_status"] == "approved"
    cand = store.get_candidate(cid)
    assert cand["review_status"] == "approved"
    assert cand["promotion_status"] == "not_promoted"  # approval never promotes
    events = store.list_review_events(cid)
    assert len(events) == 1
    # rejection preserves the candidate
    other = write.candidate_ids[1]
    store.apply_review_action(other, "reject", reason="nope")
    assert store.get_candidate(other) is not None
    assert store.get_candidate(other)["review_status"] == "rejected"


@pytest.mark.no_db
def test_file_promotion_requires_approval_and_dry_run_writes_nothing(tmp_path):
    store, write, _ = _staged_file_store(tmp_path)
    cid = write.candidate_ids[0]
    refused = promote_candidate(store, cid, dry_run=True)
    assert refused.outcome == "refused"
    assert "approved" in refused.refusal_reason
    store.apply_review_action(cid, "approve", reviewer="t")
    dry = promote_candidate(store, cid, dry_run=True)
    assert dry.outcome in {"would_promote"}
    assert dry.planned_actions
    cand = store.get_candidate(cid)
    assert cand["promotion_status"] == "not_promoted"  # dry run changes nothing
    promos = store.list_promotions(cid)
    assert promos and promos[-1]["promotion_action"] == "dry_run"


@pytest.mark.no_db
def test_file_negative_doc_stages_no_claims(tmp_path):
    store, write, row = _staged_file_store(tmp_path, doc_key="negative")
    claims = store.list_candidates(candidate_type="claim", limit=100)
    relations = store.list_candidates(candidate_type="relation", limit=100)
    assert claims == []
    assert relations == []


@pytest.mark.no_db
def test_review_cli_file_backend_smoke(tmp_path):
    row = _v3_rows()["markdown"]
    input_path = tmp_path / "doc.txt"
    input_path.write_text(row["source_text"], encoding="utf-8")
    staging_dir = tmp_path / "staging"

    def run(*args):
        completed = subprocess.run(
            [sys.executable, "-m", "argus_neural.run_universal_review_cli",
             "--backend", "file", "--staging-dir", str(staging_dir), *args],
            capture_output=True, text=True, cwd=Path.cwd(),
        )
        return completed.returncode, json.loads(completed.stdout)

    code, staged = run("stage", str(input_path), "--model-dir", V3_MODEL_DIR)
    assert code == 0 and staged["ok"]
    code, listing = run("list", "--status", "pending", "--limit", "5")
    assert code == 0 and listing["count"] > 0
    cid = listing["candidates"][0]["staged_candidate_id"]
    code, shown = run("show", cid)
    assert code == 0
    assert ">>>" in shown["context_window"] and "<<<" in shown["context_window"]
    assert shown["span_valid"] is True
    code, approved = run("approve", cid, "--reason", "cli test", "--reviewer", "t")
    assert code == 0 and approved["event"]["new_review_status"] == "approved"
    code, promo = run("promote", cid)  # dry run by default
    assert code == 0
    assert promo["promotion"]["mode"] == "dry_run"
    code, bad = run("show", "not-a-real-id")
    assert code == 1 and bad["ok"] is False
    code, summary = run("export-review-summary")
    assert code == 0 and summary["summary"]["candidates"] > 0


# ---------------------------------------------------------------------------
# DB-backed tests (skip cleanly when Postgres is unavailable via conftest)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_store():
    store = PostgresStagingStore()  # conftest points DATABASE_URL at argus_test
    store.apply_schema()
    return store


def test_db_staging_write_and_provenance(db_store):
    row = _v3_rows()["markdown"]
    result = _v3_extractor().extract_document(row["source_text"], row["source_id"])
    write = db_store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    assert write.staged_candidates > 0
    cands = db_store.list_candidates(limit=1000)
    assert all(c["source_hash"] == row["source_hash"] for c in cands)
    assert all(c["review_status"] == "pending" for c in cands)
    segs = db_store.list_segments(write.staged_source_id)
    assert segs and all(0 <= s["start_char"] <= s["end_char"] for s in segs)
    # duplicate restage
    second = db_store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    assert second.staged_candidates == 0
    assert second.skipped_duplicates == write.staged_candidates


def test_db_review_and_guarded_promotion_and_rollback(db_store):
    import os

    dsn = os.environ["DATABASE_URL"]
    row = _v3_rows()["quote_reply"]
    result = _v3_extractor().extract_document(row["source_text"], row["source_id"])
    write = db_store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    claims = db_store.list_candidates(candidate_type="claim",
                                      staged_source_id=write.staged_source_id, limit=10)
    assert claims
    cid = claims[0]["staged_candidate_id"]

    refused = promote_candidate(db_store, cid, dry_run=False, dsn=dsn)
    assert refused.outcome == "refused"  # not approved yet

    db_store.apply_review_action(cid, "approve", reviewer="t", reason="ok")
    dry = promote_candidate(db_store, cid, dry_run=True, dsn=dsn)
    assert dry.outcome == "would_promote"
    assert db_store.get_candidate(cid)["promotion_status"] == "not_promoted"

    real = promote_candidate(db_store, cid, dry_run=False, reviewer="t",
                             reason="explicit", dsn=dsn)
    assert real.outcome == "promoted"
    assert real.promoted_object_type == "claim"
    cand = db_store.get_candidate(cid)
    assert cand["promotion_status"] == "promoted"

    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("select verification_state from claim_versions where tx_to is null")
            states = [r[0] for r in cur.fetchall()]
    assert states == ["unverified"]  # promotion never asserts truth

    again = promote_candidate(db_store, cid, dry_run=False, dsn=dsn)
    assert again.outcome == "refused"  # idempotence guard

    outcome = rollback_promotion(db_store, cid, reviewer="t", reason="test", dsn=dsn)
    assert outcome["core_action"] == "claim_verification_state_rejected"
    assert db_store.get_candidate(cid)["promotion_status"] == "rolled_back"
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("select verification_state from claim_versions where tx_to is null")
            states = [r[0] for r in cur.fetchall()]
    assert states == ["rejected"]
    actions = [e["action"] for e in db_store.list_review_events(cid)]
    assert actions == ["approve", "record_promoted", "request_rollback", "record_rolled_back"]


def test_db_supports_relation_fails_cleanly(db_store):
    import os

    dsn = os.environ["DATABASE_URL"]
    row = _v3_rows()["quote_reply"]
    result = _v3_extractor().extract_document(row["source_text"], row["source_id"])
    supports = [r for r in result.relations if r.relation_type == "supports"]
    if not supports:
        pytest.skip("fixture produced no supports relation")
    write = db_store.stage_extraction_result(result, row["source_text"], source_id=row["source_id"])
    rel = next(
        c for c in db_store.list_candidates(candidate_type="relation",
                                            staged_source_id=write.staged_source_id, limit=100)
        if c["payload"]["relation_type"] == "supports"
    )
    cid = rel["staged_candidate_id"]
    db_store.apply_review_action(cid, "approve", reviewer="t")
    result = promote_candidate(db_store, cid, dry_run=False, dsn=dsn)
    # V5 staged-only policy: not a failure — the candidate stays staged.
    assert result.outcome == "staged_only"
    assert "staged-only by policy" in result.refusal_reason
    assert db_store.get_candidate(cid)["promotion_status"] == "not_promoted"
    promos = db_store.list_promotions(cid)
    assert promos and promos[-1]["promotion_action"] == "staged_only"
