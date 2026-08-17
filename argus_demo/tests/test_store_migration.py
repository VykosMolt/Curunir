"""V6.7 §6 — migration / rollback safety and fail-closed defaults.

Curunír has one contract version and no schema evolution yet, so "migration" is
(a) additive-field backward compatibility with safe defaults, (b) a fail-closed
version guard so a backup written under an unknown contract is refused rather
than silently misread, (c) backup-based rollback, and (d) partial/old records
never defaulting to a MORE-privileged or MORE-visible state.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from curunir_operational.access import AccessContext, can_view
from curunir_operational.canonical import canonical_line
from curunir_operational.store import CONTRACT_VERSION, StoreError
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.store import WorkbenchStore

from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


# ---- fail-closed defaults for partial / pre-upgrade records -----------------

def test_missing_actor_kind_fails_closed_to_service_not_human(tmp_path):
    # a registry entry written before actor_kind existed must NOT gain the
    # privileged HUMAN value that gates human-only adjudication.
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-partial", "actor_id": "legacy", "roles": ["ANALYST"],
         "releasability": ["PUBLIC"], "organisation": "m"},  # no actor_kind
    ])
    registry = ActorRegistry(actors)
    assert registry.context_for("tok-partial").actor_kind == "SERVICE"
    assert registry.context_for_actor("legacy").actor_kind == "SERVICE"


def test_missing_marking_is_not_viewable(tmp_path):
    # a record carrying no marking is viewable by no one (never defaults to PUBLIC)
    ctx = AccessContext("c", "a", "HUMAN", ("ANALYST",), releasability=("PUBLIC",))
    assert can_view(None, ctx) is False


def test_marking_reconstruction_does_not_launder_a_missing_min_role(tmp_path):
    # review M-5: reconstructing a partial marking (no min_role) must NOT lower it
    # to OBSERVER and turn a can_view deny into an allow.
    from curunir_operational.access import marking_from_record
    ctx = AccessContext("c", "a", "HUMAN", ("OBSERVER",), releasability=("PUBLIC",))
    partial = {"owning_authority": "auth", "releasability": ["PUBLIC"]}  # no min_role
    assert can_view(partial, ctx) is False                 # raw: fail-closed
    remade = marking_from_record(partial)
    assert remade.min_role == "SUPERVISOR"                 # most restrictive, not OBSERVER
    assert can_view(remade.to_record(), ctx) is False      # still denied after round-trip


# ---- additive-field backward compatibility ---------------------------------

def test_old_execution_record_without_truncated_replays_safely(tmp_path):
    from curunir_fabric.contracts import ExecutionRecord
    from curunir_operational.access import Marking
    make_workbench(tmp_path)  # creates the store
    store = WorkbenchStore(tmp_path / "store")
    mark = Marking(owning_authority="workbench-test", releasability=("PUBLIC",))
    rec = ExecutionRecord(
        execution_id="exec-old", plan_id="", query_id="q", source_id="gleif",
        connector_id="c", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0, request_url="u", http_status=200,
        policy_decision="ALLOW", error_class=None, error_detail="",
        manifestation_ids=(), started_time="2026-08-17T12:00:00+00:00",
        completed_time="2026-08-17T12:00:00+00:00",
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=mark).to_record()
    del rec["truncated"]                       # simulate a pre-upgrade record
    store.append("FABRIC_EXECUTION_RECORDED", rec,
                 recorded_time="2026-08-17T12:00:00+00:00", actor="t")
    # reopen under current code: it replays and the absence logic reads the
    # missing field as non-truncated (the pre-upgrade behavior — transparent).
    reopened = WorkbenchStore(tmp_path / "store")
    got = [e for e in reopened.records_of("fabric_execution") if e["execution_id"] == "exec-old"]
    assert got and "truncated" not in got[0]
    assert not got[0].get("truncated")         # safe default on read
    assert reopened.verify_chain()["valid"]


# ---- version guard + rollback ----------------------------------------------

def test_incompatible_contract_version_is_refused_on_import(tmp_path):
    make_workbench(tmp_path)
    store = WorkbenchStore(tmp_path / "store")
    backup = tmp_path / "backup"
    store.export_to(backup)

    # forge a FUTURE contract version in the (authenticated) store_meta and
    # re-hash it in the manifest, so only the version guard can catch it.
    meta_path = backup / "store_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["contract_version"] = "curunir-operational-contracts-vFUTURE"
    new_bytes = (canonical_line(meta) + "\n").encode("utf-8")
    meta_path.write_bytes(new_bytes)
    manifest_path = backup / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["store_meta_sha256"] = hashlib.sha256(new_bytes).hexdigest()
    manifest_path.write_text(canonical_line(manifest) + "\n", encoding="utf-8")

    with pytest.raises(StoreError, match="contract version"):
        WorkbenchStore.import_from(backup, tmp_path / "restored")
    # and importing the UNMODIFIED (compatible) backup still works — rollback path
    store.export_to(tmp_path / "clean_backup")
    restored = WorkbenchStore.import_from(tmp_path / "clean_backup", tmp_path / "restored2")
    assert restored.meta["contract_version"] == CONTRACT_VERSION


def test_backup_rollback_restores_prior_state(tmp_path):
    # rollback = restore the pre-change backup; the original mission survives an
    # (undesired) later change intact.
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    store = WorkbenchStore(tmp_path / "store")
    pre_upgrade = store.head()
    backup = tmp_path / "pre_upgrade_backup"
    store.export_to(backup)

    # an undesired later change lands on the live store
    from curunir_analytic.impact import create_objective
    from curunir_analytic.substrate import AnalyticContext
    from semantic_support import MARK, clock
    live_ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=clock(600))
    create_objective(live_ctx, mission_context="regret", statement="undesired change")
    assert store.head() != pre_upgrade

    # roll back: restore the backup into a fresh root, previous state intact
    rolled_back = WorkbenchStore.import_from(backup, tmp_path / "rolled_back")
    assert rolled_back.head() == pre_upgrade
    assert rolled_back.verify_chain()["valid"]
    assert seeded["forecast"]["forecast_id"] in rolled_back.current_forecasts()
    # the undesired objective is not in the rolled-back mission
    assert not any(o["statement"] == "undesired change"
                   for o in rolled_back.current_objectives().values())


def test_failed_import_rolls_back_and_leaves_no_debris(tmp_path):
    # review F-I1: a post-install import failure must clean up the target root
    # (no permanently-unopenable debris) and raise a typed StoreError.
    import hashlib
    make_workbench(tmp_path)
    store = WorkbenchStore(tmp_path / "store")
    backup = tmp_path / "backup"
    store.export_to(backup)
    events = backup / "events.jsonl"
    lines = events.read_bytes().split(b"\n")
    ev = json.loads(lines[0]); ev["entry_hash"] = "de" * 32   # break the chain post-install
    lines[0] = json.dumps(ev).encode()
    new_bytes = b"\n".join(lines)
    events.write_bytes(new_bytes)
    manifest_path = backup / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["events_sha256"] = hashlib.sha256(new_bytes).hexdigest()   # so it passes the hash gate
    manifest_path.write_text(canonical_line(manifest) + "\n")
    restored = tmp_path / "restored"
    with pytest.raises(StoreError):
        WorkbenchStore.import_from(backup, restored)
    assert not (restored / "store_meta.json").exists()   # no debris
    assert not (restored / "events.jsonl").exists()


def test_import_install_failure_rolls_back_whole_root_and_is_typed(tmp_path):
    # review NEW-2: a failure DURING install (tampered payload content, a payload
    # file absent, a mid-copy error) — not only the post-install open — must roll
    # back the ENTIRE target root. It must never leave a store that opens and
    # verifies clean while silently missing payload content, nor a root that then
    # refuses a retry, and every failure surfaces as a typed StoreError.
    make_workbench(tmp_path)
    store = WorkbenchStore(tmp_path / "store")
    store.put_payload(b"evidence-A"); store.put_payload(b"evidence-B")

    # (A) tampered payload CONTENT — events.jsonl + store_meta still hash-match the
    # manifest, so both top gates pass and the failure fires mid-install.
    backup_a = tmp_path / "backup_a"
    store.export_to(backup_a)
    sorted((backup_a / "payloads").iterdir())[0].write_bytes(b"tampered")
    restored_a = tmp_path / "restored_a"
    with pytest.raises(StoreError):
        WorkbenchStore.import_from(backup_a, restored_a)
    assert not restored_a.exists()          # whole freshly-created root removed -> retry is clean

    # (B) a payload file ABSENT from the export → a typed StoreError, not a raw
    # FileNotFoundError, and again no debris.
    backup_b = tmp_path / "backup_b"
    store.export_to(backup_b)
    sorted((backup_b / "payloads").iterdir())[0].unlink()
    restored_b = tmp_path / "restored_b"
    with pytest.raises(StoreError):
        WorkbenchStore.import_from(backup_b, restored_b)
    assert not restored_b.exists()


def test_canonical_line_refuses_non_finite_float(tmp_path):
    # review NEW-C: the serialization seam refuses NaN/Infinity so no record can
    # carry a bare non-RFC-8259 token into the append-only log (the surrogate
    # class's twin). Finite floats are unaffected.
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            canonical_line({"x": bad})
        with pytest.raises(ValueError):
            canonical_line([1, {"deep": [bad]}])        # nested containers too
    assert canonical_line({"x": 1.5, "y": -2.0, "z": 3})  # finite is fine


def test_import_refuses_non_finite_event_log(tmp_path):
    # review MAJOR-1: import installs export bytes VERBATIM (bypassing the
    # canonical_line seam), so a hostile export whose manifest hashes match — or a
    # legal "1e400" that json.loads returns as inf — must be refused BY VALUE, not
    # silently reconstructed into a poisoned (chain-valid) store the projection
    # 500s on forever. And no debris in the target root.
    import hashlib
    def _poison(sub, mutate_line):
        make_workbench(tmp_path / sub)
        store = WorkbenchStore(tmp_path / sub / "store")
        exp = tmp_path / sub / "exp"; store.export_to(exp)
        lines = (exp / "events.jsonl").read_bytes().split(b"\n")
        lines[0] = mutate_line(lines[0])
        newb = b"\n".join(lines); (exp / "events.jsonl").write_bytes(newb)
        man = json.loads((exp / "export_manifest.json").read_text())
        man["events_sha256"] = hashlib.sha256(newb).hexdigest()
        (exp / "export_manifest.json").write_text(canonical_line(man) + "\n")
        dest = tmp_path / sub / "dest"
        with pytest.raises(StoreError):
            WorkbenchStore.import_from(exp, dest)
        assert not dest.exists()                       # no poisoned debris

    def _nan(line):
        ev = json.loads(line); ev["record"]["score"] = float("nan")
        return json.dumps(ev, allow_nan=True).encode()
    def _inf(line):
        ev = json.loads(line); ev["record"]["score"] = float("inf")
        return json.dumps(ev, allow_nan=True).encode()
    def _e400(line):                                    # legal RFC-8259, parses to inf
        ev = json.loads(line); ev["record"]["score"] = 0.0
        return json.dumps(ev).replace('"score":0.0', '"score":1e400').encode()
    _poison("nan", _nan)
    _poison("inf", _inf)
    _poison("e400", _e400)


def test_import_refuses_non_finite_store_meta(tmp_path):
    # review MAJOR-1 / N-5 self-review: store_meta is byte-copied too, so it needs
    # the same by-value + depth guard (a non-finite float AND a deeply nested
    # hostile meta must both be a typed StoreError, never an untyped RecursionError).
    import hashlib
    def _poison_meta(sub, produce_bytes):
        make_workbench(tmp_path / sub)
        store = WorkbenchStore(tmp_path / sub / "store")
        exp = tmp_path / sub / "exp"; store.export_to(exp)
        newb = produce_bytes(json.loads((exp / "store_meta.json").read_text()))
        (exp / "store_meta.json").write_bytes(newb)
        man = json.loads((exp / "export_manifest.json").read_text())
        man["store_meta_sha256"] = hashlib.sha256(newb).hexdigest()
        (exp / "export_manifest.json").write_text(canonical_line(man) + "\n")
        with pytest.raises(StoreError):
            WorkbenchStore.import_from(exp, tmp_path / sub / "dest")

    def _inf(meta):
        meta["junk"] = float("inf"); return json.dumps(meta, allow_nan=True).encode()
    def _deep(meta):
        node = meta
        for _ in range(2500):
            node["n"] = {}; node = node["n"]
        return json.dumps(meta).encode()
    _poison_meta("meta_inf", _inf)
    _poison_meta("meta_deep", _deep)
    _poison_meta("meta_scalar", lambda meta: b"5")            # a non-dict meta (self-review)


def test_import_refuses_duplicate_keyed_and_deeply_nested_events(tmp_path):
    # review N-4/N-5: a by-VALUE non-finite check cannot catch a duplicate-key
    # token ("score":NaN,"score":1.0 parses finite but leaves a bare NaN in the
    # archived bytes), and a deeply nested event must not escape as an untyped
    # RecursionError. Both are refused with a typed StoreError and no debris.
    import hashlib
    def _poison(sub, mutate_bytes):
        make_workbench(tmp_path / sub)
        store = WorkbenchStore(tmp_path / sub / "store")
        exp = tmp_path / sub / "exp"; store.export_to(exp)
        lines = (exp / "events.jsonl").read_bytes().split(b"\n")
        lines[0] = mutate_bytes(lines[0])
        newb = b"\n".join(lines); (exp / "events.jsonl").write_bytes(newb)
        man = json.loads((exp / "export_manifest.json").read_text())
        man["events_sha256"] = hashlib.sha256(newb).hexdigest()
        (exp / "export_manifest.json").write_text(canonical_line(man) + "\n")
        dest = tmp_path / sub / "dest"
        with pytest.raises(StoreError):
            WorkbenchStore.import_from(exp, dest)
        assert not dest.exists()

    def _dupkey(line):                       # a duplicate key (both values finite)
        # operate on the raw canonical bytes (no spaces), so the injection matches
        return line.replace(b'"record":{', b'"record":{"dup":1,"dup":2,', 1)
    def _deep(line):                         # nested past Python's recursion limit
        ev = json.loads(line); node = ev["record"]
        for _ in range(2000):
            node["n"] = {}; node = node["n"]
        return json.dumps(ev).encode()
    _poison("dupkey", _dupkey)
    _poison("deep", _deep)


def test_export_is_locked_and_caught_up_so_backups_are_restorable(tmp_path):
    # review B-2: export_to must hold the append lock + catch up, so a STALE
    # in-memory instance cannot write a manifest head that disagrees with the
    # copied events.jsonl (a backup import_from then silently refuses).
    make_workbench(tmp_path)
    stale = WorkbenchStore(tmp_path / "store")          # opens, caches head in memory
    _ = stale.head()
    # a SECOND instance (another process) appends after `stale` cached its head
    from curunir_analytic.impact import create_objective
    from curunir_analytic.substrate import AnalyticContext
    from semantic_support import MARK, clock
    other = WorkbenchStore(tmp_path / "store")
    create_objective(AnalyticContext(store=other, actor="t", marking=MARK, now_fn=clock(600)),
                     mission_context="m", statement="appended by another writer")
    # the stale instance exports — must catch up under the lock, so the backup restores
    backup = tmp_path / "backup"; stale.export_to(backup)
    restored = WorkbenchStore.import_from(backup, tmp_path / "restored")
    assert restored.head()["head_hash"] == other.head()["head_hash"]   # backup == true head
