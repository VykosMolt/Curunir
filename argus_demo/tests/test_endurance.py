"""V6.7 §8 — bounded endurance: does Curunír stay coherent and usable under
sustained normal operation?

A persistent mission runs many cycles of collection → semantic processing →
operator read/write, with a poison manifestation injected mid-run. We assert the
lifecycle invariants that matter — the hash chain stays valid throughout, file
descriptors do not leak, the poison record does not drive a retry storm, and the
run completes within a wall-clock budget — and REPORT the measured memory,
descriptor and per-cycle latency trends. (This is a coherence exercise, not a
production-scale benchmark; the whole-log-in-memory store's per-op scan cost
grows with mission size — a documented scale limitation, not a defect.)
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.impact import create_objective
from curunir_analytic.substrate import AnalyticContext
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import ManifestationRecord
from curunir_operational.access import Marking
from curunir_semantic.pipeline import MAX_PROCESSING_ATTEMPTS, SemanticPipeline
from curunir_workbench.store import WorkbenchStore

pytestmark = pytest.mark.no_db

CYCLES = 120
MARK = Marking(owning_authority="endurance", releasability=("PUBLIC",))


def _clock():
    t = [datetime(2026, 8, 17, 13, 0, 0, tzinfo=timezone.utc)]

    def now():
        t[0] += timedelta(seconds=1)
        return t[0].isoformat()
    return now


def _rss_kb() -> int:
    with open("/proc/self/statm") as handle:
        resident_pages = int(handle.read().split()[1])
    return resident_pages * (os.sysconf("SC_PAGE_SIZE") // 1024)


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def _gleif_body(i: int) -> bytes:
    # a distinct-but-valid GLEIF record per cycle, so each manifestation
    # normalizes and integrates real semantic state.
    return (
        b'{"data": {"id": "ENDUR%04d", "attributes": {"lei": "ENDUR%04d", '
        b'"entity": {"legalName": {"name": "Endurance Co %d"}, "jurisdiction": "NO", '
        b'"status": "ACTIVE", "otherNames": [], "legalAddress": {"city": "Oslo", '
        b'"country": "NO"}}, "registration": {"status": "ISSUED", '
        b'"initialRegistrationDate": "2014-03-02", "lastUpdateDate": "2026-08-01"}}}}'
    ) % (i, i, i)


def _plant(store, custody_root, now_fn, body: bytes, native: str):
    digest = hashlib.sha256(body).hexdigest()
    path = custody_root / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(body)
    rec = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest), source_id="gleif",
        connector_id="gleif-endur", connector_version="1.0", native_id=native,
        request_url=native, final_url=native, content_sha256=digest,
        content_store_path=str(path), media_type="application/json",
        temporal_status="LIVE", source_time=None, archive_capture_time=None,
        retrieval_time=now_fn(), http_status=200, redirects=(), etag="", last_modified="",
        truncated=False, retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native), prior_manifestation_id=None, marking=MARK)
    store.append("FABRIC_MANIFESTATION_RECORDED", rec, recorded_time=now_fn(), actor="t")
    return rec.manifestation_id


def test_sustained_operation_stays_coherent(tmp_path, capsys):
    root = tmp_path / "store"
    custody = tmp_path / "custody"
    store = WorkbenchStore.create(root, "endurance", "2026-08-17T12:00:00+00:00")
    seed_starter_catalog(store, recorded_time="2026-08-17T12:00:00+00:00", actor="t")
    now = _clock()
    pipeline = SemanticPipeline(store=store, custody_root=custody, actor="t",
                               marking=MARK, now_fn=now)
    actx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=now)

    # a poison manifestation planted once: its custody bytes are removed so it
    # fails forever — over the rest of the run it must NOT retry-storm.
    poison_body = b'{"data": {"id": "POISON"}}'
    poison_id = _plant(store, custody, now, poison_body, "poison")
    pdig = hashlib.sha256(poison_body).hexdigest()
    (custody / "sha256" / pdig[:2] / pdig[2:4] / pdig).unlink()

    rss0, fd0 = _rss_kb(), _fd_count()
    fd_samples = []
    poison_attempts = 0
    latencies = []
    for i in range(CYCLES):
        t0 = time.perf_counter()
        _plant(store, custody, now, _gleif_body(i), f"lei/ENDUR{i:04d}")  # collection
        result = pipeline.process_new_evidence()                           # semantic
        # count ACTUAL processing attempts on the poison (not just review records)
        if any(p["manifestation_id"] == poison_id for p in result["processed"]):
            poison_attempts += 1
        _ = store.current_objectives()                                     # operator read
        create_objective(actx, mission_context="endur", statement=f"objective {i}")  # write
        latencies.append(time.perf_counter() - t0)
        if i % 20 == 0:
            fd_samples.append(_fd_count())
    rss1, fd1 = _rss_kb(), _fd_count()

    # ---- coherence invariants ----
    assert store.verify_chain()["valid"], "in-memory chain must stay valid under load"
    # DURABILITY: reopen the store from the ON-DISK log (full replay from scratch)
    # and re-verify — proves the persisted log, not just the in-process instance.
    reopened = WorkbenchStore(root)
    assert reopened.verify_chain()["valid"], "on-disk chain must replay valid"
    assert reopened.head() == store.head(), "on-disk head must match in-memory"
    # no descriptor leak: fd stays in a tight band, not growing with the log
    assert max(fd_samples + [fd1]) <= fd0 + 3, \
        f"file-descriptor growth: {fd0} -> samples {fd_samples} -> {fd1}"
    # the poison was auto-retried at most the cap, not every cycle (no storm)
    assert poison_attempts <= MAX_PROCESSING_ATTEMPTS, \
        f"poison retry storm: {poison_attempts} attempts over {CYCLES} cycles"
    # every cycle's real work landed exactly once (no duplication), on disk too
    assert len([o for o in reopened.current_objectives().values()
                if o["mission_context"] == "endur"]) == CYCLES
    # the run stayed usable (a generous wall-clock budget for this bounded size)
    assert sum(latencies) < 90.0, f"endurance run too slow: {sum(latencies):.1f}s"

    first_decile = sum(latencies[:12]) / 12
    last_decile = sum(latencies[-12:]) / 12
    print(f"\n[endurance] cycles={CYCLES} events={store.head()['event_count']} "
          f"RSS {rss0//1024}->{rss1//1024} MiB  fd {fd0}->{fd1}  "
          f"per-cycle {first_decile*1000:.1f}ms->{last_decile*1000:.1f}ms  "
          f"total {sum(latencies):.1f}s")
    with capsys.disabled():
        pass  # measurements captured in the printed line above
