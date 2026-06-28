"""Consolidation/verification + Clearview case assembly pass.

Read-only planning + export validation, plus applying verification decisions
(set_claim_verification_state) and case specs (create_case / add_case_item).
Temporary fixtures only.
"""
from __future__ import annotations

import uuid

from argus import actions, case_assembly, export


def _count(cur, table) -> int:
    cur.execute(f"select count(*) as n from {table}")
    return cur.fetchone()["n"]


def _doc(doc_id: str) -> tuple[str, str]:
    source_id = actions.create_source(f"src-{doc_id}", "public_body")["source_id"]
    res = actions.ingest_text_document(
        f"text for {doc_id}", title=doc_id, source_id=source_id,
        metadata={"doc_id": doc_id, "manifest": {}},
    )
    return str(res["document_version_id"]), str(res["document_id"])


def _claim(dv: str, predicate: str, claim_text: str, subject_entity=None, object_entity=None) -> str:
    span = actions.create_evidence_span(dv, exact_quote=claim_text[:50] or "x")["evidence_span_id"]
    subj = obj = None
    if subject_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"s-{predicate}-{claim_text[:5]}")["evidence_span_id"]
        subj = actions.create_mention(dv, "s", "regulator", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(subj, subject_entity)
    if object_entity:
        s = actions.create_evidence_span(dv, exact_quote=f"o-{predicate}-{claim_text[:5]}")["evidence_span_id"]
        obj = actions.create_mention(dv, "o", "company", evidence_span_id=s)["mention_id"]
        actions.assign_mention_to_entity(obj, object_entity)
    return str(actions.create_claim(claim_text, "enforcement_action", predicate, span,
                                    subject_mention_id=subj, object_mention_id=obj)["claim_id"])


# ---- 1: plan is read-only ---------------------------------------------------

def test_build_case_plan_is_read_only(db_conn):
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    dv, _ = _doc("xx-clearview-1")
    _claim(dv, "issued_fine", "Regulator fined Clearview AI.", object_entity=cv)
    with db_conn.cursor() as cur:
        before = {t: _count(cur, t) for t in ("cases", "case_items", "claim_versions", "claim_relations")}
    plan = case_assembly.build_case_plan(db_conn)
    assert plan["summary"]["clearview_claims"] >= 1
    assert all(d["accepted"] is False for d in plan["suggested_verification_decisions"])
    with db_conn.cursor() as cur:
        for t, c0 in before.items():
            assert _count(cur, t) == c0, t


# ---- 2: apply only accepted decisions ---------------------------------------

def test_verification_applies_only_accepted(db_conn):
    dv, _ = _doc("c1")
    cid = _claim(dv, "issued_fine", "x")
    report = case_assembly.apply_verification_decisions(
        [{"claim_id": cid, "new_state": "human_verified", "accepted": False}])
    assert report["summary"]["applied"] == 0
    assert report["summary"]["skipped_not_accepted"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select verification_state from claim_versions where claim_id = %s and tx_to is null", (cid,))
        assert cur.fetchone()["verification_state"] == "unverified"


# ---- 3: applying a decision creates a new current version -------------------

def test_apply_verification_creates_new_version(db_conn):
    dv, _ = _doc("c2")
    cid = _claim(dv, "issued_fine", "x")
    report = case_assembly.apply_verification_decisions(
        [{"claim_id": cid, "new_state": "human_verified", "accepted": True, "reason": "clear evidence"}])
    assert report["summary"]["applied"] == 1
    assert report["verification_state_counts"].get("human_verified") == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_versions where claim_id = %s", (cid,))
        assert cur.fetchone()["n"] == 2
        cur.execute("select verification_state from claim_versions where claim_id = %s and tx_to is null", (cid,))
        assert cur.fetchone()["verification_state"] == "human_verified"
        cur.execute("select count(*) as n from claim_events where claim_id = %s "
                    "and event_type = 'verification_state_change'", (cid,))
        assert cur.fetchone()["n"] == 1


# ---- 4: re-applying same state is idempotent --------------------------------

def test_reapply_verification_idempotent(db_conn):
    dv, _ = _doc("c3")
    cid = _claim(dv, "issued_fine", "x")
    decisions = [{"claim_id": cid, "new_state": "human_verified", "accepted": True}]
    case_assembly.apply_verification_decisions(decisions)
    second = case_assembly.apply_verification_decisions(decisions)
    assert second["summary"]["applied"] == 0
    assert second["summary"]["already_in_state"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from claim_versions where claim_id = %s", (cid,))
        assert cur.fetchone()["n"] == 2  # not 3


# ---- 5: invalid state rejected ----------------------------------------------

def test_invalid_verification_state_rejected(db_conn):
    dv, _ = _doc("c4")
    cid = _claim(dv, "issued_fine", "x")
    report = case_assembly.apply_verification_decisions(
        [{"claim_id": cid, "new_state": "super_verified", "accepted": True}])
    assert report["summary"]["error"] == 1
    assert report["summary"]["applied"] == 0
    with db_conn.cursor() as cur:
        cur.execute("select verification_state from claim_versions where claim_id = %s and tx_to is null", (cid,))
        assert cur.fetchone()["verification_state"] == "unverified"


# ---- 6: verification creates no extraction objects --------------------------

def test_verification_creates_no_extraction_objects(db_conn):
    dv, _ = _doc("c5")
    cid = _claim(dv, "issued_fine", "x")
    tables = ("claims", "entities", "mentions", "evidence_spans", "claim_relations")
    with db_conn.cursor() as cur:
        before = {t: _count(cur, t) for t in tables}
    case_assembly.apply_verification_decisions(
        [{"claim_id": cid, "new_state": "disputed", "accepted": True}])
    with db_conn.cursor() as cur:
        for t, c0 in before.items():
            assert _count(cur, t) == c0, t


# ---- 7: case assembly adds claim/document/entity/relation/note items --------

def test_case_assembly_adds_items(db_conn):
    ent = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    dv, doc = _doc("a1-clearview")
    c1 = _claim(dv, "issued_fine", "fine", object_entity=ent)
    c2 = _claim(dv, "ordered_deletion", "order")
    rel = str(actions.create_claim_relation(c1, c2, "supersedes")["relation_id"])
    spec = {"title": "Clearview test case", "description": "d", "items": [
        {"type": "entity", "id": str(ent), "label": "Clearview"},
        {"type": "claim", "id": c1, "label": "fine"},
        {"type": "document", "id": doc, "label": "doc"},
        {"type": "relation", "id": rel, "label": "supersedes"},
        {"type": "note", "label": "a limitations note"},
    ]}
    report = case_assembly.apply_case_spec(spec)
    assert report["summary"]["added"] == 5
    assert report["summary"]["error"] == 0
    assert report["items_added_by_type"] == {"entity": 1, "claim": 1, "document": 1, "relation": 1, "note": 1}
    with db_conn.cursor() as cur:
        assert _count(cur, "cases") == 1
        cur.execute("select count(*) as n from case_items where case_id = %s", (report["case_id"],))
        assert cur.fetchone()["n"] == 5


def test_case_assembly_reuses_case_and_is_idempotent(db_conn):
    ent = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    spec = {"title": "Reuse case", "items": [{"type": "entity", "id": str(ent), "label": "x"},
                                             {"type": "note", "label": "n"}]}
    case_assembly.apply_case_spec(spec)
    second = case_assembly.apply_case_spec(spec)
    assert second["case_reused"] is True
    assert second["summary"]["added"] == 0
    assert second["summary"]["duplicate_existing"] == 2
    with db_conn.cursor() as cur:
        assert _count(cur, "cases") == 1
        cur.execute("select count(*) as n from case_items where case_id = %s", (second["case_id"],))
        assert cur.fetchone()["n"] == 2  # not duplicated


# ---- 8: invalid/missing case item ids rejected ------------------------------

def test_case_assembly_rejects_invalid_ids(db_conn):
    spec = {"title": "Bad case", "items": [
        {"type": "claim", "id": str(uuid.uuid4()), "label": "ghost"},   # nonexistent claim
        {"type": "document", "id": None, "label": "noid"},              # missing id
        {"type": "bogus", "id": str(uuid.uuid4())},                     # invalid type
    ]}
    report = case_assembly.apply_case_spec(spec)
    assert report["summary"]["added"] == 0
    assert report["summary"]["error"] == 3
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from case_items where case_id = %s", (report["case_id"],))
        assert cur.fetchone()["n"] == 0


# ---- 9, 10: export validation -----------------------------------------------

def test_validate_catches_bad_export():
    bad = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "x", "current_version": None, "evidence_span": None,
                   "events": [], "relations": {}}},
    ]}
    assert case_assembly.validate_case_export(bad)["valid"] is False
    # a claim entry missing the expanded claim object entirely also fails
    missing = {"case": {"title": "C"}, "items": [{"case_item": {"item_type": "claim"}}]}
    assert case_assembly.validate_case_export(missing)["valid"] is False


def test_validate_passes_minimal_valid_export():
    good = {"case": {"title": "Clearview case"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "c1",
                   "current_version": {"verification_state": "unverified"},
                   "evidence_span": {"exact_quote": "Reg fined Clearview.", "quote_hash": "abc123"},
                   "subject": {"mention": {}, "resolved_entity": {"current_version": {"canonical_name": "X"}}},
                   "object": None,
                   "events": [{"event_type": "created"}],
                   "relations": {"incoming": [], "outgoing": []}}},
        {"case_item": {"item_type": "document"},
         "document": {"document": {"title": "D"}, "versions": [{"raw_hash": "h"}]}},
        {"case_item": {"item_type": "entity"},
         "entity": {"entity": {}, "current_version": {"canonical_name": "X"}}},
        {"case_item": {"item_type": "relation"},
         "relation": {"relation": {"relation": "supersedes", "src_claim_id": "a", "dst_claim_id": "b"}}},
        {"case_item": {"item_type": "note"}},
    ]}
    report = case_assembly.validate_case_export(good)
    assert report["valid"] is True
    assert report["summary"]["failed_checks"] == 0
    assert any("surface forms" in g for g in report["export_gaps"])  # older/minimal export


# ---- export readability: entity surface forms + assigned mentions -----------

def _setup_entity_case() -> tuple[str, str]:
    cv = actions.create_entity("company", "Clearview AI, Inc.")["entity_id"]
    source_id = actions.create_source("Example Authority", "regulator")["source_id"]
    text = ("Clearview AI was fined. Separately, CLEARVIEW AI appeared. "
            "Later, Clearview AI again. The firm Clearview AI, Inc. is canonical.")
    dv = str(actions.ingest_text_document(
        text, title="Doc One", source_id=source_id,
        metadata={"doc_id": "fr-cnil-2022-clearview-fine", "manifest": {}},
    )["document_version_id"])

    def mk(needle, search_from=0):
        start = text.index(needle, search_from)
        span = actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
        mid = actions.create_mention(dv, needle, "company", evidence_span_id=span)["mention_id"]
        actions.assign_mention_to_entity(mid, cv)

    mk("Clearview AI")                                   # "Clearview AI" (1)
    mk("Clearview AI", search_from=text.index("Later"))  # "Clearview AI" (2)
    mk("CLEARVIEW AI")
    mk("Clearview AI, Inc.")
    case_id = actions.create_case("Entity readability case")["case_id"]
    actions.add_case_item(case_id, "entity", item_id=cv)
    return str(case_id), str(cv)


def _entity_item(db_conn, case_id):
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    return next(it["entity"] for it in bundle["items"] if it["case_item"]["item_type"] == "entity")


def test_entity_export_includes_assigned_mentions(db_conn):
    case_id, cv = _setup_entity_case()
    ent = _entity_item(db_conn, case_id)
    assert ent["entity_id"] == cv
    assert ent["current_version"]["canonical_name"] == "Clearview AI, Inc."
    assert ent["current_version"]["entity_type"] == "company"
    assert ent["assigned_mention_count"] == 4
    assert len(ent["assigned_mentions"]) == 4
    assert {m["surface_text"] for m in ent["assigned_mentions"]} == {
        "Clearview AI", "CLEARVIEW AI", "Clearview AI, Inc."}


def test_entity_export_groups_surface_forms(db_conn):
    case_id, _ = _setup_entity_case()
    ent = _entity_item(db_conn, case_id)
    by_surface = {f["surface_text"]: f["count"] for f in ent["surface_forms"]}
    assert by_surface == {"Clearview AI": 2, "CLEARVIEW AI": 1, "Clearview AI, Inc.": 1}
    assert sum(f["count"] for f in ent["surface_forms"]) == ent["assigned_mention_count"]


def test_entity_export_mentions_have_document_and_evidence_context(db_conn):
    case_id, _ = _setup_entity_case()
    ent = _entity_item(db_conn, case_id)
    m = ent["assigned_mentions"][0]
    assert m["mention_id"] and m["surface_text"]
    assert m["doc_id_from_metadata"] == "fr-cnil-2022-clearview-fine"
    assert m["document_title"] == "Doc One"
    assert m["document_id"] and m["document_version_id"]
    assert m["evidence_span_id"] and m["quote"]
    assert m["source_name"] == "Example Authority"


def test_validate_passes_entity_export_with_assigned_mentions(db_conn):
    case_id, _ = _setup_entity_case()
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    report = case_assembly.validate_case_export(bundle)
    assert report["valid"] is True
    assert report["summary"]["failed_checks"] == 0
    assert not any("older/minimal" in g for g in report["export_gaps"])


def test_validate_fails_malformed_current_style_entity():
    bad = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "entity"},
         "entity": {"entity_id": "e1", "entity": {}, "current_version": {"canonical_name": "X"},
                    "assigned_mention_count": 2,
                    "assigned_mentions": [{"mention_id": "m1", "surface_text": "X", "document_id": "d1"}]}},
    ]}  # current-style (has assigned data) but missing surface_forms
    report = case_assembly.validate_case_export(bad)
    assert report["valid"] is False
    assert any(c["check"] == "entity_has_surface_forms" and not c["ok"] for c in report["checks"])


# ---- export readability: claim subject/object resolution blocks --------------

def _setup_claim_case() -> tuple[str, str, str]:
    cnil = actions.create_entity("regulator", "CNIL")["entity_id"]
    source_id = actions.create_source("Example Authority", "regulator")["source_id"]
    text = "CNIL fined the organization EUR 20 million per the decision."
    dv = str(actions.ingest_text_document(
        text, title="Decision Doc", source_id=source_id,
        metadata={"doc_id": "fr-cnil-2022-clearview-fine", "manifest": {}},
    )["document_version_id"])

    def span(needle):
        start = text.index(needle)
        return actions.create_evidence_span(dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]

    subj_m = actions.create_mention(dv, "CNIL", "regulator", evidence_span_id=span("CNIL"))["mention_id"]
    actions.assign_mention_to_entity(subj_m, cnil)                       # subject resolved
    obj_m = actions.create_mention(dv, "the organization", "organization",
                                   evidence_span_id=span("the organization"))["mention_id"]  # NOT assigned
    claim_span = actions.create_evidence_span(dv, start_char=0, end_char=len(text))["evidence_span_id"]
    c1 = str(actions.create_claim("CNIL fined the organization EUR 20 million.", "enforcement_action",
                                  "issued_fine", claim_span, subject_mention_id=subj_m,
                                  object_mention_id=obj_m, value={"amount": 20000000, "currency": "EUR"})["claim_id"])
    # c2: no subject/object mentions (missing), but a claim evidence span exists
    c2_span = actions.create_evidence_span(dv, exact_quote="The fine was reduced to 5200000 EUR.")["evidence_span_id"]
    c2 = str(actions.create_claim("The fine was reduced to 5200000 EUR.", "enforcement_action",
                                  "reduced_fine", c2_span, value={"amount": 5200000, "currency": "EUR"})["claim_id"])
    case_id = actions.create_case("Claim readability case")["case_id"]
    actions.add_case_item(case_id, "claim", item_id=c1)
    actions.add_case_item(case_id, "claim", item_id=c2)
    return str(case_id), c1, c2


def _claims_by_id(db_conn, case_id):
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    return {it["claim"]["claim_id"]: it["claim"]
            for it in bundle["items"] if it["case_item"]["item_type"] == "claim"}, bundle


def test_claim_export_has_resolution_blocks(db_conn):
    case_id, c1, c2 = _setup_claim_case()
    claims, _ = _claims_by_id(db_conn, case_id)
    for role in ("subject", "object"):
        assert "resolution_status" in claims[c1][role]
        assert "resolution_status" in claims[c2][role]


def test_claim_resolved_subject_has_lightweight_entity(db_conn):
    case_id, c1, _ = _setup_claim_case()
    claims, _ = _claims_by_id(db_conn, case_id)
    subj = claims[c1]["subject"]
    assert subj["resolution_status"] == "resolved"
    ent = subj["resolved_entity"]
    assert ent["entity_id"] and ent["canonical_name"] == "CNIL" and ent["entity_type"] == "regulator"
    assert "assigned_mentions" not in ent  # lightweight, not the rich entity view
    assert subj["mention"]["surface_text"] == "CNIL"


def test_claim_unresolved_object_explicit(db_conn):
    case_id, c1, _ = _setup_claim_case()
    claims, _ = _claims_by_id(db_conn, case_id)
    obj = claims[c1]["object"]
    assert obj["resolution_status"] == "unresolved_mention"
    assert obj["mention"]["surface_text"] == "the organization"
    assert obj["resolved_entity"] is None
    assert obj["resolution_note"]


def test_claim_missing_mention_explicit(db_conn):
    case_id, _, c2 = _setup_claim_case()
    claims, _ = _claims_by_id(db_conn, case_id)
    subj = claims[c2]["subject"]
    assert subj["resolution_status"] == "missing_mention"
    assert subj["mention"] is None
    assert subj["resolution_note"]
    assert claims[c2]["claim_evidence_context"]  # evidence fallback present


def test_claim_evidence_context_has_document_and_source(db_conn):
    case_id, _, c2 = _setup_claim_case()
    claims, _ = _claims_by_id(db_conn, case_id)
    cec = claims[c2]["claim_evidence_context"]
    assert cec["quote"] == "The fine was reduced to 5200000 EUR."
    assert cec["doc_id_from_metadata"] == "fr-cnil-2022-clearview-fine"
    assert cec["document_title"] == "Decision Doc"
    assert cec["source_name"] == "Example Authority"
    assert cec["document_id"] and cec["document_version_id"]


def test_validate_passes_current_style_claim_export(db_conn):
    case_id, c1, c2 = _setup_claim_case()
    _, bundle = _claims_by_id(db_conn, case_id)
    report = case_assembly.validate_case_export(bundle)
    assert report["valid"] is True
    assert report["summary"]["failed_checks"] == 0
    # c2 subject is missing -> reported as a gap (warning), not a failure
    assert any("subject entity not resolved" in g for g in report["export_gaps"])


def test_validate_fails_malformed_current_style_claim():
    bad = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "c1",
                   "current_version": {"verification_state": "unverified"},
                   "evidence_span": {"exact_quote": "q", "quote_hash": "h"},
                   "events": [{"event_type": "created"}],
                   "relations": {"incoming": [], "outgoing": []},
                   "claim_evidence_context": {"quote": "q"},          # marks it current-style
                   "subject": {"mention": {"mention_id": "m"}},        # missing resolution_status
                   "object": {"resolution_status": "missing_mention", "resolution_note": "n"}}},
    ]}
    report = case_assembly.validate_case_export(bad)
    assert report["valid"] is False
    assert any(c["check"] == "claim_subject_has_resolution_status" and not c["ok"]
               for c in report["checks"])


# ---- evidence roles: origin vs verification-event vs relation evidence -------

def _origin_and_verification_case() -> dict:
    """A claim born from a recital (origin evidence), later verified against a
    different, primary-source document via a verification-event evidence span."""
    court = actions.create_entity("court", "First-tier Tribunal")["entity_id"]
    # origin document: the Upper Tribunal's recital of the FTT decision
    osrc = actions.create_source("Upper Tribunal (host)", "court")["source_id"]
    otext = "The Upper Tribunal records that the First-tier Tribunal allowed the appeal."
    odv = str(actions.ingest_text_document(otext, title="UT judgment", source_id=osrc,
              metadata={"doc_id": "uk-ut-recital", "manifest": {}})["document_version_id"])
    origin_span = str(actions.create_evidence_span(
        odv, exact_quote="the First-tier Tribunal allowed the appeal")["evidence_span_id"])
    smid = actions.create_mention(
        odv, "First-tier Tribunal", "court",
        evidence_span_id=actions.create_evidence_span(
            odv, exact_quote="First-tier Tribunal")["evidence_span_id"])["mention_id"]
    actions.assign_mention_to_entity(smid, court)
    claim_id = str(actions.create_claim(
        "First-tier Tribunal allowed the appeal.", "appeal_decision", "appeal_allowed",
        origin_span, subject_mention_id=smid)["claim_id"])
    # primary document: the FTT's own decision; its span verifies the claim
    psrc = actions.create_source("First-tier Tribunal", "court")["source_id"]
    ptext = "The Tribunal allows the appeal and substitutes its decision for the Commissioner's."
    pdv = str(actions.ingest_text_document(ptext, title="FTT decision", source_id=psrc,
              metadata={"doc_id": "uk-ftt-primary", "manifest": {}})["document_version_id"])
    primary_span = str(actions.create_evidence_span(
        pdv, exact_quote="The Tribunal allows the appeal")["evidence_span_id"])
    # verify the EXISTING claim using the primary span (origin is NOT replaced)
    actions.set_claim_verification_state(
        claim_id, "human_verified",
        reason="FTT primary decision directly confirms the appeal was allowed",
        evidence_span_id=primary_span)
    case_id = str(actions.create_case("Evidence-role case")["case_id"])
    actions.add_case_item(case_id, "claim", item_id=claim_id)
    return {"case_id": case_id, "claim_id": claim_id,
            "origin_span": origin_span, "primary_span": primary_span}


def _claim_item(db_conn, case_id):
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    claim = next(it["claim"] for it in bundle["items"]
                 if it["case_item"]["item_type"] == "claim")
    return claim, bundle


# Part B test 1: origin evidence is distinguished from verification-event evidence
def test_evidence_roles_distinguish_origin_from_verification(db_conn):
    f = _origin_and_verification_case()
    claim, _ = _claim_item(db_conn, f["case_id"])
    roles = claim["evidence_roles"]
    assert roles["origin"]["evidence_span_id"] == f["origin_span"]
    assert roles["origin"]["doc_id_from_metadata"] == "uk-ut-recital"
    ver = roles["verification_events"]
    assert len(ver) == 1
    assert ver[0]["evidence_span_id"] == f["primary_span"]
    assert ver[0]["doc_id_from_metadata"] == "uk-ftt-primary"
    assert ver[0]["previous_state"] == "unverified"
    assert ver[0]["new_state"] == "human_verified"


# Part B test 2: verification-event evidence carries quote + document + source
def test_verification_event_evidence_has_quote_doc_source(db_conn):
    f = _origin_and_verification_case()
    claim, _ = _claim_item(db_conn, f["case_id"])
    ve = claim["evidence_roles"]["verification_events"][0]
    assert ve["quote"] == "The Tribunal allows the appeal"
    assert ve["document_title"] == "FTT decision"
    assert ve["source_name"] == "First-tier Tribunal"
    assert ve["document_id"] and ve["document_version_id"]
    assert ve["reason"]


# Part B test 3: origin evidence is NOT replaced when verification evidence exists
def test_origin_evidence_not_replaced_by_verification(db_conn):
    f = _origin_and_verification_case()
    claim, _ = _claim_item(db_conn, f["case_id"])
    assert str(claim["current_version"]["evidence_span_id"]) == f["origin_span"]
    assert claim["evidence_roles"]["origin"]["evidence_span_id"] == f["origin_span"]
    assert claim["claim_evidence_context"]["evidence_span_id"] == f["origin_span"]
    assert f["origin_span"] != f["primary_span"]
    assert claim["evidence_roles"]["verification_events"][0]["evidence_span_id"] == f["primary_span"]
    assert claim["current_version"]["verification_state"] == "human_verified"


def _relation_with_evidence_case() -> dict:
    dv, _ = _doc("rel-evidence-doc")
    c1 = _claim(dv, "issued_fine", "Regulator fined the company.")
    c2 = _claim(dv, "appeal_allowed", "Tribunal allowed the appeal.")
    rel_span = str(actions.create_evidence_span(
        dv, exact_quote="text for rel-evidence-doc")["evidence_span_id"])
    actions.create_claim_relation(c2, c1, "supersedes", evidence_span_id=rel_span)
    case_id = str(actions.create_case("Relation-evidence case")["case_id"])
    actions.add_case_item(case_id, "claim", item_id=c2)
    return {"case_id": case_id, "claim_id": c2, "rel_span": rel_span}


# Part B test 4: relation evidence is exported when present
def test_relation_evidence_exported_when_present(db_conn):
    f = _relation_with_evidence_case()
    claim, _ = _claim_item(db_conn, f["case_id"])
    rel_events = claim["evidence_roles"]["relation_events"]
    assert len(rel_events) == 1
    assert rel_events[0]["relation"] == "supersedes"
    assert rel_events[0]["evidence_span_id"] == f["rel_span"]
    assert rel_events[0]["quote"]  # quote/context exported


# Part B test 5: validation passes a valid current-style evidence-role export
def test_validate_passes_evidence_role_export(db_conn):
    f = _origin_and_verification_case()
    _, bundle = _claim_item(db_conn, f["case_id"])
    report = case_assembly.validate_case_export(bundle)
    assert report["valid"] is True
    assert report["summary"]["failed_checks"] == 0
    names = {c["check"] for c in report["checks"]}
    assert "claim_evidence_role_origin_present" in names
    assert "verification_event_evidence_has_quote" in names  # the evidence-bearing event was checked


# Part B test 6: validation fails malformed current-style evidence-role blocks
def test_validate_fails_malformed_evidence_role_block():
    bad = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "c1",
                   "current_version": {"verification_state": "human_verified"},
                   "evidence_span": {"exact_quote": "q", "quote_hash": "h"},
                   "events": [{"event_type": "verification_state_change"}],
                   "relations": {"incoming": [], "outgoing": []},
                   "claim_evidence_context": {"quote": "q"},
                   "subject": {"resolution_status": "missing_mention", "resolution_note": "n"},
                   "object": {"resolution_status": "missing_mention", "resolution_note": "n"},
                   "evidence_roles": {
                       "origin": {"evidence_span_id": "s1", "quote": "q"},
                       # claims an evidence span but exports no quote/document/source -> malformed
                       "verification_events": [
                           {"event_id": "e1", "event_type": "verification_state_change",
                            "new_state": "human_verified", "evidence_span_id": "sX",
                            "quote": None, "document_title": None, "source_name": None}],
                       "relation_events": []}}},
    ]}
    report = case_assembly.validate_case_export(bad)
    assert report["valid"] is False
    assert any(c["check"] == "verification_event_evidence_has_quote" and not c["ok"]
               for c in report["checks"])


# Part B test 7: minimal/older exports (no evidence_roles block) are unaffected
def test_validate_minimal_claim_without_evidence_roles_unaffected():
    good = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "c1",
                   "current_version": {"verification_state": "unverified"},
                   "evidence_span": {"exact_quote": "q", "quote_hash": "h"},
                   "events": [{"event_type": "created"}],
                   "relations": {"incoming": [], "outgoing": []}}}]}
    report = case_assembly.validate_case_export(good)
    names = {c["check"] for c in report["checks"]}
    assert "claim_evidence_role_origin_present" not in names  # new checks did not run
    assert report["valid"] is True


# Part B: the data-file path threads evidence_span_id through to the action
def test_apply_verification_decisions_records_evidence_span(db_conn):
    court = actions.create_entity("court", "First-tier Tribunal")["entity_id"]
    dv, _ = _doc("ev-decisionfile")
    cid = _claim(dv, "appeal_allowed", "FTT allowed the appeal.", subject_entity=court)
    span = str(actions.create_evidence_span(dv, exact_quote="text for ev-decisionfile")["evidence_span_id"])
    report = case_assembly.apply_verification_decisions(
        [{"claim_id": cid, "new_state": "human_verified", "accepted": True,
          "reason": "primary source", "evidence_span_id": span}])
    assert report["summary"]["applied"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select evidence_span_id from claim_events where claim_id = %s "
                    "and event_type = 'verification_state_change'", (cid,))
        assert str(cur.fetchone()["evidence_span_id"]) == span


# ---- predication-risk triage surfaced in exports + validation (Task 1C) ------

def _claim_with_predication(dv: str) -> str:
    span = actions.create_evidence_span(dv, exact_quote="Reg fined the company.")["evidence_span_id"]
    return str(actions.create_claim(
        "Reg fined the company.", "enforcement_action", "issued_fine", span,
        metadata={"predication": {"flags": ["negation_near_trigger"],
                                  "triage_level": "likely_negative",
                                  "proposal_confidence": 0.17,
                                  "review_note": "appears to deny the fine"}})["claim_id"])


def test_export_includes_predication_metadata(db_conn):
    dv, _ = _doc("pred-meta")
    cid = _claim_with_predication(dv)
    case_id = actions.create_case("Predication meta case")["case_id"]
    actions.add_case_item(case_id, "claim", item_id=cid)
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    claim = next(it["claim"] for it in bundle["items"]
                 if it["case_item"]["item_type"] == "claim")
    assert claim["predication"]["triage_level"] == "likely_negative"
    assert "negation_near_trigger" in claim["predication"]["flags"]
    report = case_assembly.validate_case_export(bundle)
    assert report["valid"] is True
    assert report["summary"]["failed_checks"] == 0
    assert any(c["check"] == "claim_predication_triage_valid" for c in report["checks"])


def test_export_without_predication_stays_valid(db_conn):
    # a claim created without predication metadata exports predication=None and
    # is NOT failed for it (old/hand-curated claims must keep validating).
    dv, _ = _doc("no-pred")
    cid = _claim(dv, "issued_fine", "Regulator fined the company.")
    case_id = actions.create_case("No predication case")["case_id"]
    actions.add_case_item(case_id, "claim", item_id=cid)
    with db_conn.cursor() as cur:
        bundle = export.build_case_export(cur, case_id)
    claim = next(it["claim"] for it in bundle["items"]
                 if it["case_item"]["item_type"] == "claim")
    assert claim["predication"] is None
    report = case_assembly.validate_case_export(bundle)
    assert report["valid"] is True
    names = {c["check"] for c in report["checks"]}
    assert "claim_predication_triage_valid" not in names  # gated out when absent


def test_validate_fails_malformed_predication():
    bad = {"case": {"title": "C"}, "items": [
        {"case_item": {"item_type": "claim"},
         "claim": {"claim_id": "c1",
                   "current_version": {"verification_state": "unverified"},
                   "evidence_span": {"exact_quote": "q", "quote_hash": "h"},
                   "events": [{"event_type": "created"}],
                   "relations": {"incoming": [], "outgoing": []},
                   "predication": {"triage_level": "bogus", "flags": "not-a-list"}}}]}
    report = case_assembly.validate_case_export(bad)
    assert report["valid"] is False
    assert any(c["check"] == "claim_predication_triage_valid" and not c["ok"]
               for c in report["checks"])
    assert any(c["check"] == "claim_predication_flags_is_list" and not c["ok"]
               for c in report["checks"])
