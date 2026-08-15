"""Section 11 — reviewer-seat custody, enforced at the write boundary.

The V5.3 panel failed three times, and every one of those failures was found
by quarantine *after* the rows already existed:

* one seat returned templated null work, identical reasoning row after row;
* one seat fanned out into parallel forks that raced on a single shared output
  file, so rows silently overwrote one another;
* a third process wrote a foreign file directly into a sealed reviewer path.

Quarantine is the wrong instrument for all three.  Each is a *write* that
should never have been accepted, and describing it afterwards leaves the
artifact contaminated and the count unrecoverable.  This module moves the
decision to the moment of the write.

A :class:`ReviewerSeat` owns exactly one directory, holds one deterministically
derived capability token and one process lease (pid + session), and appends to
one hash chain in a fixed assignment order.  Every other write is refused with
:class:`CustodyViolation`:

* a record whose ``seat_id`` is not the handle's own seat;
* an absent or invalid capability token;
* a different pid or a different session than the lease opened with;
* an output root shared with, nested in, or containing another seat's root;
* a previous-record hash that is missing, or that does not equal the chain
  head as it actually stands on disk (this is what a racing fork looks like);
* a dossier already recorded, or a dossier out of the assigned order;
* a foreign file appearing in the sealed seat root;
* a second finalization, or any append after finalization.

Resume is deliberately narrow.  A provider failure may be continued only by the
same seat, with the same capability token and the same session, from the last
valid link of a chain that verifies from genesis.  The pid is allowed to change
— a crashed process cannot resume itself — and that is the only relaxation.

Research shadow only.  Nothing here claims human validation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import canonical_json, require_aware, require_hash
from ..v5_1.models import Record, now_utc, sha256

# ---------------------------------------------------------------------------
# Vocabulary and constants
# ---------------------------------------------------------------------------

#: Exactly three seats.  A fourth reviewer is a protocol change, not a value.
SEAT_IDS: tuple[str, ...] = ("REVIEWER_A", "REVIEWER_B", "REVIEWER_C")

#: The documented genesis constant.  The first record of every seat chain
#: declares this as its ``previous_record_hash``.  Sixty-four zeros is reserved:
#: it is never the SHA-256 of any record this module writes, so "chains from
#: genesis" and "chains from a real record" can never be confused.
GENESIS_RECORD_HASH: str = "0" * 64

#: Domain separation for the capability HMAC, so a run salt reused elsewhere
#: cannot yield a token that is valid here.
CAPABILITY_DOMAIN: str = "curunir-v5-4-reviewer-seat-capability"

RECORDS_FILENAME = "records.jsonl"
LEASE_FILENAME = "seat_lease.json"
MANIFEST_FILENAME = "seat_manifest.json"

#: The complete set of files a sealed seat root may contain.  Anything else is
#: a foreign write, whoever made it.
SEAT_FILENAMES = frozenset({RECORDS_FILENAME, LEASE_FILENAME, MANIFEST_FILENAME})

#: Section 11.6 quality gate thresholds.
MIN_DISTINCT_REASONING_RATIO = 0.5
MAX_CONSTANT_LABEL_SURFACES = 1

_UNSET: Any = object()


class CustodyViolation(ValueError):
    """A write crossed a custody boundary.  Never downgraded to a warning."""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _require_hash(value: Any, what: str) -> str:
    try:
        require_hash(str(value))
    except (TypeError, ValueError):
        raise CustodyViolation(f"{what} is not a SHA-256 digest: {value!r}") from None
    return str(value)


def _require_aware(value: Any, what: str) -> str:
    try:
        require_aware(str(value))
    except (TypeError, ValueError):
        raise CustodyViolation(f"{what} is not a timezone-aware timestamp: {value!r}") from None
    return str(value)


def _normalize_payload(payload: Any) -> dict[str, Any]:
    """Round-trip the reviewer payload through canonical JSON.

    The payload is opaque to custody, but the chain hashes it, so it must be a
    JSON object whose serialization is stable (tuples become lists here, once,
    rather than differing between the writer and a later verifier).
    """
    if not isinstance(payload, Mapping):
        raise CustodyViolation("reviewer payload must be a mapping")
    try:
        return json.loads(canonical_json(dict(payload)))
    except (TypeError, ValueError) as exc:
        raise CustodyViolation(f"reviewer payload is not JSON-serializable: {exc}") from None


def chain_record_hash(*, previous_record_hash: str, seat_id: str, dossier_id: str,
                      payload: Mapping[str, Any]) -> str:
    """The chain link: H(previous, seat, dossier, payload).

    Deliberately identical in the writer and in :func:`verify_chain`; there is
    one implementation so a verifier cannot be made to agree with a writer that
    computed something else.
    """
    return sha256({
        "previous_record_hash": previous_record_hash,
        "seat_id": seat_id,
        "dossier_id": dossier_id,
        "payload": payload,
    })


def derive_capability_token(*, seat_id: str, run_salt: str) -> str:
    """Deterministic per-seat capability token.

    Derived, not drawn: ``random``/``uuid4`` would make a run irreproducible and
    an audit unrepeatable.  The run salt is the caller's secret for the run and
    is never written to disk; only ``sha256(token)`` reaches any record.
    """
    if seat_id not in SEAT_IDS:
        raise CustodyViolation(f"unknown reviewer seat: {seat_id!r}")
    if not str(run_salt).strip():
        raise CustodyViolation("a run salt is required to derive a seat capability token")
    return hmac.new(str(run_salt).encode("utf-8"),
                    f"{CAPABILITY_DOMAIN}|{seat_id}".encode("utf-8"),
                    "sha256").hexdigest()


def capability_token_hash(token: str) -> str:
    """What a record is allowed to carry: the token's SHA-256, never the token."""
    return sha256(str(token))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _order_hash(assignment_order: Sequence[str]) -> str:
    return sha256(list(assignment_order))


# ---------------------------------------------------------------------------
# Path relations — two seats may never share, contain, or sit inside a root
# ---------------------------------------------------------------------------

def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def path_relation(first: str | Path, second: str | Path) -> str:
    """``IDENTICAL``, ``NESTED`` (either direction), or ``DISJOINT``."""
    left, right = _resolve(first), _resolve(second)
    if left == right:
        return "IDENTICAL"
    if left.is_relative_to(right) or right.is_relative_to(left):
        return "NESTED"
    return "DISJOINT"


def assert_disjoint_seat_roots(seat_roots: Iterable[str | Path]) -> dict[str, Any]:
    """Refuse a panel layout in which any two seats can reach each other's rows.

    Sharing a path is the V5.3 fork race; a parent-child relationship is the
    same defect wearing a subdirectory.
    """
    roots = [_resolve(root) for root in seat_roots]
    for index, left in enumerate(roots):
        for right in roots[index + 1:]:
            relation = path_relation(left, right)
            if relation == "IDENTICAL":
                raise CustodyViolation(
                    f"shared output path: {left} is used by two seats")
            if relation == "NESTED":
                raise CustodyViolation(
                    f"nested output path: {left} and {right} are in a "
                    "parent-child relationship")
    return {"roots": [str(root) for root in roots], "disjoint": True}


# Process-local registry of live seat roots.  It cannot see another process,
# which is why ``open`` also refuses a root nested under an existing lease and
# why ``audit_panel`` re-checks the layout from the filesystem.
_OPEN_SEAT_ROOTS: dict[str, str] = {}


def _assert_no_root_conflict(seat_id: str, root: Path) -> None:
    for existing, owner in _OPEN_SEAT_ROOTS.items():
        if owner == seat_id and existing == str(root):
            continue
        relation = path_relation(existing, root)
        if relation == "IDENTICAL":
            raise CustodyViolation(
                f"shared output path: {seat_id} was given the output root "
                f"already held by {owner} ({existing})")
        if relation == "NESTED":
            raise CustodyViolation(
                f"nested output path: {seat_id} root {root} is in a "
                f"parent-child relationship with {owner} root {existing}")


def _register_root(seat_id: str, root: Path) -> None:
    _assert_no_root_conflict(seat_id, root)
    _OPEN_SEAT_ROOTS[str(root)] = seat_id


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeatRecord(Record):
    """One reviewer row, bound to the seat, the lease, and the chain.

    ``record_hash`` is recomputed in ``__post_init__``: a record that does not
    chain over its own content cannot be constructed at all, so a tampered row
    read back from disk cannot be rehydrated into a valid object.
    """

    seat_id: str
    dossier_id: str
    dossier_index: int
    process_id: int
    session_id: str
    capability_token_hash: str
    previous_record_hash: str
    record_hash: str
    timestamp: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.seat_id not in SEAT_IDS:
            raise CustodyViolation(f"unknown reviewer seat: {self.seat_id!r}")
        if not str(self.dossier_id).strip():
            raise CustodyViolation("a reviewer record requires a dossier id")
        if not isinstance(self.dossier_index, int) or self.dossier_index < 0:
            raise CustodyViolation("dossier index must be a non-negative integer")
        if not isinstance(self.process_id, int) or self.process_id <= 0:
            raise CustodyViolation("a reviewer record requires the writing pid")
        if not str(self.session_id).strip():
            raise CustodyViolation("a reviewer record requires the session id")
        _require_hash(self.capability_token_hash, "capability token hash")
        _require_hash(self.previous_record_hash, "previous record hash")
        _require_hash(self.record_hash, "record hash")
        _require_aware(self.timestamp, "record timestamp")
        if not isinstance(self.payload, Mapping):
            raise CustodyViolation("reviewer payload must be a mapping")
        expected = chain_record_hash(
            previous_record_hash=self.previous_record_hash, seat_id=self.seat_id,
            dossier_id=self.dossier_id, payload=self.payload)
        if not hmac.compare_digest(expected, self.record_hash):
            raise CustodyViolation(
                "record hash does not chain over (previous, seat, dossier, payload)")


# ---------------------------------------------------------------------------
# The seat
# ---------------------------------------------------------------------------

class ReviewerSeat:
    """A custody handle over exactly one reviewer output root.

    Construct through :meth:`open` (a fresh seat) or :meth:`resume` (a seat
    interrupted mid-run).  The constructor is private by convention because a
    handle that was not created against the filesystem has no lease to enforce.
    """

    def __init__(self, *, seat_id: str, seat_root: Path, session_id: str,
                 process_id: int, capability_token: str,
                 assignment_order: tuple[str, ...], head_hash: str,
                 completed: tuple[str, ...], finalized: bool) -> None:
        self.seat_id = seat_id
        self.seat_root = seat_root
        self.session_id = session_id
        self.process_id = process_id
        self.assignment_order = assignment_order
        self._capability_token = capability_token
        self.capability_token_hash = capability_token_hash(capability_token)
        self._head_hash = head_hash
        self._completed: list[str] = list(completed)
        self._finalized = finalized

    # -- introspection ----------------------------------------------------

    @property
    def records_path(self) -> Path:
        return self.seat_root / RECORDS_FILENAME

    @property
    def lease_path(self) -> Path:
        return self.seat_root / LEASE_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.seat_root / MANIFEST_FILENAME

    @property
    def head_hash(self) -> str:
        return self._head_hash

    @property
    def rows(self) -> int:
        return len(self._completed)

    @property
    def completed_dossiers(self) -> tuple[str, ...]:
        return tuple(self._completed)

    @property
    def finalized(self) -> bool:
        return self._finalized

    @property
    def next_dossier_index(self) -> int:
        """The index into ``assignment_order`` that may be written next."""
        return len(self._completed)

    @property
    def next_dossier_id(self) -> str | None:
        index = self.next_dossier_index
        if index >= len(self.assignment_order):
            return None
        return self.assignment_order[index]

    # -- opening ----------------------------------------------------------

    @classmethod
    def open(cls, *, seat_id: str, seat_root: str | Path, run_salt: str,
             session_id: str, assignment_order: Sequence[str],
             process_id: int | None = None) -> "ReviewerSeat":
        """Create a fresh seat root atomically and take the lease.

        The root is created with ``O_EXCL`` semantics: if it already exists the
        seat is not reopened, because reopening is precisely how a second fork
        joins a chain it does not own.  A finalized root is refused outright; an
        unfinalized one must go through :meth:`resume`.
        """
        if seat_id not in SEAT_IDS:
            raise CustodyViolation(f"unknown reviewer seat: {seat_id!r}")
        order = tuple(str(item) for item in assignment_order)
        if not order:
            raise CustodyViolation("a seat requires a non-empty assignment order")
        if len(set(order)) != len(order):
            raise CustodyViolation("assignment order repeats a dossier")
        if not str(session_id).strip():
            raise CustodyViolation("a seat lease requires a session id")
        root = _resolve(seat_root)
        _assert_no_root_conflict(seat_id, root)
        for ancestor in root.parents:
            if (ancestor / LEASE_FILENAME).exists():
                raise CustodyViolation(
                    f"nested output path: {root} sits inside the existing seat "
                    f"root {ancestor}")
        token = derive_capability_token(seat_id=seat_id, run_salt=run_salt)
        pid = int(process_id) if process_id is not None else os.getpid()
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(root, 0o700)
        except FileExistsError:
            if (root / MANIFEST_FILENAME).exists():
                raise CustodyViolation(
                    f"seat root {root} already exists and is finalized; a sealed "
                    "seat is never reopened") from None
            raise CustodyViolation(
                f"seat root {root} already exists; an interrupted seat is "
                "continued through resume, never reopened") from None
        _register_root(seat_id, root)
        fd = os.open(root / RECORDS_FILENAME,
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        _write_json_exclusive(root / LEASE_FILENAME, {
            "seat_id": seat_id,
            "session_id": session_id,
            "opened_process_id": pid,
            "capability_token_hash": capability_token_hash(token),
            "assignment_order": list(order),
            "assignment_order_hash": _order_hash(order),
            "genesis_record_hash": GENESIS_RECORD_HASH,
            "records_file": RECORDS_FILENAME,
            "opened_time": now_utc(),
        })
        return cls(seat_id=seat_id, seat_root=root, session_id=session_id,
                   process_id=pid, capability_token=token, assignment_order=order,
                   head_hash=GENESIS_RECORD_HASH, completed=(), finalized=False)

    @classmethod
    def resume(cls, *, seat_id: str, seat_root: str | Path, run_salt: str,
               session_id: str, assignment_order: Sequence[str],
               process_id: int | None = None) -> "ReviewerSeat":
        """Continue an interrupted seat, or refuse.

        Permits exactly one thing: the same seat, with the same capability
        token, the same session, and the same assignment order, continuing from
        the last valid link of a chain that verifies from genesis.  Earlier
        records are never rewritten.  The pid is rebound — a process that died
        cannot resume itself — and that is the only relaxation.

        ``seat.next_dossier_index`` on the returned handle is the next expected
        dossier index.
        """
        if seat_id not in SEAT_IDS:
            raise CustodyViolation(f"unknown reviewer seat: {seat_id!r}")
        root = _resolve(seat_root)
        lease = _read_json(root / LEASE_FILENAME)
        if lease is None:
            raise CustodyViolation(f"no seat lease to resume at {root}")
        if lease.get("seat_id") != seat_id:
            raise CustodyViolation(
                f"foreign seat id: {seat_id} may not resume the seat held by "
                f"{lease.get('seat_id')!r}")
        if lease.get("session_id") != session_id:
            raise CustodyViolation(
                "resume requires the same session; this seat was opened under "
                f"session {lease.get('session_id')!r}")
        token = derive_capability_token(seat_id=seat_id, run_salt=run_salt)
        if not hmac.compare_digest(str(lease.get("capability_token_hash", "")),
                                   capability_token_hash(token)):
            raise CustodyViolation(
                "resume requires the same capability token as the opened seat")
        order = tuple(str(item) for item in assignment_order)
        if _order_hash(order) != lease.get("assignment_order_hash"):
            raise CustodyViolation(
                "resume requires the same assignment order as the opened seat")
        if (root / MANIFEST_FILENAME).exists():
            raise CustodyViolation(
                f"seat {seat_id} is already finalized; a sealed seat is never resumed")
        verification = verify_chain(root)
        if not verification["chain_intact"]:
            raise CustodyViolation(
                f"refusing to resume {seat_id}: hash chain is broken at index "
                f"{verification['break_index']} ({verification['break_reason']})")
        completed = tuple(verification["dossiers"])
        if completed != order[:len(completed)]:
            raise CustodyViolation(
                "refusing to resume: the recorded dossiers are not a prefix of "
                "the assignment order")
        _register_root(seat_id, root)
        pid = int(process_id) if process_id is not None else os.getpid()
        return cls(seat_id=seat_id, seat_root=root, session_id=session_id,
                   process_id=pid, capability_token=token, assignment_order=order,
                   head_hash=verification["head_hash"], completed=completed,
                   finalized=False)

    # -- the write boundary ----------------------------------------------

    def append(self, *, dossier_id: str, payload: Mapping[str, Any],
               seat_id: Any = _UNSET, capability_token: Any = _UNSET,
               process_id: Any = _UNSET, session_id: Any = _UNSET,
               previous_record_hash: Any = _UNSET) -> SeatRecord:
        """Append one reviewer row, or refuse.

        The optional arguments exist so a caller can state what it believes it
        is writing; each is checked against the lease rather than trusted.  A
        caller that omits them is checked against the live process anyway.
        """
        if self._finalized:
            raise CustodyViolation(
                f"append after finalization: seat {self.seat_id} is sealed")

        claimed_seat = self.seat_id if seat_id is _UNSET else seat_id
        if claimed_seat != self.seat_id:
            raise CustodyViolation(
                f"foreign seat id: this handle owns {self.seat_id}, the record "
                f"claims {claimed_seat!r}")

        token = self._capability_token if capability_token is _UNSET else capability_token
        if token is None or not str(token).strip():
            raise CustodyViolation("capability token absent; the write is unauthorized")
        if not hmac.compare_digest(str(token), self._capability_token):
            raise CustodyViolation(
                f"capability token invalid for seat {self.seat_id}")

        pid = os.getpid() if process_id is _UNSET else process_id
        if pid != self.process_id:
            raise CustodyViolation(
                f"process lease violated: seat {self.seat_id} is leased to pid "
                f"{self.process_id}, the write comes from pid {pid}")
        session = self.session_id if session_id is _UNSET else session_id
        if session != self.session_id:
            raise CustodyViolation(
                f"session lease violated: seat {self.seat_id} is leased to "
                f"session {self.session_id!r}, the write claims {session!r}")

        self._assert_root_sealed()

        if not self.records_path.exists():
            raise CustodyViolation(
                f"record file for seat {self.seat_id} has disappeared")
        on_disk = _load_rows(self.records_path)
        disk_head = GENESIS_RECORD_HASH
        if on_disk:
            last = on_disk[-1]
            if not isinstance(last, Mapping) or "record_hash" not in last:
                raise CustodyViolation(
                    "previous-record hash unreadable: the last row on disk is "
                    "not a custody record")
            disk_head = str(last["record_hash"])
        if len(on_disk) != len(self._completed) or disk_head != self._head_hash:
            raise CustodyViolation(
                f"previous-record hash mismatch: seat {self.seat_id} holds head "
                f"{self._head_hash[:12]}… over {len(self._completed)} rows, the "
                f"file holds {disk_head[:12]}… over {len(on_disk)} rows; another "
                "writer is appending to this seat")

        previous = self._head_hash if previous_record_hash is _UNSET else previous_record_hash
        if previous is None or not str(previous).strip():
            raise CustodyViolation("previous-record hash missing")
        if str(previous) != self._head_hash:
            raise CustodyViolation(
                f"previous-record hash mismatch: expected {self._head_hash}, "
                f"the write declares {previous}")

        if dossier_id in self._completed:
            raise CustodyViolation(
                f"append-only: dossier {dossier_id!r} is already recorded for "
                f"seat {self.seat_id} and may not be overwritten")
        index = self.next_dossier_index
        if index >= len(self.assignment_order):
            raise CustodyViolation(
                f"assignment order exhausted for seat {self.seat_id}; "
                f"{dossier_id!r} is not assigned")
        expected = self.assignment_order[index]
        if dossier_id != expected:
            raise CustodyViolation(
                f"out-of-order dossier: seat {self.seat_id} is assigned "
                f"{expected!r} at index {index}, the write offers {dossier_id!r}")

        body = _normalize_payload(payload)
        record_hash = chain_record_hash(
            previous_record_hash=str(previous), seat_id=self.seat_id,
            dossier_id=dossier_id, payload=body)
        record = SeatRecord(
            seat_id=self.seat_id, dossier_id=dossier_id, dossier_index=index,
            process_id=int(pid), session_id=str(session),
            capability_token_hash=self.capability_token_hash,
            previous_record_hash=str(previous), record_hash=record_hash,
            timestamp=now_utc(), payload=body)
        _append_line(self.records_path, record.to_record())
        self._head_hash = record_hash
        self._completed.append(dossier_id)
        return record

    def _assert_root_sealed(self) -> None:
        """Refuse to keep writing into a root a foreign process has touched."""
        for entry in sorted(self.seat_root.rglob("*")):
            if entry.is_dir():
                raise CustodyViolation(
                    f"foreign file in the sealed seat root: {entry} is a "
                    "subdirectory; a seat root holds custody files only")
            if entry.name not in SEAT_FILENAMES or entry.parent != self.seat_root:
                raise CustodyViolation(
                    f"foreign file in the sealed seat root of {self.seat_id}: {entry}")

    # -- finalization -----------------------------------------------------

    def finalize(self, *, capability_token: Any = _UNSET,
                 process_id: Any = _UNSET) -> dict[str, Any]:
        """Seal the seat with an immutable manifest, once.

        The manifest is written with ``O_EXCL`` and then made read-only, so a
        second finalization fails in the filesystem even if the in-memory flag
        is bypassed.
        """
        if self._finalized or self.manifest_path.exists():
            raise CustodyViolation(
                f"seat {self.seat_id} is already finalized; a second "
                "finalization would replace a sealed manifest")
        token = self._capability_token if capability_token is _UNSET else capability_token
        if token is None or not str(token).strip():
            raise CustodyViolation("capability token absent; the write is unauthorized")
        if not hmac.compare_digest(str(token), self._capability_token):
            raise CustodyViolation(
                f"capability token invalid for seat {self.seat_id}")
        pid = os.getpid() if process_id is _UNSET else process_id
        if pid != self.process_id:
            raise CustodyViolation(
                f"process lease violated: seat {self.seat_id} is leased to pid "
                f"{self.process_id}, finalization comes from pid {pid}")
        self._assert_root_sealed()

        verification = verify_chain(self.seat_root)
        if not verification["chain_intact"]:
            raise CustodyViolation(
                f"refusing to finalize {self.seat_id}: hash chain is broken at "
                f"index {verification['break_index']} "
                f"({verification['break_reason']})")
        if verification["rows"] != self.rows or verification["head_hash"] != self._head_hash:
            raise CustodyViolation(
                f"refusing to finalize {self.seat_id}: the record file no longer "
                "matches the chain this handle wrote")

        rows = _load_rows(self.records_path)
        first_hash = str(rows[0]["record_hash"]) if rows else GENESIS_RECORD_HASH
        last_hash = str(rows[-1]["record_hash"]) if rows else GENESIS_RECORD_HASH
        manifest = {
            "seat_id": self.seat_id,
            "session_id": self.session_id,
            "process_id": self.process_id,
            "capability_token_hash": self.capability_token_hash,
            "genesis_record_hash": GENESIS_RECORD_HASH,
            "rows": len(rows),
            "first_record_hash": first_hash,
            "last_record_hash": last_hash,
            "chain_head": self._head_hash,
            "assignment_order": list(self.assignment_order),
            "completed_dossiers": list(self._completed),
            "assignment_complete": tuple(self._completed) == self.assignment_order,
            "records_file": RECORDS_FILENAME,
            "records_file_sha256": _file_sha256(self.records_path),
            "finalized_time": now_utc(),
        }
        _write_json_exclusive(self.manifest_path, manifest)
        os.chmod(self.manifest_path, 0o444)
        os.chmod(self.records_path, 0o444)
        self._finalized = True
        return manifest


# ---------------------------------------------------------------------------
# Panel helpers
# ---------------------------------------------------------------------------

def open_seat(*, seat_id: str, seat_root: str | Path, run_salt: str,
              session_id: str, assignment_order: Sequence[str],
              process_id: int | None = None) -> ReviewerSeat:
    """Module-level alias for :meth:`ReviewerSeat.open`."""
    return ReviewerSeat.open(seat_id=seat_id, seat_root=seat_root,
                             run_salt=run_salt, session_id=session_id,
                             assignment_order=assignment_order,
                             process_id=process_id)


def open_panel(*, panel_root: str | Path, run_salt: str, session_id: str,
               assignments: Mapping[str, Sequence[str]]) -> dict[str, ReviewerSeat]:
    """Open all three seats under disjoint per-seat roots."""
    missing = set(SEAT_IDS) - set(assignments)
    if missing:
        raise CustodyViolation(f"the panel is three seats; missing {sorted(missing)}")
    unknown = set(assignments) - set(SEAT_IDS)
    if unknown:
        raise CustodyViolation(f"unknown reviewer seats: {sorted(unknown)}")
    root = _resolve(panel_root)
    roots = {seat_id: root / seat_id.lower() for seat_id in SEAT_IDS}
    assert_disjoint_seat_roots(roots.values())
    return {
        seat_id: ReviewerSeat.open(
            seat_id=seat_id, seat_root=roots[seat_id], run_salt=run_salt,
            session_id=session_id, assignment_order=assignments[seat_id])
        for seat_id in SEAT_IDS
    }


# ---------------------------------------------------------------------------
# Filesystem primitives
# ---------------------------------------------------------------------------

def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise CustodyViolation(f"{path} already exists and is immutable") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_line(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_rows(path: Path) -> list[Any]:
    """Rows as they actually are on disk.  Unparsable lines survive as ``None``."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append(None)
    return rows


# ---------------------------------------------------------------------------
# Verification and audit
# ---------------------------------------------------------------------------

def verify_chain(seat_root: str | Path) -> dict[str, Any]:
    """Verify one seat chain from genesis.  Reports; never raises.

    An auditor that can be made to crash on a malformed file is an auditor an
    adversary controls, so every defect is returned as data.
    """
    root = _resolve(seat_root)
    records_path = root / RECORDS_FILENAME
    manifest = _read_json(root / MANIFEST_FILENAME)
    lease = _read_json(root / LEASE_FILENAME)
    result: dict[str, Any] = {
        "seat_root": str(root),
        "seat_id": (manifest or lease or {}).get("seat_id"),
        "rows": 0,
        "chain_intact": False,
        "break_index": None,
        "break_reason": None,
        "head_hash": GENESIS_RECORD_HASH,
        "first_record_hash": None,
        "finalized": manifest is not None,
        "manifest_consistent": None,
        "dossiers": [],
        "lease_mismatch_rows": [],
        "foreign_seat_rows": [],
    }
    if not records_path.exists():
        result["break_reason"] = "record file is missing"
        return result

    rows = _load_rows(records_path)
    result["rows"] = len(rows)
    owner = result["seat_id"]
    lease_token_hash = (lease or {}).get("capability_token_hash")
    lease_session = (lease or {}).get("session_id")

    previous = GENESIS_RECORD_HASH
    break_index: int | None = None
    reason: str | None = None
    dossiers: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            break_index, reason = index, "row is not valid JSON"
            break
        required = ("seat_id", "dossier_id", "payload", "previous_record_hash",
                    "record_hash")
        if any(key not in row for key in required):
            break_index, reason = index, "row is missing custody fields"
            break
        if str(row["previous_record_hash"]) != previous:
            break_index, reason = index, "previous-record hash does not match the chain head"
            break
        expected = chain_record_hash(
            previous_record_hash=previous, seat_id=str(row["seat_id"]),
            dossier_id=str(row["dossier_id"]), payload=row["payload"])
        if expected != str(row["record_hash"]):
            break_index, reason = index, "record hash does not match its content"
            break
        if owner is not None and row["seat_id"] != owner:
            result["foreign_seat_rows"].append(index)
        if lease_token_hash and row.get("capability_token_hash") != lease_token_hash:
            result["lease_mismatch_rows"].append(
                {"index": index, "field": "capability_token_hash"})
        elif lease_session and row.get("session_id") != lease_session:
            result["lease_mismatch_rows"].append(
                {"index": index, "field": "session_id"})
        dossiers.append(str(row["dossier_id"]))
        previous = str(row["record_hash"])
        if index == 0:
            result["first_record_hash"] = previous

    result["dossiers"] = dossiers
    result["head_hash"] = previous
    result["break_index"] = break_index
    result["break_reason"] = reason
    result["chain_intact"] = break_index is None

    if manifest is not None:
        result["manifest_consistent"] = bool(
            manifest.get("rows") == len(rows)
            and manifest.get("chain_head") == previous
            and manifest.get("last_record_hash") == previous
            and (not rows or manifest.get("first_record_hash") == result["first_record_hash"])
            and break_index is None)
    return result


def _seat_files_report(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for entry in sorted(root.rglob("*")):
        if entry.is_dir():
            findings.append({"kind": "FOREIGN_DIRECTORY_IN_SEAT_ROOT",
                             "path": str(entry)})
        elif entry.name not in SEAT_FILENAMES or entry.parent != root:
            findings.append({"kind": "FOREIGN_FILE_IN_SEAT_ROOT",
                             "path": str(entry)})
    return findings


def audit_panel(seat_roots: Iterable[str | Path]) -> dict[str, Any]:
    """Audit the whole panel: chains, foreign writes, and the layout itself."""
    roots = [_resolve(root) for root in seat_roots]
    per_seat: dict[str, Any] = {}
    foreign_writes: list[dict[str, Any]] = []
    hash_chain_breaks: list[dict[str, Any]] = []

    for root in roots:
        verification = verify_chain(root)
        key = str(verification["seat_id"] or root.name)
        per_seat[key] = verification
        if not verification["chain_intact"]:
            hash_chain_breaks.append({
                "seat": key, "seat_root": str(root),
                "break_index": verification["break_index"],
                "reason": verification["break_reason"],
            })
        for index in verification["foreign_seat_rows"]:
            foreign_writes.append({"seat": key, "kind": "FOREIGN_SEAT_ID_IN_RECORD",
                                   "index": index, "path": str(root)})
        for mismatch in verification["lease_mismatch_rows"]:
            foreign_writes.append({"seat": key, "kind": "FOREIGN_LEASE_IN_RECORD",
                                   "index": mismatch["index"],
                                   "field": mismatch["field"], "path": str(root)})
        if root.exists():
            for finding in _seat_files_report(root):
                foreign_writes.append({"seat": key, **finding})

    shared_output_paths: list[dict[str, Any]] = []
    for index, left in enumerate(roots):
        for right in roots[index + 1:]:
            relation = path_relation(left, right)
            if relation != "DISJOINT":
                shared_output_paths.append({
                    "relation": relation, "first": str(left), "second": str(right)})

    clean = (not foreign_writes and not shared_output_paths
             and not hash_chain_breaks
             and all(seat["chain_intact"] for seat in per_seat.values())
             and all(seat["manifest_consistent"] is not False
                     for seat in per_seat.values()))
    return {
        "seats": len(roots),
        "per_seat": per_seat,
        "foreign_writes": foreign_writes,
        "shared_output_paths": shared_output_paths,
        "hash_chain_breaks": hash_chain_breaks,
        "verdict": "CUSTODY_CLEAN" if clean else "CUSTODY_VIOLATED",
    }


# ---------------------------------------------------------------------------
# Section 11.6 — seat quality gate
# ---------------------------------------------------------------------------

def _normalized_reasoning(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def seat_quality_gate(seat_root: str | Path, *,
                      surfaces_by_dossier: Mapping[str, str]) -> dict[str, Any]:
    """Section 11.6: is this seat's output usable at all?

    Custody proves the rows are the seat's own.  It cannot prove they are work.
    This gate is what catches the V5.3 templated seat: identical reasoning, or a
    label held constant across more than one surface, fails regardless of how
    clean the chain is.
    """
    root = _resolve(seat_root)
    verification = verify_chain(root)
    lease = _read_json(root / LEASE_FILENAME) or {}
    manifest = _read_json(root / MANIFEST_FILENAME)
    rows = [row for row in _load_rows(root / RECORDS_FILENAME)
            if isinstance(row, Mapping)]

    assigned = [str(item) for item in lease.get("assignment_order", [])]
    completed = [str(row.get("dossier_id")) for row in rows]
    missing = [dossier for dossier in assigned if dossier not in completed]
    all_assigned_completed = bool(assigned) and not missing

    reasonings = [_normalized_reasoning((row.get("payload") or {}).get("reasoning"))
                  for row in rows]
    distinct = len({text for text in reasonings if text})
    empty = sum(1 for text in reasonings if not text)
    ratio = round(distinct / len(rows), 4) if rows else 0.0
    reasoning_ok = bool(rows) and ratio >= MIN_DISTINCT_REASONING_RATIO and empty == 0

    labels_by_surface: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for row in rows:
        dossier = str(row.get("dossier_id"))
        surface = surfaces_by_dossier.get(dossier)
        if surface is None:
            unmapped.append(dossier)
            continue
        label = str((row.get("payload") or {}).get("label"))
        labels_by_surface.setdefault(str(surface), []).append(label)
    constant_surfaces = sorted(
        surface for surface, labels in labels_by_surface.items()
        if len(labels) >= 2 and len(set(labels)) == 1)
    labels_ok = len(constant_surfaces) <= MAX_CONSTANT_LABEL_SURFACES

    custody_ok = bool(
        root.exists()
        and verification["chain_intact"]
        and not verification["foreign_seat_rows"]
        and not verification["lease_mismatch_rows"]
        and not _seat_files_report(root))
    finalized_ok = bool(
        manifest is not None
        and manifest.get("seat_id") == lease.get("seat_id")
        and manifest.get("session_id") == lease.get("session_id")
        and manifest.get("capability_token_hash") == lease.get("capability_token_hash")
        and verification["manifest_consistent"] is True)

    checks = {
        "all_assigned_dossiers_completed": all_assigned_completed,
        "distinct_reasoning_ratio_met": reasoning_ok,
        "constant_label_surfaces_within_limit": labels_ok,
        "custody_chain_valid": custody_ok,
        "finalized_by_owning_seat": finalized_ok,
    }
    return {
        "seat_root": str(root),
        "seat_id": lease.get("seat_id") or verification["seat_id"],
        "rows": len(rows),
        "assigned": len(assigned),
        "missing_dossiers": missing,
        "distinct_reasoning_ratio": ratio,
        "empty_reasonings": empty,
        "minimum_distinct_reasoning_ratio": MIN_DISTINCT_REASONING_RATIO,
        "constant_label_surfaces": constant_surfaces,
        "unmapped_dossiers": sorted(set(unmapped)),
        "checks": checks,
        "verdict": "PASS" if all(checks.values()) else "FAIL",
    }
