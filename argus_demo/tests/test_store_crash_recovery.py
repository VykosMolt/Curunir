"""V6.7 §4/§5 — failure injection & recovery on the REAL mission store.

The append path holds an exclusive lock and does a single write+flush of one
line, so the ONLY corruption a crash (process kill, power loss) can produce is a
torn last line — every prior event is complete and chain-valid. These tests
inject that crash and prove: the torn tail is deterministically recovered, prior
events survive intact, tampering / mid-file damage is REFUSED (never silently
altered), a retried versioned write cannot double-apply, and a recovered mission
backs up, restores into a fresh store, and verifies.
"""
from __future__ import annotations

import json

import pytest

from curunir_analytic.impact import create_objective
from curunir_analytic.substrate import AnalyticContext
from curunir_operational.store import StoreError
from curunir_workbench.store import WorkbenchStore

from semantic_support import MARK, clock
from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


def _mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    return tmp_path / "store", seeded


def _events(root):
    return root / "events.jsonl"


def test_torn_tail_from_crash_is_recovered_prior_events_survive(tmp_path):
    root, _ = _mission(tmp_path)
    before = WorkbenchStore(root).head()
    # a writer killed mid-append: an incomplete final line, no trailing newline
    with _events(root).open("ab") as handle:
        handle.write(b'{"seq": 9999, "event_type": "WORKBENCH_ANNOTATION_RECORD')
    with pytest.raises(StoreError):        # fails loud by default (no silent skip)
        WorkbenchStore(root)
    result = WorkbenchStore.recover_torn_tail(root)
    assert result["recovered"] and result["truncated_bytes"] > 0
    after = WorkbenchStore(root)
    assert after.head() == before          # exactly the pre-crash state
    assert after.verify_chain()["valid"]
    assert _events(root).with_name("events.jsonl.torn").exists()  # forensic remainder


def test_recovery_is_noop_on_a_healthy_store(tmp_path):
    root, _ = _mission(tmp_path)
    result = WorkbenchStore.recover_torn_tail(root)
    assert result == {"recovered": False, "truncated_bytes": 0}


def test_recovery_refuses_tampered_complete_final_line(tmp_path):
    root, _ = _mission(tmp_path)
    original = _events(root).read_bytes()
    # a COMPLETE, well-formed JSON line that does not link (tampering / a forged
    # append), NOT a torn write — recovery must refuse to touch it.
    forged = json.dumps({"seq": 9999, "event_type": "X", "recorded_time": "2026-08-17T00:00:00+00:00",
                         "actor": "attacker", "record": {}, "prev_hash": "de" * 32,
                         "entry_hash": "ad" * 32})
    with _events(root).open("a", encoding="utf-8") as handle:
        handle.write(forged + "\n")
    with pytest.raises(StoreError):
        WorkbenchStore(root)
    with pytest.raises(StoreError, match="not a torn-tail crash|tampering|diverged"):
        WorkbenchStore.recover_torn_tail(root)
    # the file was not modified and no .torn backup was minted
    assert not _events(root).with_name("events.jsonl.torn").exists()
    assert _events(root).read_bytes().startswith(original)


def test_recovery_refuses_mid_file_damage(tmp_path):
    root, _ = _mission(tmp_path)
    segments = _events(root).read_bytes().split(b"\n")
    # damage an EARLIER line (not the tail): truncate line 1 to partial JSON but
    # keep a complete final line. This is not a crash signature — refuse.
    assert len(segments) > 4
    segments[1] = segments[1][: max(1, len(segments[1]) // 2)]
    _events(root).write_bytes(b"\n".join(segments))
    with pytest.raises(StoreError):
        WorkbenchStore(root)
    with pytest.raises(StoreError, match="not a torn tail|not a crash signature|earlier line"):
        WorkbenchStore.recover_torn_tail(root)


def test_after_recovery_store_is_usable_and_torn_append_left_nothing(tmp_path):
    root, _ = _mission(tmp_path)
    count0 = WorkbenchStore(root).head()["event_count"]
    with _events(root).open("ab") as handle:      # interrupted append
        handle.write(b'{"seq": 9999, "partial": tru')
    WorkbenchStore.recover_torn_tail(root)
    store = WorkbenchStore(root)
    assert store.head()["event_count"] == count0  # the in-flight append is durable-nothing
    # the store is fully usable: a real command appends and replays
    ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=clock(600))
    obj = create_objective(ctx, mission_context="crash", statement="post-recovery objective")
    assert obj["objective_id"] in WorkbenchStore(root).current_objectives()
    assert WorkbenchStore(root).head()["event_count"] > count0


def test_retried_versioned_write_cannot_double_apply(tmp_path):
    # the exactly-once mechanism under retry: a versioned family refuses a repeat
    # of an already-recorded version (a crash-then-retry cannot duplicate it).
    root, _ = _mission(tmp_path)
    store = WorkbenchStore(root)
    ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=clock(600))
    obj = create_objective(ctx, mission_context="idem", statement="single objective")
    current = store.current_objectives()[obj["objective_id"]]
    assert current["version"] == 1
    # replaying the SAME version (a duplicate retry) is refused, not shadowed
    with pytest.raises(StoreError, match="version"):
        store.append("MISSION_OBJECTIVE_RECORDED", dict(current),
                     recorded_time=clock(660)(), actor="t")


def test_recovered_mission_backs_up_restores_and_verifies(tmp_path):
    root, seeded = _mission(tmp_path)
    # crash + recover, then prove the recovered mission survives a clean-store
    # round trip with intact chain and state.
    with _events(root).open("ab") as handle:
        handle.write(b'{"seq": 9999, "torn')
    WorkbenchStore.recover_torn_tail(root)
    store = WorkbenchStore(root)

    backup = tmp_path / "backup"
    store.export_to(backup)
    restored = WorkbenchStore.import_from(backup, tmp_path / "restored")
    assert restored.head() == store.head()
    assert restored.verify_chain()["valid"]
    # seeded analytic state survives the boundary
    assert set(store.current_forecasts()) == set(restored.current_forecasts())
    assert seeded["forecast"]["forecast_id"] in restored.current_forecasts()
    # replay reconstructs without network/providers (pure log replay above)
