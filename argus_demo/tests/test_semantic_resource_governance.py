"""V6.7 §3a: a permanently-unprocessable manifestation must not be re-attempted
on every pass forever (each retry can fork a 30 s subprocess) — one poison
record would otherwise be an unbounded work loop. Auto-retry is bounded; the
failure stays visible to a human; exhaustion is represented truthfully, never as
"processed".
"""
from __future__ import annotations

import hashlib
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
        '{"data": {"id": "ACMELEI000000000001", "attributes": {"lei": '
        '"ACMELEI000000000001", "entity": {"legalName": {"name": "Acme Industri AS"}, '
        '"jurisdiction": "NO", "status": "%s", "otherNames": [], "legalAddress": '
        '{"city": "Oslo", "country": "NO"}}, "registration": {"status": "ISSUED", '
        '"initialRegistrationDate": "2014-03-02", "lastUpdateDate": "2026-08-16"}}}}' % status
    ).encode("utf-8")


def _plant_truncated(pipeline, *, body, retrieval_time, prior_manifestation_id):
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native_id = "lei/ACMELEI000000000001"
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native_id, digest, retrieval_time),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native_id, request_url=native_id, final_url=native_id,
        content_sha256=digest, content_store_path=str(path), media_type="application/json",
        temporal_status="LIVE", source_time=None, archive_capture_time=None,
        retrieval_time=retrieval_time, http_status=200, redirects=(), etag="",
        last_modified="", truncated=True,   # the byte-capped re-retrieval
        retrieval_id=digest_id("retrieval", digest, retrieval_time),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native_id, retrieval_time),
        prior_manifestation_id=prior_manifestation_id, marking=MARK)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", record,
                          recorded_time=pipeline.now_fn(), actor="t")
    return record.to_record()


def test_truncated_re_retrieval_is_not_interpreted_as_removal_or_staleness(tmp_path):
    # review F4: a byte-capped current manifestation that diffs against the prior
    # full read must NOT produce REMOVED_PROPOSITION / SOURCE_RETRACTION /
    # VALUE_CHANGED (which move a claim to STALE/RETRACTED) — a resource limit is
    # not evidence of deletion. The loss/change classes become UNRESOLVED_CHANGE.
    pipeline = make_pipeline(tmp_path, start_minute=1)
    v1 = plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                             body=_gleif("ACTIVE"), media_type="application/json",
                             retrieval_time="2026-08-17T12:05:00+00:00")
    v2 = _plant_truncated(pipeline, body=_gleif("LAPSED"),
                          retrieval_time="2026-08-17T14:00:00+00:00",
                          prior_manifestation_id=v1["manifestation_id"])
    pipeline.process_new_evidence()

    changes = interpret_change(pipeline.context(), v1["manifestation_id"], v2["manifestation_id"])
    classes = {c["change_class"] for c in changes}
    lifecycle = {"REMOVED_PROPOSITION", "RELATION_REMOVED", "SOURCE_RETRACTION",
                 "VALUE_CHANGED", "ROLE_CHANGED", "ENTITY_ATTRIBUTE_CHANGED", "SOURCE_CORRECTION"}
    assert not (classes & lifecycle), f"a truncated read must not assert loss/change: {classes}"
    assert "UNRESOLVED_CHANGE" in classes
    trunc = [c for c in changes if c["change_class"] == "UNRESOLVED_CHANGE"]
    assert any("byte-capped" in c["detail"] or "truncated" in c["detail"].lower() for c in trunc)
    # no claim was moved to a STALE/RETRACTED lifecycle state by the truncated read
    assert not [s for s in pipeline.store.records_of("claim_state")
                if s.get("state") in ("STALE", "RETRACTED", "SUPERSEDED")]


def test_region_map_cap_is_not_treated_as_content_truncation():
    # review F4-round-C: REGIONS_TRUNCATED_* caps the anchor MAP only — the
    # normalized content is complete — so it must NOT trigger the truncation guard
    # (which would SUPPRESS genuine change detection). Only content caps do.
    from curunir_semantic.changes import _current_read_truncated

    class _FakeStore:
        def __init__(self, warnings):
            self._docs = [{"manifestation_id": "m1", "warnings": warnings}]
        def records_of(self, record_type):
            return self._docs if record_type == "semantic_document" else []

    manifestation = {"manifestation_id": "m1", "truncated": False}
    assert _current_read_truncated(_FakeStore(("REGIONS_TRUNCATED_AT_400",)),
                                   manifestation, "m1") is False   # content complete
    assert _current_read_truncated(_FakeStore(("FIELDS_TRUNCATED_AT_50000",)),
                                   manifestation, "m1") is True    # content truncated
    assert _current_read_truncated(_FakeStore(("PDF_TEXT_DERIVATIVE_TRUNCATED_TO_BOUND",)),
                                   manifestation, "m1") is True
    assert _current_read_truncated(_FakeStore(("ACTIVE_SCRIPT_PRESENT_NOT_EXECUTED",)),
                                   manifestation, "m1") is False   # unrelated warning


def test_normalizer_field_cap_truncation_is_not_interpreted_as_removal(tmp_path, monkeypatch):
    # review finding 4 (F4-round-B): the SEMANTIC normalizer's own caps (field /
    # region / pdf) truncate a read while the manifestation's transport `truncated`
    # flag stays False. That incomplete read must ALSO not diff as removal.
    import curunir_semantic.normalize as norm
    from curunir_semantic.normalize import normalized_document_id
    pipeline = make_pipeline(tmp_path, start_minute=1)
    v1 = plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                             body=_gleif("ACTIVE"), media_type="application/json",
                             retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()                       # v1 normalized in FULL

    monkeypatch.setattr(norm, "MAX_FIELDS", 3)            # now the field table is capped tiny
    v2 = plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                             body=_gleif("LAPSED"), media_type="application/json",
                             retrieval_time="2026-08-17T14:00:00+00:00")
    pipeline.process_new_evidence()                       # v2 normalized TRUNCATED (field cap)

    v2rec = next(m for m in pipeline.store.records_of("fabric_manifestation")
                 if m["manifestation_id"] == v2["manifestation_id"])
    assert v2rec["truncated"] is False                   # the transport flag is the blind spot
    doc = next(d for d in pipeline.store.records_of("semantic_document")
               if d["document_id"] == normalized_document_id(v2["manifestation_id"]))
    assert any("TRUNCATED" in w for w in doc["warnings"])  # but the document says truncated

    changes = interpret_change(pipeline.context(), v1["manifestation_id"], v2["manifestation_id"])
    classes = {c["change_class"] for c in changes}
    assert not (classes & {"REMOVED_PROPOSITION", "RELATION_REMOVED", "SOURCE_RETRACTION",
                           "VALUE_CHANGED", "ROLE_CHANGED", "ENTITY_ATTRIBUTE_CHANGED"}), \
        f"a normalizer-truncated read must not assert removal/change: {classes}"


def test_poison_manifestation_retry_is_bounded(tmp_path):
    pipeline = make_pipeline(tmp_path, start_minute=1)
    m = plant_manifestation(pipeline, source_id="gleif", native_id="poison",
                            body=b'{"data": {}}', media_type="application/json",
                            retrieval_time=pipeline.now_fn())
    mid = m["manifestation_id"]
    # make normalization fail deterministically and forever: remove the custody
    # bytes the pipeline needs to read.
    digest = m["content_sha256"]
    (Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest).unlink()

    attempts = 0
    for _ in range(MAX_PROCESSING_ATTEMPTS + 3):
        result = pipeline.process_new_evidence()
        if any(p["manifestation_id"] == mid for p in result["processed"]):
            attempts += 1
    assert attempts == MAX_PROCESSING_ATTEMPTS, (
        f"a poison manifestation must be auto-retried at most "
        f"{MAX_PROCESSING_ATTEMPTS} times, not {attempts}")

    # the failure is still OPEN (a human must see it) and truthfully marked
    # exhausted — never silently treated as processed.
    failures = [r for r in pipeline.store.latest_by_id("review_item", "item_id").values()
                if r["kind"] == "PROCESSING_FAILED" and r["subject_id"] == mid]
    assert failures and failures[0]["status"] == "OPEN"
    assert "exhausted" in failures[0]["detail"]
    # and the poison never produced a semantic document
    assert not [d for d in pipeline.store.records_of("semantic_document")
                if d.get("manifestation_id") == mid]


def test_recovered_manifestation_resets_the_attempt_budget(tmp_path):
    # if a transiently-failing manifestation later succeeds, its attempt budget
    # resets — a fresh future failure is retried again, not treated as exhausted.
    pipeline = make_pipeline(tmp_path, start_minute=1)
    m = plant_manifestation(pipeline, source_id="gleif", native_id="transient",
                            body=b'{"data": {"id": "X", "attributes": {}}}',
                            media_type="application/json", retrieval_time=pipeline.now_fn())
    mid = m["manifestation_id"]
    digest = m["content_sha256"]
    custody = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    body = custody.read_bytes()

    custody.unlink()                              # fail twice
    pipeline.process_new_evidence()
    pipeline.process_new_evidence()
    custody.parent.mkdir(parents=True, exist_ok=True)
    custody.write_bytes(body)                     # then recover
    pipeline.process_new_evidence()

    attempts = pipeline._processing_attempts().get(mid, 0)
    assert attempts == 0, "a successful re-process resets the attempt budget"
    resolved = [r for r in pipeline.store.latest_by_id("review_item", "item_id").values()
                if r["kind"] == "PROCESSING_FAILED" and r["subject_id"] == mid]
    assert resolved and resolved[0]["status"] == "RESOLVED"
