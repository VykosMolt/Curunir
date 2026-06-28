"""Proofs 2-6 and 8: ingest, evidence spans, claim creation/anchoring,
verification-state versioning, relation enums, and the writes-only-in-actions
invariant.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

import psycopg
import pytest

from argus import actions

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _doc_a() -> str:
    return (FIXTURES / "doc_a.txt").read_text(encoding="utf-8")


def _ingest_a():
    text = _doc_a()
    return text, actions.ingest_text_document(text, title="Decision 2024-17")


def _span(dv, quote="quote"):
    return actions.create_evidence_span(dv, exact_quote=quote)["evidence_span_id"]


# ---- proof 2: ingest ---------------------------------------------------------

def test_ingest_creates_document_and_version_with_hash(db_conn):
    text, res = _ingest_a()
    with db_conn.cursor() as cur:
        cur.execute("select * from documents where id = %s", (res["document_id"],))
        doc = cur.fetchone()
        cur.execute("select * from document_versions where id = %s", (res["document_version_id"],))
        dv = cur.fetchone()
    assert doc["title"] == "Decision 2024-17"
    assert dv["raw_hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert dv["raw_hash"] == res["raw_hash"]
    assert dv["extracted_text"] == text


# ---- proof 3: evidence spans -------------------------------------------------

def test_create_span_stores_exact_quote_and_context(db_conn):
    text, res = _ingest_a()
    dv = res["document_version_id"]
    needle = "administrative fine of €4,500,000"
    start = text.find(needle)
    assert start >= 0
    span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))
    with db_conn.cursor() as cur:
        cur.execute("select * from evidence_spans where id = %s", (span["evidence_span_id"],))
        row = cur.fetchone()
    assert row["exact_quote"] == needle
    assert row["quote_hash"] == hashlib.sha256(needle.encode("utf-8")).hexdigest()
    assert row["prefix_context"] and row["prefix_context"] in text
    assert row["suffix_context"] is not None
    assert span["exact_quote"] == needle


def test_span_offsets_out_of_range_raise(db_conn):
    _, res = _ingest_a()
    with pytest.raises(ValueError):
        actions.create_evidence_span(res["document_version_id"], start_char=0, end_char=10**9)


# ---- proof 4: claims require span, can anchor a mention ----------------------

def test_create_claim_requires_span_and_anchors_mention(db_conn):
    text, res = _ingest_a()
    dv = res["document_version_id"]
    needle = "The Authority imposed an administrative fine of €4,500,000 on Northwind Analytics Ltd."
    start = text.find(needle)
    span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
    subj = actions.create_mention(dv, "Example Data Protection Authority", "organization",
                                  evidence_span_id=span)["mention_id"]
    claim = actions.create_claim("EDPA fined Northwind.", "enforcement", "issued_fine", span,
                                 subject_mention_id=subj)
    with db_conn.cursor() as cur:
        cur.execute("select * from claim_versions where claim_id = %s and tx_to is null",
                    (claim["claim_id"],))
        cv = cur.fetchone()
    assert cv["evidence_span_id"] == span
    assert cv["subject_mention_id"] == subj
    assert cv["verification_state"] == "unverified"


def test_create_claim_with_nonexistent_span_raises(db_conn):
    with pytest.raises(ValueError):
        actions.create_claim("x", "t", "p", uuid.uuid4())


def test_create_claim_cannot_be_born_human_verified(db_conn):
    _, res = _ingest_a()
    span = _span(res["document_version_id"])
    with pytest.raises(ValueError):
        actions.create_claim("x", "t", "p", span, verification_state="human_verified")


# ---- proof 5: verification state change => new version + event ---------------

def test_verification_change_creates_new_version_and_event(db_conn):
    _, res = _ingest_a()
    span = _span(res["document_version_id"])
    claim = actions.create_claim("c", "enforcement", "issued_fine", span)
    cid, v1 = claim["claim_id"], claim["version_id"]

    upd = actions.set_claim_verification_state(cid, "human_verified", reason="reviewed")

    with db_conn.cursor() as cur:
        cur.execute("select * from claim_versions where claim_id = %s", (cid,))
        all_versions = cur.fetchall()
        cur.execute("select * from claim_versions where claim_id = %s and tx_to is null", (cid,))
        current = cur.fetchall()
        cur.execute("select * from claim_events where claim_id = %s", (cid,))
        events = cur.fetchall()

    assert len(all_versions) == 2, "old version kept, new version added"
    assert len(current) == 1, "exactly one current version"
    assert current[0]["verification_state"] == "human_verified"

    old = next(v for v in all_versions if v["version_id"] == v1)
    assert old["tx_to"] is not None, "old version was closed with tx_to"

    change_events = [e for e in events if e["event_type"] == "verification_state_change"]
    assert len(change_events) == 1
    ev = change_events[0]
    assert ev["previous_state"] == "unverified"
    assert ev["new_state"] == "human_verified"
    assert ev["action_log_id"] == upd.action_log_id
    assert upd["changed"] is True


def test_verification_change_noop_when_same_state(db_conn):
    _, res = _ingest_a()
    span = _span(res["document_version_id"])
    claim = actions.create_claim("c", "t", "p", span)
    upd = actions.set_claim_verification_state(claim["claim_id"], "unverified")
    assert upd["changed"] is False
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_versions where claim_id = %s", (claim["claim_id"],))
        assert cur.fetchone()["n"] == 1


# ---- proof 6: relation enums -------------------------------------------------

def test_claim_relations_accept_only_the_three_relations(db_conn):
    _, res = _ingest_a()
    span = _span(res["document_version_id"])
    c1 = actions.create_claim("a", "t", "p", span)["claim_id"]
    c2 = actions.create_claim("b", "t", "p", span)["claim_id"]
    for good in ("contradicts", "supersedes", "duplicates"):
        actions.create_claim_relation(c1, c2, good)
    with pytest.raises(ValueError):
        actions.create_claim_relation(c1, c2, "related_to")
    with pytest.raises(ValueError):
        actions.create_claim_relation(c1, c1, "contradicts")  # self relation


def test_relation_check_constraint_blocks_direct_bad_insert(db_conn):
    _, res = _ingest_a()
    span = _span(res["document_version_id"])
    c1 = actions.create_claim("a", "t", "p", span)["claim_id"]
    c2 = actions.create_claim("b", "t", "p", span)["claim_id"]
    with db_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "insert into claim_relations (src_claim_id, dst_claim_id, relation) "
                "values (%s, %s, 'bogus')",
                (c1, c2),
            )


# ---- proof 8: writes only live in actions.py --------------------------------

_WRITE_RE = re.compile(r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from)\b", re.IGNORECASE)


def test_writes_only_in_actions_module():
    pkg = Path(actions.__file__).resolve().parent
    for modname in ("db.py", "export.py", "cli.py", "schemas.py", "extract.py",
                    "corpus.py", "proposals.py", "entity_proposals.py",
                    "claim_proposals.py", "claim_relation_proposals.py",
                    "case_assembly.py", "__init__.py"):
        src = (pkg / modname).read_text(encoding="utf-8")
        match = _WRITE_RE.search(src)
        assert match is None, f"{modname} contains a SQL write ({match.group(0)!r}); writes belong in actions.py"
    actions_src = (pkg / "actions.py").read_text(encoding="utf-8").lower()
    assert "insert into action_log" in actions_src


def test_each_action_appends_exactly_one_action_log_row(db_conn):
    def count() -> int:
        with db_conn.cursor() as cur:
            cur.execute("select count(*) as n from action_log")
            return cur.fetchone()["n"]

    before = count()
    actions.create_source("Some Authority", "regulator")
    assert count() == before + 1


# ---- patch 3: evidence-span quote anchoring ---------------------------------

def test_offset_span_has_offset_anchoring_status(db_conn):
    text, res = _ingest_a()
    needle = "administrative fine of €4,500,000"
    start = text.find(needle)
    span = actions.create_evidence_span(res["document_version_id"], start_char=start,
                                        end_char=start + len(needle))
    assert span["anchoring_status"] == "offset"
    assert span["anchoring_note"] is None
    with db_conn.cursor() as cur:
        cur.execute("select anchoring_status from evidence_spans where id = %s",
                    (span["evidence_span_id"],))
        assert cur.fetchone()["anchoring_status"] == "offset"


def test_quote_only_span_found_fills_offsets_and_context(db_conn):
    text, res = _ingest_a()
    needle = "imposed an administrative fine of €4,500,000"
    assert text.count(needle) == 1
    span = actions.create_evidence_span(res["document_version_id"], exact_quote=needle)
    assert span["anchoring_status"] == "quote_found"
    assert span["start_char"] == text.find(needle)
    assert span["end_char"] == text.find(needle) + len(needle)
    with db_conn.cursor() as cur:
        cur.execute("select * from evidence_spans where id = %s", (span["evidence_span_id"],))
        row = cur.fetchone()
    assert row["start_char"] is not None and row["end_char"] is not None
    assert row["exact_quote"] == needle
    assert row["prefix_context"] and row["prefix_context"] in text
    assert row["suffix_context"] is not None
    assert row["anchoring_status"] == "quote_found"


def test_quote_only_span_not_found_does_not_crash(db_conn):
    text, res = _ingest_a()
    missing = "this exact phrase is definitely not present in the document"
    assert missing not in text
    span = actions.create_evidence_span(res["document_version_id"], exact_quote=missing)
    assert span["anchoring_status"] == "quote_not_found"
    assert span["start_char"] is None and span["end_char"] is None
    with db_conn.cursor() as cur:
        cur.execute("select * from evidence_spans where id = %s", (span["evidence_span_id"],))
        row = cur.fetchone()
    assert row["start_char"] is None and row["end_char"] is None
    assert row["exact_quote"] == missing
    assert row["quote_hash"] == hashlib.sha256(missing.encode("utf-8")).hexdigest()
    assert row["anchoring_status"] == "quote_not_found"
    assert row["anchoring_note"]


def test_quote_with_mismatched_offsets_raises(db_conn):
    _, res = _ingest_a()
    with pytest.raises(ValueError):
        actions.create_evidence_span(res["document_version_id"], start_char=0, end_char=5,
                                     exact_quote="Northwind")


def test_quote_with_matching_offsets_is_offset_anchored(db_conn):
    text, res = _ingest_a()
    needle = "Northwind Analytics Ltd"
    start = text.find(needle)
    span = actions.create_evidence_span(res["document_version_id"], start_char=start,
                                        end_char=start + len(needle), exact_quote=needle)
    assert span["anchoring_status"] == "offset"
    assert span["exact_quote"] == needle


def test_anchoring_status_is_recorded_in_action_log(db_conn):
    _, res = _ingest_a()
    span = actions.create_evidence_span(res["document_version_id"], exact_quote="absent phrase zzz")
    with db_conn.cursor() as cur:
        cur.execute("select result_json from action_log where id = %s", (span.action_log_id,))
        result = cur.fetchone()["result_json"]
    assert result["anchoring_status"] == "quote_not_found"
    assert result["anchoring_note"]


# ---- patch 1: case-item id validation ---------------------------------------

def test_add_case_item_rejects_document_version_id_as_document(db_conn):
    _, res = _ingest_a()
    case_id = actions.create_case("c")["case_id"]
    # a document_version id is NOT a documents.id -> must be rejected
    with pytest.raises(ValueError):
        actions.add_case_item(case_id, "document", item_id=res["document_version_id"])
    # the logical document id is accepted
    actions.add_case_item(case_id, "document", item_id=res["document_id"])


def test_add_case_item_rejects_unknown_typed_ids(db_conn):
    case_id = actions.create_case("c")["case_id"]
    for item_type in ("document", "claim", "entity", "relation"):
        with pytest.raises(ValueError):
            actions.add_case_item(case_id, item_type, item_id=uuid.uuid4())


def test_add_case_item_note_needs_no_item_id(db_conn):
    case_id = actions.create_case("c")["case_id"]
    res = actions.add_case_item(case_id, "note", note="freeform note")
    with db_conn.cursor() as cur:
        cur.execute("select item_type, item_id, note from case_items where id = %s",
                    (res["case_item_id"],))
        row = cur.fetchone()
    assert row["item_type"] == "note"
    assert row["item_id"] is None
    assert row["note"] == "freeform note"
