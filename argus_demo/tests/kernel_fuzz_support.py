"""Deterministic support code for the dedicated ARGUS kernel torture database.

No corpus, NumPy, hypothesis, network, or raw domain SQL writes are used here.
All generated mutations go through argus.actions; SQL is read-only invariant
inspection. Deliberate raw-SQL attack probes live in the test module and are
clearly separated from generated action sequences.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus import actions, db
from argus import export as export_mod


TORTURE_DB = "argus_sol_torture"
TORTURE_DSN = f"postgresql://argus:argus@localhost:5544/{TORTURE_DB}"
DOMAIN_TABLES = (
    "sources", "documents", "document_versions", "evidence_spans", "mentions",
    "entities", "entity_versions", "entity_mentions", "entity_resolution_events",
    "claims", "claim_versions", "claim_relations", "claim_events", "cases",
    "case_items", "action_log",
)


def select_conn():
    conn = db.connect(TORTURE_DSN)
    actual = conn.execute("select current_database() as name").fetchone()["name"]
    if actual != TORTURE_DB:
        conn.close()
        raise AssertionError(f"torture harness refused database {actual!r}")
    return conn


def reset_torture_db() -> None:
    db.init_db(reset=True, dsn=TORTURE_DSN)
    os.environ["DATABASE_URL"] = TORTURE_DSN
    with select_conn() as conn:
        assert conn.execute("select current_database() as n").fetchone()["n"] == TORTURE_DB


def table_counts() -> dict[str, int]:
    with select_conn() as conn:
        return {
            table: conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
            for table in DOMAIN_TABLES
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value) if value is not None and not isinstance(value, (str, int, float, bool)) else value


def normalized_state() -> dict[str, Any]:
    """Semantic current state, deliberately excluding UUIDs and wall-clock values."""
    with select_conn() as conn:
        claims = conn.execute(
            """
            select cv.claim_text, cv.predicate, cv.verification_state,
                   sev.canonical_name as subject, oev.canonical_name as object,
                   cv.value_jsonb, cv.qualifiers_jsonb, es.exact_quote as origin
            from claim_versions cv
            left join entity_versions sev on sev.entity_id=cv.subject_entity_id and sev.tx_to is null
            left join entity_versions oev on oev.entity_id=cv.object_entity_id and oev.tx_to is null
            join evidence_spans es on es.id=cv.evidence_span_id
            where cv.tx_to is null order by cv.claim_text
            """
        ).fetchall()
        relations = conn.execute(
            """
            select s.claim_text as src, d.claim_text as dst, r.relation, es.exact_quote as evidence
            from claim_relations r
            join claim_versions s on s.claim_id=r.src_claim_id and s.tx_to is null
            join claim_versions d on d.claim_id=r.dst_claim_id and d.tx_to is null
            left join evidence_spans es on es.id=r.evidence_span_id
            where r.tx_to is null order by src, dst, r.relation
            """
        ).fetchall()
        assignments = conn.execute(
            """
            select m.surface_text, ev.canonical_name
            from entity_mentions em join mentions m on m.id=em.mention_id
            join entity_versions ev on ev.entity_id=em.entity_id and ev.tx_to is null
            where em.active order by m.surface_text, ev.canonical_name
            """
        ).fetchall()
        versions = conn.execute(
            """
            select cv.claim_text, count(*) as versions
            from claim_versions cv group by cv.claim_id, cv.claim_text order by cv.claim_text
            """
        ).fetchall()
        return _jsonable({
            "claims": claims, "relations": relations,
            "assignments": assignments, "claim_version_counts": versions,
        })


def state_fingerprint() -> str:
    payload = json.dumps(normalized_state(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def immutable_row_snapshot() -> dict[str, Any]:
    """Rows/payload columns that no later action may rewrite in place."""
    specs = {
        "document_versions": ("id", set()),
        "evidence_spans": ("id", set()),
        "entity_versions": ("version_id", {"tx_to"}),
        "claim_versions": ("version_id", {"tx_to"}),
        "claim_relations": ("id", {"tx_to"}),
        "entity_mentions": ("id", {"active", "tx_to"}),
        "claim_events": ("id", set()),
        "entity_resolution_events": ("id", set()),
        "action_log": ("id", set()),
    }
    out: dict[str, Any] = {}
    with select_conn() as conn:
        for table, (key, mutable) in specs.items():
            rows = {}
            for row in conn.execute(f"select * from {table}").fetchall():
                row = dict(row)
                row_id = str(row.pop(key))
                for column in mutable:
                    row.pop(column, None)
                rows[row_id] = _jsonable(row)
            out[table] = rows
    return out


def assert_prior_rows_unchanged(before: dict[str, Any], after: dict[str, Any], *, tag: str) -> None:
    for table, rows in before.items():
        for row_id, payload in rows.items():
            assert row_id in after[table], f"INV-04 {tag}: {table} row {row_id} disappeared"
            assert after[table][row_id] == payload, f"INV-05 {tag}: {table} row {row_id} mutated"


def assert_cheap_invariants(expected_actions: int, *, seed: int, step: int) -> None:
    tag = f"seed={seed} step={step}"
    with select_conn() as conn:
        logs = conn.execute("select count(*) as n from action_log").fetchone()["n"]
        assert logs == expected_actions, f"INV-02 {tag}: logs={logs}, actions={expected_actions}"

        bad = conn.execute(
            """
            select claim_id, count(*) as n from claim_versions where tx_to is null
            group by claim_id having count(*) <> 1
            """
        ).fetchall()
        assert not bad, f"INV-06 {tag}: claim current rows {bad}"
        bad = conn.execute(
            """
            select entity_id, count(*) as n from entity_versions where tx_to is null
            group by entity_id having count(*) <> 1
            """
        ).fetchall()
        assert not bad, f"INV-06 {tag}: entity current rows {bad}"
        bad = conn.execute(
            "select mention_id from entity_mentions where active group by mention_id having count(*) > 1"
        ).fetchall()
        assert not bad, f"INV-16 {tag}: multiple active assignments {bad}"

        spans = conn.execute(
            """
            select es.*, dv.extracted_text from evidence_spans es
            join document_versions dv on dv.id=es.document_version_id
            """
        ).fetchall()
        for span in spans:
            assert span["quote_hash"] == hashlib.sha256(span["exact_quote"].encode()).hexdigest(), tag
            if span["start_char"] is not None and span["end_char"] is not None:
                assert span["extracted_text"][span["start_char"]:span["end_char"]] == span["exact_quote"], tag

        origins = conn.execute(
            """
            select claim_id, array_agg(distinct evidence_span_id) as spans,
                   bool_or(evidence_span_id is null) as has_null
            from claim_versions group by claim_id
            """
        ).fetchall()
        assert all(not r["has_null"] and len(r["spans"]) == 1 for r in origins), f"INV-11 {tag}"

        bad_birth = conn.execute(
            """
            select cv.claim_id from claim_versions cv
            where cv.tx_from=(select min(x.tx_from) from claim_versions x where x.claim_id=cv.claim_id)
              and cv.verification_state not in ('unverified','machine_supported')
            """
        ).fetchall()
        assert not bad_birth, f"INV-12 {tag}: {bad_birth}"

        cache_bad = conn.execute(
            """
            select cv.claim_id from claim_versions cv
            left join entity_mentions sm on sm.mention_id=cv.subject_mention_id and sm.active
            left join entity_mentions om on om.mention_id=cv.object_mention_id and om.active
            where cv.tx_to is null
              and (cv.subject_entity_id is distinct from sm.entity_id
                   or cv.object_entity_id is distinct from om.entity_id)
            """
        ).fetchall()
        assert not cache_bad, f"INV-15 {tag}: {cache_bad}"


def assert_full_invariants(expected_actions: int, *, seed: int, step: int) -> None:
    assert_cheap_invariants(expected_actions, seed=seed, step=step)
    tag = f"seed={seed} step={step}"
    with select_conn() as conn:
        # Closed intervals are coherent and never overlap for versioned objects.
        bad = conn.execute(
            """
            select version_id from claim_versions where tx_to is not null and tx_to < tx_from
            union all
            select version_id from entity_versions where tx_to is not null and tx_to < tx_from
            """
        ).fetchall()
        assert not bad, f"INV-07 {tag}: inverted intervals {bad}"
        overlaps = conn.execute(
            """
            select a.version_id, b.version_id from claim_versions a join claim_versions b
              on a.claim_id=b.claim_id and a.version_id<b.version_id
             and tstzrange(a.tx_from, coalesce(a.tx_to,'infinity'))
                 && tstzrange(b.tx_from, coalesce(b.tx_to,'infinity'))
            """
        ).fetchall()
        assert not overlaps, f"INV-07 {tag}: overlapping claim versions {overlaps}"
        bad_hash = conn.execute(
            "select id from document_versions where raw_hash <> encode(digest(extracted_text, 'sha256'),'hex')"
        ).fetchall()
        assert not bad_hash, f"INV-08 {tag}: {bad_hash}"
        dangling_events = conn.execute(
            """
            select ce.id from claim_events ce left join action_log al on al.id=ce.action_log_id
            where al.id is null
            union all
            select er.id from entity_resolution_events er left join action_log al on al.id=er.action_log_id
            where al.id is null
            """
        ).fetchall()
        assert not dangling_events, f"INV-02 {tag}: dangling event audit links {dangling_events}"


@dataclass
class ActionRunner:
    seed: int
    ids: dict[str, Any] = field(default_factory=dict)
    action_count: int = 0
    active_relations: list[str] = field(default_factory=list)

    def call(self, fn, *args, **kwargs):
        before = immutable_row_snapshot()
        result = fn(*args, **kwargs)
        after = immutable_row_snapshot()
        self.action_count += 1
        assert_prior_rows_unchanged(before, after, tag=f"seed={self.seed} step={self.action_count}")
        assert_cheap_invariants(self.action_count, seed=self.seed, step=self.action_count)
        return result

    def bootstrap(self) -> None:
        source = self.call(actions.create_source, "Synthetic Regulator", "regulator")
        self.ids["source"] = source["source_id"]
        quotes = [
            "Regulator Alpha fined Company One 100 EUR in decision A.",
            "Regulator Alpha fined Company One 200 EUR in decision B.",
        ]
        for i, quote in enumerate(quotes):
            doc = self.call(
                actions.ingest_text_document, quote, title=f"Synthetic {i}",
                source_id=self.ids["source"], metadata={"doc_id": f"synthetic-{i}"},
            )
            self.ids[f"doc{i}"] = doc["document_id"]
            self.ids[f"dv{i}"] = doc["document_version_id"]
            origin = self.call(actions.create_evidence_span, self.ids[f"dv{i}"], exact_quote=quote)
            self.ids[f"span{i}"] = origin["evidence_span_id"]

        for key, name, typ in (
            ("reg", "Regulator Alpha", "regulator"),
            ("co1", "Company One", "company"),
            ("co2", "Company Two", "company"),
        ):
            self.ids[key] = self.call(actions.create_entity, typ, name)["entity_id"]

        for i in range(2):
            reg_span = self.call(actions.create_evidence_span, self.ids[f"dv{i}"], exact_quote="Regulator Alpha")
            co_span = self.call(actions.create_evidence_span, self.ids[f"dv{i}"], exact_quote="Company One")
            self.ids[f"sm{i}"] = self.call(
                actions.create_mention, self.ids[f"dv{i}"], "Regulator Alpha", "regulator",
                evidence_span_id=reg_span["evidence_span_id"],
            )["mention_id"]
            self.ids[f"om{i}"] = self.call(
                actions.create_mention, self.ids[f"dv{i}"], "Company One", "company",
                evidence_span_id=co_span["evidence_span_id"],
            )["mention_id"]
            self.call(actions.assign_mention_to_entity, self.ids[f"sm{i}"], self.ids["reg"])
            self.call(actions.assign_mention_to_entity, self.ids[f"om{i}"], self.ids["co1"])
            self.ids[f"claim{i}"] = self.call(
                actions.create_claim, f"Synthetic claim {i}", "enforcement_action", "issued_fine",
                self.ids[f"span{i}"], subject_mention_id=self.ids[f"sm{i}"],
                object_mention_id=self.ids[f"om{i}"],
                value={"amount": 100 + i * 100, "currency": "EUR"},
                qualifiers={"decision_number": chr(ord('A') + i)},
                valid_from=f"2024-01-0{i + 1}",
            )["claim_id"]

        self.ids["case"] = self.call(actions.create_case, "Synthetic torture case")["case_id"]
        for i in range(2):
            self.call(actions.add_case_item, self.ids["case"], "claim", item_id=self.ids[f"claim{i}"])

    def execute_op(self, op: tuple[str, int | str | None]) -> None:
        kind, arg = op
        if kind == "verify":
            claim_i = int(arg) % 2
            states = ("unverified", "machine_supported", "human_verified", "disputed", "rejected")
            state = states[int(arg) % len(states)]
            kwargs = {"reason": f"seed {self.seed} state {state}"}
            if state == "human_verified":
                kwargs["evidence_span_id"] = self.ids[f"span{claim_i}"]
            self.call(actions.set_claim_verification_state, self.ids[f"claim{claim_i}"], state, **kwargs)
        elif kind == "assign":
            mention_i = int(arg) % 2
            entity = self.ids["co1"] if int(arg) % 2 == 0 else self.ids["co2"]
            self.call(actions.assign_mention_to_entity, self.ids[f"om{mention_i}"], entity)
        elif kind == "relate":
            relation = ("duplicates", "contradicts", "supersedes")[int(arg) % 3]
            result = self.call(
                actions.create_claim_relation, self.ids["claim0"], self.ids["claim1"], relation,
                evidence_span_id=self.ids["span0"], metadata={"seed": self.seed},
            )
            key = f"rel{len(self.active_relations)}"
            self.ids[key] = result["relation_id"]
            self.active_relations.append(key)
        elif kind == "retract" and self.active_relations:
            key = self.active_relations.pop(0)
            self.call(actions.retract_claim_relation, self.ids[key], reason=f"seed {self.seed}")
        elif kind == "note":
            self.call(actions.add_case_item, self.ids["case"], "note", note=f"seed {self.seed} note {arg}")


def generate_plan(seed: int, steps: int) -> list[tuple[str, int]]:
    rng = random.Random(seed)
    plan: list[tuple[str, int]] = []
    active_relations = 0
    for _ in range(steps):
        choices = ["verify", "assign", "relate", "note"]
        if active_relations:
            choices.append("retract")
        kind = rng.choice(choices)
        arg = rng.randrange(0, 20)
        if kind == "relate":
            active_relations += 1
        elif kind == "retract":
            active_relations -= 1
        plan.append((kind, arg))
    return plan


def run_plan(seed: int, steps: int) -> tuple[list[tuple[str, int]], dict[str, Any], int]:
    reset_torture_db()
    runner = ActionRunner(seed)
    runner.bootstrap()
    plan = generate_plan(seed, steps)
    for op in plan:
        runner.execute_op(op)
    assert_full_invariants(runner.action_count, seed=seed, step=runner.action_count)
    return plan, normalized_state(), runner.action_count


def export_path(name: str) -> Path:
    return Path("/tmp") / f"argus-sol-torture-{name}.json"


def build_export(case_id) -> dict[str, Any]:
    with select_conn() as conn:
        with conn.cursor() as cur:
            return export_mod.build_case_export(cur, case_id)


def normalize_export(bundle: dict[str, Any]) -> dict[str, Any]:
    normalized = _jsonable(bundle)
    normalized.get("export", {}).pop("generated_at", None)
    normalized.get("export", {}).pop("action_log_id", None)
    return normalized


def independent_export_errors(bundle: dict[str, Any]) -> list[str]:
    """Independent cross-check of hashes, references, roles, and audit links."""
    errors: list[str] = []
    with select_conn() as conn:
        for entry in bundle.get("items") or []:
            item_type = (entry.get("case_item") or {}).get("item_type")
            if item_type == "document":
                for version in ((entry.get("document") or {}).get("versions") or []):
                    text = version.get("extracted_text") or ""
                    expected = hashlib.sha256(text.encode()).hexdigest()
                    if version.get("raw_hash") != expected:
                        errors.append("document raw hash mismatch")
            if item_type != "claim":
                continue
            claim = entry.get("claim") or {}
            cv = claim.get("current_version") or {}
            evidence = claim.get("evidence_span") or {}
            claim_id = claim.get("claim_id")
            db_cv = conn.execute(
                "select * from claim_versions where claim_id=%s and tx_to is null",
                (claim_id,),
            ).fetchone()
            if db_cv is None:
                errors.append(f"claim {claim_id} missing current DB version")
                continue
            if str(db_cv["version_id"]) != str(cv.get("version_id")):
                errors.append(f"claim {claim_id} current version mismatch")
            if str(db_cv["evidence_span_id"]) != str(evidence.get("id")):
                errors.append(f"claim {claim_id} origin span mismatch")
            quote = evidence.get("exact_quote") or ""
            if evidence.get("quote_hash") != hashlib.sha256(quote.encode()).hexdigest():
                errors.append(f"claim {claim_id} quote hash mismatch")
            roles = claim.get("evidence_roles") or {}
            origin = roles.get("origin") or {}
            if str(origin.get("evidence_span_id")) != str(evidence.get("id")):
                errors.append(f"claim {claim_id} origin role mismatch")
            if origin.get("quote") != evidence.get("exact_quote"):
                errors.append(f"claim {claim_id} origin quote mismatch")

            db_events = conn.execute(
                "select * from claim_events where claim_id=%s order by created_at,id", (claim_id,)
            ).fetchall()
            exported_events = claim.get("events") or []
            if [str(e["id"]) for e in db_events] != [str(e.get("id")) for e in exported_events]:
                errors.append(f"claim {claim_id} event history mismatch")
            for event in exported_events:
                if not conn.execute("select 1 from action_log where id=%s", (event.get("action_log_id"),)).fetchone():
                    errors.append(f"claim {claim_id} event has dangling action log")

            expected_ver = [e for e in db_events if e["event_type"] == "verification_state_change"]
            if len(roles.get("verification_events") or []) != len(expected_ver):
                errors.append(f"claim {claim_id} verification role count mismatch")
            current_relations = conn.execute(
                """
                select id from claim_relations
                where tx_to is null and (src_claim_id=%s or dst_claim_id=%s)
                order by id
                """,
                (claim_id, claim_id),
            ).fetchall()
            role_ids = sorted(str(r.get("relation_id")) for r in roles.get("relation_events") or [])
            if role_ids != sorted(str(r["id"]) for r in current_relations):
                errors.append(f"claim {claim_id} relation role mismatch")
    return errors


def as_of_state(at) -> dict[str, Any]:
    """Normalize versioned state at one transaction-time boundary."""
    with select_conn() as conn:
        claims = conn.execute(
            """
            select cv.claim_text, cv.predicate, cv.verification_state,
                   sev.canonical_name as subject, oev.canonical_name as object,
                   cv.value_jsonb, cv.qualifiers_jsonb, es.exact_quote as origin
            from claim_versions cv
            left join entity_mentions sm on sm.mention_id=cv.subject_mention_id
              and sm.tx_from <= %s and (sm.tx_to is null or sm.tx_to > %s)
            left join entity_versions sev on sev.entity_id=sm.entity_id
              and sev.tx_from <= %s and (sev.tx_to is null or sev.tx_to > %s)
            left join entity_mentions om on om.mention_id=cv.object_mention_id
              and om.tx_from <= %s and (om.tx_to is null or om.tx_to > %s)
            left join entity_versions oev on oev.entity_id=om.entity_id
              and oev.tx_from <= %s and (oev.tx_to is null or oev.tx_to > %s)
            join evidence_spans es on es.id=cv.evidence_span_id
            where cv.tx_from <= %s and (cv.tx_to is null or cv.tx_to > %s)
            order by cv.claim_text
            """,
            (at, at, at, at, at, at, at, at, at, at),
        ).fetchall()
        relations = conn.execute(
            """
            select s.claim_text as src, d.claim_text as dst, r.relation, es.exact_quote as evidence
            from claim_relations r
            join claim_versions s on s.claim_id=r.src_claim_id
              and s.tx_from <= %s and (s.tx_to is null or s.tx_to > %s)
            join claim_versions d on d.claim_id=r.dst_claim_id
              and d.tx_from <= %s and (d.tx_to is null or d.tx_to > %s)
            left join evidence_spans es on es.id=r.evidence_span_id
            where r.tx_from <= %s and (r.tx_to is null or r.tx_to > %s)
            order by src,dst,r.relation
            """,
            (at, at, at, at, at, at),
        ).fetchall()
        assignments = conn.execute(
            """
            select m.surface_text, ev.canonical_name
            from entity_mentions em join mentions m on m.id=em.mention_id
            join entity_versions ev on ev.entity_id=em.entity_id
              and ev.tx_from <= %s and (ev.tx_to is null or ev.tx_to > %s)
            where em.tx_from <= %s and (em.tx_to is null or em.tx_to > %s)
            order by m.surface_text, ev.canonical_name
            """,
            (at, at, at, at),
        ).fetchall()
        return _jsonable({"claims": claims, "relations": relations, "assignments": assignments})
