"""Persistent watch: durable scheduling, change detection, restart survival."""
from __future__ import annotations

import json

import pytest

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import digest_id
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import WatchDefinition
from curunir_fabric.executor import ExecutionContext, RateGate
from curunir_fabric.mission_bridge import alert_from_change
from curunir_fabric.registry import load_registry
from curunir_fabric.store import FabricStore
from curunir_fabric.watch import due_watches, register_watch, retire_watch, run_watch, tick
from curunir_operational.access import Marking

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
MARK = Marking(owning_authority="test-fabric", releasability=("PUBLIC",))

FEED_V1 = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>One</title><link>https://x/1</link><guid>guid-1</guid>
<pubDate>Fri, 14 Aug 2026 10:00:00 GMT</pubDate></item>
<item><title>Two</title><link>https://x/2</link><guid>guid-2</guid>
<pubDate>Fri, 14 Aug 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""
FEED_V2 = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Two updated</title><link>https://x/2</link><guid>guid-2</guid>
<pubDate>Sat, 15 Aug 2026 09:00:00 GMT</pubDate></item>
<item><title>Three</title><link>https://x/3</link><guid>guid-3</guid>
<pubDate>Sat, 15 Aug 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
PAGE_V1 = b"<html>state one</html>"
PAGE_V2 = b"<html>state two</html>"


class MutableTransport:
    def __init__(self, body: bytes, content_type: str):
        self.body = body
        self.content_type = content_type
        self.status: int | None = 200
        self.error: str | None = None

    def __call__(self, **kw):
        return {"body": self.body, "status": self.status, "final_url": kw["url"],
                "headers": {"content-type": self.content_type}, "redirects": (),
                "error": self.error, "truncated": False}


def _clock(start_minute: int = 1):
    from itertools import count
    tick_counter = count(start_minute)
    def now():
        minutes = next(tick_counter)
        return f"2026-08-15T{12 + minutes // 60:02d}:{minutes % 60:02d}:00+00:00"
    return now


def _context(tmp_path, transports, seeded=True, start_minute=1):
    root = tmp_path / "store"
    if (root / "store_meta.json").exists():
        store = FabricStore(root)
    else:
        store = FabricStore.create(root, "watch-test", NOW)
        if seeded:
            seed_starter_catalog(store, recorded_time=NOW, actor="t")
    return ExecutionContext(store=store, registry=load_registry(store),
                            custody=SourceCustodyStore(tmp_path / "custody"),
                            actor="t", marking=MARK, now_fn=_clock(start_minute),
                            transports=transports, rate_gate=RateGate(0.0))


def _watch(store, *, watch_id="w-feed", source_id="federal-register-feed",
           target_kind="FEED", target_ref="https://www.federalregister.gov/api/v1/documents.rss",
           operation="POLL", cadence=3600, created_time=NOW) -> WatchDefinition:
    definition = WatchDefinition(
        watch_id=watch_id, need_id="need-1", target_kind=target_kind, target_ref=target_ref,
        source_id=source_id, operation=operation, query_value=target_ref,
        cadence_seconds=cadence, active=True, blind_spots=("feed window only",),
        created_by="t", created_time=created_time, marking=MARK)
    register_watch(store, definition, actor="t")
    return definition


def test_first_run_is_baseline_second_run_diffs_feed(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    first_runs = tick(ctx, now="2026-08-15T12:05:00+00:00")
    assert len(first_runs) == 1
    assert first_runs[0].outcome == "EXECUTED_WITH_RESULTS"
    assert first_runs[0].change_observation_ids == ()  # baseline, not change

    transport.body = FEED_V2
    second_runs = tick(ctx, now="2026-08-15T14:00:00+00:00")
    assert len(second_runs) == 1
    changes = {r["change_type"]: r for r in ctx.store.records_of("fabric_change")}
    assert "NEW_OBJECT" in changes            # guid-3 appeared
    assert "DISAPPEARED_OBJECT" in changes    # guid-1 left the window
    assert "CHANGED_SOURCE_RECORD" in changes # guid-2 moved its source time
    assert "not deletion evidence" in changes["DISAPPEARED_OBJECT"]["detail"]
    for record in ctx.store.records_of("fabric_change"):
        if record["change_type"] != "RETRIEVAL_FAILURE":
            assert record["evidence_manifestation_ids"]


def test_url_watch_detects_content_change_with_linked_manifestations(tmp_path):
    transport = MutableTransport(PAGE_V1, "text/html")
    ctx = _context(tmp_path, {"web-page-v1": transport})
    _watch(ctx.store, watch_id="w-url", source_id="live-web", target_kind="URL",
           target_ref="https://example.org/page", operation="FETCH")
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    same = tick(ctx, now="2026-08-15T14:00:00+00:00")  # unchanged content
    assert same[0].change_observation_ids == ()
    transport.body = PAGE_V2
    tick(ctx, now="2026-08-15T16:00:00+00:00")
    changes = [r for r in ctx.store.records_of("fabric_change")
               if r["change_type"] == "CONTENT_CHANGED"]
    assert len(changes) == 1
    change = changes[0]
    assert change["prior_ref"] and change["current_ref"]
    manifestations = {r["manifestation_id"]: r for r in ctx.store.records_of("fabric_manifestation")}
    assert change["prior_ref"] in manifestations
    assert change["current_ref"] in manifestations
    assert manifestations[change["current_ref"]]["prior_manifestation_id"] == change["prior_ref"]


def test_watch_respects_cadence(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store, cadence=3600)
    assert len(tick(ctx, now="2026-08-15T12:05:00+00:00")) == 1
    # only minutes later: not due again
    assert tick(ctx, now="2026-08-15T12:20:00+00:00") == []


def test_watch_state_survives_process_restart(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    head = ctx.store.head()["head_hash"]

    # "restart": all objects rebuilt from disk alone
    transport2 = MutableTransport(FEED_V2, "application/rss+xml")
    ctx2 = _context(tmp_path, {"rss-feed-v1": transport2}, start_minute=120)
    assert ctx2.store.head()["head_hash"] == head
    assert due_watches(ctx2.store, now="2026-08-15T12:30:00+00:00") == []  # cadence honoured
    runs = tick(ctx2, now="2026-08-15T14:00:00+00:00")
    assert len(runs) == 1
    # the diff used the pre-restart baseline: real changes were detected
    change_types = {r["change_type"] for r in ctx2.store.records_of("fabric_change")}
    assert "NEW_OBJECT" in change_types
    assert ctx2.store.verify_chain()["valid"]


def test_retrieval_failure_is_an_observation_and_watch_continues(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    transport.status, transport.error, transport.body = 503, "HTTP_503", b""
    failed = tick(ctx, now="2026-08-15T14:00:00+00:00")
    assert failed[0].outcome == "SOURCE_FAILED"
    failures = [r for r in ctx.store.records_of("fabric_change")
                if r["change_type"] == "RETRIEVAL_FAILURE"]
    assert failures and failures[0]["prior_ref"]  # points at last good manifestation
    # source recovers; watch keeps observing
    transport.status, transport.error, transport.body = 200, None, FEED_V1
    recovered = tick(ctx, now="2026-08-15T16:00:00+00:00")
    assert recovered[0].outcome == "EXECUTED_WITH_RESULTS"


def test_retired_watch_stops_running_but_history_remains(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    watch_record = ctx.store.latest_by_id("fabric_watch", "watch_id")["w-feed"]
    retire_watch(ctx.store, watch_record, now="2026-08-15T12:30:00+00:00", actor="t", marking=MARK)
    assert tick(ctx, now="2026-08-15T18:00:00+00:00") == []
    assert len(ctx.store.records_of("fabric_watch_run")) == 1  # history intact


def test_change_observation_raises_evidence_bound_mission_alert(tmp_path):
    transport = MutableTransport(PAGE_V1, "text/html")
    ctx = _context(tmp_path, {"web-page-v1": transport})
    _watch(ctx.store, watch_id="w-url", source_id="live-web", target_kind="URL",
           target_ref="https://example.org/page", operation="FETCH")
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    transport.body = PAGE_V2
    tick(ctx, now="2026-08-15T14:00:00+00:00")
    change = [r for r in ctx.store.records_of("fabric_change")
              if r["change_type"] == "CONTENT_CHANGED"][0]
    alert_id, created = alert_from_change(ctx.store, change,
                                          now="2026-08-15T15:00:00+00:00",
                                          actor="fabric-watch", marking=MARK)
    assert created
    alert = ctx.store.records_of("alert")[0]
    assert alert["alert_id"] == alert_id
    assert set(change["evidence_manifestation_ids"]) <= set(alert["evidence_refs"])
    # same change cannot spam duplicate alerts
    _id, created_again = alert_from_change(ctx.store, change,
                                           now="2026-08-15T15:05:00+00:00",
                                           actor="fabric-watch", marking=MARK)
    assert not created_again


def test_export_replay_preserves_watch_lineage(tmp_path):
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    transport.body = FEED_V2
    tick(ctx, now="2026-08-15T14:00:00+00:00")
    export_dir = tmp_path / "export"
    manifest = ctx.store.export_to(export_dir)
    imported = FabricStore.import_from(export_dir, tmp_path / "imported")
    assert imported.verify_chain()["valid"]
    assert imported.head()["head_hash"] == manifest["head_hash"]
    assert len(imported.records_of("fabric_change")) == \
        len(ctx.store.records_of("fabric_change"))
    assert len(imported.records_of("fabric_watch_run")) == 2


def test_retrieval_failure_alert_cites_change_id_not_a_possibly_dangling_run(tmp_path):
    # review F-B1 residual: a RETRIEVAL_FAILURE change carries no manifestation
    # evidence, so alert_from_change falls back — and must cite the change
    # observation's OWN id (which necessarily exists), never the run_id, which a
    # torn tail between the change appends and the WatchRun append can leave
    # pointing at a WatchRun that was never written.
    transport = MutableTransport(FEED_V1, "application/rss+xml")
    ctx = _context(tmp_path, {"rss-feed-v1": transport})
    _watch(ctx.store)
    tick(ctx, now="2026-08-15T12:05:00+00:00")
    transport.status, transport.error, transport.body = 503, "HTTP_503", b""
    tick(ctx, now="2026-08-15T14:00:00+00:00")
    failure = [r for r in ctx.store.records_of("fabric_change")
               if r["change_type"] == "RETRIEVAL_FAILURE"][0]
    assert not failure["evidence_manifestation_ids"]        # exercises the fallback branch
    alert_id, created = alert_from_change(ctx.store, failure,
                                          now="2026-08-15T15:00:00+00:00",
                                          actor="fabric-watch", marking=MARK)
    assert created
    alert = [a for a in ctx.store.records_of("alert") if a["alert_id"] == alert_id][0]
    assert failure["change_id"] in alert["evidence_refs"]
    assert failure["run_id"] not in alert["evidence_refs"]   # never the (possibly torn) run
