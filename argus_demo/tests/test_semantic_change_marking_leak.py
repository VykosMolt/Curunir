"""V6.7 exploit lock — the watch/background semantic-change path must not
carry a restricted manifestation's value into a lower-marked derived record.

`interpret_change` runs on the watch path with the pipeline's default (PUBLIC)
context, yet the SemanticChangeRecord it emits copies the changed observation's
literal value into `current_value`/`detail`, the claim-state and review-item it
propagates repeat that detail, and the semantic alert body (`explain_change`)
quotes it again. When the current manifestation is RESTRICTED, every one of
those derived records must inherit its marking, so a lower-cleared actor learns
nothing about the restricted change through the ordinary projection.

Behavioral lock: asserts on what CTX_B (no SPECIAL compartment) can read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_fabric.contracts import ManifestationRecord
from curunir_semantic.changes import interpret_change
from curunir_workbench.projections import MissionProjection

from semantic_support import T0, plant_manifestation
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"
SECRET_STATUS = "CLASSIFIEDXYZZY"


def _gleif_body(status: str) -> bytes:
    return (
        '{"data": {"id": "ACMELEI000000000001", "attributes": {'
        '"lei": "ACMELEI000000000001", "entity": {"legalName": {"name": '
        '"Acme Industri AS"}, "jurisdiction": "NO", "status": "%s", '
        '"otherNames": [], "legalAddress": {"city": "Oslo", "country": "NO"}}, '
        '"registration": {"status": "ISSUED", "initialRegistrationDate": '
        '"2014-03-02", "lastUpdateDate": "2026-08-16"}}}}' % status
    ).encode("utf-8")


def _plant_restricted(pipeline, *, body: bytes, retrieval_time: str,
                      prior_manifestation_id: str) -> dict:
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native_id = "lei/ACMELEI000000000001"
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native_id, digest, retrieval_time),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native_id, request_url=native_id, final_url=native_id,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=retrieval_time, http_status=200,
        redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest, retrieval_time),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native_id, retrieval_time),
        prior_manifestation_id=prior_manifestation_id, marking=RESTRICTED_MARK)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", record,
                          recorded_time=pipeline.now_fn(), actor="t")
    return record.to_record()


def test_semantic_change_from_restricted_manifestation_does_not_leak(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    store = ctx.store

    # v1 PUBLIC (status ACTIVE), then a RESTRICTED update carrying the secret
    v1 = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
        body=_gleif_body("ACTIVE"), media_type="application/json", retrieval_time=T0)
    _plant_restricted(pipeline, body=_gleif_body(SECRET_STATUS),
                      retrieval_time="2026-08-17T14:00:00+00:00",
                      prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()
    v2 = next(m for m in store.records_of("fabric_manifestation")
              if m["content_sha256"] == hashlib.sha256(_gleif_body(SECRET_STATUS)).hexdigest())
    assert v2["marking"]["compartments"] == ["SPECIAL"]

    # interpret the byte change semantically, exactly as the watch path does —
    # through the pipeline's ordinary PUBLIC context
    changes = interpret_change(pipeline.context(), v1["manifestation_id"],
                               v2["manifestation_id"])
    assert changes, "the status change must be interpreted"
    # and raise the evidence-bound alert the watch path raises
    pipeline._raise_semantic_alerts(changes)

    # the cleared actor sees the change and its value
    proj_a = MissionProjection(store, CTX_A)
    a_blob = json.dumps([proj_a.family("semantic_change"),
                         proj_a.base_view["alerts"]])
    assert SECRET_STATUS in a_blob, "the cleared actor still sees the change"

    # the uncleared actor learns nothing restricted through any family
    proj_b = MissionProjection(store, CTX_B)
    b_blob = json.dumps([
        proj_b.family("semantic_change"),
        proj_b.family("semantic_claim_state"),
        proj_b.family("review_item"),
        proj_b.base_view["alerts"],
        proj_b.overview(),
    ])
    assert SECRET_STATUS not in b_blob, \
        "the restricted manifestation's value leaked into an uncleared view"
