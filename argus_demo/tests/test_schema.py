"""Proof 1: the schema loads, with the expected tables, views, and guard indexes."""
from __future__ import annotations

EXPECTED_TABLES = {
    "sources", "documents", "document_versions", "evidence_spans", "mentions",
    "entities", "entity_versions", "entity_mentions", "entity_resolution_events",
    "claims", "claim_versions", "claim_relations", "action_log", "claim_events",
    "cases", "case_items",
}


def test_all_tables_present(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables "
            "where table_schema = 'public' and table_type = 'BASE TABLE'"
        )
        tables = {r["table_name"] for r in cur.fetchall()}
    assert EXPECTED_TABLES <= tables, f"missing: {EXPECTED_TABLES - tables}"


def test_current_views_present(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.views where table_schema = 'public'"
        )
        views = {r["table_name"] for r in cur.fetchall()}
    assert {"current_entity_versions", "current_claim_versions"} <= views


def test_pgcrypto_available(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("select gen_random_uuid() as u")
        assert cur.fetchone()["u"] is not None


def test_one_current_version_guard_indexes_exist(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("select indexname from pg_indexes where schemaname = 'public'")
        idx = {r["indexname"] for r in cur.fetchall()}
    for name in (
        "claim_versions_one_current",
        "entity_versions_one_current",
        "entity_mentions_one_active",
    ):
        assert name in idx, f"missing guard index {name}"


def test_verification_state_check_constraint_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "select 1 from information_schema.check_constraints "
            "where constraint_schema = 'public' and check_clause ilike '%verification_state%'"
        )
        assert cur.fetchone() is not None


def test_entity_resolution_events_action_log_fk_exists(db_conn):
    """Patch 2: entity_resolution_events.action_log_id is a real FK to action_log.id."""
    with db_conn.cursor() as cur:
        cur.execute(
            """
            select a.attname as column_name
            from pg_constraint c
            join pg_attribute a
              on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
            where c.contype = 'f'
              and c.conrelid = 'entity_resolution_events'::regclass
              and c.confrelid = 'action_log'::regclass
            """
        )
        columns = {r["column_name"] for r in cur.fetchall()}
    assert "action_log_id" in columns, "missing FK entity_resolution_events.action_log_id -> action_log.id"


def test_evidence_spans_have_anchoring_columns(db_conn):
    """Patch 3: evidence_spans gained anchoring_status / anchoring_note."""
    with db_conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'evidence_spans'"
        )
        cols = {r["column_name"] for r in cur.fetchall()}
    assert {"anchoring_status", "anchoring_note"} <= cols
