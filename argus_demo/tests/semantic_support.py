"""Shared fixtures for the semantic-plane tests: a store with custody and a
helper that plants deterministic manifestations the way the fabric would."""
from __future__ import annotations

import hashlib
from itertools import count
from pathlib import Path

from argus.source_intelligence.models import digest_id
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import ManifestationRecord
from curunir_operational.access import Marking
from curunir_semantic.pipeline import SemanticPipeline
from curunir_semantic.store import SemanticStore

T0 = "2026-08-17T12:00:00+00:00"
MARK = Marking(owning_authority="semantic-test", releasability=("PUBLIC",))


def clock(start_minute: int = 1):
    ticks = count(start_minute)

    def now() -> str:
        minutes = next(ticks)
        return f"2026-08-17T{12 + minutes // 60:02d}:{minutes % 60:02d}:00+00:00"
    return now


def make_pipeline(tmp_path: Path, *, seeded: bool = True, start_minute: int = 1) -> SemanticPipeline:
    root = tmp_path / "store"
    if (root / "store_meta.json").exists():
        store = SemanticStore(root)
    else:
        store = SemanticStore.create(root, "semantic-test", T0)
        if seeded:
            seed_starter_catalog(store, recorded_time=T0, actor="t")
    return SemanticPipeline(store=store, custody_root=tmp_path / "custody",
                            actor="t", marking=MARK, now_fn=clock(start_minute))


def plant_manifestation(pipeline: SemanticPipeline, *, source_id: str, native_id: str,
                        body: bytes, media_type: str, retrieval_time: str,
                        temporal_status: str = "LIVE",
                        source_time: str | None = None,
                        archive_capture_time: str | None = None,
                        request_url: str = "", final_url: str = "",
                        prior_manifestation_id: str | None = None) -> dict:
    """Preserve bytes in custody and append the manifestation record, exactly
    as the fabric executor would."""
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    manifestation = ManifestationRecord(
        manifestation_id=digest_id("manifestation", source_id, native_id, digest, retrieval_time),
        source_id=source_id, connector_id=f"{source_id}-test", connector_version="1.0",
        native_id=native_id, request_url=request_url or native_id,
        final_url=final_url or request_url or native_id,
        content_sha256=digest, content_store_path=str(path), media_type=media_type,
        temporal_status=temporal_status, source_time=source_time,
        archive_capture_time=archive_capture_time,
        retrieval_time=retrieval_time, http_status=200, redirects=(),
        etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest, retrieval_time),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", source_id, digest),
        execution_id=digest_id("execution", source_id, native_id, retrieval_time),
        prior_manifestation_id=prior_manifestation_id, marking=MARK,
    )
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", manifestation,
                          recorded_time=pipeline.now_fn(), actor="t")
    return manifestation.to_record()


GLEIF_RECORD_V1 = b"""{
  "data": {"id": "TESTLEI0000000000001", "attributes": {
    "lei": "TESTLEI0000000000001",
    "entity": {"legalName": {"name": "Vessia Steel AS"}, "jurisdiction": "NO",
               "status": "ACTIVE", "otherNames": [],
               "legalAddress": {"city": "Oslo", "country": "NO"}},
    "registration": {"status": "ISSUED", "initialRegistrationDate": "2015-04-01",
                     "lastUpdateDate": "2026-08-01T00:00:00+00:00"}}}}
"""
GLEIF_RECORD_V2 = GLEIF_RECORD_V1.replace(b"Vessia Steel AS", b"Vessia Materials AS") \
    .replace(b"2026-08-01", b"2026-08-16")
GLEIF_RECORD_LAPSED = GLEIF_RECORD_V1.replace(b'"ISSUED"', b'"LAPSED"') \
    .replace(b"2026-08-01", b"2026-08-16")

PAGE_V1 = b"""<html><head><title>Vessia Steel AS</title>
<meta charset="utf-8"></head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<h1>Vessia Steel AS</h1>
<p>Managing Director: Kari Nordmann.</p>
<footer>Published by Vessia Steel AS</footer></body></html>"""
PAGE_V1_CHROME_ONLY = PAGE_V1.replace(b'<nav><a href="/">Home</a><a href="/about">About</a></nav>',
                                      b'<nav class="new"><a href="/">Start</a></nav>')
PAGE_V2_SEMANTIC = PAGE_V1.replace(b"Kari Nordmann", b"Ola Hansen")
PAGE_CORRECTION = PAGE_V1.replace(
    b"<p>Managing Director: Kari Nordmann.</p>",
    b"<p>Correction: an earlier version misstated the managing director. "
    b"Managing Director: Ola Hansen.</p>")
