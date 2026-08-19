"""Regression locks for the adversarial-review findings on the store durability
layer (V6.7 crash-recovery review). Each asserts the CORRECTED behavior.

C-1 content-triggered corruption via U+2028/U+2029/U+0085; M-1 an unterminated
final line accepted as committed; M-2 a torn tail cutting a UTF-8 sequence; C-2
recovery racing a live writer; C-3 the two-rename data-loss window; M-4 the
version guard bypassed by the plain constructor.
"""
from __future__ import annotations

import fcntl
import threading
import time

import pytest

from curunir_analytic.impact import create_objective
from curunir_analytic.substrate import AnalyticContext
from curunir_operational.store import StoreError
from curunir_workbench.store import WorkbenchStore

from semantic_support import MARK, clock
from workbench_support import make_workbench, seed_mission


pytestmark = pytest.mark.no_db


def _store(tmp_path):
    make_workbench(tmp_path)
    return tmp_path / "store"


# ---- C-1: U+2028/U+2029/U+0085 in recorded text must NOT corrupt the store ---

@pytest.mark.parametrize("sep", [" ", " ", ""])
def test_unicode_line_separators_in_text_do_not_corrupt_the_store(tmp_path, sep):
    root = _store(tmp_path)
    store = WorkbenchStore(root)
    ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=clock(600))
    create_objective(ctx, mission_context="c1", statement=f"before{sep}after prose")
    # the writer emits the separator RAW (ensure_ascii=False) — this is the trap
    if sep:
        assert sep.encode("utf-8") in (root / "events.jsonl").read_bytes()
    before = WorkbenchStore(root).head()          # reopens cleanly (no split corruption)
    assert WorkbenchStore(root).verify_chain()["valid"]
    assert before["event_count"] == store.head()["event_count"]


# ---- M-1: a complete-but-unterminated final line is a torn (uncommitted) write

def test_unterminated_final_line_is_treated_as_torn_and_recovered(tmp_path):
    root = _store(tmp_path)
    n = WorkbenchStore(root).head()["event_count"]
    events = root / "events.jsonl"
    raw = events.read_bytes()
    assert raw.endswith(b"\n")
    events.write_bytes(raw[:-1])                  # drop the terminating newline
    with pytest.raises(StoreError):               # not accepted as committed state
        WorkbenchStore(root)
    result = WorkbenchStore.recover_torn_tail(root)
    assert result["recovered"]
    assert WorkbenchStore(root).head()["event_count"] == n - 1  # the unconfirmed append is discarded


# ---- M-2: a torn tail cutting a multi-byte UTF-8 sequence recovers cleanly ----

def test_torn_tail_cutting_utf8_sequence_recovers_without_decode_error(tmp_path):
    root = _store(tmp_path)
    n = WorkbenchStore(root).head()["event_count"]
    with (root / "events.jsonl").open("ab") as handle:
        handle.write(b'{"seq": 99999, "detail": "caf\xc3')  # incomplete 2-byte char, no \n
    with pytest.raises(StoreError):
        WorkbenchStore(root)                       # torn, not an uncaught UnicodeDecodeError
    result = WorkbenchStore.recover_torn_tail(root)
    assert result["recovered"]
    assert WorkbenchStore(root).head()["event_count"] == n


# ---- C-3: events.jsonl is never absent; the crashed original is COPIED aside --

def test_recovery_preserves_original_by_copy_not_move(tmp_path):
    root = _store(tmp_path)
    with (root / "events.jsonl").open("ab") as handle:
        handle.write(b'{"torn')
    WorkbenchStore.recover_torn_tail(root)
    assert (root / "events.jsonl").exists()        # never moved away (no missing-file window)
    assert (root / "events.jsonl.torn").exists()   # original preserved as a COPY
    assert WorkbenchStore(root).verify_chain()["valid"]
    # a second crash + recovery does not clobber the first forensic remainder
    with (root / "events.jsonl").open("ab") as handle:
        handle.write(b'{"torn2')
    WorkbenchStore.recover_torn_tail(root)
    assert (root / "events.jsonl.torn").exists()
    torns = list(root.glob("events.jsonl.torn*"))
    assert len(torns) >= 2                          # earlier remainder kept


# ---- C-3 (round 2): invalid truncated content is refused, log left untouched --

def test_recovery_refuses_and_leaves_log_untouched_when_truncation_is_invalid(tmp_path):
    # a torn tail PLUS deeper damage (a chain-broken but parseable earlier line):
    # recovery must validate the truncated content BEFORE any write and, on
    # failure, leave events.jsonl byte-identical (no silent shortening).
    import json as _json
    root = _store(tmp_path)
    events = root / "events.jsonl"
    lines = events.read_bytes().split(b"\n")
    lines = [l for l in lines if l.strip()]
    assert len(lines) > 4
    victim = _json.loads(lines[2])
    victim["entry_hash"] = "de" * 32                      # valid JSON, broken chain
    lines[2] = _json.dumps(victim).encode()
    corrupted = b"\n".join(lines) + b"\n" + b'{"torn'     # + a torn tail
    events.write_bytes(corrupted)
    before = events.read_bytes()

    with pytest.raises(StoreError):
        WorkbenchStore.recover_torn_tail(root)
    assert events.read_bytes() == before                  # untouched: no silent loss
    assert not list(root.glob("events.jsonl.torn*"))      # no forensic file minted


# ---- C-2: recovery serializes against a live writer via the append lock -------

def test_recovery_blocks_on_the_append_lock(tmp_path):
    root = _store(tmp_path)
    with (root / "events.jsonl").open("ab") as handle:
        handle.write(b'{"torn')                     # a torn tail to recover
    done = threading.Event()

    def _recover():
        WorkbenchStore.recover_torn_tail(root)
        done.set()

    # hold the SAME lock the append path (and recovery) use
    with (root / ".append.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = threading.Thread(target=_recover, daemon=True)
        worker.start()
        time.sleep(0.3)
        assert not done.is_set(), "recovery must block while the append lock is held"
        fcntl.flock(held, fcntl.LOCK_UN)
    worker.join(timeout=5)
    assert done.is_set(), "recovery must proceed once the lock is released"
    assert WorkbenchStore(root).verify_chain()["valid"]


# ---- finding 1: recovery refuses a version-incompatible store (truthful result) -

def test_recovery_refuses_version_incompatible_store_even_with_torn_tail(tmp_path):
    import json
    root = _store(tmp_path)
    meta_path = root / "store_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["contract_version"] = "curunir-operational-contracts-vFUTURE"
    meta_path.write_text(json.dumps(meta))
    with (root / "events.jsonl").open("ab") as handle:
        handle.write(b'{"torn')                       # a real torn tail too
    # recovery must NOT report success on a store that would still refuse to open
    with pytest.raises(StoreError, match="contract version|not compatible"):
        WorkbenchStore.recover_torn_tail(root)


# ---- M-4: the plain constructor also refuses an incompatible contract version -

def test_constructor_refuses_incompatible_contract_version(tmp_path):
    import json
    root = _store(tmp_path)
    meta_path = root / "store_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["contract_version"] = "curunir-operational-contracts-vFUTURE"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(StoreError, match="contract version"):
        WorkbenchStore(root)                        # directory-copy / snapshot restore path


def test_put_payload_repairs_a_torn_write_and_get_payload_fails_loud(tmp_path):
    # review A-F2: an existing payload whose content does not hash to its name (a
    # prior torn write: SIGKILL/OOM/ENOSPC) must be REPAIRED by put_payload, not
    # trusted via the existence gate; get_payload must fail LOUD on a corrupt
    # payload rather than serve silent wrong evidence.
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import make_store
    store = make_store(tmp_path)
    body = b"the real evidence bytes, long enough to matter"
    digest = store.put_payload(body)
    (store.payload_dir / digest).write_bytes(b"trunc")           # simulate a torn write
    with pytest.raises(StoreError):
        store.get_payload(digest)                                # loud, not silent wrong bytes
    assert store.put_payload(body) == digest                     # self-repairs
    assert store.get_payload(digest) == body                     # correct again


def test_export_ignores_stray_non_digest_files_in_payload_dir(tmp_path):
    # review B-1: put_payload's temp lives OUTSIDE payload_dir and export copies
    # ONLY 64-hex content-address files, so a leftover temp / operator file can
    # never enter the manifest and make import_from refuse the whole restore.
    from curunir_operational.store import MissionDataStore
    from operational_support import make_store
    store = make_store(tmp_path)
    store.put_payload(b"real evidence bytes")
    (store.payload_dir / ".0abc.4242.99.tmp").write_bytes(b"orphan temp")   # simulate a crash orphan
    (store.payload_dir / "operator-note.txt").write_bytes(b"stray file")
    # put_payload's own temp never lingers inside payload_dir (it writes to root)
    assert not any(p.name.startswith(".payload.") for p in store.payload_dir.iterdir())
    exp = tmp_path / "exp"; store.export_to(exp)
    import json
    manifest = json.loads((exp / "export_manifest.json").read_text())
    assert manifest["payloads"] and all(len(p) == 64 for p in manifest["payloads"])   # only digests
    restored = MissionDataStore.import_from(exp, tmp_path / "dest")                    # not refused
    assert restored.verify_chain()["valid"]


def test_export_refuses_a_torn_payload(tmp_path):
    # R25B-1: export_to must SIGNAL at backup time when a 64-hex slot is
    # torn, not copy the truncated bytes and leave import_from to refuse.
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import make_store
    store = make_store(tmp_path)
    body = b"the real evidence bytes, long enough to matter"
    digest = store.put_payload(body)
    (store.payload_dir / digest).write_bytes(b"trunc")
    with pytest.raises(StoreError, match="corrupt"):
        store.export_to(tmp_path / "exp")


def test_import_retries_over_a_dest_without_store_meta(tmp_path):
    # R25B-2: store_meta is the commit marker. A leftover dest with events
    # but no store_meta (the crash window the old importer left mid-copy)
    # must not block retry, and the successful import must carry payloads.
    from curunir_operational.store import MissionDataStore
    from operational_support import make_store
    store = make_store(tmp_path)
    digest = store.put_payload(b"evidence bytes that must survive restore")
    exp = tmp_path / "exp"
    store.export_to(exp)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "events.jsonl").write_bytes(b"")          # crash leftover, no meta
    (dest / "payloads").mkdir()
    restored = MissionDataStore.import_from(exp, dest)
    assert restored.verify_chain()["valid"]
    assert restored.get_payload(digest) == b"evidence bytes that must survive restore"


def test_export_refuses_a_symlink_payload_slot(tmp_path):
    # R26B-1: a 64-hex symlink must fail the backup NOW, not be skipped so
    # import restores a store that cannot serve the evidence.
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import make_store
    store = make_store(tmp_path)
    body = b"evidence behind a planted symlink slot"
    digest = store.put_payload(body)
    real = tmp_path / "real-bytes"
    real.write_bytes(body)
    (store.payload_dir / digest).unlink()
    (store.payload_dir / digest).symlink_to(real)
    with pytest.raises(StoreError, match="not a regular file"):
        store.export_to(tmp_path / "exp")


def test_export_refuses_a_dest_symlink(tmp_path):
    # R26B-2 / R30B-1: dest-side writes must not follow a pre-planted
    # symlink. Staging-atomic export replaces dest wholesale, so the
    # planted member is never opened for write.
    from operational_support import make_store
    store = make_store(tmp_path)
    store.put_payload(b"payload body")
    victim = tmp_path / "victim"
    victim.write_bytes(b"ORIGINAL VICTIM -- must survive")
    dest = tmp_path / "exp"
    dest.mkdir()
    (dest / "payloads").mkdir()
    (dest / "events.jsonl").symlink_to(victim)
    store.export_to(dest)
    assert victim.read_bytes() == b"ORIGINAL VICTIM -- must survive"
    assert (dest / "events.jsonl").is_file()
    assert not (dest / "events.jsonl").is_symlink()


def test_import_overwrites_dest_with_torn_store_meta(tmp_path):
    # R26B-4: an empty/torn store_meta is not a store; retry must succeed.
    from curunir_operational.store import MissionDataStore
    from operational_support import make_store
    store = make_store(tmp_path)
    digest = store.put_payload(b"must survive retry over torn meta")
    exp = tmp_path / "exp"
    store.export_to(exp)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "store_meta.json").write_bytes(b"")
    restored = MissionDataStore.import_from(exp, dest)
    assert restored.get_payload(digest) == b"must survive retry over torn meta"


def test_import_refuses_dest_inside_or_containing_a_live_store(tmp_path):
    # R32B-1: dest = live.payload_dir or live.parent must not rmtree evidence.
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import make_store
    live = make_store(tmp_path, name="live")
    digest = live.put_payload(b"live evidence must survive dest overlap")
    exp = tmp_path / "exp"
    live.export_to(exp)
    with pytest.raises(StoreError, match="inside an existing store"):
        MissionDataStore.import_from(exp, live.payload_dir)
    assert live.get_payload(digest) == b"live evidence must survive dest overlap"
    with pytest.raises(StoreError, match="contains an existing store"):
        MissionDataStore.import_from(exp, tmp_path)
    assert (tmp_path / "live" / "store_meta.json").is_file()
    assert live.get_payload(digest) == b"live evidence must survive dest overlap"


def test_delta_refuses_dest_inside_the_source_store(tmp_path):
    from curunir_operational.delta import build_delta_bundle
    from curunir_operational.store import StoreError
    from operational_support import make_store
    store = make_store(tmp_path)
    store.put_payload(b"payload that a delta must not plant onto itself")
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_delta_bundle(store, store.root, base_seq=0)
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_delta_bundle(store, store.payload_dir, base_seq=0)


def test_create_refuses_leftover_payload_fifo(tmp_path):
    # R32B-2 sibling: leftover 64-hex FIFOs / digest files in payloads/
    # must not become a fresh store's evidence slots.
    import os
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import T0
    root = tmp_path / "fresh"
    payloads = root / "payloads"
    payloads.mkdir(parents=True)
    leftover = payloads / ("ab" * 32)
    os.mkfifo(leftover)
    with pytest.raises(StoreError, match="leftover payload"):
        MissionDataStore.create(root, "fresh-store", T0)
    leftover.unlink()
    leftover.write_bytes(b"torn leftover digest")
    with pytest.raises(StoreError, match="leftover payload"):
        MissionDataStore.create(root, "fresh-store", T0)


def test_export_refuses_planted_export_manifest_on_a_live_store(tmp_path):
    # R33B-1: a dest-side plant of export_manifest.json must not make
    # export_to rmtree a live store.
    from curunir_operational.store import StoreError
    from operational_support import make_store
    live = make_store(tmp_path, name="live")
    digest = live.put_payload(b"live evidence must survive planted manifest")
    src = make_store(tmp_path, name="src")
    src.put_payload(b"source payload")
    (live.root / "export_manifest.json").write_bytes(b"")
    with pytest.raises(StoreError, match="existing store"):
        src.export_to(live.root)
    assert live.get_payload(digest) == b"live evidence must survive planted manifest"
    (live.root / "export_manifest.json").unlink()
    (live.root / "export_manifest.json").symlink_to(tmp_path / "src" / "store_meta.json")
    with pytest.raises(StoreError, match="existing store"):
        src.export_to(live.root)
    assert live.get_payload(digest) == b"live evidence must survive planted manifest"


def test_pace_refuses_dest_inside_the_source_store(tmp_path):
    from curunir_operational.projection import Projection
    from curunir_operational.sovereignty import build_pace_bundle
    from curunir_operational.store import StoreError
    from operational_support import LOW_CONTEXT, make_store, t
    store = make_store(tmp_path)
    projection = Projection(store, snapshot_time=t(1))
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_pace_bundle(store, projection, LOW_CONTEXT, store.root,
                          operational_context="test")
    with pytest.raises(StoreError, match="overlaps the source store"):
        build_pace_bundle(store, projection, LOW_CONTEXT, store.payload_dir,
                          operational_context="test")


def test_create_refuses_hardlinked_empty_events(tmp_path):
    # R33B-3: empty leftover events is allowed (R30) only as a private
    # inode, not a hardlink to a live store's log.
    import os
    from curunir_operational.store import MissionDataStore, StoreError
    from operational_support import T0, make_store
    live = make_store(tmp_path, name="live")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    os.link(live.events_path, fresh / "events.jsonl")
    with pytest.raises(StoreError, match="hardlinked"):
        MissionDataStore.create(fresh, "fresh-store", T0)
    assert live.head()["event_count"] == 0
