"""Shared fixtures for the analytic tests: a store-backed pipeline over planted
evidence, plus the fixed GLEIF records the analytic engines read."""
from __future__ import annotations

from pathlib import Path

from curunir_analytic.store import AnalyticStore
from curunir_analytic.substrate import AnalyticContext
from curunir_fabric.catalog import seed_starter_catalog
from curunir_semantic.pipeline import SemanticPipeline

from semantic_support import MARK, T0, clock, plant_manifestation


def make_analytic(tmp_path: Path, *, seeded: bool = True, start_minute: int = 1
                  ) -> tuple[SemanticPipeline, AnalyticContext]:
    root = tmp_path / "store"
    if (root / "store_meta.json").exists():
        store = AnalyticStore(root)
    else:
        store = AnalyticStore.create(root, "analytic-test", T0)
        if seeded:
            seed_starter_catalog(store, recorded_time=T0, actor="t")
    now_fn = clock(start_minute)
    pipeline = SemanticPipeline(store=store, custody_root=tmp_path / "custody",
                                actor="t", marking=MARK, now_fn=now_fn)
    ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=now_fn)
    return pipeline, ctx


GLEIF_ACME = b"""{
  "data": {"id": "ACMELEI000000000001", "attributes": {
    "lei": "ACMELEI000000000001",
    "entity": {"legalName": {"name": "Acme Industri AS"}, "jurisdiction": "NO",
               "status": "ACTIVE", "otherNames": [],
               "legalAddress": {"city": "Oslo", "country": "NO"}},
    "registration": {"status": "ISSUED", "initialRegistrationDate": "2014-03-02",
                     "lastUpdateDate": "2026-08-01"}}}
}"""

GLEIF_ACME_SUSPENDED = b"""{
  "data": {"id": "ACMELEI000000000001", "attributes": {
    "lei": "ACMELEI000000000001",
    "entity": {"legalName": {"name": "Acme Industri AS"}, "jurisdiction": "NO",
               "status": "INACTIVE", "otherNames": [],
               "legalAddress": {"city": "Oslo", "country": "NO"}},
    "registration": {"status": "LAPSED", "initialRegistrationDate": "2014-03-02",
                     "lastUpdateDate": "2026-08-10"}}}
}"""


ACME = "LEI:ACMELEI000000000001"


def seed_acme(pipeline, ctx, body=GLEIF_ACME):
    """Plant the Acme registry record and index the resulting claims by predicate."""
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=body, media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    return {c["predicate"]: c["claim_id"] for c in ctx.store.current_claims().values()
            if c["subject_ref"] == ACME}


def plant_page(pipeline, *, url: str, body: str, retrieval_time: str,
               source_id: str = "live-web", **kwargs) -> dict:
    return plant_manifestation(
        pipeline, source_id=source_id, native_id=url, body=body.encode("utf-8"),
        media_type="text/html", retrieval_time=retrieval_time,
        request_url=url, final_url=url, **kwargs)


def statement_page(statement: str, extra: str = "") -> str:
    """A tiny page whose "Statement:" line the extractor captures verbatim."""
    return (f"<html><head><title>Notice</title></head><body>"
            f"<p>Statement: {statement}.</p><p>{extra}</p></body></html>")
