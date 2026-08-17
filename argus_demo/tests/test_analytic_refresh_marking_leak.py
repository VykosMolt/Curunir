"""V6.7 exploit lock — background refresh must not declassify a compartmented
analytic object (adversarial-review Finding 1, CRITICAL).

A SPECIAL theme authored by a cleared analyst, then refreshed on the ordinary
PUBLIC propagation/watch pass, was re-appended at the context marking — its
current version became PUBLIC and its analyst-authored title/description became
readable by an uncleared actor. A re-append must never re-classify DOWN: the
version is floored on the object's own marking at the single append chokepoint,
whichever engine performed the re-append.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.substrate import AnalyticContext
from curunir_analytic.themes import create_theme, refresh_theme
from curunir_fabric.contracts import ManifestationRecord
from curunir_workbench.projections import MissionProjection

from semantic_support import T0, plant_manifestation
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"
SECRET_TITLE = "COVERT-OP-BLUEJAY-TARGETS-ACME"


def _gleif(status: str) -> bytes:
    return (
        '{"data": {"id": "ACMELEI000000000001", "attributes": {"lei": '
        '"ACMELEI000000000001", "entity": {"legalName": {"name": "Acme AS"}, '
        '"jurisdiction": "NO", "status": "%s", "otherNames": [], "legalAddress": '
        '{"city": "Oslo", "country": "NO"}}, "registration": {"status": "ISSUED", '
        '"initialRegistrationDate": "2014-03-02", "lastUpdateDate": '
        '"2026-08-16"}}}}' % status).encode("utf-8")


def _plant_special(pipeline, status: str, retrieval_time: str, prior=None) -> str:
    body = _gleif(status)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = "lei/ACMELEI000000000001"
    mid = digest_id("manifestation", native, digest)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=mid, source_id="gleif", connector_id="gleif-test",
        connector_version="1.0", native_id=native, request_url=native, final_url=native,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=retrieval_time, http_status=200,
        redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native, retrieval_time),
        prior_manifestation_id=prior, marking=RESTRICTED_MARK),
        recorded_time=pipeline.now_fn(), actor="t")
    return mid


def test_public_refresh_does_not_declassify_special_theme(tmp_path):
    pipeline, public_ctx = make_workbench(tmp_path)  # public_ctx.marking is PUBLIC
    store = public_ctx.store
    v1 = _plant_special(pipeline, "ACTIVE", T0)
    pipeline.process_new_evidence()
    status_claim = next(c for c in store.current_claims().values()
                        if c["subject_ref"] == ACME and c["predicate"] == "entity_status")
    assert status_claim["marking"]["compartments"] == ["SPECIAL"]

    # a CLEARED analyst authors a SPECIAL theme (compartmented tasking)
    cleared_ctx = AnalyticContext(store=store, actor="analyst-a",
                                  marking=RESTRICTED_MARK, now_fn=public_ctx.now_fn)
    theme = create_theme(cleared_ctx, title=SECRET_TITLE,
                         description="compartmented tasking rationale",
                         supporting_claim_ids=[status_claim["claim_id"]])
    tid = theme["theme_id"]
    assert theme["marking"]["compartments"] == ["SPECIAL"]
    assert MissionProjection(store, CTX_B).get("analytic_theme", tid) is None

    # a materially different SPECIAL manifestation lands, then the ordinary
    # PUBLIC propagation pass refreshes the theme
    _plant_special(pipeline, "INACTIVE", "2026-08-17T15:00:00+00:00", prior=v1)
    pipeline.process_new_evidence()
    refreshed = refresh_theme(public_ctx, tid, caused_by="propagate")

    # the theme is still SPECIAL — its title never reaches the uncleared actor
    assert refreshed["marking"]["compartments"] == ["SPECIAL"], \
        "a PUBLIC-context refresh declassified a compartmented theme"
    proj_b = MissionProjection(store, CTX_B)
    assert proj_b.get("analytic_theme", tid) is None
    assert SECRET_TITLE not in json.dumps(proj_b.overview())
    assert not proj_b.transitions(tid), "even the theme's transitions stay hidden"
    # the cleared actor still sees it (capability preserved)
    assert MissionProjection(store, CTX_A).get("analytic_theme", tid) is not None
