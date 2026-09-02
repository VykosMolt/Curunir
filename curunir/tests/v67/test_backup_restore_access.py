"""The backup boundary preserves replay, marking, and access decisions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_fabric.contracts import ManifestationRecord
from curunir_operational.store import StoreError
from curunir_workbench.projections import MissionProjection
from curunir_workbench.store import WorkbenchStore

from semantic_support import T0, plant_manifestation
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

SUBJECT = "LEI:ACMELEI000000000001"
SECRET = "CLASSIFIEDXYZZY"


def _gleif(status: str) -> bytes:
    return (
        '{"data":{"id":"ACMELEI000000000001","attributes":{"lei":'
        '"ACMELEI000000000001","entity":{"legalName":{"name":"Acme AS"},'
        '"jurisdiction":"NO","status":"%s","otherNames":[],"legalAddress":'
        '{"city":"Oslo","country":"NO"}},"registration":{"status":"ISSUED",'
        '"initialRegistrationDate":"2014-03-02","lastUpdateDate":"2026-08-16"}}}}'
        % status
    ).encode()


def _seed_restricted_claim(tmp_path) -> WorkbenchStore:
    pipeline, _ = make_workbench(tmp_path)
    plant_manifestation(
        pipeline,
        source_id="gleif",
        native_id="lei/ACMELEI000000000001",
        body=_gleif("ACTIVE"),
        media_type="application/json",
        retrieval_time=T0,
    )
    body = _gleif(SECRET)
    digest = hashlib.sha256(body).hexdigest()
    custody_path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    custody_path.parent.mkdir(parents=True, exist_ok=True)
    custody_path.write_bytes(body)
    native_id = "lei/ACMELEI000000000001"
    pipeline.store.append(
        "FABRIC_MANIFESTATION_RECORDED",
        ManifestationRecord(
            manifestation_id=digest_id("manifestation", native_id, digest),
            source_id="gleif",
            connector_id="gleif-test",
            connector_version="1",
            native_id=native_id,
            request_url=native_id,
            final_url=native_id,
            content_sha256=digest,
            content_store_path=str(custody_path),
            media_type="application/json",
            temporal_status="LIVE",
            source_time=None,
            archive_capture_time=None,
            retrieval_time="2026-08-17T14:00:00+00:00",
            http_status=200,
            redirects=(),
            etag="",
            last_modified="",
            truncated=False,
            retrieval_id=digest_id("retrieval", digest),
            custody_ingestion_id=digest_id("ingestion", digest),
            source_object_id=digest_id("source-object", digest),
            execution_id=digest_id("execution", native_id),
            prior_manifestation_id=None,
            marking=RESTRICTED_MARK,
        ),
        recorded_time=pipeline.now_fn(),
        actor="backup-test",
    )
    pipeline.process_new_evidence()
    return pipeline.store


def test_clean_restore_replays_restricted_state_without_disclosure(tmp_path):
    store = _seed_restricted_claim(tmp_path)
    claim = next(
        item for item in store.current_claims().values()
        if item["subject_ref"] == SUBJECT
        and item["predicate"] == "entity_status"
        and item["object_or_value"] == SECRET
    )
    assert claim["marking"]["compartments"] == ["SPECIAL"]

    backup = tmp_path / "backup"
    store.export_to(backup)
    restored = WorkbenchStore.import_from(backup, tmp_path / "restored")
    assert restored.verify_chain()["valid"]
    assert restored.current_claims()[claim["claim_id"]]["marking"]["compartments"] == ["SPECIAL"]

    uncleared = MissionProjection(restored, CTX_B)
    assert claim["claim_id"] not in {
        item["claim_id"] for item in uncleared.family("semantic_claim")
    }
    assert SECRET not in json.dumps(uncleared.overview())
    assert claim["claim_id"] in {
        item["claim_id"] for item in MissionProjection(restored, CTX_A).family("semantic_claim")
    }


def test_tampered_backup_event_bytes_are_refused_without_destination(tmp_path):
    store = _seed_restricted_claim(tmp_path)
    backup = tmp_path / "backup"
    store.export_to(backup)
    events = backup / "events.jsonl"
    body = bytearray(events.read_bytes())
    body[len(body) // 2] ^= 1
    events.write_bytes(body)

    destination = tmp_path / "restored"
    with pytest.raises(StoreError, match="tampered"):
        WorkbenchStore.import_from(backup, destination)
    assert not destination.exists()
