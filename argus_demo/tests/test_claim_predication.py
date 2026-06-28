"""Predication-risk annotation for the deterministic claim proposer.

ARGUS stays HIGH-RECALL: risky propositions are still emitted as claim candidates,
but annotated with `predication_flags`, a lowered `proposal_confidence`
(extraction/proposal confidence — NOT probability of truth), a `triage_level`, and
a `review_note`. The reviewer decides; ARGUS does not silently launder risky
co-occurrence into clean claims, and it does not silently suppress relevant signal.

These tests therefore expect *emission with flags*, not suppression. They cover the
six failure modes plus multiple-entity windows, and assert that the clean control
still produces a clean, high-confidence proposal.
"""
from __future__ import annotations

from argus import actions, claim_proposals


# mention types that can resolve to an entity (so the proposal is entity-complete
# and its confidence reflects predication risk, not unresolved entities).
_ENTITY_TYPES = {"regulator", "court", "company", "organization", "public_body"}


def _doc_with_mentions(text: str, mentions: list[tuple[str, str]], doc_id: str = "adv-1") -> str:
    sid = actions.create_source("Adversarial Authority", "public_body")["source_id"]
    dv = str(actions.ingest_text_document(
        text, title="Adversarial Doc", source_id=sid,
        metadata={"doc_id": doc_id, "manifest": {}})["document_version_id"])
    ent_cache: dict[tuple[str, str], str] = {}
    for needle, mtype in mentions:
        start = text.index(needle)
        span = actions.create_evidence_span(
            dv, start_char=start, end_char=start + len(needle))["evidence_span_id"]
        mid = actions.create_mention(dv, needle, mtype, evidence_span_id=span)["mention_id"]
        if mtype in _ENTITY_TYPES:
            key = (needle, mtype)
            if key not in ent_cache:
                ent_cache[key] = actions.create_entity(mtype, needle)["entity_id"]
            actions.assign_mention_to_entity(mid, ent_cache[key])
    return dv


def _fines(db_conn) -> list[dict]:
    return [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
            if p["predicate"] == "issued_fine"]


def _one_fine(db_conn) -> dict:
    fines = _fines(db_conn)
    assert len(fines) == 1, f"expected exactly one issued_fine candidate, got {len(fines)}"
    return fines[0]


def _one(db_conn, predicate: str) -> dict:
    cands = [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
             if p["predicate"] == predicate]
    assert cands, f"expected at least one {predicate} candidate"
    return cands[0]  # highest proposal_confidence first (clean before risky)


_REG_CO_MONEY = [("Hellenic DPA", "regulator"), ("Clearview AI", "company"),
                 ("€20 million", "money")]


# ---- 1: clean control -> emitted, clean, high confidence, no risk flags -----

def test_clean_fine_is_clean_high_confidence(db_conn):
    _doc_with_mentions("The ICO fined Clearview AI £7,552,800.",
                       [("ICO", "regulator"), ("Clearview AI", "company"), ("£7,552,800", "money")])
    f = _one_fine(db_conn)
    assert f["triage_level"] == "clean"
    assert f["proposal_confidence"] >= 0.7
    assert f["predication_flags"] == []
    assert f["object_surface"] == "Clearview AI"


# ---- 2: explicit negation -> emitted, likely_negative, flagged, low confidence

def test_negation_emitted_as_likely_negative(db_conn):
    _doc_with_mentions("Clearview AI was not fined €20 million by the Hellenic DPA.", _REG_CO_MONEY)
    f = _one_fine(db_conn)
    # coherent subject/object/value exist, so the candidate is EMITTED (not suppressed)
    assert f["subject_surface"] and f["object_surface"] and f["value"].get("amount")
    assert f["triage_level"] in ("likely_negative", "high_review")
    assert "negation_near_trigger" in f["predication_flags"]
    assert f["proposal_confidence"] <= 0.4
    assert "deny" in f["review_note"].lower()


# ---- 3: cross-attribution -> not clean, contrastive/negation flags ----------

def test_cross_attribution_not_clean(db_conn):
    _doc_with_mentions(
        "Unlike the Italian Garante, the Hellenic DPA did not fine Clearview AI €20 million.",
        _REG_CO_MONEY)
    f = _one_fine(db_conn)
    assert f["triage_level"] != "clean"
    assert ("contrastive_clause" in f["predication_flags"]
            or "wrong_party_risk" in f["predication_flags"])
    assert f["proposal_confidence"] <= 0.4


# ---- 4: hypothetical / proposed -> not clean, hypothetical/negation flags ----

def test_hypothetical_not_clean(db_conn):
    _doc_with_mentions(
        "The regulator considered whether to fine Clearview AI €20 million but did not impose a penalty.",
        [("regulator", "regulator"), ("Clearview AI", "company"), ("€20 million", "money")])
    f = _one_fine(db_conn)
    assert f["triage_level"] != "clean"
    assert ("hypothetical_or_considered" in f["predication_flags"]
            or "negation_near_trigger" in f["predication_flags"])
    assert f["proposal_confidence"] <= 0.4


# ---- 5: appeal / procedural undoing -> emitted, procedural flag, not clean ---

def test_annulment_emitted_as_procedural(db_conn):
    _doc_with_mentions("The ICO's £7,552,800 fine against Clearview AI was annulled on appeal.",
                       [("ICO", "regulator"), ("Clearview AI", "company"), ("£7,552,800", "money")])
    f = _one_fine(db_conn)
    assert f["triage_level"] in ("high_review", "likely_negative")
    assert ("procedural_undoing" in f["predication_flags"]
            or "appeal_or_annulment_context" in f["predication_flags"])
    assert f["proposal_confidence"] < 0.85  # down-ranked vs a clean fine


# ---- 6: reported / secondhand -> emitted, reported flag, not primary --------

def test_reported_secondhand_flagged(db_conn):
    _doc_with_mentions(
        "The Upper Tribunal recorded that the Garante had fined Clearview AI €20 million.",
        [("Upper Tribunal", "court"), ("Garante", "regulator"),
         ("Clearview AI", "company"), ("€20 million", "money")])
    f = _one_fine(db_conn)
    assert "reported_secondhand" in f["predication_flags"]
    assert f["triage_level"] in ("review", "high_review")
    assert f["triage_level"] != "clean"


def test_flagging_helper_unit_level():
    # unit-level test of the flagging function itself (no new appeal extractor needed)
    flags = claim_proposals._issued_fine_flags(
        "The court recorded that the regulator had fined the company.", n_regs=1, n_comps=1,
        wrong_party=False)
    assert "reported_secondhand" in flags
    assert claim_proposals._triage_from_flags(["reported_secondhand"]) == "review"
    assert claim_proposals._triage_from_flags(["negation_near_trigger"]) == "likely_negative"
    assert claim_proposals._triage_from_flags(["procedural_undoing"]) == "high_review"
    assert claim_proposals._triage_from_flags([]) == "clean"


# ---- 7: wrong-party window -> never pairs ICO with the out-of-clause company -

def test_wrong_party_window_does_not_pair_clearview(db_conn):
    _doc_with_mentions(
        "The ICO fined Company A €5 million, while Clearview AI appealed a separate decision.",
        [("ICO", "regulator"), ("Company A", "company"),
         ("€5 million", "money"), ("Clearview AI", "company")], doc_id="adv-wrong-party")
    fines = _fines(db_conn)
    # no proposal pairs the fining ICO with the out-of-clause Clearview
    assert not any(f["object_surface"] == "Clearview AI" for f in fines)
    # the in-clause Company A candidate is emitted, but flagged (not laundered clean)
    ca = [f for f in fines if f["object_surface"] == "Company A"]
    assert ca and ca[0]["subject_surface"] == "ICO"
    assert ca[0]["triage_level"] != "clean"
    assert "contrastive_clause" in ca[0]["predication_flags"]


def test_wrong_party_risk_when_only_company_is_cross_clause(db_conn):
    _doc_with_mentions("The ICO acted, while Clearview AI was fined €5 million.",
                       [("ICO", "regulator"), ("Clearview AI", "company"), ("€5 million", "money")],
                       doc_id="adv-wrong-party-2")
    f = _one_fine(db_conn)
    assert "wrong_party_risk" in f["predication_flags"]
    assert f["triage_level"] == "high_review"


# ---- 8: multiple entities -> multiple-company flag, local pairing -----------

def test_multiple_companies_window_flagged(db_conn):
    _doc_with_mentions("The ICO fined Company A €5 million and later investigated Clearview AI.",
                       [("ICO", "regulator"), ("Company A", "company"),
                        ("€5 million", "money"), ("Clearview AI", "company")], doc_id="adv-multi")
    f = _one_fine(db_conn)
    assert f["object_surface"] == "Company A"           # local, coherent pairing
    assert f["object_surface"] != "Clearview AI"
    assert "multiple_companies_same_window" in f["predication_flags"]


# ---- malformed / no coherent candidate -> still not emitted -----------------

def test_no_money_no_issued_fine_candidate(db_conn):
    # without a value (money), an issued_fine candidate is not coherent -> not emitted.
    # This is the only legitimate "do not emit" case; risk words alone never suppress.
    _doc_with_mentions("Clearview AI was not fined by the Hellenic DPA.",
                       [("Hellenic DPA", "regulator"), ("Clearview AI", "company")])
    assert _fines(db_conn) == []


# ---- apply behavior: risky proposals still require human acceptance ----------

def test_risky_proposal_applies_as_unverified_with_metadata(db_conn):
    _doc_with_mentions("Clearview AI was not fined €20 million by the Hellenic DPA.", _REG_CO_MONEY)
    f = _one_fine(db_conn)
    assert f["triage_level"] == "likely_negative"
    # a human accepts it; the claim is still created, born unverified, with the risk
    # recorded on metadata (provenance) -- the flag never becomes verification.
    report = claim_proposals.apply_claim_proposals([dict(f, accepted=True)])
    assert report["summary"]["applied"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select verification_state, metadata from claim_versions where tx_to is null")
        row = cur.fetchone()
    assert row["verification_state"] == "unverified"
    pred = row["metadata"]["predication"]
    assert pred["triage_level"] == "likely_negative"
    assert "negation_near_trigger" in pred["flags"]


# ============================================================================
# Task 1B: the same high-recall annotation now covers the other predicates.
# Clean direct assertions stay clean/high-confidence; risky ones are emitted with
# flags + lower confidence, never suppressed (except no-coherent-candidate cases).
# ============================================================================

# ---- reduced_fine ----------------------------------------------------------

def test_reduced_fine_clean(db_conn):
    _doc_with_mentions("The CNIL reduced the penalty to €5.2 million.",
                       [("CNIL", "regulator"), ("€5.2 million", "money")], doc_id="rf-clean")
    p = _one(db_conn, "reduced_fine")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []
    assert p["proposal_confidence"] >= 0.7


def test_reduced_fine_negation(db_conn):
    _doc_with_mentions("The penalty was not reduced to €5.2 million by the CNIL.",
                       [("CNIL", "regulator"), ("€5.2 million", "money")], doc_id="rf-neg")
    p = _one(db_conn, "reduced_fine")
    assert p["triage_level"] == "likely_negative"
    assert "negation_near_trigger" in p["predication_flags"]
    assert p["proposal_confidence"] <= 0.4


def test_reduced_fine_reported_secondhand(db_conn):
    _doc_with_mentions("The EDPB noted that the CNIL had reduced the penalty to €5.2 million.",
                       [("EDPB", "regulator"), ("CNIL", "regulator"), ("€5.2 million", "money")],
                       doc_id="rf-reported")
    p = _one(db_conn, "reduced_fine")
    assert "reported_secondhand" in p["predication_flags"]
    assert p["triage_level"] in ("review", "high_review")


# ---- ordered_deletion ------------------------------------------------------

def test_ordered_deletion_clean(db_conn):
    _doc_with_mentions("The CNIL ordered Clearview AI to delete the data.",
                       [("CNIL", "regulator"), ("Clearview AI", "company")], doc_id="od-clean")
    p = _one(db_conn, "ordered_deletion")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []


def test_ordered_deletion_negation(db_conn):
    _doc_with_mentions("The CNIL did not order deletion against Clearview AI.",
                       [("CNIL", "regulator"), ("Clearview AI", "company")], doc_id="od-neg")
    p = _one(db_conn, "ordered_deletion")
    assert p["triage_level"] == "likely_negative"
    assert "negation_near_trigger" in p["predication_flags"]


def test_ordered_deletion_annulled(db_conn):
    _doc_with_mentions("The court later annulled the deletion order against Clearview AI.",
                       [("court", "court"), ("Clearview AI", "company")], doc_id="od-undo")
    p = _one(db_conn, "ordered_deletion")
    assert p["triage_level"] == "high_review"
    assert "procedural_undoing" in p["predication_flags"]


# ---- used_system -----------------------------------------------------------

def test_used_system_clean(db_conn):
    _doc_with_mentions("The Swedish Police used Clearview AI.",
                       [("Swedish Police", "public_body"), ("Clearview AI", "company")],
                       doc_id="us-clean")
    p = _one(db_conn, "used_system")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []


def test_used_system_reported_secondhand(db_conn):
    _doc_with_mentions("The report alleged that Foodinho used the Clearview app.",
                       [("Foodinho", "company")], doc_id="us-reported")
    p = _one(db_conn, "used_system")
    assert "reported_secondhand" in p["predication_flags"]
    assert p["triage_level"] in ("review", "high_review")


# ---- concerns_biometric_processing -----------------------------------------

def test_concerns_biometric_clean(db_conn):
    _doc_with_mentions("The decision concerns biometric processing by Clearview AI.",
                       [("Clearview AI", "company")], doc_id="cb-clean")
    p = _one(db_conn, "concerns_biometric_processing")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []


def test_concerns_biometric_negation(db_conn):
    _doc_with_mentions("The case did not concern Clearview's biometric processing.",
                       [("Clearview", "company")], doc_id="cb-neg")
    p = _one(db_conn, "concerns_biometric_processing")
    assert "negation_near_trigger" in p["predication_flags"]
    assert p["triage_level"] == "likely_negative"


# ---- appeal_allowed --------------------------------------------------------

def test_appeal_allowed_clean(db_conn):
    _doc_with_mentions("The First-tier Tribunal allowed the appeal.",
                       [("First-tier Tribunal", "court")], doc_id="aa-clean")
    p = _one(db_conn, "appeal_allowed")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []  # 'appeal' is subject-matter, not a risk flag here


def test_appeal_allowed_negation(db_conn):
    _doc_with_mentions("The Upper Tribunal did not allow the appeal.",
                       [("Upper Tribunal", "court")], doc_id="aa-neg")
    p = _one(db_conn, "appeal_allowed")
    assert p["triage_level"] == "likely_negative"
    assert "negation_near_trigger" in p["predication_flags"]


def test_appeal_allowed_reported_secondhand(db_conn):
    _doc_with_mentions("The judgment records that the First-tier Tribunal had allowed the appeal.",
                       [("First-tier Tribunal", "court")], doc_id="aa-reported")
    p = _one(db_conn, "appeal_allowed")
    assert "reported_secondhand" in p["predication_flags"]
    assert p["triage_level"] in ("review", "high_review")


def test_appeal_allowed_partial(db_conn):
    _doc_with_mentions("The Upper Tribunal allowed the appeal in part only.",
                       [("Upper Tribunal", "court")], doc_id="aa-partial")
    p = _one(db_conn, "appeal_allowed")
    assert "partial_or_qualified" in p["predication_flags"]
    assert p["triage_level"] == "high_review"


# ---- decision_remitted -----------------------------------------------------

def test_decision_remitted_clean(db_conn):
    _doc_with_mentions("The Upper Tribunal remitted the matter to a new tribunal.",
                       [("Upper Tribunal", "court")], doc_id="dr-clean")
    p = _one(db_conn, "decision_remitted")
    assert p["triage_level"] == "clean"
    assert p["predication_flags"] == []


def test_decision_remitted_declined(db_conn):
    _doc_with_mentions("The tribunal declined to remit the matter.",
                       [("tribunal", "court")], doc_id="dr-neg")
    p = _one(db_conn, "decision_remitted")
    assert "negation_near_trigger" in p["predication_flags"]
    assert p["triage_level"] == "likely_negative"


def test_no_court_no_appeal_candidate(db_conn):
    # malformed / no coherent candidate: appeal_allowed needs a court (or subject)
    _doc_with_mentions("The appeal is allowed.", [], doc_id="aa-nocourt")
    cands = [p for p in claim_proposals.propose_claims(db_conn)["proposals"]
             if p["predicate"] == "appeal_allowed"]
    assert cands == []


# ---- claim-report surfaces triage/flags (Task 1C) --------------------------

def test_claim_report_surfaces_triage_and_flags(db_conn):
    _doc_with_mentions("Clearview AI was not fined €20 million by the Hellenic DPA.", _REG_CO_MONEY)
    f = _one_fine(db_conn)
    assert f["triage_level"] == "likely_negative"
    claim_proposals.apply_claim_proposals([dict(f, accepted=True)])
    report = claim_proposals.build_claim_report(db_conn)
    assert report["claims_with_predication_metadata"] == 1
    assert report["claims_by_triage_level"].get("likely_negative") == 1
    assert "negation_near_trigger" in report["claims_by_predication_flag"]
    assert report["lowest_confidence_claims"][0]["triage_level"] == "likely_negative"
    assert report["lowest_confidence_claims"][0]["verification_state"] == "unverified"
