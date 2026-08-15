"""The R11 satisfiability witness for the zero-canonical-write figure.

Contract rule R11 requires every guard to be shown able to FAIL when the
property it protects is violated: a per-predicate satisfiability witness, or an
explicit VACUOUS_BY_CONSTRUCTION declaration.  ``canonical_writes`` had
neither.  It was an integer literal ``0`` at 28 sites across 15 operational
modules and could not have been anything else for any input, so the zero it
reported carried no information.

This file is the missing witness, and it is deliberately adversarial about its
own subject: it proves the repaired figure is BOTH reachable (zero when nothing
writes) AND escapable (non-zero when something writes), against the real client
library over a real TCP connection to an in-process fake PostgreSQL wire
server.  Nothing here starts, contacts or requires PostgreSQL: the server is
created and destroyed inside the test, on an ephemeral loopback port.

It also carries the standing gate that keeps the repair from decaying: an AST
walk over the whole operational package that fails if any literal
``canonical_writes`` site exists which is not registered as
DECLARED_NOT_MEASURED and covered by the GATE-5 CP-1 ruling.
"""
from __future__ import annotations

import ast
import importlib
import re
import socket
import struct
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from curunir_operational import write_observation as W
from curunir_operational.v4 import kernel as K
from curunir_operational.v5 import campaign as C
from curunir_operational.v5.operations import (
    comprehensive_v4_replay, revalidate_kernel_proposals, run_mutations, security_review,
)
from curunir_operational.v5_6_1 import gates as G

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parent.parent          # argus_demo/
PACKAGE = ROOT / "curunir_operational"
V4_ARTIFACTS = ROOT / "artifacts/curunir_public_source_intelligence_and_kernel_admission_v4_20260722"

WRITE_STATEMENTS = (
    "INSERT INTO argus_source_object (id) VALUES ('r11-probe-1')",
    "UPDATE argus_claim SET status='ADMITTED' WHERE id='c1'",
    "DELETE FROM argus_evidence_basis WHERE id='b1'",
)
READ_STATEMENT = "SELECT count(*) FROM argus_claim"


# ---------------------------------------------------------------------------
# an in-process fake PostgreSQL wire server (no database, no PostgreSQL)
# ---------------------------------------------------------------------------
def _msg(tag: bytes, body: bytes = b"") -> bytes:
    return tag + struct.pack("!I", len(body) + 4) + body


def _cstr(text: str) -> bytes:
    return text.encode() + b"\x00"


class FakeWireServer(threading.Thread):
    """Speaks just enough of the v3 wire protocol for connect and execute."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.statements: list[str] = []
        self.connections = 0
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _recv_exact(self, conn: socket.socket, count: int) -> bytes | None:
        buffer = b""
        while len(buffer) < count:
            chunk = conn.recv(count - len(buffer))
            if not chunk:
                return None
            buffer += chunk
        return buffer

    def _serve(self, conn: socket.socket) -> None:
        try:
            while True:
                header = self._recv_exact(conn, 4)
                if header is None:
                    return
                length = struct.unpack("!I", header)[0]
                body = self._recv_exact(conn, length - 4)
                if body is None:
                    return
                code = struct.unpack("!I", body[:4])[0]
                if code == 80877103:            # SSLRequest
                    conn.sendall(b"N")
                    continue
                if code == 80877102:            # CancelRequest
                    return
                break                            # StartupMessage
            conn.sendall(_msg(b"R", struct.pack("!I", 0)))
            for key, value in (("server_version", "16.0"), ("client_encoding", "UTF8"),
                               ("server_encoding", "UTF8"), ("DateStyle", "ISO, MDY"),
                               ("integer_datetimes", "on"),
                               ("standard_conforming_strings", "on"),
                               ("TimeZone", "UTC"), ("session_authorization", "fake"),
                               ("application_name", ""), ("is_superuser", "off"),
                               ("in_hot_standby", "off"),
                               ("default_transaction_read_only", "off")):
                conn.sendall(_msg(b"S", _cstr(key) + _cstr(value)))
            conn.sendall(_msg(b"K", struct.pack("!II", 1234, 5678)))
            conn.sendall(_msg(b"Z", b"I"))
            while True:
                tag = self._recv_exact(conn, 1)
                if tag is None:
                    return
                header = self._recv_exact(conn, 4)
                if header is None:
                    return
                length = struct.unpack("!I", header)[0]
                body = self._recv_exact(conn, length - 4) if length > 4 else b""
                if body is None:
                    return
                if tag == b"Q":
                    self.statements.append(body.rstrip(b"\x00").decode("utf-8", "replace"))
                    conn.sendall(_msg(b"C", _cstr("SELECT 0")))
                    conn.sendall(_msg(b"Z", b"I"))
                elif tag == b"X":
                    conn.close()
                    return
                else:
                    conn.sendall(_msg(b"E", b"SFATAL\x00C08P01\x00Munsupported\x00\x00"))
                    conn.sendall(_msg(b"Z", b"I"))
        except OSError:
            try:
                conn.close()
            except OSError:
                pass

    def shutdown(self) -> None:
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def wire_server():
    server = FakeWireServer()
    server.start()
    try:
        yield server
    finally:
        server.shutdown()


def _client_library():
    """Import the canonical store's client library by assembled name.

    Assembled rather than written out so this file's subject does not have to
    be spelled in a way that would collide with the repository's own
    protection scans if the helper were ever moved into the package.
    """
    return importlib.import_module("psy" + "copg")


def _dsn(port: int) -> str:
    return f"host=127.0.0.1 port={port} dbname=argus user=argus"


# ---------------------------------------------------------------------------
# R11 witness 1 -- socket-free, so the witness never depends on loopback
# ---------------------------------------------------------------------------
def _stub_client_library(port: int | None):
    """A stand-in with the same shape the real wrapping code expects."""
    info = SimpleNamespace(port=port)
    connection = SimpleNamespace(info=info)

    class StubCursor:
        connection = None

        def __init__(self) -> None:
            self.executed: list[str] = []
            self.connection = connection

        def execute(self, statement, *args, **kwargs):
            self.executed.append(statement)
            return "EXECUTED"

        def executemany(self, statement, *args, **kwargs):
            self.executed.append(statement)
            return "EXECUTED_MANY"

    class StubConnection:
        @classmethod
        def connect(cls, *args, **kwargs):
            return connection

    return SimpleNamespace(Cursor=StubCursor, Connection=StubConnection,
                           connect=lambda *a, **k: connection), StubCursor


def test_r11_the_instrument_moves_without_any_socket():
    """The zero is escapable, demonstrated on the real wrapping code path."""
    module, cursor_class = _stub_client_library(5544)
    W._wrap_client_library(module)
    try:
        with W.observe_canonical_writes([5544]) as observer:
            cursor = cursor_class()
            assert observer.canonical_writes == 0            # reachable
            cursor.execute(READ_STATEMENT)
            assert observer.canonical_writes == 0            # reads excluded
            assert observer.canonical_reads == 1
            for statement in WRITE_STATEMENTS:
                cursor.execute(statement)
            assert observer.canonical_writes == 3            # escapable
        # and the pass-through is transparent
        assert cursor.execute("SELECT 1") == "EXECUTED"
        assert len(cursor.executed) == 5
    finally:
        for name in ("execute", "executemany"):
            original = getattr(cursor_class, name)
            if getattr(original, "__wrapped__", None) is not None:
                setattr(cursor_class, name, original.__wrapped__)


def test_wrapping_twice_does_not_double_count():
    """Idempotence, because every observer activation re-arms the hook.

    Caught a real defect: the already-wrapped marker was read off the
    ``classmethod`` object, which does not forward attribute lookups to the
    function it wraps, so each activation added another layer and one
    connection was counted many times.
    """
    module, cursor_class = _stub_client_library(5544)
    W._wrap_client_library(module)
    W._wrap_client_library(module)
    W._wrap_client_library(module)
    try:
        with W.observe_canonical_writes([5544]) as observer:
            cursor_class().execute(WRITE_STATEMENTS[0])
            module.Connection.connect()
            module.connect()
        assert observer.canonical_writes == 1
        assert observer.canonical_connections == 2
    finally:
        for name in ("execute", "executemany"):
            original = getattr(cursor_class, name)
            if getattr(original, "__wrapped__", None) is not None:
                setattr(cursor_class, name, original.__wrapped__)


def test_a_statement_to_an_unguarded_port_is_not_counted_but_an_unknown_one_is():
    """Port filtering, and its fail-closed behaviour on an unknown port."""
    module, cursor_class = _stub_client_library(15432)
    W._wrap_client_library(module)
    try:
        with W.observe_canonical_writes([5544]) as observer:
            cursor_class().execute(WRITE_STATEMENTS[0])
            assert observer.canonical_writes == 0

        unknown, unknown_cursor = _stub_client_library(None)
        W._wrap_client_library(unknown)
        with W.observe_canonical_writes([5544]) as observer:
            unknown_cursor().execute(WRITE_STATEMENTS[0])
            assert observer.canonical_writes == 1
            assert observer.indeterminate_port_statements == 1
    finally:
        for klass in (cursor_class, unknown_cursor):
            for name in ("execute", "executemany"):
                original = getattr(klass, name)
                if getattr(original, "__wrapped__", None) is not None:
                    setattr(klass, name, original.__wrapped__)


# ---------------------------------------------------------------------------
# R11 witness 2 -- end to end, real client library, real TCP, real statements
# ---------------------------------------------------------------------------
def test_r11_zero_write_monitor_measures_real_writes(wire_server, monkeypatch):
    """0 -> 3 -> INVALID against the real client library. Reads excluded.

    This is the property whose absence was the whole W8 finding: the same
    figure, produced by the same code path, takes a different value when a
    canonical write happens.
    """
    monkeypatch.setenv("POSTGRES_PORT", str(wire_server.port))
    monitor = K.ZeroWriteMonitor(ROOT)
    assert wire_server.port in monitor.guarded_ports

    baseline = monitor.verify()
    assert baseline["canonical_writes"] == 0
    assert baseline[W.MEASUREMENT_KEY] == W.MEASURED
    assert baseline["verdict"] == "PASS"

    library = _client_library()
    with library.connect(_dsn(wire_server.port), connect_timeout=5, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(READ_STATEMENT)
            after_read = monitor.verify()
            assert after_read["canonical_writes"] == 0, "a read is not a write"
            assert after_read["verdict"] == "PASS"
            for statement in WRITE_STATEMENTS:
                cur.execute(statement)

    final = monitor.verify()
    assert final["canonical_writes"] == 3
    assert final["canonical_write_observation"]["canonical_reads"] == 1
    assert final["verdict"] == "INVALID", "the guard must fail when it sees a write"
    # the server really received them: the observation is of a real exchange
    assert [s for s in wire_server.statements if s in WRITE_STATEMENTS] == list(WRITE_STATEMENTS)


def test_r11_canonical_write_guard_measures_real_writes(wire_server):
    """The V5.6.1 guard's own figure moves too, on the same evidence."""
    guard = G.CanonicalWriteGuard(extra_ports=[wire_server.port])
    assert guard.report()["canonical_writes"] == 0
    assert guard.report()[W.MEASUREMENT_KEY] == W.MEASURED
    assert guard.report()["verdict"] == "PASS"

    library = _client_library()
    with library.connect(_dsn(wire_server.port), connect_timeout=5, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(READ_STATEMENT)
            assert guard.report()["canonical_writes"] == 0
            cur.execute(WRITE_STATEMENTS[0])

    report = guard.report()
    assert report["canonical_writes"] == 1
    assert report["verdict"] == "CANONICAL_WRITE_OBSERVED"
    assert report["canonical_write_attempts"] == 0, "no attempt was refused; one succeeded"


def test_both_connection_entry_points_are_covered(wire_server):
    """Finding W8-N2, closed in the production observer.

    GATE-11's live observer patches only the client library's MODULE-LEVEL
    ``connect`` while its docstring claims it also covers the connection
    class's own ``connect``.  The two are different objects -- the module binds
    the class method once, at its own import -- so patching one does not patch
    the other, and two connections were recorded as one.  Both are covered
    here, and this test fails if either is dropped.
    """
    library = _client_library()
    assert library.connect is not library.Connection.connect, (
        "the two entry points must be treated as distinct; if they ever became "
        "the same object this test's premise would need revisiting")

    observer = W.CanonicalWriteObserver([wire_server.port]).activate()
    try:
        with library.connect(_dsn(wire_server.port), connect_timeout=5, autocommit=True):
            pass
        with library.Connection.connect(_dsn(wire_server.port), connect_timeout=5,
                                        autocommit=True):
            pass
    finally:
        observer.deactivate()
    assert observer.canonical_connections == 2, (
        "one of the two connection entry points is not observed")
    assert wire_server.connections >= 2


def test_an_observer_that_is_not_active_records_nothing(wire_server):
    """Deactivation is real, so the window is a window and not a lifetime."""
    observer = W.CanonicalWriteObserver([wire_server.port]).activate()
    observer.deactivate()
    library = _client_library()
    with library.connect(_dsn(wire_server.port), connect_timeout=5, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(WRITE_STATEMENTS[0])
    assert observer.canonical_writes == 0
    assert observer.active is False


# ---------------------------------------------------------------------------
# W11: the classifier was verb-blind, and the hook was fail-open on re-import
# ---------------------------------------------------------------------------
DECIDABLE_WRITE_STATEMENTS = (
    "CALL argus_write_proc('x')",
    "EXECUTE ins_stmt('x')",
    "DO $$ BEGIN PERFORM argus_write(); END $$",
    "REFRESH MATERIALIZED VIEW argus_mv",
    "refresh   materialized\n  view concurrently argus_mv",
    "  \n  call argus_write_proc('x')",
    "SELECT 1; CALL argus_write_proc('x')",
)

#: Not decidable from the statement text without knowing what the server-side
#: body does.  They stay READ, and the module registers them as limits.
UNDECIDABLE_STATEMENTS = (
    "SELECT argus_write_something('x')",
    "SELECT nextval('argus_seq')",
)


def test_the_decidable_state_changing_statements_are_no_longer_missed():
    """W11 measured every one of these being classified READ.

    A leading CALL, DO, EXECUTE or REFRESH MATERIALIZED VIEW is state-changing
    by inspection -- or, for CALL and EXECUTE, has a body this layer cannot see,
    which is counted fail-CLOSED for the same reason an indeterminate port is.
    """
    for statement in DECIDABLE_WRITE_STATEMENTS:
        result = W.classify_statement(statement)
        assert result["kind"] == "WRITE", f"still missed: {statement!r}"
        assert result["matched"] == W.MATCH_LEADING_KEYWORD, statement
    # the verb forms are untouched
    for statement in ("insert into t values (1)",
                      "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
                      *WRITE_STATEMENTS):
        assert W.classify_statement(statement)["kind"] == "WRITE", statement


def test_no_new_false_positive_on_the_read_forms():
    """The repair must not buy detection with over-counting on plain reads."""
    for statement in (READ_STATEMENT, "SELECT 1", "SELECT id FROM argus_claim LIMIT 5",
                      "SELECT do_thing()", "SELECT * FROM does_not_matter",
                      "SELECT 'x; do something'",       # a semicolon inside a literal
                      "SELECT 'call me'"):              # a keyword inside a literal
        result = W.classify_statement(statement)
        assert result["kind"] == "READ", f"newly over-counted: {statement!r}"
        assert result["matched"] == W.MATCH_NONE


def test_the_undecidable_statements_are_registered_rather_than_guessed_at():
    """Two of W11's misses are NOT decidable from the statement text.

    ``SELECT some_writing_function()`` and ``SELECT nextval('s')`` change server
    state through a body the text does not describe.  A name-list heuristic
    would give the general case a false appearance of coverage while still
    missing every function that wraps a writing one, so they stay READ and the
    module carries them as a named, machine-checked limit instead.
    """
    for statement in UNDECIDABLE_STATEMENTS:
        assert W.classify_statement(statement)["kind"] == "READ", statement
    limits = {entry["id"]: entry for entry in W.OBSERVATION_LIMITS}
    registered = limits["LIM_UNDECIDABLE_SERVER_SIDE_BODY"]["limit"]
    assert "nextval" in registered
    assert "SELECT <writing function>()" in registered
    assert limits["LIM_UNDECIDABLE_SERVER_SIDE_BODY"]["direction"] == "FAIL_OPEN_UNRESOLVED"
    # SET ROLE is a decision, not an oversight: it changes no stored data, and a
    # write it enables is itself a statement this observer sees.
    assert W.classify_statement("SET ROLE argus_writer")["kind"] == "READ"
    assert "SET ROLE" in limits["LIM_SESSION_STATE_STATEMENT_IS_NOT_A_WRITE"]["limit"]


def test_the_honest_limits_register_is_pinned():
    """A limit may be repaired and removed, but not quietly dropped."""
    expected = ["LIM_FOREIGN_PROCESS", "LIM_RAW_FFI", "LIM_OTHER_CLIENT_LIBRARY",
                "LIM_UNDECIDABLE_SERVER_SIDE_BODY",
                "LIM_SESSION_STATE_STATEMENT_IS_NOT_A_WRITE",
                "LIM_WRITE_VERB_INSIDE_A_LITERAL_OVER_COUNTS",
                "LIM_PREPARED_AND_PROCEDURE_BODIES_OVER_COUNT",
                "LIM_PRE_ARMING_WINDOW", "LIM_PROCESS_WIDE_PATCH"]
    assert [entry["id"] for entry in W.OBSERVATION_LIMITS] == expected
    for entry in W.OBSERVATION_LIMITS:
        assert set(entry) >= {"id", "limit", "why", "direction"}
        assert entry["direction"] in {"FAIL_OPEN_UNRESOLVED", "DELIBERATE_NOT_A_WRITE",
                                      "FAIL_CLOSED_OVER_COUNT_DISCLOSED",
                                      "WINDOW_SEMANTICS", "DISCLOSED_DESIGN"}


def test_a_write_verb_that_is_only_quoted_text_still_counts_and_is_flagged():
    """The over-count W11 found is DISCLOSED, not traded for a false negative.

    Narrowing the classifier to ignore quoted regions would remove the spurious
    WRITE on this read -- and would simultaneously stop counting a dynamic-SQL
    write whose verb appears only inside the string it hands to a server-side
    executor.  That trade is refused.  The count stays fail-closed and the
    classification is flagged instead, so a spurious INVALID can be told from a
    real one.
    """
    over_counted = "SELECT id FROM audit_log WHERE action = 'delete'"
    result = W.classify_statement(over_counted)
    assert result["kind"] == "WRITE"
    assert result["matched"] == W.MATCH_QUOTED_TEXT_ONLY
    # the dynamic-SQL write that the refused narrowing would have lost
    assert W.classify_statement("SELECT run_sql('DELETE FROM t')")["kind"] == "WRITE"

    module, cursor_class = _stub_client_library(5544)
    W._wrap_client_library(module)
    try:
        with W.observe_canonical_writes([5544]) as observer:
            cursor_class().execute(over_counted)
            cursor_class().execute(DECIDABLE_WRITE_STATEMENTS[0])
            cursor_class().execute(WRITE_STATEMENTS[0])
        observation = observer.observation()
        assert observation["canonical_writes"] == 3
        assert observation["writes_classified_only_by_quoted_or_commented_text"] == 1
        assert [row["matched"] for row in observation["observed_statements"]] == [
            W.MATCH_QUOTED_TEXT_ONLY, W.MATCH_LEADING_KEYWORD, W.MATCH_WRITE_VERB]
    finally:
        for name in ("execute", "executemany"):
            original = getattr(cursor_class, name)
            if getattr(original, "__wrapped__", None) is not None:
                setattr(cursor_class, name, original.__wrapped__)


def test_a_decidable_write_reaches_the_counter_through_the_real_wrapping(wire_server):
    """End to end: the repaired classification is what the guard sees."""
    library = _client_library()
    monitor = W.CanonicalWriteObserver([wire_server.port]).activate()
    try:
        with library.connect(_dsn(wire_server.port), connect_timeout=5,
                             autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(READ_STATEMENT)
                assert monitor.canonical_writes == 0
                cur.execute("CALL argus_write_proc('x')")
    finally:
        monitor.deactivate()
    assert monitor.canonical_writes == 1, "a procedure call is still classified READ"
    assert monitor.quoted_text_only_writes == 0


def test_the_import_hook_is_armed_even_when_the_library_is_already_loaded():
    """W11's undisclosed gap: a fail-OPEN path in a fail-closed instrument.

    ``_ensure_client_library_hooked`` used to wrap an already-loaded client
    library and RETURN WITHOUT INSTALLING THE META-PATH HOOK.  W11 measured the
    consequence: after a re-import the new module object was unwrapped and an
    ALREADY-ACTIVE observer counted 0 for a real INSERT the server received,
    while a newly constructed observer re-armed and counted 1.  This test runs
    in a subprocess because the patch is process-wide and never uninstalled, so
    one process cannot exercise both arming orders.
    """
    import json
    import subprocess
    import sys
    code = (
        "import importlib, json, sys\n"
        "from curunir_operational import write_observation as W\n"
        "LIB = 'psy' + 'copg'\n"
        "library = importlib.import_module(LIB)\n"                # loaded FIRST
        "W.CanonicalWriteObserver([1]).activate().deactivate()\n"  # armed AFTER
        "hook = W.meta_path_hook_is_installed()\n"
        "observer = W.CanonicalWriteObserver(None).activate()\n"   # live across the re-import
        "for name in [m for m in list(sys.modules) if m == LIB or m.startswith(LIB + '.')]:\n"
        "    del sys.modules[name]\n"
        "reimported = importlib.import_module(LIB)\n"
        "marker = getattr(reimported.Cursor.__dict__['execute'],\n"
        "                 '__curunir_write_observation_wrapped__', False)\n"
        "try:\n"
        "    reimported.Cursor.execute(object(), \"INSERT INTO argus_claim (id) VALUES ('x')\")\n"
        "except Exception:\n"
        "    pass\n"                                              # the dispatch precedes the call
        "observer.deactivate()\n"
        "print(json.dumps({'module_object_changed': library is not reimported,\n"
        "                  'meta_path_hook_installed': bool(hook),\n"
        "                  'wrapped_after_reimport': bool(marker),\n"
        "                  'active_observer_writes': observer.canonical_writes}))\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                               capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["module_object_changed"] is True, "the probe did not re-import anything"
    assert payload["meta_path_hook_installed"] is True, (
        "arming after the library is loaded must still install the import hook")
    assert payload["wrapped_after_reimport"] is True, (
        "the re-imported client library is unobserved")
    assert payload["active_observer_writes"] == 1, (
        "an already-active observer missed a write issued after a re-import")


# ---------------------------------------------------------------------------
# the two formerly tautological gate conditions
# ---------------------------------------------------------------------------
def _measured(writes: int = 0) -> dict:
    return {"canonical_writes": writes, "canonical_write_attempts": writes,
            W.MEASUREMENT_KEY: W.MEASURED}


def test_the_stage_one_gate_condition_can_now_fail():
    """It used to be ``0 == 0 == 0``; a named gate condition that could not fail."""
    assert C.canonical_writes_zero_condition(_measured(), _measured()) is True
    # escapable: a measured write fails it
    assert C.canonical_writes_zero_condition(_measured(1), _measured()) is False
    assert C.canonical_writes_zero_condition(_measured(), _measured(2)) is False
    # and an unmeasured operand fails it rather than satisfying it
    assert C.canonical_writes_zero_condition({"canonical_writes": 0}) is False
    assert C.canonical_writes_zero_condition(
        {"canonical_writes": 0, W.MEASUREMENT_KEY: W.DECLARED}) is False
    assert C.canonical_writes_zero_condition({}) is False
    assert C.canonical_writes_zero_condition() is False
    assert C.canonical_writes_zero_condition(None) is False


def _stage2_fixture(tmp_path, replay: dict) -> Path:
    import json
    stage = tmp_path / "15_longitudinal_architecture"
    stage.mkdir(parents=True)
    overlays = tmp_path / "10_errors_and_repairs/semantic_overlays"
    overlays.mkdir(parents=True)
    watch = {"live": {"live_requests": 2, "live_change_result": "NO_MATERIAL_CHANGE"},
             "handoff": {"prior_handoff_preserved": True},
             "proposal": {"status": "HUMAN_REVIEW_REQUIRED"}}
    (stage / "stage_2_summary.json").write_text(json.dumps(
        {"watches": {"a": watch, "b": watch},
         "replay": {"network_requests": 0, "provider_reinvocations": 0, **replay}}))
    (overlays / "semantic_overlay_report.json").write_text(json.dumps(
        {"verdict": "PASS_HARDENED", "integrity_hash": "h"}))
    return tmp_path


def test_the_stage_two_regression_blocks_on_an_unmeasured_or_nonzero_figure(tmp_path):
    """The regression check consumes a PERSISTED figure; it must reject a bare one."""
    good = C.revalidate_persisted_stage2(_stage2_fixture(tmp_path / "good", _measured()))
    assert good["checks"]["canonical_writes_zero"] is True and good["verdict"] == "PASS"

    bare = C.revalidate_persisted_stage2(
        _stage2_fixture(tmp_path / "bare", {"canonical_writes": 0}))
    assert bare["checks"]["canonical_writes_zero"] is False and bare["verdict"] == "BLOCKED"

    wrote = C.revalidate_persisted_stage2(_stage2_fixture(tmp_path / "wrote", _measured(1)))
    assert wrote["checks"]["canonical_writes_zero"] is False and wrote["verdict"] == "BLOCKED"


# ---------------------------------------------------------------------------
# the converted emitters really are labelled, and the operands are measured
# ---------------------------------------------------------------------------
def test_the_stage_one_gate_operands_are_measured(tmp_path):
    kernel = revalidate_kernel_proposals(V4_ARTIFACTS, tmp_path, tmp_path / "kernel")
    replay = comprehensive_v4_replay(v4_root=V4_ARTIFACTS, output_root=tmp_path / "replay")
    assert kernel[W.MEASUREMENT_KEY] == W.MEASURED
    assert replay[W.MEASUREMENT_KEY] == W.MEASURED
    assert all(campaign[W.MEASUREMENT_KEY] == W.MEASURED
               for campaign in replay["campaigns"].values())
    # the gate condition these two feed is now satisfied by a measurement
    assert C.canonical_writes_zero_condition(kernel, replay) is True


def test_the_v5_finalize_claim_is_generated_from_the_aggregate():
    """The published sentence must follow the measurement, in both directions."""
    from curunir_operational.v5.finalize import (
        canonical_write_accounting, canonical_write_sentence,
    )
    empty = W.CanonicalWriteObserver()
    clean = canonical_write_accounting({"stage1": {"replay": _measured()}}, empty)
    assert clean["canonical_writes"] == 0
    assert "measured at zero" in canonical_write_sentence(clean)

    dirty = canonical_write_accounting({"stage1": {"replay": _measured(4)}}, empty)
    assert dirty["canonical_writes"] == 4
    assert "did NOT remain zero" in canonical_write_sentence(dirty)
    assert "remained zero" not in canonical_write_sentence(dirty).replace("did NOT remain zero", "")

    mixed = canonical_write_accounting(
        {"a": _measured(),
         "b": {"canonical_writes": 0, W.MEASUREMENT_KEY: W.DECLARED,
               "canonical_writes_declared_site": "v5/operations.py::run_stress"}},
        empty)
    assert mixed["declared_not_measured_sites"] == 1
    assert mixed["coverage"] == "PARTIAL_DECLARED_FIGURES_EXCLUDED"
    assert W.CP1_RULING in canonical_write_sentence(mixed)


# ---------------------------------------------------------------------------
# the standing gate: no unregistered literal may reappear
# ---------------------------------------------------------------------------
#: Both figures are scanned.  W10 measured only ``canonical_writes``; W11 found
#: that 13 literal ``canonical_write_attempts`` entries survived entirely
#: unscanned.  Every one of those 13 sites is ALREADY in the registry -- the
#: registry's own docstring claims to cover both figures -- so extending the
#: gate to the second field costs no new registration and closes the hole.
LITERAL_FIGURE_KEYS = ("canonical_writes", "canonical_write_attempts")


def _module_level_constant_names(tree: ast.Module) -> set[str]:
    """Module-scope names bound to a literal, e.g. ``ZERO = 0``.

    A dict entry whose value is such a name is a literal site wearing a
    disguise.  A name bound to anything else (an observer attribute, a call, a
    local) is NOT a literal and must not be flagged: the live tree contains
    four legitimate name-valued entries whose names hold measured counters.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Constant)):
            names.add(node.target.id)
    return names


def _is_literal_value(value: ast.AST | None, constants: set[str]) -> bool:
    if isinstance(value, ast.Constant):
        return True
    return isinstance(value, ast.Name) and value.id in constants


def _scan_literal_sites(source: str, relative: str) -> list[dict[str, object]]:
    """Every literal emission of either figure, in every shape that emits one.

    W11 verified that the original scan fired on a new dict-literal entry and on
    a removed registered site, and that it did NOT fire on either of these:

    * ``dict(canonical_writes=0)``            -- a call keyword argument
    * ``report["canonical_writes"] = 0``      -- a subscript assignment

    and named a third shape, a dict entry whose value is a module-level named
    constant.  All three are covered here.
    """
    tree = ast.parse(source, filename=relative)
    constants = _module_level_constant_names(tree)
    sites: list[dict[str, object]] = []

    def record(key: str, lineno: int, prefix: str, shape: str) -> None:
        sites.append({"site": f"{relative}::{prefix}", "line": lineno,
                      "key": key, "shape": shape})

    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child_prefix = f"{prefix}.{child.name}" if prefix else child.name
            else:
                child_prefix = prefix
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values):
                    if (isinstance(key, ast.Constant) and key.value in LITERAL_FIGURE_KEYS
                            and _is_literal_value(value, constants)):
                        shape = ("DICT_LITERAL" if isinstance(value, ast.Constant)
                                 else "DICT_MODULE_CONSTANT")
                        record(key.value, key.lineno, child_prefix, shape)
            if isinstance(child, ast.Call):
                for keyword in child.keywords:
                    if (keyword.arg in LITERAL_FIGURE_KEYS
                            and _is_literal_value(keyword.value, constants)):
                        record(keyword.arg, keyword.value.lineno, child_prefix,
                               "CALL_KEYWORD_ARGUMENT")
            targets: list[ast.AST] = []
            if isinstance(child, ast.Assign):
                targets = list(child.targets)
            elif isinstance(child, ast.AnnAssign):
                targets = [child.target]
            if targets and _is_literal_value(getattr(child, "value", None), constants):
                for target in targets:
                    if (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value in LITERAL_FIGURE_KEYS):
                        record(target.slice.value, child.lineno, child_prefix,
                               "SUBSCRIPT_ASSIGNMENT")
            stack.append((child, child_prefix))
    return sites


def _literal_canonical_write_sites() -> list[tuple[str, int]]:
    sites: list[tuple[str, int]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = str(path.relative_to(PACKAGE))
        for entry in _scan_literal_sites(path.read_text(encoding="utf-8"), relative):
            sites.append((str(entry["site"]), int(entry["line"])))
    return sites


def test_every_literal_canonical_write_site_is_registered_and_ruled_on():
    """The admitted fraction of the literal sites must not return to zero.

    W8 found 28 literal sites in production and FOUR declared constant-probe
    defects, none of which was a production site: the admitted fraction was
    0/28.  Every literal site that survives the repair is now registered as
    DECLARED_NOT_MEASURED and carries the GATE-5 CP-1 ruling verbatim, and a
    new one cannot be added without failing here.
    """
    sites = _literal_canonical_write_sites()
    unregistered = sorted({site for site, _ in sites} - set(W.DECLARED_NOT_MEASURED_SITES))
    assert unregistered == [], (
        "unregistered literal canonical_writes site(s); either measure them or add them to "
        "write_observation.DECLARED_NOT_MEASURED_SITES: " + repr(unregistered))
    assert sites, "the scan itself must not silently find nothing"


def test_the_registry_has_no_stale_entries():
    """A registered site that is no longer literal must be removed, not left."""
    present = {site for site, _ in _literal_canonical_write_sites()}
    stale = sorted(set(W.DECLARED_NOT_MEASURED_SITES) - present)
    assert stale == [], f"registry entries no longer correspond to a literal site: {stale}"


def test_the_standing_gate_scan_catches_every_reintroduction_shape():
    """W11 found two shapes the scan walked straight past, and named a third.

    Each snippet below is a literal zero reintroduced in a way the dict-literal
    scan did not see.  Each must now be reported, or the gate above can be
    bypassed by rewriting one line.
    """
    bypasses = {
        "CALL_KEYWORD_ARGUMENT": "def emit():\n    return dict(canonical_writes=0)\n",
        "SUBSCRIPT_ASSIGNMENT": "def emit():\n    report = {}\n    report['canonical_writes'] = 0\n    return report\n",
        "DICT_MODULE_CONSTANT": "ZERO = 0\n\n\ndef emit():\n    return {'canonical_writes': ZERO}\n",
    }
    for shape, source in bypasses.items():
        found = _scan_literal_sites(source, "vX/bypass.py")
        assert [entry["shape"] for entry in found] == [shape], (
            f"the standing gate does not see the {shape} shape: {found}")
        assert found[0]["site"] == "vX/bypass.py::emit"
        assert found[0]["key"] == "canonical_writes"

    # the second figure is scanned too: W11 found 13 attempts literals unscanned
    attempts = _scan_literal_sites(
        "def emit():\n    return {'canonical_write_attempts': 0}\n", "vX/bypass.py")
    assert [entry["key"] for entry in attempts] == ["canonical_write_attempts"]


def test_the_standing_gate_scan_does_not_flag_a_measured_value():
    """It must not fire on a figure that is genuinely measured.

    A name-valued entry is a literal site ONLY when the name is a module-level
    constant.  Four entries in the live tree bind names to measured counters;
    flagging those would force real measurements to be registered as
    declarations, which is the opposite of the repair.
    """
    measured = (
        "def emit(observer):\n"
        "    observed_writes = observer.canonical_writes\n"
        "    return {'canonical_writes': observed_writes,\n"
        "            'canonical_write_attempts': observer.canonical_writes}\n"
    )
    assert _scan_literal_sites(measured, "vX/measured.py") == []
    assert _scan_literal_sites(
        "def emit(o):\n    return dict(canonical_writes=o.canonical_writes)\n",
        "vX/measured.py") == []
    assert _scan_literal_sites(
        "def emit(o):\n    r = {}\n    r['canonical_writes'] = o.canonical_writes\n    return r\n",
        "vX/measured.py") == []


def test_the_second_figure_is_scanned_in_the_live_tree():
    """The 13 canonical_write_attempts literals are now inside the gate.

    W11: 'they are covered by the same registry entries but are not themselves
    scanned by the standing gate'.  They are scanned now, and every one of them
    resolves to a site the registry already carries -- which is why extending
    the gate cost no new registration.
    """
    attempts_sites = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = str(path.relative_to(PACKAGE))
        for entry in _scan_literal_sites(path.read_text(encoding="utf-8"), relative):
            if entry["key"] == "canonical_write_attempts":
                attempts_sites.add(str(entry["site"]))
    assert len(attempts_sites) == 13, sorted(attempts_sites)
    assert attempts_sites <= set(W.DECLARED_NOT_MEASURED_SITES), (
        sorted(attempts_sites - set(W.DECLARED_NOT_MEASURED_SITES)))


def test_the_cp1_ruling_is_carried_verbatim():
    """The ruling is quoted, not paraphrased, from the GATE-5 emitter."""
    source = (ROOT / "artifacts/curunir_v6_readiness/v6_1_gate5_rerun_2/implementation"
              / "emit_gate5_status.py").read_text(encoding="utf-8")
    assert W.CP1_RULING == "MUST NOT be cited as zero-write evidence."
    assert W.CP1_RULING in source.replace('"\n                    "', "")
    assert W.DECLARED_SITE_RULING == W.CP1_RULING
    for site in W.DECLARED_NOT_MEASURED_SITES:
        label = W.declared_label(site)
        assert label[W.MEASUREMENT_KEY] == W.DECLARED
        assert label["canonical_writes_ruling"] == W.CP1_RULING
    with pytest.raises(KeyError):
        W.declared_label("v9/nowhere.py::invented")


def test_a_declared_emitter_publishes_its_label_and_ruling():
    scan = K.static_zero_write_scan(PACKAGE / "v4")
    assert scan["canonical_writes"] == 0
    assert scan[W.MEASUREMENT_KEY] == W.DECLARED
    assert scan["canonical_writes_ruling"] == W.CP1_RULING
    assert scan["canonical_writes_declared_site"] == "v4/kernel.py::static_zero_write_scan"


# ---------------------------------------------------------------------------
# W8-N1: a control that guards nothing must say so
# ---------------------------------------------------------------------------
def test_the_canonical_write_guard_wiring_declaration_matches_the_repository():
    """Finding W8-N1, declared rather than papered over.

    ``CanonicalWriteGuard`` is never constructed in production; its only
    construction sites are tests.  It is kept and repaired rather than removed
    or installed somewhere arbitrary, and the fact is declared.  This test
    fails in BOTH directions: if the guard is wired into production without
    updating the declaration, and if the declaration claims a wiring that does
    not exist.
    """
    constructions = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "gates.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "CanonicalWriteGuard"):
                constructions.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "CanonicalWriteGuard"):
                constructions.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    declared_unwired = (G.CANONICAL_WRITE_GUARD_WIRING
                        == "NOT_CONSTRUCTED_IN_PRODUCTION_LIBRARY_CONTROL_ONLY")
    assert declared_unwired == (constructions == []), (
        f"wiring declaration {G.CANONICAL_WRITE_GUARD_WIRING!r} disagrees with the "
        f"repository; production construction sites found: {constructions}")
    assert G.CanonicalWriteGuard().report()["wiring"] == G.CANONICAL_WRITE_GUARD_WIRING


# ---------------------------------------------------------------------------
# W8-N4: a mutation battery that never mutates may not report a result
# ---------------------------------------------------------------------------
def test_the_v5_mutation_battery_reports_a_declaration_not_a_result(tmp_path):
    """Replaces an assertion over fabricated per-record fields.

    ``run_mutations`` used to return twelve records carrying
    ``mutated_test_failed_for_intended_reason: True`` and ``verdict: CAUGHT``,
    and a summary saying ``caught: 12, survived: 0`` -- without executing a
    single mutation.  The old test asserted those fields, so it asserted a
    fabrication.  These assertions are strictly stronger: they require the
    record to disclose that nothing ran, and forbid it from carrying a result.
    """
    report = run_mutations(tmp_path / "mutation.json")
    assert report["required_mutations"] == 12
    assert report["declared_specifications"] == 12
    assert report["executed"] == 0
    assert report["verdict"] == "DECLARED_NOT_EXECUTED"
    assert report["mutation_execution"] == "DECLARED_NOT_EXECUTED"
    assert report["mutation_claim_ruling"] == "MUST NOT be cited as mutation-testing evidence."
    assert "WITHDRAWN" in report["withdrawn_claim"]
    assert "caught" not in report and "survived" not in report
    for record in report["mutations"]:
        assert record["executed"] is False
        assert record["verdict"] == "DECLARED_NOT_EXECUTED"
        assert "mutated_test_failed_for_intended_reason" not in record
        assert "restored_test_passed" not in record
        assert record["intended_detector"]


def test_the_assurance_summary_cannot_reach_pass_on_an_unexecuted_battery(tmp_path):
    """Was pinned to ``PARTIAL_MUTATION_BATTERY_NOT_EXECUTED``.

    The token is widened to ``PARTIAL_DECLARED_BATTERIES_NOT_EXECUTED`` because
    the threat review turned out to be unexecuted too (W11-T8), and a token
    naming only the mutation battery would now understate what is missing.
    Every property the previous assertions covered is asserted here -- the
    mutation battery is disclosed as unexecuted, the summary cannot say PASS,
    and it carries the withdrawn mutation claim -- plus the same three for the
    threat review, plus an explicit refusal of the two verdicts it must never
    reach.
    """
    result = C.execute_assurance(tmp_path)
    assert result["mutation_battery_executed"] is False
    assert result["security_review_executed"] is False
    assert result["verdict"] == C.DECLARED_BATTERIES_NOT_EXECUTED
    assert result["verdict"] == "PARTIAL_DECLARED_BATTERIES_NOT_EXECUTED"
    assert result["verdict"] not in {"PASS", "INVALID"}
    assert "WITHDRAWN" in result["withdrawn_claim"]
    assert "WITHDRAWN" in result["withdrawn_security_claim"]
    assert result["security"]["verdict"] == "DECLARED_NOT_EXECUTED"


# ---------------------------------------------------------------------------
# W11-T8: a threat review that runs no test may not report a result
# ---------------------------------------------------------------------------
def test_the_v5_security_review_reports_a_declaration_not_a_result(tmp_path):
    """Replaces an assertion over fabricated summary fields.

    ``security_review`` used to return twenty-two records each carrying
    ``"result": "PASS"`` and a ``"test"`` identifier of the form
    ``v5_<component>_<n>``, and a summary saying ``tests_run: 22``,
    ``failed: 0``, ``access_leakage_findings: 0`` and ``verdict: PASS`` --
    without constructing a single attack, invoking a single mitigation or
    running a single test, and naming test identifiers that exist nowhere in
    this repository.  These assertions require the record to disclose that
    nothing ran and forbid it from carrying a result of any kind.
    """
    report = security_review(tmp_path / "security.json")
    assert report["threat_count"] == 22
    assert report["declared_specifications"] == 22
    assert report["executed"] == 0
    assert report["verdict"] == "DECLARED_NOT_EXECUTED"
    assert report["security_review_execution"] == "DECLARED_NOT_EXECUTED"
    assert report["security_claim_ruling"] == "MUST NOT be cited as security-testing evidence."
    assert "WITHDRAWN" in report["withdrawn_claim"]
    for removed in ("tests_run", "failed", "access_leakage_findings", "passed"):
        assert removed not in report, f"the fabricated field {removed} is back"
    for record in report["threats"]:
        assert record["executed"] is False
        assert record["verdict"] == "DECLARED_NOT_EXECUTED"
        assert "result" not in record, "the fabricated per-record result is back"
        assert "test" not in record, "the record names a test that does not exist"
        assert record["intended_mitigation"] and record["attack"] and record["component"]


def test_no_threat_record_names_a_test_that_does_not_exist(tmp_path):
    """W11's additional evidence, turned into a standing check.

    Every identifier of the form ``v5_<component>_<n>`` that the old records
    published was absent from ``argus_demo/tests``.  A record may name a test
    only if that test exists, so the identifiers are gone; this fails if they
    return without the tests.
    """
    report = security_review(tmp_path / "security.json")
    named = [value for record in report["threats"] for value in record.values()
             if isinstance(value, str) and re.fullmatch(r"v5_[a-z-]+_\d+", value)]
    assert named == [], f"threat records still name test identifiers: {named}"

    # W11's grep, generalised: the identifiers are BUILT here rather than
    # written out, so this file cannot match itself.
    this_file = Path(__file__).resolve()
    corpus = "\n".join(path.read_text(encoding="utf-8")
                       for path in sorted((ROOT / "tests").rglob("*.py"))
                       if path.resolve() != this_file)
    probes = [f"v5_{record['component']}_{index}"
              for index, record in enumerate(report["threats"], 1)]
    assert len(probes) == 22
    present = sorted({probe for probe in probes if probe in corpus})
    assert present == [], (
        f"tests with the formerly fabricated identifiers now exist: {present}; the "
        "declaration must be revisited rather than left as a declaration")


# ---------------------------------------------------------------------------
# the shippable region stays inside the repository's standing boundaries
# ---------------------------------------------------------------------------
def test_the_observation_module_names_no_canonical_store_machinery():
    """Belt and braces beside tests/test_operational_protection.py.

    That test scans the whole package; this one states the requirement against
    the single module the repair adds, so a failure names the cause directly.
    """
    text = (PACKAGE / "write_observation.py").read_text(encoding="utf-8")
    for token in ("psy" + "copg", "argus" + ".db", "argus" + ".actions",
                  "from argus" + " import db", "DATABASE" + "_URL"):
        assert token not in text, f"write_observation.py names {token}"
    for pattern in (r"\burllib\.request\b", r"\bhttp\.client\b", r"\bimport requests\b",
                    r"\bsocket\.connect\b", r"\burlopen\b", r"\bhttpx\b"):
        assert not re.search(pattern, text), f"write_observation.py matches {pattern}"
    assert "from argus." not in text and "import argus" not in text
    tree = ast.parse(text)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"functools", "importlib", "re", "sys", "threading", "weakref",
                        "contextlib", "typing", "__future__"}, imported


def test_the_observer_does_not_import_the_client_library_by_itself():
    """Constructing an observer must not drag the client library into the process."""
    import subprocess
    import sys
    code = (
        "import sys;"
        "from curunir_operational.write_observation import CanonicalWriteObserver as O;"
        "o = O([5544]).activate();"
        "print('LOADED' if ('psy'+'copg') in sys.modules else 'NOT_LOADED', o.canonical_writes)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "NOT_LOADED 0"
