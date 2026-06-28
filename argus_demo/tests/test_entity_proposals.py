"""Entity proposal + curated resolution pass: read-only clustering of existing
mentions, then applying accepted clusters via the action handlers (entities +
entity_mentions only).

Uses temporary fixture mentions, never the real corpus.
"""
from __future__ import annotations

from argus import actions, entity_proposals


def _doc(source_name: str = "Test Source", doc_id: str = "d1") -> str:
    source_id = actions.create_source(source_name, "regulator")["source_id"]
    res = actions.ingest_text_document(
        "fixture text", title="Doc", source_id=source_id,
        metadata={"doc_id": doc_id, "manifest": {}},
    )
    return str(res["document_version_id"])


def _mention(dv: str, surface: str, mtype: str) -> str:
    return str(actions.create_mention(dv, surface, mtype)["mention_id"])


# ---- 1, 6: read-only; defaults accepted:false -------------------------------

def test_propose_entities_is_read_only_and_unaccepted(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    payload = entity_proposals.propose_entities(db_conn)
    assert payload["summary"]["total_entity_proposals"] >= 1
    assert all(p["accepted"] is False for p in payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from entity_mentions")
        assert cur.fetchone()["n"] == 0


# ---- 2: only eligible mention types -----------------------------------------

def test_only_eligible_mention_types_considered(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    _mention(dv, "€20 million", "money")
    _mention(dv, "Article 9", "legal_article")
    _mention(dv, "2022-07-13", "date")
    payload = entity_proposals.propose_entities(db_conn)
    s = payload["summary"]
    assert s["total_mentions_scanned"] == 4
    assert s["eligible_mentions_scanned"] == 1
    for p in payload["proposals"]:
        assert p["entity_type"] in entity_proposals.ELIGIBLE_MENTION_TYPES


# ---- 3: Clearview variants cluster to one -----------------------------------

def test_clearview_variants_cluster_to_one(db_conn):
    dv = _doc()
    for surface in ["Clearview AI", "Clearview AI, Inc.", "CLEARVIEW AI", "Clearview AI Inc"]:
        _mention(dv, surface, "company")
    payload = entity_proposals.propose_entities(db_conn)
    cv = [p for p in payload["proposals"] if "clearview" in p["canonical_name"].lower()]
    assert len(cv) == 1
    assert cv[0]["canonical_name"] == "Clearview AI, Inc."
    assert cv[0]["entity_type"] == "company"
    assert len(cv[0]["mention_ids"]) == 4
    assert cv[0]["heuristic_name"] == "known_alias_cluster"
    assert set(cv[0]["surface_forms"]) == {"Clearview AI", "Clearview AI, Inc.", "CLEARVIEW AI", "Clearview AI Inc"}


# ---- 4: regulator aliases cluster (EDPB + ICO) ------------------------------

def test_regulator_aliases_cluster(db_conn):
    dv = _doc()
    _mention(dv, "EDPB", "regulator")
    _mention(dv, "European Data Protection Board", "regulator")
    _mention(dv, "ICO", "regulator")
    _mention(dv, "Information Commissioner's Office", "regulator")
    by_canon = {p["canonical_name"]: p for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert len(by_canon["European Data Protection Board"]["mention_ids"]) == 2
    assert len(by_canon["Information Commissioner's Office"]["mention_ids"]) == 2


# ---- 5: ambiguous short tokens not over-merged unless safe -------------------

def test_ambiguous_ap_not_merged_without_context(db_conn):
    dv = _doc(source_name="Some Other Authority", doc_id="x")
    _mention(dv, "AP", "regulator")
    payload = entity_proposals.propose_entities(db_conn)
    canons = {p["canonical_name"] for p in payload["proposals"]}
    assert "Autoriteit Persoonsgegevens" not in canons
    ap = [p for p in payload["proposals"] if "ap" in p["normalized_keys"]]
    assert ap and ap[0]["heuristic_name"] == "singleton"


def test_ambiguous_ap_merges_with_dutch_context(db_conn):
    dv = _doc(source_name="Autoriteit Persoonsgegevens", doc_id="nl")
    _mention(dv, "AP", "regulator")
    canons = {p["canonical_name"] for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "Autoriteit Persoonsgegevens" in canons


# ---- 7: apply one accepted -> one entity + assignments ----------------------

def test_apply_one_accepted_creates_entity_and_assigns(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    _mention(dv, "CLEARVIEW AI", "company")
    payload = entity_proposals.propose_entities(db_conn)
    cv = dict(next(p for p in payload["proposals"] if "clearview" in p["canonical_name"].lower()),
              accepted=True)
    report = entity_proposals.apply_entity_proposals([cv])
    assert report["entities_created"] == 1
    assert report["assignments_made"] == 2
    assert report["cluster_summary"]["applied"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == 1
        cur.execute("select canonical_name from entity_versions where tx_to is null")
        assert cur.fetchone()["canonical_name"] == "Clearview AI, Inc."
        cur.execute("select count(*) as n from entity_mentions where active")
        assert cur.fetchone()["n"] == 2


# ---- 8: none accepted -> nothing --------------------------------------------

def test_apply_nothing_when_none_accepted(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    payload = entity_proposals.propose_entities(db_conn)
    report = entity_proposals.apply_entity_proposals(payload["proposals"])
    assert report["entities_created"] == 0
    assert report["cluster_summary"]["skipped_not_accepted"] == len(payload["proposals"])
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == 0


# ---- 9: re-apply is idempotent ----------------------------------------------

def test_reapply_is_idempotent(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    _mention(dv, "Clearview AI, Inc.", "company")
    cv = dict(next(p for p in entity_proposals.propose_entities(db_conn)["proposals"]
                   if "clearview" in p["canonical_name"].lower()), accepted=True)
    first = entity_proposals.apply_entity_proposals([cv])
    assert first["entities_created"] == 1 and first["assignments_made"] == 2

    second = entity_proposals.apply_entity_proposals([cv])
    assert second["entities_created"] == 0
    assert second["entities_reused"] == 1
    assert second["assignments_made"] == 0
    assert second["mention_summary"]["already_assigned_to_same_entity"] == 2
    assert second["cluster_summary"]["duplicate_existing"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] == 1
        cur.execute("select count(*) as n from entity_mentions")
        assert cur.fetchone()["n"] == 2  # no extra (closed) assignment rows


# ---- 10: apply creates no claims --------------------------------------------

def test_apply_creates_no_claims(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    _mention(dv, "CNIL", "regulator")
    payload = entity_proposals.propose_entities(db_conn)
    entity_proposals.apply_entity_proposals([dict(p, accepted=True) for p in payload["proposals"]])
    with db_conn.cursor() as cur:
        for table in ("claims", "claim_versions", "claim_relations"):
            cur.execute(f"select count(*) as n from {table}")
            assert cur.fetchone()["n"] == 0, table
        cur.execute("select count(*) as n from entities")
        assert cur.fetchone()["n"] >= 1


# ---- 11: conflict not silently reassigned -----------------------------------

def test_conflict_not_reassigned_without_flag(db_conn):
    dv = _doc()
    m = _mention(dv, "Clearview AI", "company")
    other = actions.create_entity("company", "Other Co")["entity_id"]
    actions.assign_mention_to_entity(m, other)

    cv = dict(next(p for p in entity_proposals.propose_entities(db_conn)["proposals"]
                   if "clearview" in p["canonical_name"].lower()), accepted=True)
    report = entity_proposals.apply_entity_proposals([cv])  # allow_reassign not set
    assert report["mention_summary"]["error"] == 1
    assert report["mention_summary"].get("reassigned", 0) == 0
    with db_conn.cursor() as cur:
        cur.execute("select entity_id from entity_mentions where mention_id = %s and active", (m,))
        assert cur.fetchone()["entity_id"] == other  # untouched

    report2 = entity_proposals.apply_entity_proposals([dict(cv, allow_reassign=True)])
    assert report2["mention_summary"]["reassigned"] == 1
    with db_conn.cursor() as cur:
        cur.execute("select entity_id from entity_mentions where mention_id = %s and active", (m,))
        new_ent = cur.fetchone()["entity_id"]
        cur.execute("select canonical_name from entity_versions where entity_id = %s and tx_to is null", (new_ent,))
        assert cur.fetchone()["canonical_name"] == "Clearview AI, Inc."


# ---- 12: entity-report counts -----------------------------------------------

def test_entity_report_counts(db_conn):
    dv = _doc()
    _mention(dv, "Clearview AI", "company")
    _mention(dv, "CLEARVIEW AI", "company")
    _mention(dv, "CNIL", "regulator")
    payload = entity_proposals.propose_entities(db_conn)
    entity_proposals.apply_entity_proposals([dict(p, accepted=True) for p in payload["proposals"]])
    report = entity_proposals.build_entity_report(db_conn)
    assert report["total_entities"] == 2
    assert report["total_active_entity_mentions"] == 3
    assert report["entities_by_type"].get("company") == 1
    assert report["entities_by_type"].get("regulator") == 1
    assert report["unassigned_eligible_mentions"] == 0
    assert report["clearview_entity"] and report["clearview_entity"][0]["mentions"] == 2
    assert "create_entity" in report["action_log_counts"]


# ---- CNIL / French SA alias + restricted-committee context guard ------------

def test_french_sa_and_cnil_cluster_to_cnil(db_conn):
    dv = _doc(doc_id="fr-cnil-2023-clearview-penalty")
    _mention(dv, "French SA", "regulator")
    _mention(dv, "CNIL", "regulator")
    by_canon = {p["canonical_name"]: p for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "CNIL" in by_canon
    assert len(by_canon["CNIL"]["mention_ids"]) == 2


def test_restricted_committee_not_clustered_without_french_context(db_conn):
    dv = _doc(source_name="Some School Board", doc_id="xx-school-2020")
    _mention(dv, "restricted committee", "regulator")
    canons = {p["canonical_name"] for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "CNIL" not in canons  # generic restricted committee is not blindly mapped


def test_ftt_and_first_tier_tribunal_cluster_to_one(db_conn):
    dv = _doc(doc_id="uk-ico-2025-clearview-ut-judgment")
    _mention(dv, "First-tier Tribunal", "court")
    _mention(dv, "FTT", "court")
    by_canon = {p["canonical_name"]: p for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "First-tier Tribunal" in by_canon
    assert by_canon["First-tier Tribunal"]["entity_type"] == "court"
    assert len(by_canon["First-tier Tribunal"]["mention_ids"]) == 2


def test_chair_of_the_cnil_clusters_to_cnil(db_conn):
    dv = _doc(doc_id="fr-cnil-2022-clearview-fine")
    _mention(dv, "Chair of the CNIL", "regulator")
    _mention(dv, "CNIL", "regulator")
    by_canon = {p["canonical_name"]: p for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "CNIL" in by_canon
    assert len(by_canon["CNIL"]["mention_ids"]) == 2


def test_restricted_committee_clusters_to_cnil_in_cnil_doc(db_conn):
    dv = _doc(source_name="EDPB", doc_id="fr-cnil-2023-clearview-penalty")
    _mention(dv, "restricted committee", "regulator")
    _mention(dv, "CNIL", "regulator")
    by_canon = {p["canonical_name"]: p for p in entity_proposals.propose_entities(db_conn)["proposals"]}
    assert "CNIL" in by_canon
    assert len(by_canon["CNIL"]["mention_ids"]) == 2
