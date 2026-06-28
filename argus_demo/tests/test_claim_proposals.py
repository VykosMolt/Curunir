"""Deterministic claim proposal + curated claim creation pass.

Read-only proposal generation over existing mentions/entities, then applying
accepted proposals via actions.create_claim (+ claim evidence spans). Uses a
temporary fixture document, never the real corpus.
"""
from __future__ import annotations

from argus import actions, claim_proposals

FIX = (
    "The CNIL imposed an administrative fine and a penalty of €20 million on Clearview AI, Inc. "
    "Clearview AI relies on facial recognition and biometric data. "
    "Foodinho used automated decision-making and profiling of riders. "
    "The Swedish Police used Clearview AI."
)


def _setup() -> dict:
    source_id = actions.create_source("Test Authority", "regulator")["source_id"]
    dv = str(actions.ingest_text_document(
        FIX, title="Doc", source_id=source_id, metadata={"doc_id": "d1", "manifest": {}},
    )["document_version_id"])
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    clearview = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    foodinho = actions.create_entity("company", "Foodinho")["entity_id"]

    def mk(needle: str, mtype: str, entity=None, search_from: int = 0) -> str:
        start = FIX.index(needle, search_from)
        span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
        mid = actions.create_mention(dv, needle, mtype, evidence_span_id=span)["mention_id"]
        if entity:
            actions.assign_mention_to_entity(mid, entity)
        return str(mid)

    mk("CNIL", "regulator", cnil)
    mk("Clearview AI, Inc.", "company", clearview)
    mk("€20 million", "money")
    mk("Clearview AI", "company", clearview, search_from=FIX.index("Inc."))     # sentence 2
    mk("Foodinho", "company", foodinho)
    mk("Clearview AI", "company", clearview, search_from=FIX.index("Swedish"))  # sentence 4
    return {"dv": dv, "clearview": clearview}


def _by_predicate(payload):
    out = {}
    for p in payload["proposals"]:
        out.setdefault(p["predicate"], []).append(p)
    return out


# ---- 1, 2: read-only; creates no mentions/entities --------------------------

def test_propose_claims_is_read_only(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    assert payload["summary"]["total_proposals"] >= 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claims")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from evidence_spans")
        spans_before = cur.fetchone()["n"]
    claim_proposals.propose_claims(db_conn)  # again
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from evidence_spans")
        assert cur.fetchone()["n"] == spans_before


def test_propose_creates_no_mentions_or_entities(db_conn):
    _setup()
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from mentions")
        m0 = cur.fetchone()["n"]
        cur.execute("select count(*) as n from entities")
        e0 = cur.fetchone()["n"]
    claim_proposals.propose_claims(db_conn)
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] == m0
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == e0


# ---- 3, 4, 5, 6: predicate patterns + default unaccepted --------------------

def test_fine_pattern_proposes_issued_fine(db_conn):
    _setup()
    fines = [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
             if p["predicate"] == "issued_fine"]
    assert len(fines) == 1
    f = fines[0]
    assert f["subject_surface"] == "CNIL"
    assert f["object_surface"] == "Clearview AI, Inc."
    assert f["value"]["amount"] == 20000000 and f["value"]["currency"] == "EUR"
    assert f["subject_entity_id"] and f["object_entity_id"]
    assert f["accepted"] is False


def test_biometric_pattern_proposed(db_conn):
    _setup()
    preds = {p["predicate"] for p in claim_proposals.propose_claims(db_conn)["proposals"]}
    assert "concerns_biometric_processing" in preds


def test_adm_pattern_proposed(db_conn):
    _setup()
    by_pred = _by_predicate(claim_proposals.propose_claims(db_conn))
    assert "concerns_automated_decision_making" in by_pred
    assert by_pred["concerns_automated_decision_making"][0]["subject_surface"] == "Foodinho"


def test_proposals_default_unaccepted(db_conn):
    _setup()
    assert all(p["accepted"] is False for p in claim_proposals.propose_claims(db_conn)["proposals"])


# ---- 7: deduplication of repeated anchors -----------------------------------

def test_dedup_repeated_anchors(db_conn):
    _setup()
    # sentence 1 has 3 fine anchors (imposed / fine / penalty) -> exactly one proposal
    fines = [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
             if p["predicate"] == "issued_fine"]
    assert len(fines) == 1


# ---- 8: apply one accepted -> evidence span + one claim ---------------------

def test_apply_one_accepted_creates_claim(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    f = dict(next(p for p in payload["proposals"] if p["predicate"] == "issued_fine"), accepted=True)
    report = claim_proposals.apply_claim_proposals([f])
    assert report["claims_created"] == 1
    assert report["evidence_spans_created"] == 1
    assert report["summary"]["applied"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claims")
        assert cur.fetchone()["n"] == 1
        cur.execute("select count(*) as n from claim_versions where tx_to is null")
        assert cur.fetchone()["n"] == 1
        cur.execute("select verification_state, predicate, subject_entity_id, object_entity_id "
                    "from claim_versions where tx_to is null")
        cv = cur.fetchone()
    assert cv["verification_state"] == "unverified"
    assert cv["predicate"] == "issued_fine"
    assert cv["subject_entity_id"] and cv["object_entity_id"]


# ---- 9: none accepted -> nothing --------------------------------------------

def test_apply_nothing_when_none_accepted(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    report = claim_proposals.apply_claim_proposals(payload["proposals"])
    assert report["claims_created"] == 0
    assert report["summary"]["skipped_not_accepted"] == len(payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claims")
        assert cur.fetchone()["n"] == 0


# ---- 10: re-apply is idempotent ---------------------------------------------

def test_reapply_is_idempotent(db_conn):
    _setup()
    f = dict(next(p for p in claim_proposals.propose_claims(db_conn)["proposals"]
                  if p["predicate"] == "issued_fine"), accepted=True)
    first = claim_proposals.apply_claim_proposals([f])
    assert first["claims_created"] == 1
    second = claim_proposals.apply_claim_proposals([f])
    assert second["claims_created"] == 0
    assert second["summary"]["duplicate_existing"] == 1
    assert second["evidence_spans_created"] == 0
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claims")
        assert cur.fetchone()["n"] == 1


# ---- 11: apply creates no entities / mentions / claim_relations -------------

def test_apply_creates_no_entities_or_relations(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entities")
        e0 = cur.fetchone()["n"]
        cur.execute("select count(*) as n from mentions")
        m0 = cur.fetchone()["n"]
    claim_proposals.apply_claim_proposals([dict(p, accepted=True) for p in payload["proposals"][:3]])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_relations")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == e0
        cur.execute("select count(*) as n from mentions")
        assert cur.fetchone()["n"] == m0
        cur.execute("select count(*) as n from claims")
        assert cur.fetchone()["n"] >= 1


# ---- 12: unverified unless explicitly machine_supported ---------------------

def test_claim_state_unverified_unless_machine_supported(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    fine = next(p for p in payload["proposals"] if p["predicate"] == "issued_fine")
    bio = next(p for p in payload["proposals"] if p["predicate"] == "concerns_biometric_processing")
    claim_proposals.apply_claim_proposals([dict(fine, accepted=True)])
    claim_proposals.apply_claim_proposals([dict(bio, accepted=True, verification_state="machine_supported")])
    with db_conn.cursor() as cur:
        cur.execute("select predicate, verification_state from claim_versions where tx_to is null")
        states = {r["predicate"]: r["verification_state"] for r in cur.fetchall()}
    assert states["issued_fine"] == "unverified"
    assert states["concerns_biometric_processing"] == "machine_supported"


# ---- 13: claim-report counts ------------------------------------------------

def test_claim_report_counts(db_conn):
    _setup()
    payload = claim_proposals.propose_claims(db_conn)
    fine = dict(next(p for p in payload["proposals"] if p["predicate"] == "issued_fine"), accepted=True)
    bio = dict(next(p for p in payload["proposals"] if p["predicate"] == "concerns_biometric_processing"),
               accepted=True)
    claim_proposals.apply_claim_proposals([fine, bio])
    report = claim_proposals.build_claim_report(db_conn)
    assert report["total_claims"] == 2
    assert report["total_current_claim_versions"] == 2
    assert report["claims_by_predicate"].get("issued_fine") == 1
    assert report["claims_by_verification_state"].get("unverified") == 2
    assert "create_claim" in report["action_log_counts"]


# ---- 14: boilerplate window cleanup (Part A) --------------------------------

def test_boilerplate_window_does_not_pick_edpb_subject(db_conn):
    text = ("Hellenic DPA fines Clearview AI EUR 20 million | European Data Protection Board "
            "Skip to main content Menu.")
    source_id = actions.create_source("Src", "public_body")["source_id"]
    dv = str(actions.ingest_text_document(
        text, title="D", source_id=source_id, metadata={"doc_id": "e1", "manifest": {}},
    )["document_version_id"])
    hel = actions.create_entity("regulator", "Hellenic Data Protection Authority")["entity_id"]
    edpb = actions.create_entity("regulator", "European Data Protection Board")["entity_id"]
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]

    def mk(needle, mtype, entity=None):
        start = text.index(needle)
        span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
        mid = actions.create_mention(dv, needle, mtype, evidence_span_id=span)["mention_id"]
        if entity:
            actions.assign_mention_to_entity(mid, entity)

    mk("Hellenic DPA", "regulator", hel)
    mk("Clearview AI", "company", cv)
    mk("EUR 20 million", "money")
    mk("European Data Protection Board", "regulator", edpb)  # in the boilerplate tail

    fines = [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
             if p["predicate"] == "issued_fine"]
    assert len(fines) == 1
    # the window is trimmed at " | ", so the EDPB site name cannot supply the subject
    assert fines[0]["subject_surface"] == "Hellenic DPA"
    quote = fines[0]["evidence_span"]["exact_quote"]
    assert "Skip to main content" not in quote
    assert "European Data Protection Board" not in quote


# ---- reduced_fine / penalty subject anchoring (French SA -> CNIL) -----------

def _reduced_doc(text, doc_id="fr-cnil-2023-clearview-penalty", source="EDPB"):
    sid = actions.create_source(source, "public_body")["source_id"]
    dv = str(actions.ingest_text_document(text, title="CNIL penalty", source_id=sid,
             metadata={"doc_id": doc_id, "manifest": {}})["document_version_id"])
    return dv


def _mk(dv, text, needle, mtype, entity=None, search_from=0):
    start = text.index(needle, search_from)
    span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
    mid = actions.create_mention(dv, needle, mtype, evidence_span_id=span)["mention_id"]
    if entity:
        actions.assign_mention_to_entity(mid, entity)
    return str(mid)


def test_reduced_fine_anchors_subject_to_cnil(db_conn):
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    text = ("On 13 April 2023, the French SA's restricted committee considered that Clearview AI "
            "had not complied and imposed a penalty payment of EUR 5,200,000.")
    dv = _reduced_doc(text)
    _mk(dv, text, "French SA", "regulator", cnil)
    _mk(dv, text, "Clearview AI", "company")
    _mk(dv, text, "EUR 5,200,000", "money")
    red = [p for p in claim_proposals.propose_claims(db_conn)["proposals"] if p["predicate"] == "reduced_fine"]
    assert red
    r = red[0]
    assert r["subject_mention_id"] is not None
    assert r["subject_surface"] == "French SA"
    assert r["subject_entity_id"] == str(cnil)
    assert r["value"]["amount"] == 5200000


def test_reduced_fine_prefers_cnil_over_edpb(db_conn):
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    edpb = actions.create_entity("regulator", "European Data Protection Board")["entity_id"]
    text = ("European Data Protection Board Skip to main content. On 13 April 2023, the French SA "
            "imposed a penalty payment of EUR 5,200,000 on Clearview AI.")
    dv = _reduced_doc(text)
    _mk(dv, text, "European Data Protection Board", "regulator", edpb)  # boilerplate header
    _mk(dv, text, "French SA", "regulator", cnil)
    _mk(dv, text, "Clearview AI", "company")
    _mk(dv, text, "EUR 5,200,000", "money")
    red = [p for p in claim_proposals.propose_claims(db_conn)["proposals"] if p["predicate"] == "reduced_fine"]
    assert red
    assert red[0]["subject_surface"] == "French SA"
    assert red[0]["subject_entity_id"] == str(cnil)


def test_reduced_fine_subject_missing_when_no_regulator(db_conn):
    text = "On 13 April 2023, the company was ordered to pay a penalty payment of EUR 5,200,000."
    dv = _reduced_doc(text)
    _mk(dv, text, "EUR 5,200,000", "money")
    red = [p for p in claim_proposals.propose_claims(db_conn)["proposals"] if p["predicate"] == "reduced_fine"]
    assert red
    assert red[0]["subject_mention_id"] is None  # no regulator present -> not fabricated
