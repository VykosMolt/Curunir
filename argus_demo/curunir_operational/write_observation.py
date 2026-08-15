"""Driver-layer observation of canonical write statements.

Why this module exists
----------------------
Until W10 the ``canonical_writes`` figure reported by this package's own
zero-write guards was an INTEGER LITERAL ``0``.  Nothing was observed, no
counter was read, and the figure could not have been non-zero for any input.
The counter beside it (``canonical_write_attempts``) was, and remains, real.

A socket-layer guard cannot see a write issued through the canonical store's
client library, because that library performs its connection and its statement
exchange inside a C shared object: the call never passes through
``socket.socket``.  That is a genuine and unfixable blindness AT THE SOCKET
LAYER.  It does not follow that the write is unobservable.  The client library
sits above the C layer and its cursor object is ordinary Python: every
statement this repository is capable of issuing passes through a cursor's
``execute``/``executemany``.  Wrapping the CLIENT LIBRARY rather than the
socket yields a genuine statement-level observation with

* no new dependency (standard-library :mod:`importlib` only),
* no live database and no connection of any kind opened by this module,
* no literal reference to the client library's name anywhere in this file --
  the module name is assembled at run time, the same idiom
  ``v4.kernel.canonical_database_ports`` already uses so that the standing
  repository-protection scan keeps passing on its intent rather than on a
  spelling.

This module NEVER imports the client library.  It registers an import hook so
that IF some other part of the process imports it, its cursor classes are
wrapped.  When nothing imports it -- the normal case for this package, which is
forbidden to touch the canonical store at all -- the observer reports a
MEASURED zero over an empty observation.

Honest boundary (carried forward from limitation LIM-30)
--------------------------------------------------------
The full, machine-readable list is :data:`OBSERVATION_LIMITS`, which
``tests/test_operational_zero_write_measurement.py`` pins so that a limit
cannot be quietly dropped.  In summary, this observation cannot see:

* a write issued by a foreign process this process did not spawn (partly
  covered by the existing subprocess refusal path),
* a write issued through a raw foreign-function call that bypasses the client
  library,
* a write issued by a client library other than the hooked one,
* a write performed INSIDE a server-side function or a prepared statement whose
  name says nothing about its body -- ``SELECT some_writing_function()`` and
  ``SELECT nextval('s')`` are not decidable from the statement text without
  server knowledge this layer does not have, so they are REGISTERED here rather
  than guessed at.

None of the middle two occurs anywhere in this repository.

Satisfiability (contract rule R11)
----------------------------------
The zero this module reports is DEMONSTRABLY ESCAPABLE: see
``tests/test_operational_zero_write_measurement.py``, which drives the same
instrument to a non-zero count against the real client library over an
in-process fake wire server, and to zero on a reads-only control.
"""
from __future__ import annotations

import functools
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import re
import sys
import threading
import weakref
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

#: Value of the ``canonical_writes_measurement`` key beside a figure that was
#: produced by the observation implemented here.
MEASURED = "MEASURED_DRIVER_LAYER_STATEMENT_OBSERVATION"

#: Value of the same key beside a figure that is a declaration, not a
#: measurement.  Every remaining literal site in this package carries it
#: through :data:`DECLARED_NOT_MEASURED_SITES`.
DECLARED = "DECLARED_NOT_MEASURED"

#: The GATE-5 CP-1 ruling, quoted verbatim from
#: ``artifacts/curunir_v6_readiness/v6_1_gate5_rerun_2/implementation/emit_gate5_status.py``.
#: CP-1 was raised against a campaign harness copy; W8 established that the
#: production sites had never been declared defective at all, so the same
#: ruling is extended verbatim to every one of them below.
CP1_RULING = "MUST NOT be cited as zero-write evidence."

#: Key used to label a reported ``canonical_writes`` figure.
MEASUREMENT_KEY = "canonical_writes_measurement"

_CLIENT_LIBRARY = "psy" + "copg"

_WRITE_VERB = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|"
    r"merge|upsert|replace|reindex|vacuum|comment|import|restore)\b",
    re.IGNORECASE,
)

#: Statement forms that are state-changing BY INSPECTION OF THEIR LEADING
#: KEYWORD even though their text carries none of the verbs above.  W11 measured
#: every one of these being classified READ by the verb scan alone.
#:
#: ``CALL`` runs a procedure and ``EXECUTE`` runs a prepared statement: neither
#: body is visible at this layer, so both are counted fail-CLOSED (a prepared
#: SELECT invoked by name is therefore over-counted, which is the correct
#: direction for an instrument whose zero is a safety claim).  ``DO`` runs an
#: anonymous code block.  ``REFRESH MATERIALIZED VIEW`` rewrites the view's
#: stored contents.
_LEADING_WRITE_STATEMENT = re.compile(
    r"^(?:call|do|execute|refresh\s+materialized\s+view)\b", re.IGNORECASE)

#: Opening or closing marker of a dollar-quoted string, e.g. ``$$`` or ``$tag$``.
_DOLLAR_QUOTE_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")

#: Why a statement was classified WRITE.  Published beside every observed
#: statement so that an INVALID verdict can be diagnosed without guessing.
MATCH_WRITE_VERB = "WRITE_VERB"
MATCH_LEADING_KEYWORD = "LEADING_STATEMENT_KEYWORD"
MATCH_QUOTED_TEXT_ONLY = "WRITE_VERB_ONLY_INSIDE_A_QUOTED_OR_COMMENTED_REGION"
MATCH_NONE = "NO_WRITE_INDICATION"

_LOCK = threading.RLock()
_OBSERVERS: "weakref.WeakSet[CanonicalWriteObserver]" = weakref.WeakSet()
_WRAPPED = "__curunir_write_observation_wrapped__"
_HOOK_INSTALLED = False


# ---------------------------------------------------------------------------
# the observer
# ---------------------------------------------------------------------------
class CanonicalWriteObserver:
    """Counts canonical write STATEMENTS actually issued by this process.

    ``guarded_ports`` restricts counting to the ports the canonical store can
    be reached on.  ``None`` means *count every statement the client library
    issues*, which is the fail-closed default used where the caller has no port
    set to hand.  A statement whose port cannot be determined is counted in
    either case, again fail-closed: an unidentifiable destination is treated as
    canonical rather than dismissed.
    """

    def __init__(self, guarded_ports: Iterable[int] | None = None, *,
                 label: str = "") -> None:
        self.guarded_ports = (None if guarded_ports is None
                              else frozenset(int(port) for port in guarded_ports))
        self.label = label
        self.canonical_writes = 0
        self.canonical_reads = 0
        self.canonical_connections = 0
        self.indeterminate_port_statements = 0
        self.quoted_text_only_writes = 0
        self.statements: list[dict[str, Any]] = []
        self._registered = False

    # -- lifecycle ---------------------------------------------------------
    def activate(self) -> "CanonicalWriteObserver":
        """Begin observing.  Idempotent."""
        if not self._registered:
            with _LOCK:
                _OBSERVERS.add(self)
                self._registered = True
            _ensure_client_library_hooked()
        return self

    def deactivate(self) -> "CanonicalWriteObserver":
        """Stop observing.  Idempotent.  Counts are retained."""
        if self._registered:
            with _LOCK:
                _OBSERVERS.discard(self)
                self._registered = False
        return self

    @property
    def active(self) -> bool:
        return self._registered

    def __enter__(self) -> "CanonicalWriteObserver":
        return self.activate()

    def __exit__(self, *exc: Any) -> None:
        self.deactivate()

    # -- observation -------------------------------------------------------
    def _counts(self, port: int | None) -> bool:
        if port is None:
            return True
        if self.guarded_ports is None:
            return True
        return port in self.guarded_ports

    def note_connection(self, port: int | None) -> None:
        if self._counts(port):
            self.canonical_connections += 1

    def note_statement(self, port: int | None, statement: Any) -> None:
        if not self._counts(port):
            return
        text = _statement_text(statement)
        if port is None:
            self.indeterminate_port_statements += 1
        classification = classify_statement(text)
        kind = classification["kind"]
        if kind == "WRITE":
            self.canonical_writes += 1
            if classification["matched"] == MATCH_QUOTED_TEXT_ONLY:
                self.quoted_text_only_writes += 1
        else:
            self.canonical_reads += 1
        if len(self.statements) < 200:
            self.statements.append({"port": port, "kind": kind,
                                    "matched": classification["matched"],
                                    "statement": text[:160]})

    # -- report ------------------------------------------------------------
    def observation(self) -> dict[str, Any]:
        """The measured observation, as an auditable record."""
        return {
            "observability": MEASURED,
            "guarded_ports": ("ALL_PORTS" if self.guarded_ports is None
                              else sorted(self.guarded_ports)),
            "active": self._registered,
            "client_library_hooked": _HOOK_INSTALLED,
            "canonical_connections": self.canonical_connections,
            "canonical_writes": self.canonical_writes,
            "canonical_reads": self.canonical_reads,
            "statements_with_indeterminate_port": self.indeterminate_port_statements,
            "writes_classified_only_by_quoted_or_commented_text":
                self.quoted_text_only_writes,
            "observed_statements": list(self.statements),
        }


def _statement_text(statement: Any) -> str:
    if isinstance(statement, (bytes, bytearray)):
        return statement.decode("utf-8", "replace")
    return str(statement)


def _mask_quoted_regions(text: str) -> str:
    """Blank the CONTENT of string literals, quoted identifiers and comments.

    Length and structure are preserved, so offsets still line up.  The masked
    text is used ONLY to ADD detection (a leading keyword that is genuinely a
    keyword rather than a word inside a literal) and to explain a
    classification.  The raw-text verb scan is never replaced by it, so a
    mis-masked exotic literal can make this function's diagnosis imprecise but
    can NEVER turn a counted statement into an uncounted one.
    """
    out = list(text)
    index, size = 0, len(text)
    while index < size:
        char = text[index]
        if char in ("'", '"'):
            cursor = index + 1
            while cursor < size:
                if text[cursor] == char:
                    if cursor + 1 < size and text[cursor + 1] == char:
                        out[cursor] = out[cursor + 1] = " "
                        cursor += 2
                        continue
                    break
                out[cursor] = " "
                cursor += 1
            index = cursor + 1
            continue
        if char == "$":
            opening = _DOLLAR_QUOTE_TAG.match(text, index)
            if opening is not None:
                tag = opening.group(0)
                closing = text.find(tag, opening.end())
                stop = size if closing == -1 else closing
                for position in range(opening.end(), stop):
                    out[position] = " "
                index = size if closing == -1 else closing + len(tag)
                continue
        if text.startswith("--", index):
            stop = text.find("\n", index)
            stop = size if stop == -1 else stop
            for position in range(index, stop):
                out[position] = " "
            index = stop
            continue
        if text.startswith("/*", index):
            stop = text.find("*/", index + 2)
            stop = size if stop == -1 else stop + 2
            for position in range(index, stop):
                out[position] = " "
            index = stop
            continue
        index += 1
    return "".join(out)


def _has_leading_write_keyword(masked: str) -> bool:
    """True if any top-level statement in the text opens with a write keyword.

    The split is on semicolons OUTSIDE quoted and commented regions, so a
    semicolon inside a literal or inside a ``DO $$ ... $$`` body cannot invent a
    segment.  Multi-statement strings are covered because the simple query
    protocol allows them.
    """
    return any(_LEADING_WRITE_STATEMENT.match(segment.strip())
               for segment in masked.split(";"))


def classify_statement(statement: Any) -> dict[str, str]:
    """Classify one statement as WRITE or READ, and say WHY.

    Three independent write indications, in the order they are reported:

    ``WRITE_VERB``
        a write verb outside every quoted and commented region.
    ``LEADING_STATEMENT_KEYWORD``
        the statement (or one of the statements in a multi-statement string)
        opens with ``CALL``, ``DO``, ``EXECUTE`` or
        ``REFRESH MATERIALIZED VIEW``.
    ``WRITE_VERB_ONLY_INSIDE_A_QUOTED_OR_COMMENTED_REGION``
        the only write verb is inside a string literal, a quoted identifier or a
        comment.  Such a statement is STILL counted as a write, fail-closed --
        it may be dynamic SQL handed to a server-side executor -- but the
        classification is flagged, because this is the shape that turns a
        legitimate PASS into a spurious INVALID (W11 measured it on
        ``SELECT id FROM audit_log WHERE action = 'delete'``).
    """
    text = _statement_text(statement)
    masked = _mask_quoted_regions(text)
    if _WRITE_VERB.search(masked):
        return {"kind": "WRITE", "matched": MATCH_WRITE_VERB}
    if _has_leading_write_keyword(masked):
        return {"kind": "WRITE", "matched": MATCH_LEADING_KEYWORD}
    if _WRITE_VERB.search(text):
        return {"kind": "WRITE", "matched": MATCH_QUOTED_TEXT_ONLY}
    return {"kind": "READ", "matched": MATCH_NONE}


@contextmanager
def observe_canonical_writes(guarded_ports: Iterable[int] | None = None, *,
                             label: str = "") -> Iterator[CanonicalWriteObserver]:
    """Observe canonical write statements for the duration of a block."""
    observer = CanonicalWriteObserver(guarded_ports, label=label).activate()
    try:
        yield observer
    finally:
        observer.deactivate()


def measured(observer: CanonicalWriteObserver) -> dict[str, Any]:
    """The two keys every measured emitter in this package publishes."""
    return {"canonical_writes": observer.canonical_writes,
            MEASUREMENT_KEY: MEASURED}


def measured_figures(observer: CanonicalWriteObserver, *,
                     refused: int = 0) -> dict[str, Any]:
    """Both canonical-write figures, measured, for an emitter that refuses none.

    ``canonical_write_attempts`` is the number of canonical write statements
    that were attempted: those the observer saw reach the client library, plus
    those a guard refused before they could.  In a window with no guard the
    second term is zero, so the two figures coincide -- which is a fact about
    the window, not a restatement of a constant.
    """
    return {"canonical_write_attempts": observer.canonical_writes + int(refused),
            "canonical_writes": observer.canonical_writes,
            MEASUREMENT_KEY: MEASURED}


def active_observer_count() -> int:
    """Number of live, registered observers (diagnostic; used by tests)."""
    with _LOCK:
        return len(_OBSERVERS)


# ---------------------------------------------------------------------------
# dispatch and client-library hooking
# ---------------------------------------------------------------------------
def _dispatch_statement(port: int | None, statement: Any) -> None:
    with _LOCK:
        targets = list(_OBSERVERS)
    for observer in targets:
        observer.note_statement(port, statement)


def _dispatch_connection(port: int | None) -> None:
    with _LOCK:
        targets = list(_OBSERVERS)
    for observer in targets:
        observer.note_connection(port)


def _port_of_connection(connection: Any) -> int | None:
    try:
        return int(connection.info.port)
    except Exception:
        return None


def _port_of_cursor(cursor: Any) -> int | None:
    try:
        return _port_of_connection(cursor.connection)
    except Exception:
        return None


def _wrap_statement_method(owner: type, name: str) -> None:
    original = owner.__dict__.get(name)
    if original is None or getattr(original, _WRAPPED, False):
        return
    if not callable(original):
        return

    @functools.wraps(original)
    def wrapper(self: Any, statement: Any = None, *args: Any, **kwargs: Any) -> Any:
        _dispatch_statement(_port_of_cursor(self), statement)
        return original(self, statement, *args, **kwargs)

    setattr(wrapper, _WRAPPED, True)
    setattr(owner, name, wrapper)


def _wrap_module_connect(module: Any) -> None:
    original = getattr(module, "connect", None)
    if original is None or getattr(original, _WRAPPED, False):
        return

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        connection = original(*args, **kwargs)
        _dispatch_connection(_port_of_connection(connection))
        return connection

    setattr(wrapper, _WRAPPED, True)
    module.connect = wrapper


def _wrap_class_connect(owner: type) -> None:
    """Wrap the connection class's own constructor-style entry point.

    The module-level attribute and the class method are DIFFERENT objects: the
    module captures a bound method once, at its own import, so patching one
    does not patch the other.  Coverage of only one of them was finding W8-N2
    against the GATE-11 live observer; both are covered here.
    """
    raw = owner.__dict__.get("connect")
    if raw is None:
        return
    is_classmethod = isinstance(raw, classmethod)
    function = raw.__func__ if is_classmethod else raw
    # A classmethod object does not forward attribute lookups to the function
    # it wraps, so the already-wrapped marker has to be read off the function.
    if getattr(raw, _WRAPPED, False) or getattr(function, _WRAPPED, False):
        return
    if not callable(function):
        return

    @functools.wraps(function)
    def wrapper(cls: Any, *args: Any, **kwargs: Any) -> Any:
        connection = function(cls, *args, **kwargs)
        _dispatch_connection(_port_of_connection(connection))
        return connection

    setattr(wrapper, _WRAPPED, True)
    setattr(owner, "connect", classmethod(wrapper) if is_classmethod else wrapper)


#: Cursor classes whose statement entry points are wrapped when present.  Only
#: methods defined on the class itself are wrapped, so an inherited method is
#: never wrapped twice.
_CURSOR_CLASS_NAMES = ("Cursor", "ClientCursor", "ServerCursor", "RawCursor",
                       "AsyncCursor", "AsyncClientCursor", "AsyncServerCursor",
                       "AsyncRawCursor")
_STATEMENT_METHODS = ("execute", "executemany", "copy", "stream")
_CONNECTION_CLASS_NAMES = ("Connection", "AsyncConnection")


def _wrap_client_library(module: Any) -> None:
    """Wrap every statement and connection entry point the library exposes."""
    _wrap_module_connect(module)
    for name in _CONNECTION_CLASS_NAMES:
        owner = getattr(module, name, None)
        if isinstance(owner, type):
            _wrap_class_connect(owner)
    for name in _CURSOR_CLASS_NAMES:
        owner = getattr(module, name, None)
        if not isinstance(owner, type):
            continue
        for method in _STATEMENT_METHODS:
            _wrap_statement_method(owner, method)


class _ClientLibraryImportHook(importlib.abc.MetaPathFinder):
    """Defers to the real finder, then wraps the executed module.

    The ORIGINAL spec object is returned with only its loader wrapped, so
    package semantics (submodule search locations, origin, cached) are
    preserved exactly.
    """

    def find_spec(self, fullname: str, path: Any = None,
                  target: Any = None) -> importlib.machinery.ModuleSpec | None:
        if fullname != _CLIENT_LIBRARY:
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:  # pragma: no cover - concurrent removal
            return None
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            return None
        inner = spec.loader
        if getattr(inner, _WRAPPED, False):
            # importlib.reload() consults the finders with the module still in
            # sys.modules, so find_spec can hand back a spec whose loader this
            # hook has already wrapped.  Wrapping it again would nest a new
            # layer on every reload.
            return spec

        class _Loader:
            def __getattr__(self, name: str) -> Any:
                return getattr(inner, name)

            def create_module(self, spec: Any) -> Any:
                return inner.create_module(spec)

            def exec_module(self, module: Any) -> None:
                inner.exec_module(module)
                _wrap_client_library(module)

        # Set on the CLASS so the marker is found by normal attribute lookup
        # and is never forwarded to ``inner`` by __getattr__.
        setattr(_Loader, _WRAPPED, True)
        spec.loader = _Loader()
        return spec


def _ensure_client_library_hooked() -> None:
    """Arm the import hook AND wrap the client library if it is already loaded.

    Both, always, and in that order.  Until W12 this function returned early
    when the library was already in ``sys.modules``: it wrapped the loaded
    module and never installed the meta-path hook, so a later re-import of the
    library produced a FRESH, UNWRAPPED module object and every already-active
    observer silently counted zero for real writes issued through it (W11
    measured exactly that: an active observer reported 0 for an INSERT the
    server received).  That was a fail-OPEN path in an instrument whose entire
    design principle is fail-closed.

    Installing the finder unconditionally is safe: it declines every name but
    the client library, it defers to the real finder for the spec, and it
    refuses to wrap a loader it has already wrapped.
    """
    global _HOOK_INSTALLED
    with _LOCK:
        if not any(isinstance(finder, _ClientLibraryImportHook)
                   for finder in sys.meta_path):
            sys.meta_path.insert(0, _ClientLibraryImportHook())
        module = sys.modules.get(_CLIENT_LIBRARY)
        if module is not None:
            _wrap_client_library(module)
        _HOOK_INSTALLED = True


def client_library_is_hooked() -> bool:
    """True once the observation hook has been armed in this process."""
    return _HOOK_INSTALLED


def meta_path_hook_is_installed() -> bool:
    """True if the import hook is on ``sys.meta_path`` RIGHT NOW.

    Distinct from :func:`client_library_is_hooked`, which only records that
    arming was attempted.  The difference is what made the W11 re-import gap
    invisible from inside the instrument.
    """
    return any(isinstance(finder, _ClientLibraryImportHook)
               for finder in sys.meta_path)


# ---------------------------------------------------------------------------
# what this instrument CANNOT see, stated as data rather than as prose
# ---------------------------------------------------------------------------
#: The honest-limits register.  Every entry names a class of write, or of
#: mis-classification, that this observation does not resolve, together with
#: the reason it is not resolved here.  ``tests/...`` pins the register, so a
#: limit cannot be dropped without a test failing, and a repaired limit must be
#: removed deliberately.
#:
#: ``LIM_UNDECIDABLE_SERVER_SIDE_BODY`` is the entry the W11 verification
#: demanded: two of the statements it measured being classified READ are NOT
#: decidable from the statement text, and this module refuses to guess at them.
OBSERVATION_LIMITS: tuple[dict[str, str], ...] = (
    {"id": "LIM_FOREIGN_PROCESS",
     "limit": "A write issued by a process this one did not spawn is invisible.",
     "why": "The observation is in-process, at the client library. Partly covered "
            "by the existing subprocess refusal path.",
     "direction": "FAIL_OPEN_UNRESOLVED"},
    {"id": "LIM_RAW_FFI",
     "limit": "A write issued through a raw foreign-function call that bypasses "
              "the client library is invisible.",
     "why": "Nothing in this repository issues one.",
     "direction": "FAIL_OPEN_UNRESOLVED"},
    {"id": "LIM_OTHER_CLIENT_LIBRARY",
     "limit": "A write issued through a client library other than the hooked one "
              "is invisible.",
     "why": "Nothing in this repository imports another one.",
     "direction": "FAIL_OPEN_UNRESOLVED"},
    {"id": "LIM_UNDECIDABLE_SERVER_SIDE_BODY",
     "limit": "A statement whose write happens inside a server-side body the "
              "statement text does not describe is classified READ. The two "
              "measured instances are SELECT <writing function>() -- for example "
              "SELECT argus_write_something('x') -- and SELECT nextval('seq'), "
              "which advances a sequence.",
     "why": "Deciding either one requires knowing what the named function does "
            "on the server. This layer has no such knowledge, and a name-list "
            "heuristic would give the general case a false appearance of "
            "coverage while still missing every function that wraps a writing "
            "one. The honest state is a registered limit, not a guess.",
     "direction": "FAIL_OPEN_UNRESOLVED",
     "would_be_resolved_by": "server-side observation (statement logging, an "
                             "event trigger, or a read-only role), none of which "
                             "is available to an in-process driver-layer hook"},
    {"id": "LIM_SESSION_STATE_STATEMENT_IS_NOT_A_WRITE",
     "limit": "SET ROLE, and session-state statements generally, are classified "
              "READ. W11 lists SET ROLE among the statements classified READ.",
     "why": "A session-state statement changes no stored data, so counting it as "
            "a canonical write would be a false positive rather than a repair. "
            "It can only ENABLE a later write -- and that later write is itself "
            "a statement this observer sees. Recorded as a decision, not left "
            "silent.",
     "direction": "DELIBERATE_NOT_A_WRITE"},
    {"id": "LIM_WRITE_VERB_INSIDE_A_LITERAL_OVER_COUNTS",
     "limit": "A read whose text contains a write verb only inside a string "
              "literal, a quoted identifier or a comment is still counted as a "
              "WRITE -- for example SELECT id FROM audit_log WHERE action = "
              "'delete'.",
     "why": "Narrowing the classifier to ignore quoted regions would convert "
            "every dynamic-SQL write whose verb appears only in a literal (for "
            "example a server-side executor called with a DELETE string) from "
            "counted into missed. That trades a false positive for a false "
            "negative in a fail-closed instrument, so the count is left alone "
            "and the classification is instead FLAGGED: such statements are "
            "reported with matched = "
            "WRITE_VERB_ONLY_INSIDE_A_QUOTED_OR_COMMENTED_REGION and counted in "
            "writes_classified_only_by_quoted_or_commented_text, so a spurious "
            "INVALID can be told from a real one.",
     "direction": "FAIL_CLOSED_OVER_COUNT_DISCLOSED"},
    {"id": "LIM_PREPARED_AND_PROCEDURE_BODIES_OVER_COUNT",
     "limit": "EXECUTE <prepared> and CALL <procedure> are counted as writes even "
              "when the prepared statement or procedure only reads.",
     "why": "The body is not visible at this layer. Fail-closed is the correct "
            "direction for a zero that is a safety claim.",
     "direction": "FAIL_CLOSED_OVER_COUNT_DISCLOSED"},
    {"id": "LIM_PRE_ARMING_WINDOW",
     "limit": "A write issued after an observer is CONSTRUCTED but before it is "
              "ACTIVATED is not counted.",
     "why": "__init__ deliberately does not register; activate() does, so the "
            "observation is a window and not an object lifetime. Measured by W11.",
     "direction": "WINDOW_SEMANTICS"},
    {"id": "LIM_PROCESS_WIDE_PATCH",
     "limit": "The client-library wrapping is process-wide and is never removed, "
              "and every live observer sees every statement, so a monitor held "
              "open across unrelated database work counts that work.",
     "why": "Wrapping is idempotent and dispatches only to live registered "
            "observers; every construction site in this package is function-local.",
     "direction": "DISCLOSED_DESIGN"},
)


# ---------------------------------------------------------------------------
# the declared (unconverted) sites
# ---------------------------------------------------------------------------
#: Every remaining site in this package that emits ``canonical_writes`` (or
#: ``canonical_write_attempts``) as an integer literal rather than as a
#: measurement.  W8 established that the campaign had declared four
#: constant-probe defects and that NOT ONE of them was a production site, so
#: the admitted fraction of the production sites was zero.  This registry is
#: that admission, and ``tests/test_operational_zero_write_measurement.py``
#: fails if a literal site exists that is not listed here -- so the fraction
#: cannot silently return to zero.
#:
#: Keys are ``<path relative to curunir_operational>::<enclosing qualname>``.
DECLARED_NOT_MEASURED_SITES: dict[str, str] = {
    "v3/scenario.py::ScenarioRunner.run": (
        "Synthetic multi-node scenario record. The scenario opens no client "
        "session at all; the field is an assertion about the scenario's design, "
        "not an observation."),
    "v4/kernel.py::shadow_dry_run": (
        "KernelShadowDiff construction. models.py forbids a truthy value on this "
        "dataclass, so the literal makes the invariant unfireable from production: "
        "a proof about the constructor, not about the canonical store."),
    "v4/kernel.py::static_zero_write_scan": (
        "A STATIC import scan. It executes nothing and therefore cannot observe a "
        "write; the figure is a restatement of the scan's own scope."),
    "v4/mission.py::receive_review_packet": (
        "Inter-node packet receipt record. No client session is opened on this path."),
    "v4/mutation.py::run_mutation_suite": (
        "Mutation battery summary. The battery's CATCH is genuinely measured "
        "(attacked.attempts), but this summary field is a literal."),
    "v5/adjudication.py::propagate_repairs": (
        "Correction propagation summary; file I/O only."),
    "v5/campaign.py::execute_stage1": (
        "Per-surface repair-and-regression record written inside the Stage I loop; "
        "file I/O only. The Stage I GATE CONDITION that consumes canonical write "
        "figures is separately measured -- see canonical_writes_zero_condition."),
    "v5/campaign.py::_execute_stage2": (
        "Per-watch proposal record. The Stage II longitudinal REPLAY record in the "
        "same function is measured; this per-watch field is not."),
    "v5/epistemic_repairs.py::build_semantic_overlays": (
        "Semantic overlay report; file I/O only."),
    "v5/longitudinal.py::execute_live_watch": (
        "Live recapture run record. The watch issues public HTTP recaptures through "
        "a separate capture layer and opens no client session."),
    "v5/longitudinal.py::controlled_mechanics_scenario": (
        "Controlled synthetic scenario report; file I/O only."),
    "v5/operations.py::run_stress": (
        "Bounded synthetic stress report; file I/O only."),
    "v5/operations.py::run_mutations": (
        "Declared-only mutation battery: no mutation is executed (finding W8-N4). "
        "The whole record, not merely its canonical-write figure, is a declaration."),
    "v5_1/kernel_regression.py::revalidate_prior_proposals": (
        "Persisted-proposal revalidation report; file I/O only."),
    "v5_1/kernel_regression.py::regression_report": (
        "The revalidation half of the zero-write attestation. The SHADOW half of "
        "the same attestation comes from ZeroWriteMonitor.verify and IS measured, "
        "and the fail-closed raise beside it keys on the measured verdict."),
}

#: The ruling extended verbatim to every entry above.
DECLARED_SITE_RULING = CP1_RULING


def declared_site_ruling(site: str) -> dict[str, str]:
    """The full registry entry for one unconverted literal site."""
    if site not in DECLARED_NOT_MEASURED_SITES:
        raise KeyError(f"unregistered literal canonical-write site: {site}")
    return {"site": site, MEASUREMENT_KEY: DECLARED,
            "reason": DECLARED_NOT_MEASURED_SITES[site],
            "ruling": DECLARED_SITE_RULING}


def declared_label(site: str) -> dict[str, str]:
    """The keys an emitter publishes beside a DECLARED (unmeasured) figure.

    Deliberately narrow and prefixed so it can be merged into any report
    without colliding with that report's own vocabulary.
    """
    if site not in DECLARED_NOT_MEASURED_SITES:
        raise KeyError(f"unregistered literal canonical-write site: {site}")
    return {MEASUREMENT_KEY: DECLARED,
            "canonical_writes_declared_site": site,
            "canonical_writes_ruling": DECLARED_SITE_RULING}


__all__ = [
    "MEASURED", "DECLARED", "MEASUREMENT_KEY", "CP1_RULING",
    "DECLARED_NOT_MEASURED_SITES", "DECLARED_SITE_RULING", "declared_site_ruling",
    "declared_label",
    "CanonicalWriteObserver", "observe_canonical_writes", "measured", "measured_figures",
    "active_observer_count", "client_library_is_hooked", "meta_path_hook_is_installed",
    "classify_statement", "OBSERVATION_LIMITS",
    "MATCH_WRITE_VERB", "MATCH_LEADING_KEYWORD", "MATCH_QUOTED_TEXT_ONLY", "MATCH_NONE",
]
