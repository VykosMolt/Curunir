"""V6.7 exploit lock — authoring/mutating an analytic object over restricted
evidence must not under-classify it (adversarial-review surviving MAJOR; §107#4).

Authoring a PUBLIC-context object whose basis is a restricted claim, or folding
a restricted claim into an existing lower-marked object, previously retained the
weaker marking — the object's analyst free-text (title/description/position)
then reached an uncleared actor. The single `append_version` chokepoint now
raises every version to cover the material claims it rests on, so this holds for
creation, mutation and refresh regardless of the calling engine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.themes import create_theme, update_membership
from curunir_fabric.contracts import ManifestationRecord
from curunir_workbench.projections import MissionProjection

from semantic_support import T0, plant_manifestation
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"
SECRET_TITLE = "COVERTOP-BLUEJAY-SEALEDINTEL"


def _gleif(lei: str, status: str = "ACTIVE") -> bytes:
    return (
        '{"data": {"id": "%s", "attributes": {"lei": "%s", "entity": '
        '{"legalName": {"name": "E %s"}, "jurisdiction": "NO", "status": "%s", '
        '"otherNames": [], "legalAddress": {"city": "Oslo", "country": "NO"}}, '
        '"registration": {"status": "ISSUED", "initialRegistrationDate": '
        '"2014-03-02", "lastUpdateDate": "2026-08-01"}}}}' % (lei, lei, lei, status)
    ).encode("utf-8")


def _plant_special(pipeline, lei: str) -> None:
    body = _gleif(lei)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = f"lei/{lei}"
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native, request_url=native, final_url=native,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=T0, http_status=200, redirects=(),
        etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native), prior_manifestation_id=None,
        marking=RESTRICTED_MARK), recorded_time=pipeline.now_fn(), actor="t")


def _status_claim(store, lei: str) -> dict:
    return next(c for c in store.current_claims().values()
               if c["subject_ref"] == f"LEI:{lei}" and c["predicate"] == "entity_status")


def test_authoring_theme_over_restricted_claim_is_not_public(tmp_path):
    pipeline, public_ctx = make_workbench(tmp_path)  # public_ctx.marking is PUBLIC
    store = public_ctx.store
    _plant_special(pipeline, "SEC0000000000000010")
    pipeline.process_new_evidence()
    special_claim = _status_claim(store, "SEC0000000000000010")
    assert special_claim["marking"]["compartments"] == ["SPECIAL"]

    # author a theme in a PUBLIC context whose ONLY support is the SPECIAL claim,
    # with restricted analyst free-text in the title
    theme = create_theme(public_ctx, title=SECRET_TITLE,
                         description="compartmented tasking rationale",
                         supporting_claim_ids=[special_claim["claim_id"]])
    tid = theme["theme_id"]
    assert theme["marking"]["compartments"] == ["SPECIAL"], \
        "authoring over a SPECIAL claim must not retain a PUBLIC marking"

    proj_b = MissionProjection(store, CTX_B)
    assert proj_b.get("analytic_theme", tid) is None
    assert SECRET_TITLE not in json.dumps(proj_b.overview())
    assert MissionProjection(store, CTX_A).get("analytic_theme", tid) is not None


def test_folding_restricted_claim_into_public_theme_raises_it(tmp_path):
    pipeline, public_ctx = make_workbench(tmp_path)
    store = public_ctx.store
    # a genuinely PUBLIC theme over a PUBLIC claim, visible to the uncleared actor
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/PUB0000000000000010",
                        body=_gleif("PUB0000000000000010"), media_type="application/json",
                        retrieval_time=T0)
    _plant_special(pipeline, "SEC0000000000000011")
    pipeline.process_new_evidence()
    public_claim = _status_claim(store, "PUB0000000000000010")
    special_claim = _status_claim(store, "SEC0000000000000011")
    theme = create_theme(public_ctx, title="ordinary-public-theme",
                         supporting_claim_ids=[public_claim["claim_id"]])
    tid = theme["theme_id"]
    assert theme["marking"]["compartments"] == []
    assert MissionProjection(store, CTX_B).get("analytic_theme", tid) is not None

    # fold a SPECIAL claim into it — the new version must inherit SPECIAL
    update_membership(public_ctx, tid, add_supporting=[special_claim["claim_id"]],
                      caused_by="fold", rationale="new supporting evidence")
    current = store.current_themes()[tid]
    assert current["marking"]["compartments"] == ["SPECIAL"], \
        "folding a SPECIAL claim into a PUBLIC theme must raise the version"
    assert MissionProjection(store, CTX_B).get("analytic_theme", tid) is None
    assert MissionProjection(store, CTX_A).get("analytic_theme", tid) is not None
