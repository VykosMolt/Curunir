"""Proof 7: merging / reassigning entities re-derives the subject/object entity
cache on every current claim version anchored to the affected mentions (and
leaves unaffected claims alone)."""
from __future__ import annotations

from pathlib import Path

from argus import actions

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _claim_anchored_to_object_mention():
    """Ingest doc A, make an object mention 'Northwind Analytics Ltd', and a claim
    whose object is that mention (no entity assigned yet)."""
    text = (FIXTURES / "doc_a.txt").read_text(encoding="utf-8")
    dv = actions.ingest_text_document(text, title="A")["document_version_id"]
    span = actions.create_evidence_span(dv, exact_quote="Northwind Analytics Ltd")["evidence_span_id"]
    obj = actions.create_mention(dv, "Northwind Analytics Ltd", "organization",
                                 evidence_span_id=span)["mention_id"]
    claim = actions.create_claim("c", "enforcement", "issued_fine", span, object_mention_id=obj)
    return dv, span, obj, claim


def _object_entity(db_conn, claim_id):
    with db_conn.cursor() as cur:
        cur.execute(
            "select object_entity_id from claim_versions where claim_id = %s and tx_to is null",
            (claim_id,),
        )
        return cur.fetchone()["object_entity_id"]


def _version_count(db_conn, claim_id) -> int:
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_versions where claim_id = %s", (claim_id,))
        return cur.fetchone()["n"]


def test_assignment_rederives_claim_object_entity(db_conn):
    _, _, obj, claim = _claim_anchored_to_object_mention()
    assert _object_entity(db_conn, claim["claim_id"]) is None  # unresolved at creation

    entity = actions.create_entity("organization", "Northwind Analytics Ltd")["entity_id"]
    res = actions.assign_mention_to_entity(obj, entity)

    assert claim["claim_id"] in res["rederived_claim_ids"]
    assert _object_entity(db_conn, claim["claim_id"]) == entity
    assert _version_count(db_conn, claim["claim_id"]) == 2  # original + re-derived


def test_merge_rederives_claim_entity_cache(db_conn):
    _, _, obj, claim = _claim_anchored_to_object_mention()
    canonical = actions.create_entity("organization", "Northwind Analytics Ltd")["entity_id"]
    dupe = actions.create_entity("organization", "Northwind Analytics")["entity_id"]

    actions.assign_mention_to_entity(obj, dupe)
    assert _object_entity(db_conn, claim["claim_id"]) == dupe

    merge = actions.merge_entities([dupe], canonical)
    assert claim["claim_id"] in merge["rederived_claim_ids"]
    assert _object_entity(db_conn, claim["claim_id"]) == canonical

    with db_conn.cursor() as cur:
        cur.execute("select status from entities where id = %s", (dupe,))
        assert cur.fetchone()["status"] == "merged"
        cur.execute("select entity_id from entity_mentions where mention_id = %s and active", (obj,))
        assert cur.fetchone()["entity_id"] == canonical
        cur.execute(
            "select 1 from claim_events where claim_id = %s and event_type = 'entity_rederived'",
            (claim["claim_id"],),
        )
        assert cur.fetchone() is not None


def test_merge_leaves_unaffected_claims_untouched(db_conn):
    dv, span, obj, _ = _claim_anchored_to_object_mention()

    other_entity = actions.create_entity("organization", "Unrelated Org")["entity_id"]
    other_mention = actions.create_mention(dv, "Unrelated Org", "organization",
                                           evidence_span_id=span)["mention_id"]
    actions.assign_mention_to_entity(other_mention, other_entity)
    other_claim = actions.create_claim("o", "t", "p", span, object_mention_id=other_mention)

    canonical = actions.create_entity("organization", "Northwind Analytics Ltd")["entity_id"]
    dupe = actions.create_entity("organization", "Northwind Analytics")["entity_id"]
    actions.assign_mention_to_entity(obj, dupe)

    before = _version_count(db_conn, other_claim["claim_id"])
    merge = actions.merge_entities([dupe], canonical)

    assert other_claim["claim_id"] not in merge["rederived_claim_ids"]
    assert _version_count(db_conn, other_claim["claim_id"]) == before


def test_one_active_assignment_per_mention(db_conn):
    _, _, obj, _ = _claim_anchored_to_object_mention()
    a = actions.create_entity("organization", "A")["entity_id"]
    b = actions.create_entity("organization", "B")["entity_id"]

    actions.assign_mention_to_entity(obj, a)
    actions.assign_mention_to_entity(obj, b)  # reassign

    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entity_mentions where mention_id = %s and active", (obj,))
        assert cur.fetchone()["n"] == 1
        cur.execute("select entity_id from entity_mentions where mention_id = %s and active", (obj,))
        assert cur.fetchone()["entity_id"] == b
        cur.execute("select count(*) as n from entity_mentions where mention_id = %s", (obj,))
        assert cur.fetchone()["n"] == 2  # one closed, one active


def test_unmerge_reassigns_and_rederives(db_conn):
    _, _, obj, claim = _claim_anchored_to_object_mention()
    a = actions.create_entity("organization", "A")["entity_id"]
    b = actions.create_entity("organization", "B")["entity_id"]

    actions.assign_mention_to_entity(obj, a)
    assert _object_entity(db_conn, claim["claim_id"]) == a

    res = actions.unmerge_entity_assignment(obj, b, reason="split")
    assert claim["claim_id"] in res["rederived_claim_ids"]
    assert _object_entity(db_conn, claim["claim_id"]) == b

    with db_conn.cursor() as cur:
        cur.execute("select 1 from entity_resolution_events where event_type = 'split'")
        assert cur.fetchone() is not None
