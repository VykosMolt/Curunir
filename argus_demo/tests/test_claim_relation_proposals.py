"""Claim-relation proposal + curated relation creation pass.

Read-only proposal generation over existing claims, then applying accepted
proposals via actions.create_claim_relation. Temporary fixtures only.
"""
from __future__ import annotations

from argus import actions, claim_relation_proposals as crp


def _doc(doc_id: str, published_at: str | None = None) -> str:
    source_id = actions.create_source(f"src-{doc_id}", "public_body")["source_id"]
    res = actions.ingest_text_document(
        f"text for {doc_id}", title=doc_id, source_id=source_id, published_at=published_at,
        metadata={"doc_id": doc_id, "manifest": {}},
    )
    return str(res["document_version_id"])


def _make_claim(dv, predicate, claim_text, subject_entity=None, object_entity=None,
                value=None, qualifiers=None) -> str:
    span = actions.create_evidence_span(dv, exact_quote=claim_text[:60] or "x")["evidence_span_id"]
    subj_mid = obj_mid = None
    if subject_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"subj-{predicate}-{claim_text[:6]}")["evidence_span_id"]
        subj_mid = actions.create_mention(dv, "subj", "regulator", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(subj_mid, subject_entity)
    if object_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"obj-{predicate}-{claim_text[:6]}")["evidence_span_id"]
        obj_mid = actions.create_mention(dv, "obj", "company", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(obj_mid, object_entity)
    res = actions.create_claim(
        claim_text, "enforcement_action", predicate, span,
        subject_mention_id=subj_mid, object_mention_id=obj_mid,
        value=value or {}, qualifiers=qualifiers or {},
    )
    return str(res["claim_id"])


def _supersedes_setup() -> tuple[str, str]:
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    dv22 = _doc("fr-cnil-2022-clearview-fine", "2022-10-17")
    dv23 = _doc("fr-cnil-2023-clearview-penalty", "2023-04-13")
    dst = _make_claim(dv22, "ordered_deletion", "CNIL ordered deletion of Clearview data.",
                      subject_entity=cnil)
    src = _make_claim(dv23, "reduced_fine", "The fine was reduced to 5200000 EUR.",
                      value={"amount": 5200000, "currency": "EUR"})
    return src, dst


def _rels(db_conn, relation):
    return [p for p in crp.propose_claim_relations(db_conn)["proposals"] if p["relation"] == relation]


# ---- 1: read-only -----------------------------------------------------------

def test_propose_is_read_only(db_conn):
    _supersedes_setup()
    payload = crp.propose_claim_relations(db_conn)
    assert payload["summary"]["total_relation_proposals"] >= 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 0


# ---- 2: supersedes ----------------------------------------------------------

def test_supersedes_proposed_for_later_modification(db_conn):
    src, dst = _supersedes_setup()
    sup = _rels(db_conn, "supersedes")
    assert len(sup) == 1
    assert sup[0]["src_claim_id"] == src and sup[0]["dst_claim_id"] == dst
    assert sup[0]["src_predicate"] == "reduced_fine" and sup[0]["dst_predicate"] == "ordered_deletion"


# ---- 3: duplicates ----------------------------------------------------------

def test_duplicates_proposed_for_same_action_different_docs(db_conn):
    hel = actions.create_entity("regulator", "Hellenic Data Protection Authority")["entity_id"]
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    a = _make_claim(_doc("el-hdpa-2022-clearview-edpb", "2022-07-13"), "issued_fine",
                    "Hellenic DPA fined Clearview 20M.", hel, cv, {"amount": 20000000, "currency": "EUR"})
    b = _make_claim(_doc("el-hdpa-2022-clearview-press", "2022-07-13"), "issued_fine",
                    "Hellenic DPA fined Clearview 20M.", hel, cv, {"amount": 20000000, "currency": "EUR"})
    dups = _rels(db_conn, "duplicates")
    assert len(dups) == 1
    assert {dups[0]["src_claim_id"], dups[0]["dst_claim_id"]} == {a, b}


# ---- 4: contradicts (same event, incompatible value) ------------------------

def test_contradicts_proposed_for_same_event_incompatible_value(db_conn):
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    a = _make_claim(_doc("fr-cnil-2022-a", "2022-10-17"), "issued_fine", "CNIL fined Clearview 20M.",
                    cnil, cv, {"amount": 20000000, "currency": "EUR"}, {"decision_number": "2024-17"})
    b = _make_claim(_doc("fr-cnil-2022-b", "2022-10-17"), "issued_fine", "CNIL fined Clearview 25M.",
                    cnil, cv, {"amount": 25000000, "currency": "EUR"}, {"decision_number": "2024-17"})
    cons = _rels(db_conn, "contradicts")
    assert len(cons) == 1
    assert {cons[0]["src_claim_id"], cons[0]["dst_claim_id"]} == {a, b}


# ---- 5: different regulators are NOT contradictions (the guardrail) ----------

def test_cross_regulator_amounts_not_contradiction(db_conn):
    hel = actions.create_entity("regulator", "Hellenic Data Protection Authority")["entity_id"]
    nl = actions.create_entity("regulator", "Autoriteit Persoonsgegevens")["entity_id"]
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    _make_claim(_doc("el-hdpa-2022-clearview-edpb", "2022-07-13"), "issued_fine",
                "Hellenic DPA fined Clearview 20M.", hel, cv, {"amount": 20000000, "currency": "EUR"})
    _make_claim(_doc("nl-ap-2024-clearview-press", "2024-09-03"), "issued_fine",
                "Dutch DPA fined Clearview 30.5M.", nl, cv, {"amount": 30500000, "currency": "EUR"})
    payload = crp.propose_claim_relations(db_conn)
    assert [p for p in payload["proposals"] if p["relation"] == "contradicts"] == []
    assert payload["summary"]["guarded_cross_regulator_amount_mismatches"] == 1


# ---- 6, 7: defaults + dedup -------------------------------------------------

def test_proposals_default_unaccepted(db_conn):
    _supersedes_setup()
    assert all(p["accepted"] is False for p in crp.propose_claim_relations(db_conn)["proposals"])


def test_dedup_no_duplicate_proposals(db_conn):
    hel = actions.create_entity("regulator", "Hellenic Data Protection Authority")["entity_id"]
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    _make_claim(_doc("el-hdpa-2022-clearview-edpb", "2022-07-13"), "issued_fine",
                "Hellenic DPA fined Clearview 20M.", hel, cv, {"amount": 20000000, "currency": "EUR"})
    _make_claim(_doc("el-hdpa-2022-clearview-press", "2022-07-13"), "issued_fine",
                "Hellenic DPA fined Clearview 20M.", hel, cv, {"amount": 20000000, "currency": "EUR"})
    props = crp.propose_claim_relations(db_conn)["proposals"]
    ids = [p["proposal_id"] for p in props]
    assert len(ids) == len(set(ids))
    assert sum(1 for p in props if p["relation"] == "duplicates") == 1  # not both directions


# ---- 8, 9, 10: apply / none / idempotent ------------------------------------

def test_apply_one_accepted_creates_relation(db_conn):
    src, dst = _supersedes_setup()
    p = _rels(db_conn, "supersedes")[0]
    report = crp.apply_claim_relation_proposals([dict(p, accepted=True)])
    assert report["claim_relations_created"] == 1
    assert report["summary"]["applied"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select relation, src_claim_id, dst_claim_id from claim_relations")
        r = cur.fetchone()
    assert r["relation"] == "supersedes"
    assert str(r["src_claim_id"]) == src and str(r["dst_claim_id"]) == dst


def test_apply_nothing_when_none_accepted(db_conn):
    _supersedes_setup()
    payload = crp.propose_claim_relations(db_conn)
    report = crp.apply_claim_relation_proposals(payload["proposals"])
    assert report["claim_relations_created"] == 0
    assert report["summary"]["skipped_not_accepted"] == len(payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 0


def test_reapply_is_idempotent(db_conn):
    _supersedes_setup()
    p = dict(_rels(db_conn, "supersedes")[0], accepted=True)
    first = crp.apply_claim_relation_proposals([p])
    assert first["claim_relations_created"] == 1
    second = crp.apply_claim_relation_proposals([p])
    assert second["claim_relations_created"] == 0
    assert second["summary"]["duplicate_existing"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 1


# ---- 11: apply creates no other objects -------------------------------------

def test_apply_creates_no_other_objects(db_conn):
    _supersedes_setup()
    p = dict(_rels(db_conn, "supersedes")[0], accepted=True)
    tables = ("claims", "claim_versions", "entities", "mentions", "evidence_spans")
    with db_conn.cursor() as cur:
        before = {}
        for t in tables:
            cur.execute(f"select count(*) as n from {t}")
            before[t] = cur.fetchone()["n"]
    crp.apply_claim_relation_proposals([p])
    with db_conn.cursor() as cur:
        for t, c0 in before.items():
            cur.execute(f"select count(*) as n from {t}")
            assert cur.fetchone()["n"] == c0, t
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 1


# ---- 12: invalid relation rejected ------------------------------------------

def test_invalid_relation_rejected(db_conn):
    _supersedes_setup()
    p = dict(_rels(db_conn, "supersedes")[0], accepted=True, relation="related_to")
    report = crp.apply_claim_relation_proposals([p])
    assert report["summary"]["error"] == 1
    assert report["claim_relations_created"] == 0
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 0


# ---- 13: relation report counts ---------------------------------------------

def test_relation_report_counts(db_conn):
    src, dst = _supersedes_setup()
    crp.apply_claim_relation_proposals([dict(_rels(db_conn, "supersedes")[0], accepted=True)])
    report = crp.build_relation_report(db_conn)
    assert report["total_claim_relations"] == 1
    assert report["relations_by_type"].get("supersedes") == 1
    assert "create_claim_relation" in report["action_log_counts"]
    assert report["claims_with_outgoing_relations"].get(src) == 1
    assert report["claims_with_incoming_relations"].get(dst) == 1
