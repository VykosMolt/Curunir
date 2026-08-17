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
