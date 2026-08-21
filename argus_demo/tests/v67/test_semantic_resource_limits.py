"""Resource limits remain visible and never become semantic absence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_fabric.contracts import ManifestationRecord
from curunir_semantic.changes import interpret_change
from curunir_semantic.pipeline import MAX_PROCESSING_ATTEMPTS

from semantic_support import MARK, make_pipeline, plant_manifestation

pytestmark = pytest.mark.no_db


def _gleif(status: str) -> bytes:
    return (
        '{"data":{"id":"ACMELEI000000000001","attributes":{"lei":'
        '"ACMELEI000000000001","entity":{"legalName":{"name":"Acme"},'
        '"jurisdiction":"NO","status":"%s","otherNames":[],"legalAddress":'
        '{"city":"Oslo","country":"NO"}},"registration":{"status":"ISSUED",'
        '"initialRegistrationDate":"2014-03-02","lastUpdateDate":"2026-08-16"}}}}'
        % status).encode()


def _plant_truncated(pipeline, body: bytes, prior_id: str) -> dict:
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    retrieval_time = "2026-08-17T14:00:00+00:00"
    native = "lei/ACMELEI000000000001"
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest, retrieval_time),
        source_id="gleif", connector_id="gleif-test", connector_version="1",
        native_id=native, request_url=native, final_url=native,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=retrieval_time, http_status=200,
        redirects=(), etag="", last_modified="", truncated=True,
        retrieval_id=digest_id("retrieval", digest, retrieval_time),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native, retrieval_time),
        prior_manifestation_id=prior_id, marking=MARK)
    return pipeline.store.append(
        "FABRIC_MANIFESTATION_RECORDED", record,
        recorded_time=pipeline.now_fn(), actor="test")["record"]


def test_truncated_current_read_cannot_stale_or_retract_claims(tmp_path):
    pipeline = make_pipeline(tmp_path, start_minute=1)
    prior = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
        body=_gleif("ACTIVE"), media_type="application/json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    current = _plant_truncated(pipeline, _gleif("LAPSED"), prior["manifestation_id"])
    pipeline.process_new_evidence()
    changes = interpret_change(
        pipeline.context(), prior["manifestation_id"], current["manifestation_id"])
    classes = {change["change_class"] for change in changes}
    assert "UNRESOLVED_CHANGE" in classes
    assert not (classes & {"SOURCE_RETRACTION", "SOURCE_CORRECTION", "VALUE_CHANGED",
                           "ENTITY_ATTRIBUTE_CHANGED", "ROLE_CHANGED",
                           "REMOVED_PROPOSITION", "RELATION_REMOVED"})
    assert not [state for state in pipeline.store.records_of("claim_state")
                if state["state"] in {"STALE", "RETRACTED", "SUPERSEDED"}]


def test_anchor_map_cap_is_not_content_truncation():
    from curunir_semantic.changes import _current_read_truncated

    class Store:
        def __init__(self, warnings):
            self.warnings = warnings

        def records_of(self, record_type):
            return ([{"manifestation_id": "m", "warnings": self.warnings}]
                    if record_type == "semantic_document" else [])

    assert not _current_read_truncated(
        Store(("REGIONS_TRUNCATED_AT_400",)), {"truncated": False}, "m")
    assert _current_read_truncated(
        Store(("FIELDS_TRUNCATED_AT_50000",)), {"truncated": False}, "m")


def test_poison_manifestation_auto_retry_is_bounded(tmp_path):
    pipeline = make_pipeline(tmp_path, start_minute=1)
    manifestation = plant_manifestation(
        pipeline, source_id="gleif", native_id="poison", body=b'{"data":{}}',
        media_type="application/json", retrieval_time=pipeline.now_fn())
    digest = manifestation["content_sha256"]
    (Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest).unlink()
    attempts = 0
    for _ in range(MAX_PROCESSING_ATTEMPTS + 3):
        outcome = pipeline.process_new_evidence()
        attempts += sum(item["manifestation_id"] == manifestation["manifestation_id"]
                        for item in outcome["processed"])
    assert attempts == MAX_PROCESSING_ATTEMPTS
    failure = pipeline.store.latest_by_id("review_item", "item_id")[
        pipeline._failure_item_id(manifestation["manifestation_id"])]
    assert failure["status"] == "OPEN" and "exhausted" in failure["detail"]


def test_json_lone_surrogate_is_repaired_and_disclosed(tmp_path):
    pipeline = make_pipeline(tmp_path, start_minute=1)
    body = json.dumps({"data": {"id": "X", "attributes": {"entity": {
        "legalName": {"name": "Acme\ud800Corp"}, "status": "ACTIVE"}}}}).encode()
    manifestation = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/X", body=body,
        media_type="application/json", retrieval_time=pipeline.now_fn())
    pipeline.process_new_evidence()
    document = next(record for record in pipeline.store.records_of("semantic_document")
                    if record["manifestation_id"] == manifestation["manifestation_id"])
    assert "SCRUBBED_UNENCODABLE_CHARACTERS" in document["warnings"]
