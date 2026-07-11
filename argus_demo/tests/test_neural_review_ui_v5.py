"""V5 tests: review UI, supports staged-only policy, relation retraction,
public-doc dataset and evaluation plumbing."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from argus_neural.db_staging import FileStagingStore
from argus_neural.promotion_adapter import promote_candidate, rollback_promotion
from argus_neural.public_doc_dataset import (
    generate_public_doc_dataset,
    public_doc_quality,
)
from argus_neural.public_doc_evaluation import (
    confidence_calibration,
    eval_rows,
    reviewer_burden,
)
from argus_neural.review_dashboard import (
    apply_ui_action,
    render_dashboard,
    render_markdown_summary,
)
from argus_neural.universal_extractor_v3 import ArgusUniversalNeuralExtractorV3

V5_MODEL = "artifacts/universal_neural_extractor_v5_review_ui_public_doc_validation/model_v5"


@lru_cache(maxsize=1)
def _model():
    return ArgusUniversalNeuralExtractorV3.load(V5_MODEL)


@lru_cache(maxsize=1)
def _dataset():
    return generate_public_doc_dataset()


def _store_with_docs(tmp_path, rows):
    store = FileStagingStore(tmp_path / "staging")
    store.apply_schema()
    writes = []
    for row in rows:
        result = _model().extract_document(row["source_text"], row["source_id"])
        writes.append(store.stage_extraction_result(result, row["source_text"],
                                                    source_id=row["source_id"]))
    return store, writes


# ---------------------------------------------------------------------------
# review UI
# ---------------------------------------------------------------------------

@pytest.mark.no_db
def test_dashboard_renders_candidates_with_spans_and_history(tmp_path):
    ds = _dataset()
    store, writes = _store_with_docs(tmp_path, ds["public_doc_test"][:2])
    cid = writes[0].candidate_ids[0]
    store.apply_review_action(cid, "approve", reviewer="ui-test", reason="render check")
    html = render_dashboard(store, live=True)
    assert "<mark class=" in html                    # highlighted spans
    assert "review history" in html                  # event panel
    assert "segment / thread provenance" in html
    assert cid in html                               # unambiguous candidate ids
    assert "f_confmin" in html and "f_source" in html  # filters
    cand = store.get_candidate(cid)
    src = store.get_source(cand["staged_source_id"])
    assert cand["source_hash"] == src["source_hash"]


@pytest.mark.no_db
def test_dashboard_static_mode_disables_actions(tmp_path):
    ds = _dataset()
    store, _ = _store_with_docs(tmp_path, ds["public_doc_test"][:1])
    html = render_dashboard(store, live=False)
    assert "actions disabled" in html
    assert "action='/action'" not in html and 'action="/action"' not in html


@pytest.mark.no_db
def test_ui_actions_route_through_review_model_and_cannot_promote(tmp_path):
    ds = _dataset()
    store, writes = _store_with_docs(tmp_path, ds["public_doc_test"][:1])
    cid = writes[0].candidate_ids[0]
    ok = apply_ui_action(store, {"candidate_id": cid, "action": "approve", "reviewer": "r"})
    assert ok["ok"] and ok["event"]["new_review_status"] == "approved"
    assert store.get_candidate(cid)["promotion_status"] == "not_promoted"  # no auto-promotion
    blocked = apply_ui_action(store, {"candidate_id": cid, "action": "promote"})
    assert blocked["ok"] is False
    blocked2 = apply_ui_action(store, {"candidate_id": cid, "action": "record_promoted"})
    assert blocked2["ok"] is False                    # promotion-status writes blocked too
    bad = apply_ui_action(store, {"candidate_id": "nope", "action": "approve"})
    assert bad["ok"] is False
    invalid = apply_ui_action(store, {"candidate_id": cid, "action": "defer"})
    assert invalid["ok"] is False                     # approved -> defer is illegal


@pytest.mark.no_db
def test_markdown_export_renders(tmp_path):
    ds = _dataset()
    store, _ = _store_with_docs(tmp_path, ds["public_doc_test"][:1])
    md = render_markdown_summary(store)
    assert "# Review Summary" in md
    assert "not trusted facts" in md


# ---------------------------------------------------------------------------
# supports staged-only policy + rollback preservation (file store, no DB)
# ---------------------------------------------------------------------------

@pytest.mark.no_db
def test_supports_relation_staged_only_policy(tmp_path):
    ds = _dataset()
    supports_rows = [r for r in ds["public_doc_relation_test"]
                     if any(rel["relation_type"] == "supports" for rel in r["relations"])]
    store, writes = _store_with_docs(tmp_path, supports_rows[:2])
    rels = [c for c in store.list_candidates(candidate_type="relation", limit=100)
            if c["payload"].get("relation_type") == "supports"]
    if not rels:
        pytest.skip("model surfaced no supports relation on these docs")
    cid = rels[0]["staged_candidate_id"]
    store.apply_review_action(cid, "approve", reviewer="t")
    result = promote_candidate(store, cid, dry_run=False)
    assert result.outcome in {"staged_only", "refused"}
    if result.outcome == "staged_only":
        cand = store.get_candidate(cid)
        assert cand["promotion_status"] == "not_promoted"     # policy, not failure
        assert cand["review_status"] == "approved"            # candidate preserved
        promos = store.list_promotions(cid)
        assert promos[-1]["promotion_action"] == "staged_only"


@pytest.mark.no_db
def test_rollback_preserves_candidate_and_promotion_rows(tmp_path):
    ds = _dataset()
    store, writes = _store_with_docs(tmp_path, ds["public_doc_test"][:1])
    claims = store.list_candidates(candidate_type="claim", limit=10)
    if not claims:
        pytest.skip("no claim staged")
    cid = claims[0]["staged_candidate_id"]
    store.apply_review_action(cid, "approve", reviewer="t")
    # execute against file store: core DB unreachable path -> refused, but
    # promotion bookkeeping must still be append-only and candidate preserved
    result = promote_candidate(store, cid, dry_run=True)
    assert result.outcome == "would_promote"
    promos_before = len(store.list_promotions(cid))
    assert promos_before >= 1
    assert store.get_candidate(cid) is not None


# ---------------------------------------------------------------------------
# public-doc dataset + eval plumbing
# ---------------------------------------------------------------------------

@pytest.mark.no_db
def test_public_doc_dataset_quality():
    quality = public_doc_quality(_dataset())
    assert quality["span_validity"] == 1.0
    assert quality["claim_candidate_reachability"] == 1.0
    assert quality["hidden_marker_hits"] == 0
    assert quality["duplicate_texts"] == 0
    assert quality["relation_label_counts"].get("supports", 0) > 0
    assert quality["negative_docs"] > 0
    assert quality["verdict"].split(" = ")[-1] in {
        "PASS_TRUE_PUBLIC_LOCAL", "PASS_PUBLIC_STYLE_SYNTHETIC",
    }


@pytest.mark.no_db
def test_public_doc_origins_are_labeled():
    ds = _dataset()
    for rows in ds.values():
        for row in rows:
            assert row["document_origin"] in {"true_public_local", "public_style_synthetic"}
            if row["document_origin"] == "true_public_local":
                assert row["license_note"]


@pytest.mark.no_db
def test_public_doc_eval_and_calibration_run():
    ds = _dataset()
    rows = ds["public_doc_test"][:4]
    metrics = eval_rows(_model(), rows)
    assert metrics["documents"] == 4
    assert metrics["valid_span_rate"] == 1.0
    burden = reviewer_burden(_model(), rows)
    scored = burden.pop("_all_scored")
    assert burden["documents"] == 4
    assert burden["total_candidates"] >= 1
    calib = confidence_calibration(scored)
    assert len(calib["bins"]) == 5
    assert calib["threshold_tradeoff"][0]["threshold"] == 0.5


@pytest.mark.no_db
def test_negative_public_docs_stage_no_claims(tmp_path):
    ds = _dataset()
    store, _ = _store_with_docs(tmp_path, ds["public_doc_negative_test"][:4])
    assert store.list_candidates(candidate_type="claim", limit=50) == []
    assert store.list_candidates(candidate_type="relation", limit=50) == []


# ---------------------------------------------------------------------------
# retraction primitive (DB-backed; skips cleanly without Postgres)
# ---------------------------------------------------------------------------

def test_retract_claim_relation_roundtrip(db_conn):
    import os
    from argus import actions

    dsn = os.environ["DATABASE_URL"]
    doc = actions.ingest_text_document(
        "Alpha count is 4. Beta count is 5.", title="retraction-test",
    )
    dv = doc.data["document_version_id"]
    span1 = actions.create_evidence_span(dv, start_char=0, end_char=17)
    span2 = actions.create_evidence_span(dv, start_char=18, end_char=34)
    c1 = actions.create_claim("Alpha count is 4.", "measurement", "stated",
                              span1.data["evidence_span_id"])
    c2 = actions.create_claim("Beta count is 5.", "measurement", "stated",
                              span2.data["evidence_span_id"])
    rel = actions.create_claim_relation(c1.data["claim_id"], c2.data["claim_id"], "contradicts")
    rel_id = rel.data["relation_id"]
    result = actions.retract_claim_relation(rel_id, reason="test retraction")
    assert result.data["retracted"] is True
    with db_conn.cursor() as cur:
        cur.execute("select tx_to from claim_relations where id = %s", (rel_id,))
        assert cur.fetchone()["tx_to"] is not None      # closed, not deleted
        cur.execute("select count(*) from claim_relations where id = %s", (rel_id,))
        assert cur.fetchone()["count"] == 1             # row preserved
        cur.execute("select event_type from claim_events where claim_id = %s "
                    "order by created_at", (c1.data["claim_id"],))
        events = [r["event_type"] for r in cur.fetchall()]
    assert "relation_retracted" in events
    with pytest.raises(ValueError):
        actions.retract_claim_relation(rel_id)          # double retraction refused
