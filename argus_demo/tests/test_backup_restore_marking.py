"""V6.7 backup / restore demo + locks (§103, §115, §52).

Proves the confidentiality property composes with the backup boundary: a
mission exported and re-imported into a FRESH store, with no network and no
provider, replays to the same access-filtered state — a restricted record is
still invisible to an uncleared actor after restore, and its marking is intact.
Also pins that a tampered backup is refused, never silently reconstructed.
"""
from __future__ import annotations

import hashlib
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

ACME = "LEI:ACMELEI000000000001"
SECRET = "CLASSIFIEDXYZZY"


def _gleif(status: str) -> bytes:
    return (
        '{"data": {"id": "ACMELEI000000000001", "attributes": {"lei": '
        '"ACMELEI000000000001", "entity": {"legalName": {"name": "Acme AS"}, '
        '"jurisdiction": "NO", "status": "%s", "otherNames": [], "legalAddress": '
        '{"city": "Oslo", "country": "NO"}}, "registration": {"status": "ISSUED", '
        '"initialRegistrationDate": "2014-03-02", "lastUpdateDate": '
        '"2026-08-16"}}}}' % status).encode("utf-8")


def _plant_restricted(pipeline) -> None:
    body = _gleif(SECRET)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = "lei/ACMELEI000000000001"
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native, request_url=native, final_url=native,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time="2026-08-17T14:00:00+00:00",
        http_status=200, redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native), prior_manifestation_id=None,
        marking=RESTRICTED_MARK), recorded_time=pipeline.now_fn(), actor="t")


def _seeded_mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
                        body=_gleif("ACTIVE"), media_type="application/json",
                        retrieval_time=T0)
    _plant_restricted(pipeline)
    pipeline.process_new_evidence()
    return ctx.store


def _restricted_claim_id(store) -> str:
    claim = next(c for c in store.current_claims().values()
                 if c["subject_ref"] == ACME and c["predicate"] == "entity_status")
    assert claim["marking"]["compartments"] == ["SPECIAL"]
    return claim["claim_id"]


def test_backup_restore_preserves_markings_and_access(tmp_path):
    store = _seeded_mission(tmp_path)
    restricted_claim = _restricted_claim_id(store)

    # BACKUP
    export_dir = tmp_path / "backup"
    manifest = store.export_to(export_dir)
    assert manifest["event_count"] > 0

    # CLEAN RESTORE into a fresh root — no network, no provider involved
    restored = WorkbenchStore.import_from(export_dir, tmp_path / "restored")
    assert restored.verify_chain()["valid"]

    # the restored SPECIAL claim keeps its marking …
    restored_claim = restored.current_claims()[restricted_claim]
    assert restored_claim["marking"]["compartments"] == ["SPECIAL"]

    # … and access control holds on the restored store: the uncleared actor
    # cannot see the claim or its value; the cleared actor can
    proj_b = MissionProjection(restored, CTX_B)
    assert restricted_claim not in {c["claim_id"] for c in proj_b.family("semantic_claim")}
    import json
    assert SECRET not in json.dumps(proj_b.overview())

    proj_a = MissionProjection(restored, CTX_A)
    assert restricted_claim in {c["claim_id"] for c in proj_a.family("semantic_claim")}


def test_tampered_backup_is_refused(tmp_path):
    store = _seeded_mission(tmp_path)
    export_dir = tmp_path / "backup"
    store.export_to(export_dir)

    # flip a byte in the event log — the manifest's events hash no longer matches
    events = export_dir / "events.jsonl"
    data = bytearray(events.read_bytes())
    data[len(data) // 2] ^= 0x01
    events.write_bytes(bytes(data))

    with pytest.raises(StoreError):
        WorkbenchStore.import_from(export_dir, tmp_path / "restored")


def test_tampered_payload_is_refused(tmp_path):
    store = _seeded_mission(tmp_path)
    export_dir = tmp_path / "backup"
    store.export_to(export_dir)

    # corrupt a content-addressed payload: its bytes no longer hash to its name
    payloads = sorted((export_dir / "payloads").iterdir())
    assert payloads, "the mission preserved evidence payloads"
    payloads[0].write_bytes(payloads[0].read_bytes() + b"tamper")

    with pytest.raises(StoreError):
        WorkbenchStore.import_from(export_dir, tmp_path / "restored2")
