"""Custody of a held-out partition: who may read its item text, and when.

Every test names the reason a refusal happened, not just that something was
raised, so a rewrite cannot pass by refusing for the wrong cause. Each control
is exercised both ways: the lawful path works, and the unlawful one fails.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from curunir_operational import partition_custody as PC

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# A file-open auditor, so "nothing was read" is measured rather than assumed.
# ---------------------------------------------------------------------------

_OPENS: list[str] = []
_WATCHING = [False]


def _audit(event, args):
    if _WATCHING[0] and event == "open":
        try:
            _OPENS.append(os.fspath(args[0]))
        except TypeError:
            _OPENS.append(repr(args[0]))


sys.addaudithook(_audit)


class watch_opens:
    """Record every path opened inside the block."""

    def __enter__(self):
        _OPENS.clear()
        _WATCHING[0] = True
        return _OPENS

    def __exit__(self, *exc):
        _WATCHING[0] = False
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNITS = [f"unit-{index:03d}" for index in range(6)]


def _row(unit_id: str) -> str:
    return json.dumps(
        {"unit_id": unit_id, "language": "en",
         "span_text": f"SECRET ITEM TEXT FOR {unit_id}"},
        sort_keys=True,
    )


@pytest.fixture
def croot(tmp_path):
    return str(tmp_path / "custody")


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "store.jsonl"
    path.write_text("\n".join(_row(unit) for unit in UNITS) + "\n")
    return path


@pytest.fixture
def sealed(tmp_path, croot, store):
    PC.seal_store("TEST_STORE", root=croot, relative_path=str(store),
                  reason="test store")
    return store


def _ok_prereqs():
    return {"exposed_gates_pass": True, "predeclarations_frozen": True}


def _open(croot, **kwargs):
    kwargs.setdefault("purpose", "scoring")
    kwargs.setdefault("prerequisites", _ok_prereqs())
    return PC.open_sealed_partition("TEST_STORE", root=croot, **kwargs)


# ---------------------------------------------------------------------------
# Physical separation
# ---------------------------------------------------------------------------


def test_seal_denies_a_direct_read_that_worked_before(tmp_path, croot, store):
    assert store.read_text()  # it is readable before the seal
    PC.seal_store("TEST_STORE", root=croot, relative_path=str(store),
                  reason="test store")
    assert stat.S_IMODE(store.stat().st_mode) == PC.SEALED_MODE
    with pytest.raises(PermissionError):
        store.read_text()


def test_sealing_changes_no_content(tmp_path, croot, store):
    import hashlib

    before = hashlib.sha256(store.read_bytes()).hexdigest()
    PC.seal_store("TEST_STORE", root=croot, relative_path=str(store),
                  reason="test store")
    with _open(croot) as path:
        after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before
    assert stat.S_IMODE(store.stat().st_mode) == PC.SEALED_MODE


def test_seal_records_no_digest_it_would_have_had_to_read(tmp_path, croot,
                                                          store):
    entry = PC.seal_store("TEST_STORE", root=croot, relative_path=str(store),
                          reason="test store")
    assert entry["sha256"] is None
    assert entry["digest_source"] == (
        "not_taken_hashing_would_read_holdout_bytes"
    )


def test_directory_store_is_sealed_whole(tmp_path, croot):
    tree = tmp_path / "seat_delivery"
    tree.mkdir()
    (tree / "aligned_001.json").write_text("{}")
    PC.seal_store("TEST_TREE", root=croot, relative_path=str(tree),
                  reason="seat tree", kind="DIRECTORY")
    try:
        with pytest.raises(PermissionError):
            list(tree.iterdir())
        with pytest.raises(PermissionError):
            (tree / "aligned_001.json").read_text()
        # A path inside a sealed directory counts as sealed.
        assert PC.is_sealed_path(tree / "aligned_001.json",
                                 root=croot).store_id == "TEST_TREE"
    finally:
        # Restore the mode so the temp directory can be cleaned up.
        os.chmod(tree, 0o755)


# ---------------------------------------------------------------------------
# The one-shot lock: a second opening is refused
# ---------------------------------------------------------------------------


def test_one_shot_lock_refuses_a_second_opening(croot, sealed):
    with _open(croot) as path:
        assert path.read_text()

    with pytest.raises(PC.CustodyViolation) as excinfo:
        with _open(croot):
            pytest.fail("the second opening must never reach the body")
    assert "one-shot lock" in str(excinfo.value)
    assert "opened before" in str(excinfo.value)

    # The refusal left the store sealed, not merely unread.
    assert stat.S_IMODE(sealed.stat().st_mode) == PC.SEALED_MODE
    with pytest.raises(PermissionError):
        sealed.read_text()

    observer = PC.AccessObserver(croot)
    assert observer.report("TEST_STORE")["open_count"] == 1


def test_one_shot_lock_is_exclusive_at_the_filesystem(tmp_path, croot):
    root = PC.custody_root(croot).ensure()
    lock = PC.OneShotLock(root, "SOME_PARTITION")
    assert not lock.is_taken()
    lock.acquire(purpose="first")
    assert lock.is_taken()
    with pytest.raises(PC.CustodyViolation) as excinfo:
        lock.acquire(purpose="second")
    assert "already taken" in str(excinfo.value)
    assert stat.S_IMODE(lock.path.stat().st_mode) == 0o444


def test_refused_second_opening_is_itself_recorded(croot, sealed):
    with _open(croot):
        pass
    with pytest.raises(PC.CustodyViolation):
        with _open(croot):
            pass
    report = PC.AccessObserver(croot).report("TEST_STORE")
    reasons = [entry["reason"] for entry in report["denied_attempts"]]
    assert "one_shot_lock_taken" in reasons


# ---------------------------------------------------------------------------
# First-access log
# ---------------------------------------------------------------------------


def test_first_access_is_logged_before_the_store_is_unsealed(croot, sealed):
    with _open(croot):
        pass
    log = PC.FirstAccessLog(PC.custody_root(croot))
    record = log.first_access("TEST_STORE")
    assert record is not None
    assert record["detail"]["store_was_sealed_when_logged"] is True
    assert record["detail"]["store_mode_at_log_time"] == "0o0"
    events = [entry["event"] for entry in log.events_for("TEST_STORE")]
    assert events.index("OPEN") < events.index("CLOSE")


def test_log_records_who_opened_it(croot, sealed):
    with _open(croot, purpose="GATE-8 prospective scoring"):
        pass
    record = PC.FirstAccessLog(PC.custody_root(croot)).first_access("TEST_STORE")
    assert record["actor"]["pid"] == os.getpid()
    assert record["actor"]["uid"] == os.getuid()
    assert record["detail"]["purpose"] == "GATE-8 prospective scoring"
    assert record["utc"].endswith("Z")


def test_tampering_with_a_record_breaks_the_chain(croot, sealed):
    with _open(croot):
        pass
    log_path = PC.custody_root(croot).access_log
    lines = log_path.read_text().splitlines()
    record = json.loads(lines[-1])
    record["detail"]["purpose"] = "something innocuous"
    lines[-1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    log_path.write_text("\n".join(lines) + "\n")

    state = PC.FirstAccessLog(PC.custody_root(croot)).verify()
    assert state["verified"] is False
    assert "altered" in state["reason"]


def test_deleting_the_last_record_is_detected(croot, sealed):
    with _open(croot):
        pass
    log_path = PC.custody_root(croot).access_log
    lines = log_path.read_text().splitlines()
    log_path.write_text("\n".join(lines[:-1]) + "\n")

    state = PC.FirstAccessLog(PC.custody_root(croot)).verify()
    assert state["verified"] is False
    assert "removed" in state["reason"] or "heads" in state["reason"]


def test_a_broken_log_refuses_further_appends(croot, sealed):
    with _open(croot):
        pass
    log_path = PC.custody_root(croot).access_log
    log_path.write_text(log_path.read_text().replace("CLOSE", "CLOSE_"))
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.FirstAccessLog(PC.custody_root(croot)).append(
            store_id="TEST_STORE", event="OPEN")
    assert "does not verify" in str(excinfo.value)


def test_a_broken_log_blocks_the_gate(tmp_path, croot):
    other = tmp_path / "other.jsonl"
    other.write_text(_row("unit-999") + "\n")
    PC.seal_store("OTHER_STORE", root=croot, relative_path=str(other),
                  reason="other")
    log_path = PC.custody_root(croot).access_log
    log_path.write_text(log_path.read_text().replace("SEAL", "SEAL_"))

    with pytest.raises(PC.CustodyViolation) as excinfo:
        with PC.open_sealed_partition("OTHER_STORE", root=croot,
                                      purpose="scoring",
                                      prerequisites=_ok_prereqs()):
            pytest.fail("the gate must not open over an unverifiable log")
    assert "does not verify" in str(excinfo.value)
    assert stat.S_IMODE(other.stat().st_mode) == PC.SEALED_MODE


def test_an_unwritable_ledger_fails_closed(croot, sealed):
    root = PC.custody_root(croot).ensure()
    os.chmod(root.locks, 0o500)
    try:
        with pytest.raises((PC.CustodyViolation, PermissionError, OSError)):
            with _open(croot):
                pytest.fail("must not open when the ledger cannot be written")
    finally:
        os.chmod(root.locks, 0o700)
    assert stat.S_IMODE(sealed.stat().st_mode) == PC.SEALED_MODE
    with pytest.raises(PermissionError):
        sealed.read_text()


# ---------------------------------------------------------------------------
# Access observer
# ---------------------------------------------------------------------------


def test_observer_says_no_with_evidence_before_any_access(croot, sealed):
    report = PC.AccessObserver(croot).report("TEST_STORE")
    assert report["has_been_read"] == "NO"
    assert report["open_count"] == 0
    assert report["one_shot_lock"]["taken"] is False
    assert report["store"]["sealed"] is True
    assert report["log"]["verified"] is True


def test_observer_says_yes_and_names_the_reader(croot, sealed):
    with _open(croot, purpose="GATE-9 sealed confirmation"):
        pass
    report = PC.AccessObserver(croot).report("TEST_STORE")
    assert report["has_been_read"] == "YES"
    assert report["open_count"] == 1
    assert report["unterminated_open"] is False
    reader = report["read_by"][0]
    assert reader["pid"] == os.getpid()
    assert reader["purpose"] == "GATE-9 sealed confirmation"
    assert len(reader["record_hash"]) == 64
    assert report["one_shot_lock"]["taken"] is True


def test_observer_never_says_no_when_the_ledger_is_tampered(croot, sealed):
    """Deleting the evidence must not read as innocence."""

    with _open(croot):
        pass
    assert PC.AccessObserver(croot).report("TEST_STORE")["has_been_read"] == "YES"

    PC.custody_root(croot).access_log.write_text("")

    report = PC.AccessObserver(croot).report("TEST_STORE")
    assert report["has_been_read"] == "UNKNOWN_LOG_TAMPERED"
    assert report["has_been_read"] != "NO"
    assert report["log"]["verified"] is False


def test_observer_reports_an_unterminated_open(croot, sealed):
    log = PC.FirstAccessLog(PC.custody_root(croot))
    log.append(store_id="TEST_STORE", event="OPEN", detail={"purpose": "x"})
    report = PC.AccessObserver(croot).report("TEST_STORE")
    assert report["unterminated_open"] is True


# ---------------------------------------------------------------------------
# Code-enforced prerequisite check
# ---------------------------------------------------------------------------


def test_a_false_prerequisite_refuses_and_does_not_burn_the_lock(croot, sealed):
    prereqs = {"exposed_gates_pass": False, "predeclarations_frozen": True}
    with pytest.raises(PC.CustodyViolation) as excinfo:
        with PC.open_sealed_partition("TEST_STORE", root=croot,
                                      purpose="scoring",
                                      prerequisites=prereqs):
            pytest.fail("must not open with an unsatisfied prerequisite")
    assert "exposed_gates_pass" in str(excinfo.value)

    assert stat.S_IMODE(sealed.stat().st_mode) == PC.SEALED_MODE
    report = PC.AccessObserver(croot).report("TEST_STORE")
    assert report["has_been_read"] == "NO"
    assert report["one_shot_lock"]["taken"] is False, (
        "a refused opening must remain available once the prerequisite passes"
    )
    assert "prerequisites" in [entry["reason"]
                               for entry in report["denied_attempts"]]


def test_declaring_no_prerequisites_at_all_is_refused(croot, sealed):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        with PC.open_sealed_partition("TEST_STORE", root=croot,
                                      purpose="scoring", prerequisites={}):
            pytest.fail("an undeclared prerequisite set must not open")
    assert "no prerequisites were declared" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The hardened loader
# ---------------------------------------------------------------------------


def test_loader_refuses_a_sealed_store_without_opening_it(croot, sealed):
    with watch_opens() as opened:
        with pytest.raises(PC.CustodyViolation) as excinfo:
            PC.load_exposed_population(sealed, root=croot)
    assert "sealed store" in str(excinfo.value)
    assert "No bytes were read" in str(excinfo.value)
    assert str(sealed) not in opened, (
        "the refusal must precede the open, not follow a failed one"
    )


def test_loader_refuses_a_sealed_store_reached_through_a_symlink(tmp_path,
                                                                 croot, sealed):
    link = tmp_path / "innocent_name.jsonl"
    link.symlink_to(sealed)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(link, root=croot)
    assert "sealed store" in str(excinfo.value)


def test_loader_refuses_a_foreign_identity_before_deserializing(tmp_path, croot,
                                                                monkeypatch):
    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text("\n".join(_row(unit) for unit in UNITS) + "\n")

    calls = []
    real_loads = json.loads
    monkeypatch.setattr(json, "loads",
                        lambda *a, **k: (calls.append(1), real_loads(*a, **k))[1])

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(mixed, expected_unit_ids=set(UNITS[:3]),
                                   root=croot)
    assert "not in the permitted identity set" in str(excinfo.value)
    assert "before any record is deserialized" in str(excinfo.value)
    assert calls == [], "no record may be deserialized once a foreign id is seen"


def test_loader_refuses_a_partial_population(tmp_path, croot):
    partial = tmp_path / "partial.jsonl"
    partial.write_text("\n".join(_row(unit) for unit in UNITS[:3]) + "\n")
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(partial, expected_unit_ids=set(UNITS),
                                   root=croot)
    assert "missing" in str(excinfo.value)


def test_identifier_scan_ignores_an_identifier_shaped_string_in_item_text(
        tmp_path, croot):
    """Item text containing the string "unit_id" is escaped in JSON, so the
    scanner does not mistake it for the key."""

    tricky = tmp_path / "tricky.jsonl"
    tricky.write_text(json.dumps(
        {"unit_id": "a", "span_text": '{"unit_id": "b"}'}, sort_keys=True) + "\n")
    rows = PC.load_exposed_population(tricky, expected_unit_ids={"a"},
                                      root=croot)
    assert set(rows) == {"a"}


def test_loader_refuses_a_duplicated_top_level_identifier(tmp_path, croot):
    """Two top-level unit_id keys are ambiguous, so the scanner refuses rather
    than keeping the last one the way json.loads would."""

    tricky = tmp_path / "tricky.jsonl"
    tricky.write_text('{"unit_id": "a", "unit_id": "b"}\n')
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(tricky, expected_unit_ids={"a"}, root=croot)
    assert "exactly one top-level unit_id" in str(excinfo.value)
    assert "found 2" in str(excinfo.value)


def test_loader_refuses_a_record_with_no_identifier(tmp_path, croot):
    anonymous = tmp_path / "anonymous.jsonl"
    anonymous.write_text('{"span_text": "SECRET"}\n')
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(anonymous, expected_unit_ids={"a"},
                                   root=croot)
    assert "found 0" in str(excinfo.value)
    assert "refusing to deserialize" in str(excinfo.value)


def test_loader_refuses_a_digest_mismatch(tmp_path, croot, store):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_exposed_population(store, expect_sha256="0" * 64, root=croot)
    assert "digest mismatch" in str(excinfo.value)


def test_loader_returns_exactly_what_the_legacy_idiom_returned(croot, store):
    legacy = {json.loads(line)["unit_id"]: json.loads(line)
              for line in store.read_text().splitlines() if line}
    hardened = PC.load_exposed_population(store, expected_unit_ids=set(UNITS),
                                          root=croot)
    assert hardened == legacy
    assert list(hardened) == list(legacy)


# ---------------------------------------------------------------------------
# Shard authorisation
# ---------------------------------------------------------------------------


def _shard_module():
    path = (Path(PC.__file__).resolve().parent.parent / "artifacts"
            / "curunir_autonomous_completion_v5_8_1_20260725"
            / "55_d25_role_binding_reference" / "freeze"
            / "build_aligned_shards.py")
    if not path.exists():
        pytest.skip("campaign tree not present in this checkout")
    spec = importlib.util.spec_from_file_location("_build_aligned_shards", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authorised_load_never_deserializes_an_unauthorised_packet(
        tmp_path, croot, store, monkeypatch):
    calls = []
    real_loads = json.loads
    monkeypatch.setattr(json, "loads",
                        lambda *a, **k: (calls.append(1), real_loads(*a, **k))[1])

    rows = PC.load_authorised_packets(store, UNITS[:2], root=croot)

    assert set(rows) == set(UNITS[:2])
    assert len(calls) == 2, (
        "exactly the authorised records may be deserialized; the other four "
        "must never become objects"
    )
    assert all("SECRET ITEM TEXT FOR unit-00" in row["span_text"]
               for row in rows.values())
    for unit in UNITS[2:]:
        assert unit not in rows


def test_authorised_load_refuses_a_holdout_identifier(croot, store):
    """An allowlist naming a holdout unit is refused, not quietly honoured."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED", UNITS[3:], corpus_id="V3",
                              root=croot)

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_authorised_packets(store, UNITS, what="shard build", root=croot)
    assert "registered holdout units" in str(excinfo.value)
    assert "V3_SEALED" in str(excinfo.value)
    # The refusal must not name the identifiers it is protecting.
    assert UNITS[3] not in str(excinfo.value)


def test_authorised_load_refuses_an_empty_authorisation(croot, store):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_authorised_packets(store, [], root=croot)
    assert "empty authorisation list is refused" in str(excinfo.value)


def test_authorised_load_refuses_a_sealed_store(croot, sealed):
    with watch_opens() as opened:
        with pytest.raises(PC.CustodyViolation) as excinfo:
            PC.load_authorised_packets(sealed, UNITS[:1], root=croot)
    assert "sealed store" in str(excinfo.value)
    assert str(sealed) not in opened


def test_authorised_load_refuses_a_partial_authorisation(croot, store):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_authorised_packets(store, UNITS + ["unit-999"], root=croot)
    assert "authorised identities are absent" in str(excinfo.value)


def test_shard_builder_requires_an_explicit_authorisation():
    module = _shard_module()
    with pytest.raises(SystemExit) as excinfo:
        module.main(["--corpus-id", "V3"])
    assert excinfo.value.code != 0


def test_shard_builder_refuses_when_handed_a_holdout_id(tmp_path, croot, store):
    module = _shard_module()
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED", [UNITS[5]], corpus_id="V3",
                              root=croot)

    authorisation = tmp_path / "authorised.json"
    authorisation.write_text(json.dumps(UNITS))

    with pytest.raises(PC.CustodyViolation) as excinfo:
        module.main(["--authorised-units", str(authorisation),
                     "--corpus-id", "V3", "--packets", str(store),
                     "--custody-root", croot])
    assert "registered holdout units" in str(excinfo.value)


def test_shard_builder_refuses_an_unparseable_authorisation(tmp_path, croot,
                                                            store):
    module = _shard_module()
    authorisation = tmp_path / "authorised.json"
    authorisation.write_text("[]")
    with pytest.raises(PC.CustodyViolation) as excinfo:
        module.main(["--authorised-units", str(authorisation),
                     "--corpus-id", "V3", "--packets", str(store),
                     "--custody-root", croot])
    assert "empty authorisation" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Holdout eligibility: never read, never sharded, never judged
# ---------------------------------------------------------------------------


def test_eligibility_refuses_a_corpus_nobody_was_watching(croot):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_eligible_for_holdout(UNITS, corpus_id="V3", root=croot)
    assert "no exposure-ledger scope declaration" in str(excinfo.value)
    assert "not evidence of non-exposure" in str(excinfo.value)


def test_eligibility_refuses_a_unit_that_was_sharded(croot):
    """A unit already delivered to a reviewer is not eligible, even with no
    reference answer recorded for it."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.record_exposure("SHARDED", [UNITS[2]], corpus_id="V3", root=croot)

    referenced = PC.exposed_unit_ids(corpus_id="V3", kinds=["REFERENCED"],
                                     root=croot)
    assert UNITS[2] not in referenced["REFERENCED"], (
        "the unit has no reference answer, so the old definition would have "
        "called it eligible"
    )
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_eligible_for_holdout(UNITS, corpus_id="V3", root=croot)
    assert "SHARDED" in str(excinfo.value)


def test_eligibility_refuses_a_unit_that_was_judged(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.record_exposure("JUDGED", [UNITS[1]], corpus_id="V3", root=croot)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_eligible_for_holdout(UNITS, corpus_id="V3", root=croot)
    assert "JUDGED" in str(excinfo.value)


def test_eligibility_accepts_units_no_channel_ever_touched(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.record_exposure("SHARDED", [UNITS[0]], corpus_id="V3", root=croot)
    report = PC.assert_eligible_for_holdout(UNITS[3:], corpus_id="V3",
                                            root=croot)
    assert report["eligible"] is True
    assert report["definition"] == (
        "NEVER_READ_AND_NEVER_SHARDED_AND_NEVER_JUDGED_AND_NEVER_REFERENCED"
    )


def test_exposure_kind_must_be_known(croot):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.record_exposure("GLANCED_AT", UNITS, corpus_id="V3", root=croot)
    assert "unknown exposure kind" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The standing seal on the historical stores
# ---------------------------------------------------------------------------


def test_the_historical_holdout_stores_are_sealed():
    """A failure here means a holdout store was left unsealed. Reseal it; do not
    relax the test."""

    present = [store for store in PC.SEALED_STORES if store.path.exists()]
    if not present:
        pytest.skip("campaign tree not present in this checkout")
    report = PC.verify_seals()
    breached = [store_id for store_id in report["breached"]
                if PC.store_for_id(store_id).path.exists()]
    assert breached == [], f"unsealed holdout-bearing stores: {breached}"


def test_the_shared_population_named_by_incident_306_cannot_be_read():
    store = PC.store_for_id("D25_SHARED_POPULATION_750")
    if not store.path.exists():
        pytest.skip("campaign tree not present in this checkout")
    with pytest.raises(PermissionError):
        store.path.open()
    with pytest.raises(PC.CustodyViolation):
        PC.load_exposed_population(store.path)


# ---------------------------------------------------------------------------
# The ledger names holdout identities, so it must not be world-readable, and a
# registration may commit to its membership instead of listing it.
# ---------------------------------------------------------------------------


def test_the_ledger_is_not_world_readable(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    root = PC.custody_root(croot)

    for path in (root.access_log, root.access_heads):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == PC.LEDGER_MODE, f"{path} is {mode:o}"
        assert not mode & 0o044, (
            f"{path} is readable outside its owner; the ledger names holdout "
            "identities and a control that publishes what it protects is not "
            "a control"
        )


def test_the_ledger_mode_is_repaired_on_an_already_world_readable_file(croot):
    """O_CREAT's mode is ignored once the file exists, so the writer has to set
    the mode again on every append."""

    root = PC.custody_root(croot).ensure()
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    os.chmod(root.access_log, 0o644)
    os.chmod(root.access_heads, 0o644)

    PC.declare_ledger_scope("V3_LATER", note="a later append", root=croot)

    assert stat.S_IMODE(root.access_log.stat().st_mode) == PC.LEDGER_MODE
    assert stat.S_IMODE(root.access_heads.stat().st_mode) == PC.LEDGER_MODE
    assert PC.FirstAccessLog(root).verify()["verified"], (
        "repairing the mode must not touch a single byte of the chain"
    )


def test_a_blinded_registration_does_not_publish_membership(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    record = PC.register_holdout_units(
        "V3_SEALED_BLIND", UNITS[2:], corpus_id="V3", blinded=True,
        identity_store="partitions/sealed_identity.jsonl", root=croot)

    detail = record["detail"]
    assert "unit_ids" not in detail
    assert detail["unit_count"] == len(UNITS[2:])
    assert len(detail["unit_id_commitments"]) == len(UNITS[2:])

    raw = PC.custody_root(croot).access_log.read_text()
    for unit in UNITS[2:]:
        assert unit not in raw, (
            "a blinded registration must leave no identity in the ledger"
        )
    assert PC.holdout_unit_ids(croot).get("V3_SEALED_BLIND") is None
    assert PC.holdout_commitments(croot)["V3_SEALED_BLIND"]
    assert PC.holdout_registrations(croot)["V3_SEALED_BLIND"]["forms"] == [
        "BLINDED_COMMITMENT"]


def test_the_salt_lives_outside_the_ledger_and_is_not_world_readable(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    record = PC.register_holdout_units("V3_SEALED_BLIND", UNITS[2:],
                                       corpus_id="V3", blinded=True,
                                       root=croot)
    salt_file = PC.custody_root(croot).root / PC.HOLDOUT_SALT_FILE
    salt = json.loads(salt_file.read_text())["V3_SEALED_BLIND"]

    assert salt not in json.dumps(record), (
        "the salt must not travel inside the record it blinds"
    )
    assert stat.S_IMODE(salt_file.stat().st_mode) == PC.LEDGER_MODE


def test_a_blinded_partition_is_still_refused_by_assert_not_holdout(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED_BLIND", UNITS[3:], corpus_id="V3",
                              blinded=True, root=croot)

    assert PC.assert_not_holdout(UNITS[:3], what="shard build",
                                 root=croot)["collisions"] == 0

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_not_holdout(UNITS, what="shard build", root=croot)
    assert "registered holdout units" in str(excinfo.value)
    assert "V3_SEALED_BLIND" in str(excinfo.value)
    assert UNITS[3] not in str(excinfo.value)


def test_a_blinded_registration_whose_salt_is_lost_refuses_rather_than_passes(
        croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED_BLIND", UNITS[3:], corpus_id="V3",
                              blinded=True, root=croot)
    (PC.custody_root(croot).root / PC.HOLDOUT_SALT_FILE).unlink()

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_not_holdout(UNITS[:1], what="shard build", root=croot)
    assert "cannot be tested" in str(excinfo.value)


def test_a_plaintext_registration_still_behaves_exactly_as_before(croot):
    """Adding the blinded form leaves the plaintext one working as before."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED", UNITS[3:], corpus_id="V3",
                              root=croot)
    assert PC.holdout_unit_ids(croot)["V3_SEALED"] == set(UNITS[3:])
    assert PC.holdout_registrations(croot)["V3_SEALED"]["forms"] == [
        "PLAINTEXT_IDENTITY_LIST"]
    with pytest.raises(PC.CustodyViolation):
        PC.assert_not_holdout(UNITS, what="shard build", root=croot)


def test_a_second_salt_for_one_partition_is_refused(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_SEALED_BLIND", UNITS[3:], corpus_id="V3",
                              blinded=True, root=croot)
    salt_file = PC.custody_root(croot).root / PC.HOLDOUT_SALT_FILE
    salts = json.loads(salt_file.read_text())
    salts["V3_SEALED_BLIND"] = "0" * 64
    salt_file.write_text(json.dumps(salts))

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC._store_salt(PC.custody_root(croot), "V3_SEALED_BLIND", "1" * 64)
    assert "different commitment salt" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The adjudication read: a narrow way to hand drawn units to a packet builder.
# The two older APIs cannot do it — one refuses every registered holdout id,
# the other would spend the one-shot lock reserved for the sealed opening.
# Each control below is exercised both ways.
# ---------------------------------------------------------------------------

PARTITION = "V3_TEST_PARTITION"
OTHER_PARTITION = "V3_TEST_PARTITION_OTHER"
DRAWN = UNITS[:4]
OUTSIDE = UNITS[4:]


def _adj_prereqs():
    """The adjudication's own checklist.

    The reference has to be frozen before the partition is opened, so the
    adjudication read cannot be gated on the opening checklist.
    """

    return {"draw_record_pinned": True, "identity_list_frozen": True,
            "seat_roster_engaged": True}


@pytest.fixture
def adjudication(tmp_path, croot, store):
    """A sealed unit store whose drawn units form a blinded holdout partition."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3", blinded=True,
                              identity_store="partitions/identity.jsonl",
                              root=croot)
    PC.register_holdout_units(OTHER_PARTITION, OUTSIDE, corpus_id="V3",
                              blinded=True, root=croot)
    PC.seal_store("V3_UNIT_STORE", root=croot, relative_path=str(store),
                  reason="V3 candidate unit store")
    return store


_DEFAULT_IDS = object()


def _read(croot, ids=_DEFAULT_IDS, **kwargs):
    """Call the adjudication read, passing ids through untouched, including None,
    which the module must refuse rather than read as "everything"."""

    kwargs.setdefault("store_id", "V3_UNIT_STORE")
    kwargs.setdefault("partition_id", PARTITION)
    kwargs.setdefault("purpose", "V3 adjudication packet build")
    kwargs.setdefault("prerequisites", _adj_prereqs())
    return PC.read_units_for_adjudication(
        DRAWN if ids is _DEFAULT_IDS else ids, root=croot, **kwargs)


# -- the new read works, and the old paths still refuse ----


def test_the_adjudication_read_serves_a_registered_holdout_partition(
        croot, adjudication):
    """A registered holdout partition can be read for adjudication."""

    rows = _read(croot)

    assert set(rows) == set(DRAWN)
    assert all(row["span_text"] for row in rows.values())
    assert stat.S_IMODE(adjudication.stat().st_mode) == PC.SEALED_MODE, (
        "the store must be resealed on the way out"
    )


def test_the_old_paths_still_refuse_exactly_as_they_did(croot, adjudication):
    """The adjudication read is a new narrow path, not a widening of the two
    existing strict ones."""

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.load_authorised_packets(adjudication, DRAWN, what="shard build",
                                   root=croot)
    assert "sealed store" in str(excinfo.value)

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_not_holdout(DRAWN, what="shard build", root=croot)
    assert "registered holdout units" in str(excinfo.value)

    # It still refuses after an adjudication read has happened.
    _read(croot)
    with pytest.raises(PC.CustodyViolation):
        PC.assert_not_holdout(DRAWN, what="shard build", root=croot)


# -- no code path can enumerate a population ----


def test_the_adjudication_read_has_no_population_enumerating_call_shape():
    """The signature itself rules out asking for a population: the only
    unit-selecting parameter is a positional identity list with no default, and
    everything else is keyword-only."""

    import inspect

    signature = inspect.signature(PC.read_units_for_adjudication)
    parameters = list(signature.parameters.values())

    assert parameters[0].name == "identity_list"
    assert parameters[0].default is inspect.Parameter.empty, (
        "a default identity list is a population-enumerating call shape"
    )
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for p in parameters[1:])
    forbidden = ("population", "all_units", "everything", "corpus", "units",
                 "partition_units", "identity_store")
    assert not [p.name for p in parameters if p.name in forbidden]


def test_the_adjudication_read_body_calls_no_enumeration_primitive():
    """The entry point may stream one file but calls nothing that enumerates."""

    import ast

    source = Path(PC.__file__).with_suffix(".py").read_text()
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "read_units_for_adjudication"
    )
    called = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                called.add(target.attr)
            elif isinstance(target, ast.Name):
                called.add(target.id)
    enumeration = {"iterdir", "listdir", "glob", "rglob", "scandir", "walk",
                   "read_text", "read_bytes", "readlines", "_read_lines",
                   "load_exposed_population"}
    assert not (called & enumeration), sorted(called & enumeration)


def test_the_adjudication_read_refuses_every_shape_that_means_everything(
        croot, adjudication):
    with pytest.raises(TypeError) as type_error:
        PC.read_units_for_adjudication(  # type: ignore[call-arg]
            store_id="V3_UNIT_STORE", partition_id=PARTITION,
            purpose="p", prerequisites=_adj_prereqs(), root=croot)
    assert "identity_list" in str(type_error.value)

    for bad, expected in (
        (None, "no call shape that means 'every unit'"),
        ([], "empty identity list is refused"),
        ("unit-000", "no call shape that means 'every unit'"),
        (DRAWN + [DRAWN[0]], "repeats an identifier"),
        ([""], "non-empty string"),
        # A store path where the identity list belongs would hand over everything.
        (Path("units.jsonl"), "materialised list, tuple or set"),
        ((unit for unit in DRAWN), "materialised list, tuple or set"),
    ):
        with pytest.raises(PC.CustodyViolation) as excinfo:
            _read(croot, bad)
        assert expected in str(excinfo.value), (bad, str(excinfo.value))


def test_the_adjudication_read_never_materialises_an_unnamed_record(
        croot, adjudication, monkeypatch):
    """Only the named records are deserialized; selection happens on the id."""

    seen: list[str] = []
    real_loads = json.loads

    def _recording(text, *args, **kwargs):
        seen.append(text if isinstance(text, str) else "")
        return real_loads(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", _recording)
    rows = _read(croot, DRAWN[:2])
    monkeypatch.undo()

    item_records = [text for text in seen if '"span_text"' in text]
    assert len(item_records) == 2, (
        "exactly the named records may be deserialized; the other four must "
        "never become objects"
    )
    for unit in UNITS[2:]:
        assert not any(unit in text for text in item_records)
        assert unit not in json.dumps(rows)


def test_the_adjudication_read_opens_the_unit_store_exactly_once(
        croot, adjudication):
    with watch_opens() as opened:
        _read(croot)
    assert opened.count(str(adjudication)) == 1, opened


# -- membership, and never two partitions at once ----


def test_an_identity_outside_the_partition_is_refused(croot, adjudication):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN + OUTSIDE[:1])
    message = str(excinfo.value)
    assert "are not members of partition" in message
    assert PARTITION in message
    assert OUTSIDE[0] not in message, (
        "a refusal that echoes the offending identity is its own disclosure "
        "channel out of the partition it just protected"
    )
    assert stat.S_IMODE(adjudication.stat().st_mode) == PC.SEALED_MODE


def test_one_call_cannot_reach_two_partitions(croot, adjudication):
    """Each partition is readable on its own, never both in one call."""

    assert set(_read(croot, DRAWN)) == set(DRAWN)
    assert set(_read(croot, OUTSIDE,
                     partition_id=OTHER_PARTITION)) == set(OUTSIDE)

    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN + OUTSIDE)
    assert "are not members of partition" in str(excinfo.value)

    for both in ([PARTITION, OTHER_PARTITION], (PARTITION, OTHER_PARTITION)):
        with pytest.raises(PC.CustodyViolation) as excinfo:
            _read(croot, DRAWN, partition_id=both)
        assert "exactly one partition" in str(excinfo.value)


def test_an_unregistered_partition_is_refused(croot, adjudication):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN, partition_id="V3_PARTITION_NOBODY_REGISTERED")
    assert "is not a registered holdout partition" in str(excinfo.value)


def test_an_unregistered_store_is_refused(croot, adjudication):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN, store_id="V3_STORE_NOBODY_REGISTERED")
    assert "unknown sealed store" in str(excinfo.value)


def test_a_blinded_partition_whose_salt_is_lost_refuses_the_read(
        croot, adjudication):
    (PC.custody_root(croot).root / PC.HOLDOUT_SALT_FILE).unlink()
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot)
    assert "membership cannot be tested" in str(excinfo.value)


def test_a_plaintext_partition_is_read_the_same_way(tmp_path, croot, store):
    """Membership is testable in either registration form."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units("V3_PLAINTEXT", DRAWN, corpus_id="V3",
                              root=croot)
    PC.seal_store("V3_UNIT_STORE", root=croot, relative_path=str(store),
                  reason="V3 candidate unit store")

    rows = _read(croot, DRAWN, partition_id="V3_PLAINTEXT")
    assert set(rows) == set(DRAWN)
    with pytest.raises(PC.CustodyViolation):
        _read(croot, DRAWN + OUTSIDE[:1], partition_id="V3_PLAINTEXT")


def test_a_directory_store_is_refused(tmp_path, croot):
    tree = tmp_path / "seat_delivery"
    tree.mkdir()
    (tree / "shard.jsonl").write_text(_row("unit-000") + "\n")
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3", blinded=True,
                              root=croot)
    PC.seal_store("V3_TREE", root=croot, relative_path=str(tree),
                  reason="delivery tree", kind="DIRECTORY")

    try:
        with pytest.raises(PC.CustodyViolation) as excinfo:
            _read(croot, DRAWN, store_id="V3_TREE")
        assert "is an opening by another name" in str(excinfo.value)
        assert stat.S_IMODE(tree.stat().st_mode) == PC.SEALED_DIR_MODE
    finally:
        # Restore the mode so the temp directory can be cleaned up.
        os.chmod(tree, 0o755)


# -- the one-shot lock belongs to the opening, and the read does not spend it


def test_the_adjudication_read_does_not_consume_the_one_shot_lock(
        croot, adjudication):
    """The lock guards the opening, not the adjudication read.

    Three parts: the lock starts free, reads leave it free, and an opening can
    still take it afterwards.
    """

    lock = PC.OneShotLock(PC.custody_root(croot), "V3_UNIT_STORE")
    assert not lock.is_taken()

    _read(croot)
    _read(croot, DRAWN[:1])
    assert not lock.is_taken(), (
        "an adjudication read must leave the opening lock unset, however many "
        "times it runs"
    )

    with PC.open_sealed_partition("V3_UNIT_STORE", purpose="scoring",
                                  prerequisites=_ok_prereqs(),
                                  root=croot) as path:
        assert path.exists()
    assert lock.is_taken(), (
        "the opening still consumes the lock: if this fails the lock has been "
        "weakened rather than left alone"
    )

    with pytest.raises(PC.CustodyViolation) as excinfo:
        with PC.open_sealed_partition("V3_UNIT_STORE", purpose="scoring again",
                                      prerequisites=_ok_prereqs(),
                                      root=croot):
            pass
    assert "already taken" in str(excinfo.value)


def test_the_adjudication_read_records_the_lock_it_did_not_take(
        croot, adjudication):
    _read(croot)
    record = PC.adjudication_read_records(store_id="V3_UNIT_STORE",
                                          root=croot)[0]
    assert record["detail"]["one_shot_lock_taken_before"] is False
    assert record["detail"]["one_shot_lock_consumed_by_this_read"] is False


# -- the log tells the two kinds of access apart ----


def test_the_log_distinguishes_an_adjudication_read_from_an_opening(
        croot, adjudication):
    _read(croot)
    with PC.open_sealed_partition("V3_UNIT_STORE", purpose="scoring",
                                  prerequisites=_ok_prereqs(), root=croot):
        pass

    events = [record["event"] for record
              in PC.FirstAccessLog(PC.custody_root(croot))
              .events_for("V3_UNIT_STORE")]
    assert PC.ADJUDICATION_READ_EVENT != PC.SEALED_OPENING_EVENT
    assert events == ["SEAL", "ADJUDICATION_READ", "ADJUDICATION_READ_CLOSE",
                      "OPEN", "CLOSE"]

    report = PC.AccessObserver(croot).report("V3_UNIT_STORE")
    assert report["has_been_read"] == "YES"
    assert report["has_been_read_for_adjudication"] == "YES"
    assert report["open_count"] == 1
    assert report["adjudication_read"]["count"] == 1


def test_an_adjudication_read_is_not_reported_as_an_opening(croot,
                                                            adjudication):
    _read(croot)
    report = PC.AccessObserver(croot).report("V3_UNIT_STORE")

    assert report["has_been_read"] == "NO", (
        "an adjudication read is not an opening and must not be counted as one"
    )
    assert report["open_count"] == 0
    assert report["has_been_read_for_adjudication"] == "YES", (
        "...and it must not be invisible either: a store whose units were read "
        "for adjudication has to say so"
    )
    assert report["adjudication_read"]["count"] == 1
    assert report["adjudication_read"]["unterminated"] is False
    assert report["adjudication_read"]["reads"][0]["partition_id"] == PARTITION
    assert "OPEN events only" in report["has_been_read_semantics"]


def test_the_observer_never_says_no_about_an_adjudication_read_over_a_broken_log(
        croot, adjudication):
    """A broken log makes the adjudication answer UNKNOWN, never NO."""

    _read(croot)
    log = PC.custody_root(croot).access_log
    lines = log.read_text().splitlines()
    record = json.loads(lines[-1])
    record["detail"]["purpose"] = "something else"
    lines[-1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    os.chmod(log, 0o600)
    log.write_text("\n".join(lines) + "\n")

    report = PC.AccessObserver(croot).report("V3_UNIT_STORE")
    assert report["has_been_read"] == "UNKNOWN_LOG_TAMPERED"
    assert report["has_been_read_for_adjudication"] == "UNKNOWN_LOG_TAMPERED"


def test_the_adjudication_record_carries_what_a8_requires_and_no_identity(
        croot, adjudication):
    _read(croot)
    detail = PC.adjudication_read_records(root=croot)[0]["detail"]

    assert detail["partition_id"] == PARTITION
    assert detail["identity_count"] == len(DRAWN)
    assert detail["purpose"] == "V3 adjudication packet build"
    assert detail["identity_sha256"] == PC.partition_identity_sha256(DRAWN)
    assert PC.adjudication_read_records(root=croot)[0]["utc"].endswith("Z")
    assert "unit_ids" not in detail

    raw = PC.custody_root(croot).access_log.read_text()
    for unit in UNITS:
        assert unit not in raw, (
            "an adjudication read must commit to its identity list, not "
            "republish it"
        )


def test_the_read_is_logged_while_the_store_is_still_sealed(croot,
                                                             adjudication):
    _read(croot)
    detail = PC.adjudication_read_records(root=croot)[0]["detail"]
    assert detail["store_was_sealed_when_logged"] is True
    assert detail["store_mode_at_log_time"] == f"0o{PC.SEALED_MODE:o}"


def test_a_broken_log_blocks_the_adjudication_read(croot, adjudication):
    log = PC.custody_root(croot).access_log
    os.chmod(log, 0o600)
    log.write_text(log.read_text() + '{"event":"OPEN"}\n')

    with watch_opens() as opened:
        with pytest.raises(PC.CustodyViolation) as excinfo:
            _read(croot)
    assert "does not verify" in str(excinfo.value)
    assert str(adjudication) not in opened
    assert stat.S_IMODE(adjudication.stat().st_mode) == PC.SEALED_MODE


# -- the adjudication's own prerequisite gate ----


def test_a_false_adjudication_prerequisite_refuses_and_is_recorded(
        croot, adjudication):
    prerequisites = dict(_adj_prereqs(), seat_roster_engaged=False)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN, prerequisites=prerequisites)
    assert "adjudication prerequisites not satisfied" in str(excinfo.value)
    assert "seat_roster_engaged" in str(excinfo.value)

    events = [record for record
              in PC.FirstAccessLog(PC.custody_root(croot))
              .events_for("V3_UNIT_STORE")
              if record["event"] == PC.ADJUDICATION_DENIED_EVENT]
    assert len(events) == 1
    assert events[0]["detail"]["reason"] == "prerequisites"
    assert events[0]["detail"]["failed"] == ["seat_roster_engaged"]
    assert not PC.OneShotLock(PC.custody_root(croot),
                              "V3_UNIT_STORE").is_taken()
    assert stat.S_IMODE(adjudication.stat().st_mode) == PC.SEALED_MODE


def test_declaring_no_adjudication_prerequisites_is_refused(croot,
                                                            adjudication):
    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN, prerequisites={})
    assert "no prerequisites were declared" in str(excinfo.value)


def test_a_refused_membership_is_recorded_before_it_is_raised(croot,
                                                              adjudication):
    with pytest.raises(PC.CustodyViolation):
        _read(croot, DRAWN + OUTSIDE[:1])
    denied = [record for record
              in PC.FirstAccessLog(PC.custody_root(croot))
              .events_for("V3_UNIT_STORE")
              if record["event"] == PC.ADJUDICATION_DENIED_EVENT]
    assert denied[0]["detail"]["reason"] == "partition_membership"
    assert not any(unit in json.dumps(denied) for unit in UNITS)


# -- pinning, partial authorisation, and the close record ----


def test_a_mismatched_identity_pin_is_refused_before_anything_is_touched(
        croot, adjudication):
    with watch_opens() as opened:
        with pytest.raises(PC.CustodyViolation) as excinfo:
            _read(croot, DRAWN, expect_identity_sha256="0" * 64)
    assert "does not match its pinned commitment" in str(excinfo.value)
    assert str(adjudication) not in opened
    assert PC.adjudication_read_records(root=croot) == []


def test_a_matching_identity_pin_is_accepted(croot, adjudication):
    rows = _read(croot, DRAWN,
                 expect_identity_sha256=PC.partition_identity_sha256(DRAWN))
    assert set(rows) == set(DRAWN)


def test_a_partial_authorisation_is_refused_and_the_store_is_resealed(
        tmp_path, croot, store):
    partial = tmp_path / "partial.jsonl"
    partial.write_text("\n".join(_row(unit) for unit in DRAWN[:2]) + "\n")
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3", blinded=True,
                              root=croot)
    PC.seal_store("V3_UNIT_STORE", root=croot, relative_path=str(partial),
                  reason="V3 candidate unit store")

    with pytest.raises(PC.CustodyViolation) as excinfo:
        _read(croot, DRAWN)
    assert "authorised identities are absent" in str(excinfo.value)
    assert not any(unit in str(excinfo.value) for unit in DRAWN)
    assert stat.S_IMODE(partial.stat().st_mode) == PC.SEALED_MODE

    close = [record for record
             in PC.FirstAccessLog(PC.custody_root(croot))
             .events_for("V3_UNIT_STORE")
             if record["event"] == PC.ADJUDICATION_READ_CLOSE_EVENT][0]
    assert close["detail"]["completed"] is False
    assert close["detail"]["records_returned"] == 2


def test_the_close_record_reports_a_completed_read(croot, adjudication):
    _read(croot)
    close = [record for record
             in PC.FirstAccessLog(PC.custody_root(croot))
             .events_for("V3_UNIT_STORE")
             if record["event"] == PC.ADJUDICATION_READ_CLOSE_EVENT][0]
    assert close["detail"]["completed"] is True
    assert close["detail"]["records_returned"] == len(DRAWN)
    assert close["detail"]["one_shot_lock_taken_after"] is False


# ---------------------------------------------------------------------------
# An exposure record may commit to its units instead of naming them, so that
# recording an exposure does not republish the membership the registration
# withheld.
# ---------------------------------------------------------------------------


def test_a_blinded_exposure_does_not_publish_the_exposed_identities(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3", blinded=True,
                              root=croot)

    record = PC.record_exposure("SHARDED", DRAWN, corpus_id="V3",
                                blinded=True, partition_id=PARTITION,
                                root=croot)

    detail = record["detail"]
    assert "unit_ids" not in detail
    assert detail["unit_count"] == len(DRAWN)
    assert len(detail["unit_id_commitments"]) == len(DRAWN)
    assert detail["commitment_scope"] == PARTITION
    raw = PC.custody_root(croot).access_log.read_text()
    for unit in DRAWN:
        assert unit not in raw
    assert PC.exposed_unit_ids(corpus_id="V3", root=croot)["SHARDED"] == set()
    assert PC.exposed_commitments(corpus_id="V3",
                                  root=croot)["SHARDED"][PARTITION]


def test_the_unblinded_exposure_still_names_its_units(croot):
    """The plaintext form does name its units, which is what makes the blinded
    form worth testing."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    record = PC.record_exposure("SHARDED", DRAWN, corpus_id="V3", root=croot)

    assert record["detail"]["unit_ids"] == sorted(DRAWN)
    raw = PC.custody_root(croot).access_log.read_text()
    assert all(unit in raw for unit in DRAWN)
    assert PC.exposed_unit_ids(corpus_id="V3", root=croot)["SHARDED"] == set(
        DRAWN)


def test_a_blinded_exposure_still_makes_a_unit_ineligible(croot):
    """Blinding an exposure must not turn it into silence: the unit is still
    ineligible afterwards."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3", blinded=True,
                              root=croot)
    PC.record_exposure("SHARDED", DRAWN, corpus_id="V3", blinded=True,
                       partition_id=PARTITION, root=croot)

    assert PC.assert_eligible_for_holdout(OUTSIDE, corpus_id="V3",
                                          root=croot)["eligible"]

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_eligible_for_holdout(DRAWN[:1], corpus_id="V3", root=croot)
    assert "already exposed" in str(excinfo.value)
    assert "SHARDED" in str(excinfo.value)
    assert DRAWN[0] not in str(excinfo.value)


def test_a_blinded_exposure_whose_salt_is_lost_refuses_rather_than_passes(
        croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    PC.record_exposure("JUDGED", DRAWN, corpus_id="V3", blinded=True,
                       partition_id=PARTITION, root=croot)
    (PC.custody_root(croot).root / PC.HOLDOUT_SALT_FILE).unlink()

    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.assert_eligible_for_holdout(OUTSIDE, corpus_id="V3", root=croot)
    assert "exposure cannot be tested" in str(excinfo.value)


def test_a_blinded_exposure_must_name_its_commitment_scope(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.record_exposure("SHARDED", DRAWN, corpus_id="V3", blinded=True,
                           root=croot)
    assert "must name the partition_id" in str(excinfo.value)


def test_a_partition_scoped_exposure_may_not_be_recorded_in_plaintext(croot):
    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    with pytest.raises(PC.CustodyViolation) as excinfo:
        PC.record_exposure("SHARDED", DRAWN, corpus_id="V3",
                           partition_id=PARTITION, root=croot)
    assert "recorded blinded or not at all" in str(excinfo.value)


def test_the_exposure_commitment_resolves_under_the_registration_salt(croot):
    """One salt per partition, so a registration and an exposure commit to the
    same identities and either can be checked against the other."""

    PC.declare_ledger_scope("V3", note="test corpus", root=croot)
    registration = PC.register_holdout_units(PARTITION, DRAWN, corpus_id="V3",
                                             blinded=True, root=croot)
    exposure = PC.record_exposure("READ", DRAWN, corpus_id="V3", blinded=True,
                                  partition_id=PARTITION, root=croot)
    assert (registration["detail"]["unit_id_commitments"]
            == exposure["detail"]["unit_id_commitments"])


# ---------------------------------------------------------------------------
# The seal registry is held at the ledger's mode on every write
# ---------------------------------------------------------------------------


def test_the_seal_registry_is_not_world_readable(croot, store):
    PC.seal_store("V3_UNIT_STORE", root=croot, relative_path=str(store),
                  reason="V3 candidate unit store")
    registry = PC.custody_root(croot).seal_registry
    mode = stat.S_IMODE(registry.stat().st_mode)
    assert mode == PC.LEDGER_MODE, f"{registry} is {mode:o}"
    assert not mode & 0o044, (
        "the registry names the path of every holdout-bearing store on the "
        "host"
    )


def test_the_seal_registry_mode_is_repaired_on_an_existing_file(tmp_path,
                                                                croot, store):
    """O_CREAT's mode is ignored once the file exists, so the writer re-applies
    it on every append."""

    second = tmp_path / "second.jsonl"
    second.write_text(_row("unit-100") + "\n")
    PC.seal_store("V3_UNIT_STORE", root=croot, relative_path=str(store),
                  reason="first store")
    registry = PC.custody_root(croot).seal_registry
    os.chmod(registry, 0o644)
    assert stat.S_IMODE(registry.stat().st_mode) == 0o644

    PC.seal_store("V3_SECOND_STORE", root=croot, relative_path=str(second),
                  reason="second store")

    assert stat.S_IMODE(registry.stat().st_mode) == PC.LEDGER_MODE
    assert len(PC._registry_entries(PC.custody_root(croot))) == 2, (
        "repairing the mode must not cost a registry entry"
    )


def test_the_append_writer_cannot_forget_the_mode():
    """The mode is a required keyword-only parameter, so no call site can inherit
    a permissive default."""

    import inspect

    mode = inspect.signature(PC._append_line).parameters["mode"]
    assert mode.default is inspect.Parameter.empty
    assert mode.kind is inspect.Parameter.KEYWORD_ONLY
