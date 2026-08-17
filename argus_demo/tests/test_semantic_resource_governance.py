"""V6.7 §3a: a permanently-unprocessable manifestation must not be re-attempted
on every pass forever (each retry can fork a 30 s subprocess) — one poison
record would otherwise be an unbounded work loop. Auto-retry is bounded; the
failure stays visible to a human; exhaustion is represented truthfully, never as
"processed".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from curunir_semantic.pipeline import MAX_PROCESSING_ATTEMPTS

from semantic_support import make_pipeline, plant_manifestation

pytestmark = pytest.mark.no_db


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
