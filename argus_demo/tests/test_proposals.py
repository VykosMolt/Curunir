"""Proposal/curation pass: read-only proposal generation + applying accepted
proposals through the action handlers (evidence_spans + mentions only).

Uses a temporary fixture document, never the real 20-doc corpus.
"""
from __future__ import annotations

from argus import actions, proposals

# Fixture text packed with the corpus's hard surfaces.
FIX = (
    "On 2022-07-13, the Hellenic Data Protection Authority (Hellenic DPA) and the "
    "CNIL examined Clearview AI, Inc. The Garante had already fined Clearview AI "
    "EUR 20 million. CLEARVIEW AI scrapes facial recognition data in breach of "
    "Article 9 GDPR and Article 6. See Decision 35/2022 and case C-634/21. "
    "Foodinho S.r.l. used profiling and automated decision-making. The total fine "
    "was €20,000,000."
)


def _ingest(text: str = FIX, doc_id: str = "fix-1") -> str:
    source_id = actions.create_source("Test Authority", "regulator")["source_id"]
    res = actions.ingest_text_document(
        text, title="Fixture Doc", source_id=source_id,
        metadata={"doc_id": doc_id, "topic_tags": [], "notes": None,
                  "manifest": {"local_path": "fix.txt", "jurisdiction": "EU"}},
    )
    return str(res["document_version_id"])


# ---- 1, 6: generation is read-only; everything defaults accepted:false -------

def test_propose_corpus_is_read_only_and_defaults_unaccepted(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    assert payload["summary"]["total_proposals"] > 0
    assert all(p["accepted"] is False for p in payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from evidence_spans")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] == 0


# ---- 2: proposal output carries the required fields -------------------------

def test_proposal_fields_present():
    props = proposals.propose_for_text(FIX, "00000000-0000-0000-0000-000000000000")
    p = props[0].as_dict()
    for key in ("proposal_id", "document_version_id", "mention_type", "surface_text",
                "normalized_text", "start_char", "end_char", "exact_quote",
                "prefix_context", "suffix_context", "heuristic_name", "accepted"):
        assert key in p
    assert p["accepted"] is False
    assert FIX[p["start_char"]:p["end_char"]] == p["exact_quote"] == p["surface_text"]


# ---- 3: Clearview variants -------------------------------------------------

def test_clearview_variants_proposed():
    surfaces = {p.surface_text for p in proposals.propose_for_text(FIX, "dv")}
    assert "Clearview AI, Inc." in surfaces
    assert "Clearview AI" in surfaces
    assert "CLEARVIEW AI" in surfaces


# ---- 4: decision / case / legal-article / money / date ----------------------

def test_pattern_types_proposed():
    by_type: dict[str, set[str]] = {}
    for p in proposals.propose_for_text(FIX, "dv"):
        by_type.setdefault(p.mention_type, set()).add(p.surface_text)
    assert "Decision 35/2022" in by_type["decision_number"]
    assert "C-634/21" in by_type["case_number"]
    assert any(s.startswith("Article") for s in by_type["legal_article"])
    assert "GDPR" in by_type["law"]
    assert by_type.get("money")
    assert "2022-07-13" in by_type["date"]
    assert any(s.lower() == "facial recognition" for s in by_type["ai_system_or_tool"])


# ---- 5: deduplication -------------------------------------------------------

def test_duplicate_proposals_deduplicated():
    props = proposals.propose_for_text("Foodinho S.r.l. was fined.", "dv")
    keys = [(p.mention_type, p.start_char, p.end_char, p.surface_text) for p in props]
    assert len(keys) == len(set(keys)), "exact (type,start,end,surface) duplicates present"
    # "Foodinho S.r.l." matches both the foodinho and generic-suffix company
    # patterns on the same span -> must collapse to exactly one proposal.
    same = [p for p in props if p.mention_type == "company" and p.surface_text == "Foodinho S.r.l."]
    assert len(same) == 1


# ---- 7: applying one accepted proposal -> one span + one mention ------------

def test_apply_one_accepted_creates_one_span_and_mention(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    chosen = dict(next(p for p in payload["proposals"] if p["surface_text"] == "Clearview AI, Inc."),
                  accepted=True)
    report = proposals.apply_proposals([chosen])
    assert report["summary"]["applied"] == 1
    assert report["evidence_spans_created"] == 1
    assert report["mentions_created"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from evidence_spans")
        assert cur.fetchone()["n"] == 1
        cur.execute("select surface_text, mention_type, evidence_span_id from mentions")
        m = cur.fetchone()
    assert m["surface_text"] == "Clearview AI, Inc."
    assert m["mention_type"] == "company"
    assert m["evidence_span_id"] is not None


# ---- 8: applying with none accepted creates nothing -------------------------

def test_apply_nothing_when_none_accepted(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    report = proposals.apply_proposals(payload["proposals"])  # all accepted:false
    assert report["summary"]["applied"] == 0
    assert report["summary"]["skipped_not_accepted"] == len(payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from evidence_spans")
        assert cur.fetchone()["n"] == 0


# ---- 9: re-applying is idempotent ------------------------------------------

def test_reapply_does_not_duplicate(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    chosen = dict(next(p for p in payload["proposals"] if p["surface_text"] == "CNIL"), accepted=True)
    first = proposals.apply_proposals([chosen])
    assert first["summary"]["applied"] == 1
    second = proposals.apply_proposals([chosen])
    assert second["summary"]["applied"] == 0
    assert second["summary"]["duplicate_existing"] == 1
    assert second["evidence_spans_created"] == 0
    assert second["mentions_created"] == 0
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] == 1
        cur.execute("select count(*) as n from evidence_spans")
        assert cur.fetchone()["n"] == 1


# ---- 10: applying never creates entities/claims/relations -------------------

def test_apply_creates_no_entities_claims_relations(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    chosen = [dict(p, accepted=True) for p in payload["proposals"][:5]]
    proposals.apply_proposals(chosen)
    with db_conn.cursor() as cur:
        for table in ("entities", "claims", "claim_versions", "claim_relations"):
            cur.execute(f"select count(*) as n from {table}")
            assert cur.fetchone()["n"] == 0, table
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] >= 1


# ---- 11: mention-report counts ---------------------------------------------

def test_mention_report_counts(db_conn):
    _ingest()
    payload = proposals.propose_corpus(db_conn)
    wanted = {"Clearview AI, Inc.", "CNIL", "2022-07-13"}
    chosen = [dict(p, accepted=True) for p in payload["proposals"] if p["surface_text"] in wanted]
    proposals.apply_proposals(chosen)
    report = proposals.build_mention_report(db_conn)
    assert report["total_mentions"] == len(chosen)
    assert report["total_evidence_spans"] == len(chosen)
    assert report["mentions_by_type"].get("company", 0) >= 1
    assert "create_mention" in report["action_log_counts"]
    assert any("clearview" in s.lower() for s in report["clearview_variant_surfaces"])


# ---- French SA / CNIL anchoring patterns ------------------------------------

def test_french_sa_proposed_as_regulator():
    text = "On 13 April 2023, the French SA imposed a penalty payment on Clearview AI."
    props = proposals.propose_for_text(text, "dv")
    assert any(p.surface_text == "French SA" and p.mention_type == "regulator" for p in props)


def test_restricted_committee_only_with_french_cnil_context():
    with_ctx = "The French SA's restricted committee imposed a penalty payment."
    props = proposals.propose_for_text(with_ctx, "dv")
    assert any(p.surface_text == "restricted committee" and p.mention_type == "regulator"
               for p in props)
    # without CNIL/French context, "restricted committee" is not a regulator mention
    no_ctx = "The school board's restricted committee reviewed the cafeteria budget."
    props2 = proposals.propose_for_text(no_ctx, "dv")
    assert not any(p.surface_text == "restricted committee" for p in props2)


def test_ftt_acronym_and_full_name_proposed_as_court():
    text = "The First-tier Tribunal (FTT) allowed the appeal. The FTT decision was reversed by the UKUT."
    props = proposals.propose_for_text(text, "dv")
    surfaces = {(p.surface_text, p.mention_type) for p in props}
    assert ("FTT", "court") in surfaces
    assert ("First-tier Tribunal", "court") in surfaces
    assert ("UKUT", "court") in surfaces


def test_bare_sa_and_generic_committee_not_regulator():
    text = "The SA met today. The committee approved it. The audit committee filed a report."
    regulator_surfaces = {p.surface_text for p in proposals.propose_for_text(text, "dv")
                          if p.mention_type == "regulator"}
    assert "SA" not in regulator_surfaces
    assert "committee" not in regulator_surfaces
    assert "audit committee" not in regulator_surfaces
