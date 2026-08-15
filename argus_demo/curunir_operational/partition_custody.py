"""Fail-closed custody for held-out evidence partitions — the READ boundary.

``access.py`` decides who may *see* a record that a process already holds.
``v5_4/custody.py`` decides who may *write* into a reviewer seat.  Neither one
prevents a process from reading bytes it was never supposed to touch, and that
is exactly how two holdout generations were lost:

* the V1 sealed partition was destroyed because the aligned shard plan drew
  from every partition, so 21 sealed units acquired reference answers;
* the V2 development-validation and sealed-confirmation partitions were
  destroyed by ``V581-PARTITION-CUSTODY-INCIDENT-306`` (2026-07-28): all four
  historical partitions share one plaintext file,
  ``55_d25_role_binding_reference/population/selected_population.jsonl``
  (750 rows, mode 0644), and the standing D42 loader deserialized every row
  before joining the 120 exposed identifiers.  ``item_text_bytes_read = true``.
  Both partitions were retired ``RETIRED_UNSCORED_CUSTODY_FAILURE``.

The lesson recorded in incident 306 is that a holdout partition is not
protected by intention, by review, or by a naming convention.  A later campaign
did introduce a 120-row exposed-only snapshot
(``307_d42g_structural_region_truth/exposed_population.jsonl``) and every live
runner now points at it — but that is a *convention*.  Three entry points that
still target the shared plaintext file remain runnable today, and nothing
stops the next script from writing ``read_text()`` against it.

This module replaces the convention with three mechanisms that fail closed:

``SEALED STORE``
    A holdout-bearing file is held at mode ``0o000``.  A non-root process —
    including its owner — gets ``PermissionError`` from the kernel, in any
    language, from any script, reviewed or not.  This is what makes the byte
    read impossible rather than discouraged, and it is why the historical
    defect is repaired without editing a single frozen campaign artifact: the
    three legacy loaders now fail closed with their bytes unchanged.

``ONE-SHOT LOCK``
    ``os.open(O_CREAT | O_EXCL)``.  The first opener wins atomically; every
    later attempt raises :class:`CustodyViolation`.  The lock file is never
    removed by this module.

``FIRST-ACCESS LOG``
    A hash-chained, append-only ledger.  The record that a store is about to
    be opened is appended and ``fsync``-ed *before* the store is unsealed, so
    an access that happened but was not logged requires the log write to have
    succeeded and the process to have then crashed — which the observer
    reports as an unterminated open, not as an absence.

``ADJUDICATION READ``
    A *second* gated read path, for the one lawful use that is not an opening:
    handing the evidence of an explicitly named identity list to a packet
    builder so a reference can be adjudicated.  It writes its own event class
    and it does **not** touch the one-shot lock, because
    ``V3_ADJUDICATION_PROTOCOL.json`` A8 reserves that lock for the single
    sealed OPENING.  See :func:`read_units_for_adjudication`.

:class:`AccessObserver` answers "has this partition been read, and by whom"
from that ledger.  It never answers a bare ``NO`` when the ledger does not
verify; a broken chain reports ``UNKNOWN_LOG_TAMPERED``.  It answers the
opening question and the adjudication-read question separately, because A8
requires the two event classes to stay distinguishable.

Nothing here is advisory.  There is no flag a caller can forget: the store is
unreadable until a gate unseals it, the opening gate cannot unseal without
taking the lock and writing the log first, and the adjudication gate cannot
unseal without an explicit identity list that is provably a subset of a
registered partition.

Research shadow only.  Nothing here claims human validation.
"""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

__all__ = [
    "CustodyViolation",
    "SealedStore",
    "SEALED_STORES",
    "SEALED_MODE",
    "SEALED_DIR_MODE",
    "GENESIS_HASH",
    "EXPOSURE_KINDS",
    "FirstAccessLog",
    "OneShotLock",
    "AccessObserver",
    "CustodyRoot",
    "custody_root",
    "seal_store",
    "unseal_store_for_audit",
    "verify_seals",
    "store_for_id",
    "is_sealed_path",
    "open_sealed_partition",
    "load_exposed_population",
    "load_authorised_packets",
    "SEALED_OPENING_EVENT",
    "ADJUDICATION_READ_EVENT",
    "ADJUDICATION_READ_CLOSE_EVENT",
    "ADJUDICATION_DENIED_EVENT",
    "partition_identity_sha256",
    "assert_partition_membership",
    "read_units_for_adjudication",
    "adjudication_read_records",
    "declare_ledger_scope",
    "record_exposure",
    "exposed_unit_ids",
    "exposed_commitments",
    "register_holdout_units",
    "holdout_unit_ids",
    "holdout_commitments",
    "holdout_registrations",
    "assert_not_holdout",
    "LEDGER_MODE",
    "HOLDOUT_COMMITMENT_SCHEME",
    "HOLDOUT_SALT_FILE",
    "assert_eligible_for_holdout",
]


class CustodyViolation(ValueError):
    """A custody rule was broken.  Never downgraded to a warning."""


#: Holdout-bearing stores are held at this mode.  ``0o000`` denies the owner
#: too: Linux enforces the permission bits for every process without
#: ``CAP_DAC_OVERRIDE``, so an unprivileged read fails regardless of who runs
#: it.  ``0o400`` would not do — the campaign runs as the file's owner.
SEALED_MODE = 0o000

#: Directories are sealed the same way.  ``0o000`` on a directory removes
#: search permission, so nothing inside it can be opened either — which is how
#: a whole reviewer-seat delivery tree is closed with one call.
SEALED_DIR_MODE = 0o000

#: Mode granted for the duration of one gated open.  Read-only, owner-only.
OPEN_MODE = 0o400

#: Mode granted to a directory for the duration of one gated open.
OPEN_DIR_MODE = 0o500

#: Mode held by the custody ledger and its heads file.  Owner-only.
#:
#: The ledger is identifier-bearing: ``HOLDOUT_REGISTER`` and ``EXPOSURE``
#: records name unit identities so that :func:`assert_not_holdout` can refuse
#: to shard them.  It was created at ``0o644`` by :func:`_append_line`'s
#: ``O_CREAT`` mode, which made the *membership* of a sealed partition readable
#: by any process on the host without unsealing anything — weaker than
#: ``V3_SAMPLING_RULE.json`` S5 STEP_10, which requires a partition's identity
#: list to be written once into that partition's own physically separate store
#: at mode ``0o400``.  Identity is not item text, so no eligibility conjunct is
#: breached by the exposure and no unit is spent; but a control that publishes
#: what it is protecting is not a control.
#:
#: ``0o600`` and not ``0o000``: the ledger must stay writable and verifiable by
#: the custody layer itself, which appends to it on every gated open.  A
#: sealed ledger could not record the access it exists to record.
#:
#: The seal registry is held at the same mode.  It was left at ``0o644``
#: (``LEDGER_EXPOSURE_REPAIR.json`` REPAIR_1) on the argument that a readable
#: registry is what lets a third party confirm that every holdout store sits at
#: mode ``0``.  That argument does not survive inspection: the registry is a
#: *claim* about a past chmod, while :func:`verify_seals` reads the live inode,
#: which is the *fact*, and a third party who can stat the store does not need
#: the registry to check it.  What world-readability actually publishes is the
#: path of every holdout-bearing store on the host.  Consistency at ``0o600``
#: costs a confirmation channel nobody was using and closes a directory listing
#: of where the holdouts live.
LEDGER_MODE = 0o600

#: Documented genesis constant for the first-access chain.  Sixty-four zeros
#: is never the SHA-256 of any record this module writes, so "chains from
#: genesis" is decidable.
GENESIS_HASH = "0" * 64

#: Repository root: ``.../argus_demo``.
_REPO = Path(__file__).resolve().parent.parent

_CAMPAIGN = (
    _REPO / "artifacts" / "curunir_autonomous_completion_v5_8_1_20260725"
)


@dataclass(frozen=True)
class SealedStore:
    """A file or directory carrying holdout item text.

    ``kind`` is ``"FILE"`` or ``"DIRECTORY"``.  A sealed directory is sealed
    whole: losing search permission on it closes every path beneath it in one
    inode, which is the only way to seal a delivery tree without enumerating —
    and therefore reading — what is inside it.
    """

    store_id: str
    relative_path: str
    reason: str
    kind: str = "FILE"

    @property
    def path(self) -> Path:
        return (_REPO / self.relative_path).resolve()

    @property
    def is_directory(self) -> bool:
        return self.kind == "DIRECTORY"

    @property
    def sealed_mode(self) -> int:
        return SEALED_DIR_MODE if self.is_directory else SEALED_MODE

    @property
    def open_mode(self) -> int:
        return OPEN_DIR_MODE if self.is_directory else OPEN_MODE


#: The historical holdout-bearing stores, declared in production code rather
#: than in a data file.  A data file can be deleted, renamed or "not found";
#: this tuple can only be changed by editing a reviewed source file.  It is the
#: floor of the registry, never the whole of it: V3 and later generations
#: register through :func:`seal_store`.
SEALED_STORES: tuple[SealedStore, ...] = (
    SealedStore(
        store_id="D25_SHARED_POPULATION_750",
        relative_path=(
            "artifacts/curunir_autonomous_completion_v5_8_1_20260725/"
            "55_d25_role_binding_reference/population/selected_population.jsonl"
        ),
        reason=(
            "The file named by incident 306.  750 plaintext rows carrying all "
            "four historical partitions, including both retired V2 holdouts."
        ),
    ),
    SealedStore(
        store_id="D25_ALL_UNITS",
        relative_path=(
            "artifacts/curunir_autonomous_completion_v5_8_1_20260725/"
            "55_d25_role_binding_reference/population/all_units.jsonl"
        ),
        reason=(
            "The unsampled unit corpus the partitions were drawn from; it is a "
            "superset of every holdout item text."
        ),
    ),
    SealedStore(
        store_id="D25_PACKETS",
        relative_path=(
            "artifacts/curunir_autonomous_completion_v5_8_1_20260725/"
            "55_d25_role_binding_reference/packets/packets.jsonl"
        ),
        reason="Adjudication packets carrying holdout item text.",
    ),
    SealedStore(
        store_id="D25_RESERVE_PACKETS",
        relative_path=(
            "artifacts/curunir_autonomous_completion_v5_8_1_20260725/"
            "55_d25_role_binding_reference/packets/reserve_packets.jsonl"
        ),
        reason="Reserve adjudication packets carrying holdout item text.",
    ),
) + tuple(
    SealedStore(
        store_id=f"D25_SEAT_DELIVERY_{seat.upper()}",
        relative_path=(
            "artifacts/curunir_autonomous_completion_v5_8_1_20260725/"
            "55_d25_role_binding_reference/packets/"
            f"d25_role_reference_reviewer_{seat}"
        ),
        reason=(
            "Reviewer-seat delivery tree written by build_aligned_shards.py.  "
            "It holds full item-text packets and is the artifact of the second "
            "loss mechanism: the builder sharded the entire principal "
            "population, so holdout packets were delivered to blind seats."
        ),
        kind="DIRECTORY",
    )
    for seat in ("a", "b", "c")
)


# ---------------------------------------------------------------------------
# Custody root: ledger, seal registry and locks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CustodyRoot:
    """Where the ledger, the seal registry and the one-shot locks live."""

    root: Path

    @property
    def access_log(self) -> Path:
        return self.root / "first_access_log.jsonl"

    @property
    def access_heads(self) -> Path:
        return self.root / "first_access_log.heads"

    @property
    def seal_registry(self) -> Path:
        return self.root / "seal_registry.jsonl"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    def ensure(self) -> "CustodyRoot":
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)
        return self


DEFAULT_CUSTODY_ROOT = _REPO / "artifacts" / "partition_custody"


def custody_root(root: str | Path | None = None) -> CustodyRoot:
    """Resolve a custody root.  ``None`` means the repository default."""

    return CustodyRoot(Path(root).resolve() if root is not None
                       else DEFAULT_CUSTODY_ROOT)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_line(path: Path, text: str, *, mode: int) -> None:
    """Append one line and fsync it.  ``O_APPEND`` only: never truncate, never
    seek.  The file descriptor is opened without ``O_TRUNC`` and without write
    positioning, so this writer cannot rewrite history even by mistake.

    ``mode`` is applied on creation *and* enforced on an existing file, because
    ``O_CREAT``'s mode argument is ignored once the file exists — which is
    exactly how an identifier-bearing ledger created at ``0o644`` stays
    world-readable forever.  Enforcing it here means the permission is a
    property of the writer, not of whoever happened to create the file.

    ``mode`` is required and has no default.  A default is what produced
    ``V3_ADJUDICATION_INFRA_STATUS.json`` FINDING-2: the ledger was moved to
    ``LEDGER_MODE`` by naming it at two call sites, and the third — the seal
    registry — kept the ``0o644`` default and stayed world-readable.  A writer
    that cannot forget the mode cannot reintroduce that asymmetry.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, mode)
    try:
        os.write(handle, (text + "\n").encode("utf-8"))
        os.fsync(handle)
    finally:
        os.close(handle)
    if stat.S_IMODE(path.stat().st_mode) != mode:
        os.chmod(path, mode)


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def _actor() -> dict[str, Any]:
    """Who is doing this, in terms that survive into the ledger."""

    return {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "executable": sys.executable,
        "argv": list(sys.argv),
        "cwd": os.getcwd(),
    }


# ---------------------------------------------------------------------------
# First-access log
# ---------------------------------------------------------------------------


class FirstAccessLog:
    """Append-only, hash-chained ledger of every custody event.

    Append-only is enforced three ways, because any one of them alone is a
    promise rather than a control:

    * the writer only ever opens with ``O_APPEND`` and never with ``O_TRUNC``;
    * every record carries ``previous_record_hash``, so editing or removing an
      interior record breaks the chain;
    * every append also writes a line to a separate heads ledger, so removing
      the *last* record — which a backward chain alone cannot see — leaves the
      chain head disagreeing with the recorded head.

    :meth:`verify` is the only thing anyone should trust.  It reports, it never
    repairs.
    """

    def __init__(self, root: CustodyRoot) -> None:
        self.root = root

    # -- writing ---------------------------------------------------------

    def append(self, *, store_id: str, event: str,
               detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = self.verify()
        if not state["verified"]:
            raise CustodyViolation(
                "refusing to append to a first-access log that does not "
                f"verify: {state['reason']}"
            )
        previous = state["head"]
        body = {
            "store_id": store_id,
            "event": event,
            "utc": _now_utc(),
            "sequence": state["record_count"] + 1,
            "actor": _actor(),
            "detail": dict(detail or {}),
            "previous_record_hash": previous,
        }
        record_hash = hashlib.sha256(_canonical(body).encode()).hexdigest()
        record = dict(body, record_hash=record_hash)
        _append_line(self.root.access_log, _canonical(record), mode=LEDGER_MODE)
        _append_line(
            self.root.access_heads,
            _canonical({"sequence": body["sequence"], "head": record_hash}),
            mode=LEDGER_MODE,
        )
        return record

    # -- reading ---------------------------------------------------------

    def records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in _read_lines(self.root.access_log):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"__malformed__": True, "raw_len": len(line)})
        return out

    def verify(self) -> dict[str, Any]:
        """Verify the chain from genesis and against the heads ledger."""

        records = self.records()
        heads = []
        for line in _read_lines(self.root.access_heads):
            try:
                heads.append(json.loads(line))
            except json.JSONDecodeError:
                heads.append(None)

        total = len(records)
        previous = GENESIS_HASH
        for index, record in enumerate(records):
            if record.get("__malformed__"):
                return _chain_state(False, previous, total,
                                    f"record {index + 1} is malformed")
            if record.get("previous_record_hash") != previous:
                return _chain_state(False, previous, total,
                                    f"record {index + 1} does not chain from "
                                    "the preceding record")
            body = {k: v for k, v in record.items() if k != "record_hash"}
            expected = hashlib.sha256(_canonical(body).encode()).hexdigest()
            if expected != record.get("record_hash"):
                return _chain_state(False, previous, total,
                                    f"record {index + 1} hash mismatch: the "
                                    "record body was altered")
            if record.get("sequence") != index + 1:
                return _chain_state(False, previous, total,
                                    f"record {index + 1} sequence mismatch")
            previous = record["record_hash"]

        if len(heads) != total:
            return _chain_state(
                False, previous, total,
                f"heads ledger records {len(heads)} appends but the log holds "
                f"{total}: a record was removed or a head was dropped",
            )
        for index, head in enumerate(heads):
            if head is None or head.get("sequence") != index + 1:
                return _chain_state(False, previous, total,
                                    f"heads ledger entry {index + 1} malformed")
            if head.get("head") != records[index]["record_hash"]:
                return _chain_state(
                    False, previous, total,
                    f"heads ledger entry {index + 1} does not match the log",
                )
        return _chain_state(True, previous, total, "chain verifies")

    # -- queries ---------------------------------------------------------

    def events_for(self, store_id: str) -> list[dict[str, Any]]:
        return [record for record in self.records()
                if record.get("store_id") == store_id]

    def first_access(self, store_id: str) -> dict[str, Any] | None:
        for record in self.events_for(store_id):
            if record.get("event") == "OPEN":
                return record
        return None


def _chain_state(verified: bool, head: str, count: int,
                 reason: str) -> dict[str, Any]:
    return {"verified": verified, "head": head if verified else head,
            "record_count": count, "reason": reason}


# ---------------------------------------------------------------------------
# One-shot lock
# ---------------------------------------------------------------------------


class OneShotLock:
    """A partition may be opened for scoring exactly once.

    ``O_CREAT | O_EXCL`` is atomic at the kernel: exactly one caller creates
    the file, everyone else — including a concurrent process on another host
    sharing the filesystem — gets ``EEXIST``.  The lock is written ``0o444``
    and is never removed by this module.  Releasing it is a programme act
    performed by a human with ``rm``, which leaves a trace in the shell that
    did it; there is no API for it here, on purpose.
    """

    def __init__(self, root: CustodyRoot, store_id: str) -> None:
        self.root = root
        self.store_id = store_id

    @property
    def path(self) -> Path:
        return self.root.locks / f"{self.store_id}.lock"

    def is_taken(self) -> bool:
        return self.path.exists()

    def holder(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"__unreadable__": True}

    def acquire(self, *, purpose: str) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "store_id": self.store_id,
            "purpose": purpose,
            "utc": _now_utc(),
            "actor": _actor(),
        }
        try:
            handle = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444
            )
        except FileExistsError:
            raise CustodyViolation(
                f"one-shot lock for {self.store_id!r} is already taken: this "
                f"partition has been opened before (holder: {self.holder()!r}). "
                "A second opening is refused."
            ) from None
        try:
            os.write(handle, _canonical(payload).encode() + b"\n")
            os.fsync(handle)
        finally:
            os.close(handle)
        return payload


# ---------------------------------------------------------------------------
# Seal registry and sealing
# ---------------------------------------------------------------------------


def _registry_entries(root: CustodyRoot) -> list[dict[str, Any]]:
    entries = []
    for line in _read_lines(root.seal_registry):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _registered_stores(root: CustodyRoot) -> dict[str, SealedStore]:
    """Built-in stores plus any store registered later (V3 and beyond)."""

    stores = {store.store_id: store for store in SEALED_STORES}
    for entry in _registry_entries(root):
        if entry.get("event") != "REGISTER":
            continue
        stores.setdefault(
            entry["store_id"],
            SealedStore(
                store_id=entry["store_id"],
                relative_path=entry["relative_path"],
                reason=entry.get("reason", "registered at runtime"),
                kind=entry.get("kind", "FILE"),
            ),
        )
    return stores


def store_for_id(store_id: str,
                 root: str | Path | None = None) -> SealedStore:
    stores = _registered_stores(custody_root(root))
    if store_id not in stores:
        raise CustodyViolation(f"unknown sealed store {store_id!r}")
    return stores[store_id]


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


def is_sealed_path(path: str | Path,
                   root: str | Path | None = None) -> SealedStore | None:
    """Return the sealed store a path denotes, or ``None``.

    Matching is by resolved path *and* by ``(device, inode)``.  The inode
    comparison is what defeats a rename, a hard link or a symlink pointed at
    the store: the bytes are the same bytes, so the answer is the same answer.
    This check never opens the file.
    """

    target = Path(path).resolve()
    identity = _identity(target)
    for store in _registered_stores(custody_root(root)).values():
        candidate = store.path
        if candidate == target:
            return store
        if identity is not None and _identity(candidate) == identity:
            return store
        if store.is_directory and candidate in target.parents:
            return store
    return None


def seal_store(store_id: str, *, root: str | Path | None = None,
               reason: str | None = None,
               relative_path: str | None = None,
               kind: str = "FILE",
               compute_digest: bool = False,
               known_sha256: str | None = None) -> dict[str, Any]:
    """Seal a store to its sealed mode and record it in the registry.

    Sealing never modifies file contents; it changes the permission bits only.

    ``compute_digest`` defaults to **False** on purpose.  Hashing a store reads
    every byte of it, and for a store that carries holdout item text that is
    the very act incident 306 recorded as the violation
    (``item_text_bytes_read = true``).  Sealing must not require committing the
    offence it prevents.  Where a digest is already known from an independent
    frozen record, pass it as ``known_sha256``; the registry then pins it
    without anyone reading the store.  Only a caller that legitimately holds
    the bytes already — the process that wrote the store — should pass
    ``compute_digest=True``.
    """

    croot = custody_root(root).ensure()
    stores = _registered_stores(croot)
    if store_id in stores:
        store = stores[store_id]
    elif relative_path is not None:
        store = SealedStore(store_id=store_id, relative_path=relative_path,
                            reason=reason or "registered at seal time",
                            kind=kind)
    else:
        raise CustodyViolation(
            f"unknown sealed store {store_id!r} and no relative_path given"
        )

    path = store.path
    if not path.exists():
        raise CustodyViolation(f"cannot seal a store that does not exist: {path}")

    sealed_mode = store.sealed_mode
    before_mode = stat.S_IMODE(path.stat().st_mode)
    digest = known_sha256
    if (digest is None and compute_digest and not store.is_directory
            and before_mode != sealed_mode):
        digest = _sha256_file(path)
    os.chmod(path, sealed_mode)
    after_mode = stat.S_IMODE(path.stat().st_mode)
    if after_mode != sealed_mode:
        raise CustodyViolation(f"seal did not take on {path}: mode {after_mode:o}")

    entry = {
        "event": "REGISTER",
        "store_id": store.store_id,
        "relative_path": store.relative_path,
        "reason": store.reason,
        "kind": store.kind,
        "utc": _now_utc(),
        "mode_before": f"0o{before_mode:o}",
        "mode_after": f"0o{after_mode:o}",
        "sha256": digest,
        "digest_source": (
            "known_sha256_from_frozen_record" if known_sha256 is not None
            else ("computed_at_seal_time" if digest is not None
                  else "not_taken_hashing_would_read_holdout_bytes")
        ),
        "device": path.stat().st_dev,
        "inode": path.stat().st_ino,
        "bytes": path.stat().st_size,
        "actor": _actor(),
    }
    _append_line(croot.seal_registry, _canonical(entry), mode=LEDGER_MODE)
    FirstAccessLog(croot).append(
        store_id=store.store_id, event="SEAL",
        detail={"mode_before": entry["mode_before"], "sha256": digest},
    )
    return entry


def registry_digest(store_id: str,
                    root: str | Path | None = None) -> str | None:
    """The digest recorded when the store was sealed, if any."""

    for entry in reversed(_registry_entries(custody_root(root))):
        if entry.get("store_id") == store_id and entry.get("sha256"):
            return entry["sha256"]
    return None


def verify_seals(root: str | Path | None = None) -> dict[str, Any]:
    """Report the live mode of every registered store.  Reports, never repairs."""

    croot = custody_root(root)
    rows = []
    for store in _registered_stores(croot).values():
        path = store.path
        if not path.exists():
            rows.append({"store_id": store.store_id, "present": False,
                         "sealed": None, "mode": None})
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        rows.append({
            "store_id": store.store_id,
            "present": True,
            "path": str(path),
            "kind": store.kind,
            "mode": f"0o{mode:o}",
            "sealed": mode == store.sealed_mode,
        })
    rows.sort(key=lambda row: row["store_id"])
    breached = [row["store_id"] for row in rows
                if row["present"] and not row["sealed"]]
    return {"stores": rows, "all_sealed": not breached, "breached": breached}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

#: Stores this process unsealed and has not yet resealed.  A crash between
#: unseal and reseal must not leave a holdout store readable.
_UNSEALED: dict[str, tuple[Path, int]] = {}


def _reseal_all() -> None:
    for path, mode in list(_UNSEALED.values()):
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
    _UNSEALED.clear()


atexit.register(_reseal_all)


@contextlib.contextmanager
def open_sealed_partition(
    store_id: str,
    *,
    purpose: str,
    prerequisites: Mapping[str, bool],
    root: str | Path | None = None,
    expect_sha256: str | None = None,
) -> Iterator[Path]:
    """The only sanctioned way to reach a sealed store.

    The order of operations is the control.  Nothing that could fail after the
    store becomes readable is allowed to run before it:

    1. the path is confirmed to be a registered sealed store;
    2. the first-access log is verified from genesis — a broken ledger blocks
       the open, because an open that cannot be recorded truthfully must not
       happen;
    3. every prerequisite must be ``True`` (the code-enforced prerequisite
       check required by incident 306); a failure is itself appended to the
       ledger as ``DENIED`` and does *not* consume the one-shot lock;
    4. the one-shot lock is taken with ``O_EXCL``;
    5. the ``OPEN`` record is appended and ``fsync``-ed **while the store is
       still at mode 0**, and the observed mode goes into the record, so the
       ledger proves the log preceded the read;
    6. only then is the store unsealed to ``0o400``;
    7. the store is resealed and a ``CLOSE`` record appended on the way out,
       whatever happened in between.

    Steps 4 and 5 are deliberately ordered so that a failure to write the
    ledger burns the lock.  Burning a partition is a recoverable programme
    cost; reading one without a record is not.
    """

    croot = custody_root(root).ensure()
    store = store_for_id(store_id, root=root)
    path = store.path
    log = FirstAccessLog(croot)

    if not path.exists():
        raise CustodyViolation(f"sealed store {store_id!r} is missing: {path}")

    chain = log.verify()
    if not chain["verified"]:
        raise CustodyViolation(
            "first-access log does not verify; refusing to open "
            f"{store_id!r}: {chain['reason']}"
        )

    failed = sorted(name for name, ok in prerequisites.items() if not ok)
    if failed or not prerequisites:
        log.append(store_id=store_id, event="DENIED",
                   detail={"purpose": purpose, "reason": "prerequisites",
                           "failed": failed,
                           "declared": sorted(prerequisites)})
        raise CustodyViolation(
            f"prerequisites not satisfied for {store_id!r}: "
            f"{failed or 'no prerequisites were declared'}"
        )

    lock = OneShotLock(croot, store_id)
    try:
        lock.acquire(purpose=purpose)
    except CustodyViolation:
        log.append(store_id=store_id, event="DENIED",
                   detail={"purpose": purpose, "reason": "one_shot_lock_taken",
                           "holder": lock.holder()})
        raise

    mode_at_log_time = stat.S_IMODE(path.stat().st_mode)
    log.append(
        store_id=store_id, event="OPEN",
        detail={
            "purpose": purpose,
            "path": str(path),
            "store_mode_at_log_time": f"0o{mode_at_log_time:o}",
            "store_was_sealed_when_logged": (
                mode_at_log_time == store.sealed_mode
            ),
            "prerequisites": sorted(prerequisites),
        },
    )

    digest: str | None = None
    try:
        os.chmod(path, store.open_mode)
        _UNSEALED[store_id] = (path, store.sealed_mode)
        if not store.is_directory:
            digest = _sha256_file(path)
            pinned = expect_sha256 or registry_digest(store_id, root=root)
            if pinned is not None and digest != pinned:
                raise CustodyViolation(
                    f"sealed store {store_id!r} does not match its pinned "
                    f"digest: {digest} != {pinned}"
                )
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.chmod(path, store.sealed_mode)
        _UNSEALED.pop(store_id, None)
        log.append(store_id=store_id, event="CLOSE",
                   detail={"purpose": purpose, "sha256": digest,
                           "resealed_mode": f"0o{SEALED_MODE:o}"})


def unseal_store_for_audit(store_id: str, *, purpose: str,
                           prerequisites: Mapping[str, bool],
                           root: str | Path | None = None):
    """Alias kept explicit so the call site names what it is doing."""

    return open_sealed_partition(store_id, purpose=purpose,
                                 prerequisites=prerequisites, root=root)


# ---------------------------------------------------------------------------
# The adjudication read — the second gate, and the one that is not an opening
# ---------------------------------------------------------------------------

#: The event class :func:`open_sealed_partition` writes.  Named here so the
#: distinction A8 requires between "the partition was opened" and "evidence for
#: named units was handed to a packet builder" is a constant a reader can grep,
#: not a string literal repeated in two places.
SEALED_OPENING_EVENT = "OPEN"

#: The event class :func:`read_units_for_adjudication` writes.
ADJUDICATION_READ_EVENT = "ADJUDICATION_READ"

#: Appended on the way out of an adjudication read, whatever happened.
ADJUDICATION_READ_CLOSE_EVENT = "ADJUDICATION_READ_CLOSE"

#: Appended before an adjudication read is refused, so a refusal is evidence
#: rather than an absence.  Deliberately NOT ``DENIED``: an opening denial and
#: an adjudication denial are different facts about a partition.
ADJUDICATION_DENIED_EVENT = "ADJUDICATION_DENIED"


def partition_identity_sha256(unit_ids: Sequence[str]) -> str:
    """``DRAW_RECORD.json`` STEP_10: ``sha256(join(sorted(unit_ids), '|'))``.

    The same definition the V3 packet builder pins its identity list to, so a
    custody record and a builder record commit to the same object and a third
    party recomputes either from a sorted list of identifiers.  It is a
    commitment, not a disclosure: the ledger carries this digest and a count,
    never the list.
    """

    return hashlib.sha256(
        "|".join(sorted(unit_ids)).encode("utf-8")).hexdigest()


def assert_partition_membership(partition_id: str,
                                unit_ids: Sequence[str] | set[str], *,
                                what: str,
                                root: str | Path | None = None
                                ) -> dict[str, Any]:
    """Refuse unless every identifier is a member of the named partition.

    This is the inverse of :func:`assert_not_holdout` and it is deliberately
    built on the same two registration forms, so a blinded partition is exactly
    as testable as a plaintext one and neither answer requires unsealing the
    partition's identity store:

    * a plaintext registration is tested by set membership;
    * a blinded registration is tested by recomputing each candidate's
      commitment under that partition's salt.

    Three refusals, all fail-closed:

    * a partition with no registration at all is not a partition this layer
      knows about, and an unregistered partition cannot authorise anything;
    * a blinded partition whose salt is missing is *untestable*, and an
      untestable register refuses rather than passes — the rule
      :func:`assert_not_holdout` already applies in the other direction;
    * an identifier that is not a member is refused by count.

    The refusal names counts and partitions, never identifiers.  A refusal
    message is read by whoever ran the job, and a message that echoes the
    offending identity is a disclosure channel out of the partition it just
    protected.
    """

    if not isinstance(partition_id, str) or not partition_id:
        raise CustodyViolation(
            f"{what}: partition_id must be a single non-empty string.  One "
            "call names exactly one partition, so no call can span the "
            "prospective and the sealed partition at once."
        )

    croot = custody_root(root)
    candidates = set(unit_ids)
    if not candidates:
        raise CustodyViolation(
            f"{what}: an empty identity list is refused.  Authorisation must "
            "be positive and explicit."
        )

    plaintext = holdout_unit_ids(root).get(partition_id, set())
    committed = holdout_commitments(root).get(partition_id, set())
    if not plaintext and not committed:
        raise CustodyViolation(
            f"{what}: {partition_id!r} is not a registered holdout partition.  "
            "Only a partition registered through register_holdout_units() can "
            "authorise an adjudication read, because only a registration says "
            "which identities the partition contains."
        )

    salt = _load_salts(croot).get(partition_id) if committed else None
    if committed and salt is None:
        raise CustodyViolation(
            f"{what}: {partition_id!r} carries a blinded holdout registration "
            "whose salt is missing, so membership cannot be tested.  An "
            "untestable register refuses; it does not pass."
        )

    outsiders = sum(
        1 for uid in candidates
        if uid not in plaintext
        and not (salt is not None and _commit(salt, uid) in committed)
    )
    if outsiders:
        raise CustodyViolation(
            f"{what}: {outsiders} of {len(candidates)} identities are not "
            f"members of partition {partition_id!r}.  A packet may be built "
            "only for the units of the partition being adjudicated "
            "(V3_ADJUDICATION_PROTOCOL.json A3 P1); an identity list that "
            "reaches outside it is refused whole, and no store was opened."
        )

    return {
        "partition_id": partition_id,
        "checked": len(candidates),
        "registered_units": len(plaintext) + len(committed),
        "registration_forms": sorted(
            (["PLAINTEXT_IDENTITY_LIST"] if plaintext else [])
            + (["BLINDED_COMMITMENT"] if committed else [])
        ),
        "outsiders": 0,
    }


def _assert_identity_list(identity_list: Sequence[str], *,
                          what: str) -> list[str]:
    """Validate the one unit-selecting input, before anything is opened.

    A ``str`` is a ``Sequence[str]`` and would silently authorise its own
    characters, so it is refused by type.  ``None`` is refused rather than read
    as "everything".  Only a materialised collection is accepted: a path, a
    file handle, a generator or any other lazy source is refused, because the
    authorisation has to be a list somebody wrote down, not a stream that
    produces one on demand.  Duplicates are refused so that the recorded
    identity commitment is the commitment of exactly the list that was passed.
    """

    if identity_list is None or isinstance(identity_list, (str, bytes)):
        raise CustodyViolation(
            f"{what}: identity_list must be an explicit sequence of unit "
            "identifiers.  There is no call shape that means 'every unit'."
        )
    if not isinstance(identity_list, (list, tuple, set, frozenset)):
        raise CustodyViolation(
            f"{what}: identity_list must be a materialised list, tuple or set "
            f"of unit identifiers, not {type(identity_list).__name__}.  A lazy "
            "or file-backed source is refused: there is no call shape that "
            "means 'every unit'."
        )
    ids = list(identity_list)
    if not ids:
        raise CustodyViolation(
            f"{what}: an empty identity list is refused.  Authorisation must "
            "be positive and explicit; an empty list is not 'no restriction'."
        )
    if any(not isinstance(uid, str) or not uid for uid in ids):
        raise CustodyViolation(
            f"{what}: every entry of identity_list must be a non-empty string"
        )
    if len(set(ids)) != len(ids):
        raise CustodyViolation(
            f"{what}: identity_list repeats an identifier; refusing rather "
            "than silently de-duplicating an authorisation"
        )
    return ids


def read_units_for_adjudication(
    identity_list: Sequence[str],
    *,
    store_id: str,
    partition_id: str,
    purpose: str,
    prerequisites: Mapping[str, bool],
    expect_identity_sha256: str | None = None,
    root: str | Path | None = None,
) -> dict[str, dict]:
    """Hand the evidence of an explicitly named identity list to a builder.

    This is the path that did not exist, and its absence made lawful V3
    adjudication impossible in both directions:
    :func:`load_authorised_packets` refuses every registered holdout identity —
    which every drawn V3 unit is — and :func:`open_sealed_partition` would
    permit the read but consumes the one-shot lock that
    ``V3_ADJUDICATION_PROTOCOL.json`` A8 reserves for the single sealed
    OPENING.  Preserving a partition that can never be adjudicated preserves
    nothing.

    It is a *narrower* operation than an opening, not a looser one:

    1. ``identity_list`` is the ONLY unit-selecting input.  It is positional,
       required, has no default, and there is no partition-wide, store-wide or
       "all units" call shape anywhere on this path.  ``None``, a bare string,
       an empty list and a list with duplicates are all refused.
    2. Every identity must be a member of ``partition_id``, tested against the
       holdout register in whichever form it was recorded.  ``partition_id`` is
       one string, so a single call cannot span the prospective and the sealed
       partition, and a list that mixes them is refused as a membership
       failure before any store is touched.
    3. The store must be a *registered* sealed store.  An unregistered path is
       refused by :func:`store_for_id`.
    4. The one-shot lock is **read and never taken**.  Its observed state goes
       into the record as evidence that the adjudication did not consume the
       opening.  A8: the lock guards the OPENING, not the adjudication.
    5. The ``ADJUDICATION_READ`` record is appended and ``fsync``-ed while the
       store is still sealed, with the UTC time, the partition, the identity
       count, the identity commitment and the purpose — a count and a digest,
       never the list, so the ledger does not republish what the blinded
       registration was written to stop publishing.
    6. Only then is the store unsealed to ``0o400``, and selection happens on
       the identifier: a record outside the list is never deserialized.
    7. The store is resealed and an ``ADJUDICATION_READ_CLOSE`` record appended
       on the way out, whatever happened in between.

    ``prerequisites`` is required, must be non-empty, and every value must be
    ``True``.  It is deliberately the adjudication's OWN checklist and not the
    opening checklist: ``OPENING_PRECONDITIONS.json`` C-05 gates the opening,
    and A7 requires the reference to be frozen BEFORE the partition is opened,
    so gating the adjudication read on the opening verifier would make lawful
    adjudication impossible for the second time.  A refused prerequisite is
    appended as ``ADJUDICATION_DENIED`` and consumes nothing.

    WHAT THIS FUNCTION DOES NOT ENFORCE, stated so nothing downstream claims
    it does: A8's ordering — the SEALED reference is frozen BEFORE the
    PROSPECTIVE partition is opened — is not enforceable here.  "The reference
    is frozen" is not a custody event; this layer never sees a reference.  What
    it provides is the evidence a verifier needs: every read is a sequenced,
    hash-chained record naming its partition and its time, and every opening is
    a distinct record in the same chain.  The layer that must enforce the
    ordering is the C-05 code-enforced prerequisite verifier that gates the
    openings, which has to carry "the sealed reference is frozen" as one of its
    prerequisites.  Today no reference-freeze event class exists in this
    ledger, so that prerequisite has no evidence channel yet; that gap is real
    and is recorded rather than papered over.

    Returns a mapping of ``unit_id`` to the deserialized record, exactly the
    shape :func:`load_authorised_packets` returns, so a one-identity-at-a-time
    reader can be built over it without any other change.
    """

    what = f"adjudication read {purpose!r}"
    ids = _assert_identity_list(identity_list, what=what)
    observed_identity_sha256 = partition_identity_sha256(ids)
    if (expect_identity_sha256 is not None
            and observed_identity_sha256 != expect_identity_sha256):
        raise CustodyViolation(
            f"{what}: the identity list does not match its pinned commitment "
            f"({observed_identity_sha256} != {expect_identity_sha256}).  "
            "Refusing before any store is touched."
        )

    croot = custody_root(root).ensure()
    store = store_for_id(store_id, root=root)
    path = store.path
    if not path.exists():
        raise CustodyViolation(f"sealed store {store_id!r} is missing: {path}")
    if store.is_directory:
        raise CustodyViolation(
            f"{what}: {store_id!r} is a DIRECTORY store.  An adjudication read "
            "selects records by identifier inside one file; unsealing a "
            "directory would grant search permission over everything beneath "
            "it, which is an opening by another name."
        )

    log = FirstAccessLog(croot)
    chain = log.verify()
    if not chain["verified"]:
        raise CustodyViolation(
            "first-access log does not verify; refusing an adjudication read "
            f"of {store_id!r}: {chain['reason']}"
        )

    def _deny(reason: str, detail: Mapping[str, Any]) -> None:
        log.append(store_id=store_id, event=ADJUDICATION_DENIED_EVENT,
                   detail={"purpose": purpose, "partition_id": partition_id,
                           "identity_count": len(ids),
                           "identity_sha256": observed_identity_sha256,
                           "reason": reason, **dict(detail)})

    try:
        membership = assert_partition_membership(
            partition_id, ids, what=what, root=root)
    except CustodyViolation as refusal:
        _deny("partition_membership", {"refusal": str(refusal)[:400]})
        raise

    failed = sorted(name for name, ok in prerequisites.items() if not ok)
    if failed or not prerequisites:
        _deny("prerequisites", {"failed": failed,
                                "declared": sorted(prerequisites)})
        raise CustodyViolation(
            f"{what}: adjudication prerequisites not satisfied for "
            f"{partition_id!r}: {failed or 'no prerequisites were declared'}"
        )

    lock = OneShotLock(croot, store_id)
    lock_taken_before = lock.is_taken()

    mode_at_log_time = stat.S_IMODE(path.stat().st_mode)
    log.append(
        store_id=store_id, event=ADJUDICATION_READ_EVENT,
        detail={
            "purpose": purpose,
            "partition_id": partition_id,
            "identity_count": len(ids),
            "identity_sha256": observed_identity_sha256,
            "identity_commitment_scheme":
                "sha256(join(sorted(unit_ids), '|'))",
            "path": str(path),
            "store_mode_at_log_time": f"0o{mode_at_log_time:o}",
            "store_was_sealed_when_logged": (
                mode_at_log_time == store.sealed_mode
            ),
            "one_shot_lock_taken_before": lock_taken_before,
            "one_shot_lock_consumed_by_this_read": False,
            "registration_forms": membership["registration_forms"],
            "prerequisites": sorted(prerequisites),
            "event_class_note": (
                "ADJUDICATION_READ is not an opening.  A8 reserves the "
                "one-shot lock for the single sealed OPENING and this read "
                "does not touch it."
            ),
        },
    )

    allowed = set(ids)
    rows: dict[str, dict] = {}
    completed = False
    try:
        os.chmod(path, store.open_mode)
        _UNSEALED[store_id] = (path, store.sealed_mode)
        with path.open() as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                unit_id = _scan_unit_id(line, number)
                if unit_id not in allowed:
                    continue  # never deserialized, never materialized
                if unit_id in rows:
                    raise CustodyViolation(
                        f"{what}: {path} repeats an authorised identity at "
                        f"line {number}"
                    )
                record = json.loads(line)
                if record.get("unit_id") != unit_id:
                    raise CustodyViolation(
                        f"{what}: line {number} identifier scan disagrees with "
                        "the deserialized record; refusing"
                    )
                rows[unit_id] = record
        missing = len(allowed - set(rows))
        if missing:
            raise CustodyViolation(
                f"{what}: {missing} authorised identities are absent from "
                f"{store_id!r}; refusing a partial authorisation rather than "
                "silently shipping less than was authorised"
            )
        completed = True
    finally:
        with contextlib.suppress(OSError):
            os.chmod(path, store.sealed_mode)
        _UNSEALED.pop(store_id, None)
        log.append(
            store_id=store_id, event=ADJUDICATION_READ_CLOSE_EVENT,
            detail={
                "purpose": purpose,
                "partition_id": partition_id,
                "identity_count": len(ids),
                "identity_sha256": observed_identity_sha256,
                "records_returned": len(rows),
                "completed": completed,
                "resealed_mode": f"0o{store.sealed_mode:o}",
                "one_shot_lock_taken_after": lock.is_taken(),
            },
        )
    return rows


def adjudication_read_records(*, store_id: str | None = None,
                              partition_id: str | None = None,
                              root: str | Path | None = None
                              ) -> list[dict[str, Any]]:
    """Every ``ADJUDICATION_READ`` record, optionally filtered.

    Reports, never repairs, and never resolves anything the ledger does not
    already say.  Callers that need to know whether the chain verifies must ask
    :meth:`FirstAccessLog.verify` or :class:`AccessObserver`; a list of records
    from an unverified chain is not evidence and this function does not pretend
    otherwise by returning an empty list.
    """

    out = []
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != ADJUDICATION_READ_EVENT:
            continue
        if store_id is not None and record.get("store_id") != store_id:
            continue
        detail = record.get("detail") or {}
        if partition_id is not None and detail.get("partition_id") != partition_id:
            continue
        out.append(record)
    return out


# ---------------------------------------------------------------------------
# Access observer
# ---------------------------------------------------------------------------


class AccessObserver:
    """Answers "has this partition been read, and by whom" with evidence.

    The answer is a tri-state, never a bare boolean, because the only
    interesting failure mode is a ledger that has been tampered with.  A
    tampered ledger reports ``UNKNOWN_LOG_TAMPERED``; it never reports ``NO``.

    Two questions are answered, not one, because
    ``V3_ADJUDICATION_PROTOCOL.json`` A8 makes them different facts:
    ``has_been_read`` is about the sealed OPENING and counts ``OPEN`` events
    only, and ``has_been_read_for_adjudication`` counts ``ADJUDICATION_READ``
    events.  Both are the same tri-state and both are gated on the same chain
    verification, so neither can answer ``NO`` from a ledger that does not
    verify.  Reporting one number for both would either understate an
    adjudication or overstate an opening, and the whole point of the sealed
    partition is that those are not the same event.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = custody_root(root)
        self.log = FirstAccessLog(self.root)

    def report(self, store_id: str) -> dict[str, Any]:
        chain = self.log.verify()
        events = self.log.events_for(store_id)
        opens = [e for e in events if e.get("event") == "OPEN"]
        closes = [e for e in events if e.get("event") == "CLOSE"]
        denials = [e for e in events if e.get("event") == "DENIED"]
        adjudication_reads = [e for e in events
                              if e.get("event") == ADJUDICATION_READ_EVENT]
        adjudication_closes = [
            e for e in events
            if e.get("event") == ADJUDICATION_READ_CLOSE_EVENT
        ]
        adjudication_denials = [e for e in events
                                if e.get("event") == ADJUDICATION_DENIED_EVENT]
        lock = OneShotLock(self.root, store_id)

        try:
            store = store_for_id(store_id, root=self.root.root)
            path = store.path
            present = path.exists()
            mode = (f"0o{stat.S_IMODE(path.stat().st_mode):o}"
                    if present else None)
            sealed = (stat.S_IMODE(path.stat().st_mode) == store.sealed_mode
                      if present else None)
        except CustodyViolation:
            present, mode, sealed = None, None, None

        if not chain["verified"]:
            answer = "UNKNOWN_LOG_TAMPERED"
            adjudication_answer = "UNKNOWN_LOG_TAMPERED"
        else:
            answer = "YES" if opens else "NO"
            adjudication_answer = "YES" if adjudication_reads else "NO"

        readers = [
            {
                "utc": event.get("utc"),
                "purpose": (event.get("detail") or {}).get("purpose"),
                "pid": (event.get("actor") or {}).get("pid"),
                "uid": (event.get("actor") or {}).get("uid"),
                "executable": (event.get("actor") or {}).get("executable"),
                "argv": (event.get("actor") or {}).get("argv"),
                "record_hash": event.get("record_hash"),
            }
            for event in opens
        ]

        return {
            "store_id": store_id,
            "has_been_read": answer,
            "has_been_read_semantics": (
                "OPEN events only — the sealed opening. An ADJUDICATION_READ "
                "is a different event class (A8) and is reported separately "
                "under has_been_read_for_adjudication; a store whose units "
                "were read for adjudication answers NO here and YES there."
            ),
            "has_been_read_for_adjudication": adjudication_answer,
            "adjudication_read": {
                "count": len(adjudication_reads),
                "close_count": len(adjudication_closes),
                "unterminated": len(adjudication_reads) > len(adjudication_closes),
                "denied_count": len(adjudication_denials),
                "reads": [
                    {
                        "utc": event.get("utc"),
                        "partition_id": (event.get("detail") or {})
                                        .get("partition_id"),
                        "identity_count": (event.get("detail") or {})
                                          .get("identity_count"),
                        "identity_sha256": (event.get("detail") or {})
                                           .get("identity_sha256"),
                        "purpose": (event.get("detail") or {}).get("purpose"),
                        "one_shot_lock_taken_before": (event.get("detail") or {})
                                                      .get("one_shot_lock_taken_before"),
                        "pid": (event.get("actor") or {}).get("pid"),
                        "record_hash": event.get("record_hash"),
                    }
                    for event in adjudication_reads
                ],
            },
            "read_by": readers,
            "open_count": len(opens),
            "close_count": len(closes),
            "unterminated_open": len(opens) > len(closes),
            "denied_attempts": [
                {"utc": e.get("utc"),
                 "reason": (e.get("detail") or {}).get("reason"),
                 "pid": (e.get("actor") or {}).get("pid")}
                for e in denials
            ],
            "one_shot_lock": {
                "taken": lock.is_taken(),
                "path": str(lock.path),
                "holder": lock.holder(),
            },
            "store": {"present": present, "mode": mode, "sealed": sealed},
            "log": {
                "path": str(self.root.access_log),
                "verified": chain["verified"],
                "reason": chain["reason"],
                "record_count": chain["record_count"],
                "head": chain["head"],
            },
            "evidence_basis": (
                "hash-chained append-only ledger verified from genesis and "
                "cross-checked against a separate heads ledger; lock state "
                "read from the filesystem; store mode read from the inode"
            ),
        }

    def report_all(self) -> dict[str, Any]:
        stores = sorted(_registered_stores(self.root))
        return {
            "utc": _now_utc(),
            "stores": {sid: self.report(sid) for sid in stores},
            "seals": verify_seals(self.root.root),
        }


# ---------------------------------------------------------------------------
# Exposure ledger, holdout register and eligibility
# ---------------------------------------------------------------------------

#: The ways a unit can stop being a lawful holdout candidate.  ``REFERENCED``
#: — "it has a reference answer" — is only one of them, and it is the weakest.
#: The V2 partition was drawn from units defined solely as ``not REFERENCED``,
#: roughly two and a half hours after 600 full item-text packets had already
#: been delivered to three blind seats.  ``SHARDED`` and ``JUDGED`` are what
#: that definition missed, and ``READ`` is what incident 306 missed.
EXPOSURE_KINDS: tuple[str, ...] = ("READ", "SHARDED", "JUDGED", "REFERENCED")


def declare_ledger_scope(corpus_id: str, *, note: str,
                         root: str | Path | None = None) -> dict[str, Any]:
    """Declare that the exposure ledger covers a corpus from now on.

    :func:`assert_eligible_for_holdout` refuses to answer for a corpus that has
    no scope declaration.  An empty ledger is not evidence of non-exposure; it
    is usually evidence that nobody was recording.  Requiring the declaration
    makes "we have been watching this corpus since it was built" an explicit,
    dated, hash-chained claim rather than an inference from silence.
    """

    croot = custody_root(root).ensure()
    return FirstAccessLog(croot).append(
        store_id=f"CORPUS:{corpus_id}", event="LEDGER_SCOPE",
        detail={"corpus_id": corpus_id, "note": note},
    )


def _ledger_scopes(root: str | Path | None = None) -> set[str]:
    log = FirstAccessLog(custody_root(root))
    return {
        (record.get("detail") or {}).get("corpus_id")
        for record in log.records()
        if record.get("event") == "LEDGER_SCOPE"
    }


def record_exposure(kind: str, unit_ids: Sequence[str] | set[str], *,
                    corpus_id: str, detail: Mapping[str, Any] | None = None,
                    blinded: bool = False, partition_id: str | None = None,
                    root: str | Path | None = None) -> dict[str, Any]:
    """Record that these units have been exposed, and how.

    Identifiers only.  This ledger never carries item text, which is why it
    can be consulted by code that has no right to read the store.

    ``blinded`` mirrors :func:`register_holdout_units` and exists for one
    reason: the moment adjudication starts, the units being exposed are the
    units of a holdout partition, and a plaintext ``SHARDED`` or ``JUDGED``
    record would republish in the shared ledger exactly the membership that
    ``LEDGER_EXPOSURE_REPAIR.json`` blinded the registration to stop
    publishing.  A blinded exposure writes a count and a sorted list of salted
    commitments under the partition's own salt — the SAME salt as its
    registration, so the two records commit to the same identities and
    :func:`assert_eligible_for_holdout` can still resolve them.

    ``partition_id`` names the commitment scope and is REQUIRED when
    ``blinded`` is true, because the salt is per partition.  It is REFUSED when
    ``blinded`` is false: a record that names a partition and lists its
    identities in plaintext is the disclosure this form exists to prevent, and
    offering it as an option is offering the defect.

    The blinded form hides membership from a reader of the ledger; it does not
    make membership unknowable to an actor who can enumerate the candidate
    identity space and also read the salt file.  That is the same guarantee
    :func:`register_holdout_units` states, and it is not enlarged here.
    """

    if kind not in EXPOSURE_KINDS:
        raise CustodyViolation(
            f"unknown exposure kind {kind!r}; expected one of {EXPOSURE_KINDS}"
        )
    if blinded and not (isinstance(partition_id, str) and partition_id):
        raise CustodyViolation(
            "a blinded exposure must name the partition_id it commits under: "
            "the commitment salt is per partition, and a commitment nobody can "
            "resolve is an exposure record that cannot refuse anything"
        )
    if partition_id is not None and not blinded:
        raise CustodyViolation(
            f"exposure of partition {partition_id!r} was requested in the "
            "plaintext form.  A partition-scoped exposure is recorded blinded "
            "or not at all: writing the identity list of a holdout partition "
            "into the shared ledger republishes the membership that the "
            "blinded registration exists to withhold."
        )

    ids = sorted(set(unit_ids))
    croot = custody_root(root).ensure()
    body: dict[str, Any] = {
        "corpus_id": corpus_id,
        "kind": kind,
        "unit_count": len(ids),
    }
    if blinded:
        salt = _load_salts(croot).get(partition_id) or hashlib.sha256(
            os.urandom(32)).hexdigest()
        _store_salt(croot, partition_id, salt)
        body["commitment_scope"] = partition_id
        body["unit_id_commitments"] = sorted(_commit(salt, uid) for uid in ids)
        body["commitment_scheme"] = HOLDOUT_COMMITMENT_SCHEME
        body["salt_location"] = HOLDOUT_SALT_FILE
        body["disclosure"] = (
            "This record commits to the exposed units without disclosing "
            "them. assert_eligible_for_holdout() resolves the commitments; "
            "nothing else does."
        )
    else:
        body["unit_ids"] = ids
    return FirstAccessLog(croot).append(
        store_id=f"CORPUS:{corpus_id}", event="EXPOSURE",
        detail={**body, **dict(detail or {})},
    )


def exposed_unit_ids(*, corpus_id: str | None = None,
                     kinds: Sequence[str] | None = None,
                     root: str | Path | None = None) -> dict[str, set[str]]:
    """Every unit the ledger has ever seen exposed, grouped by kind.

    Blinded exposures contribute no identifiers here, by construction — the
    same property :func:`holdout_unit_ids` has.  Use
    :func:`exposed_commitments` to see that they exist, and
    :func:`assert_eligible_for_holdout`, which resolves both forms, to test a
    candidate against them.  Reading only this function and concluding "not
    exposed" is exactly the inference a blinded record must not support, which
    is why the eligibility predicate does not rely on it alone.
    """

    wanted = set(kinds or EXPOSURE_KINDS)
    out: dict[str, set[str]] = {kind: set() for kind in wanted}
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != "EXPOSURE":
            continue
        detail = record.get("detail") or {}
        if corpus_id is not None and detail.get("corpus_id") != corpus_id:
            continue
        kind = detail.get("kind")
        if kind in wanted:
            out[kind].update(detail.get("unit_ids") or [])
    return out


def exposed_commitments(*, corpus_id: str | None = None,
                        kinds: Sequence[str] | None = None,
                        root: str | Path | None = None
                        ) -> dict[str, dict[str, set[str]]]:
    """Blinded exposure commitments, grouped by kind and commitment scope.

    Opaque digests only.  ``{kind: {partition_id: {commitment, ...}}}``.
    """

    wanted = set(kinds or EXPOSURE_KINDS)
    out: dict[str, dict[str, set[str]]] = {kind: {} for kind in wanted}
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != "EXPOSURE":
            continue
        detail = record.get("detail") or {}
        if corpus_id is not None and detail.get("corpus_id") != corpus_id:
            continue
        kind = detail.get("kind")
        commitments = detail.get("unit_id_commitments")
        if kind not in wanted or not commitments:
            continue
        scope = detail.get("commitment_scope")
        out[kind].setdefault(scope, set()).update(commitments)
    return out


#: How a blinded holdout registration commits to an identity.  Declared as a
#: constant so a reader of one ledger record can recompute the commitment
#: without reading this module.
HOLDOUT_COMMITMENT_SCHEME = "sha256(salt + '|' + unit_id)"

#: Where per-partition commitment salts live: one JSON object in the custody
#: root, at :data:`LEDGER_MODE`.  The salt is deliberately NOT in the ledger
#: record.  A ledger is the artifact people copy into reports, quote in
#: verifications and hand to auditors; a salt that travels with the commitment
#: turns every such copy back into a membership oracle.  Splitting them means
#: publishing the ledger publishes a count and an opaque digest list, and
#: nothing else.
HOLDOUT_SALT_FILE = "holdout_commitment_salts.json"


def _salt_path(croot: CustodyRoot) -> Path:
    return croot.root / HOLDOUT_SALT_FILE


def _commit(salt: str, unit_id: str) -> str:
    return hashlib.sha256(f"{salt}|{unit_id}".encode()).hexdigest()


def _load_salts(croot: CustodyRoot) -> dict[str, str]:
    path = _salt_path(croot)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        raise CustodyViolation(
            f"holdout commitment salt file is malformed: {path}"
        ) from None


def _store_salt(croot: CustodyRoot, partition_id: str, salt: str) -> None:
    path = _salt_path(croot)
    salts = _load_salts(croot)
    if salts.get(partition_id) not in (None, salt):
        raise CustodyViolation(
            f"a different commitment salt is already recorded for "
            f"{partition_id!r}; refusing to overwrite it, because the recorded "
            "commitments would stop resolving"
        )
    salts[partition_id] = salt
    path.write_text(json.dumps(salts, indent=1, sort_keys=True) + "\n")
    os.chmod(path, LEDGER_MODE)


def register_holdout_units(partition_id: str,
                           unit_ids: Sequence[str] | set[str], *,
                           corpus_id: str,
                           blinded: bool = False,
                           identity_store: str | None = None,
                           root: str | Path | None = None) -> dict[str, Any]:
    """Declare a set of identifiers to be held out.

    Once registered, :func:`assert_not_holdout` refuses to let those units be
    sharded, delivered or otherwise handled by code that is not going through
    the gate.  This is the register the shard builder consults.

    ``blinded`` controls what the ledger record itself carries.

    ``False`` (the default, and what every historical record used) writes the
    sorted identity list into the record.  That is what makes a partition's
    membership readable from the ledger without unsealing the partition —
    FINDING_2 of ``artifacts/curunir_v6_readiness/v3_draw_verification/
    V3_DRAW_VERIFICATION.json``.

    ``True`` writes a count and a sorted list of salted commitments instead,
    and the identity list then exists only in the partition's own physically
    separate store, which is where ``V3_SAMPLING_RULE.json`` S5 STEP_10 says it
    belongs.  The guarantee this buys is exact and worth stating precisely: the
    ledger no longer *discloses* membership, and membership remains *testable*
    by :func:`assert_not_holdout`, which holds the salt.  It does not make
    membership unknowable to an actor who can independently enumerate the
    candidate identity space and also read the salt file.

    ``identity_store`` records where the identity list does live, so the
    blinded record is self-describing.
    """

    ids = sorted(set(unit_ids))
    croot = custody_root(root).ensure()
    detail: dict[str, Any] = {
        "partition_id": partition_id,
        "corpus_id": corpus_id,
        "unit_count": len(ids),
    }
    if blinded:
        salt = _load_salts(croot).get(partition_id) or hashlib.sha256(
            os.urandom(32)).hexdigest()
        _store_salt(croot, partition_id, salt)
        detail["unit_id_commitments"] = sorted(_commit(salt, uid) for uid in ids)
        detail["commitment_scheme"] = HOLDOUT_COMMITMENT_SCHEME
        detail["salt_location"] = HOLDOUT_SALT_FILE
        detail["identity_list_location"] = (
            identity_store or "the partition's own physically separate store")
        detail["disclosure"] = (
            "This record commits to the membership of the partition without "
            "disclosing it. assert_not_holdout() resolves the commitments; "
            "nothing else does."
        )
    else:
        detail["unit_ids"] = ids
    return FirstAccessLog(croot).append(
        store_id=f"HOLDOUT:{partition_id}", event="HOLDOUT_REGISTER",
        detail=detail,
    )


def holdout_unit_ids(root: str | Path | None = None) -> dict[str, set[str]]:
    """Registered holdout identifiers, by partition.

    Blinded registrations contribute no identifiers here, by construction.
    Use :func:`holdout_registrations` to see that they exist at all, and
    :func:`assert_not_holdout` to test membership against them.
    """

    out: dict[str, set[str]] = {}
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != "HOLDOUT_REGISTER":
            continue
        detail = record.get("detail") or {}
        if not detail.get("unit_ids"):
            continue
        partition = detail.get("partition_id")
        out.setdefault(partition, set()).update(detail.get("unit_ids") or [])
    return out


def holdout_commitments(root: str | Path | None = None
                        ) -> dict[str, set[str]]:
    """Registered holdout commitments, by partition.  Opaque digests only."""

    out: dict[str, set[str]] = {}
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != "HOLDOUT_REGISTER":
            continue
        detail = record.get("detail") or {}
        commitments = detail.get("unit_id_commitments")
        if not commitments:
            continue
        out.setdefault(detail.get("partition_id"), set()).update(commitments)
    return out


def holdout_registrations(root: str | Path | None = None) -> dict[str, Any]:
    """Every holdout registration, by partition, with its disclosure form."""

    out: dict[str, Any] = {}
    for record in FirstAccessLog(custody_root(root)).records():
        if record.get("event") != "HOLDOUT_REGISTER":
            continue
        detail = record.get("detail") or {}
        partition = detail.get("partition_id")
        entry = out.setdefault(partition, {"units": 0, "records": 0,
                                           "forms": set()})
        entry["units"] += int(detail.get("unit_count") or 0)
        entry["records"] += 1
        entry["forms"].add("BLINDED_COMMITMENT"
                           if detail.get("unit_id_commitments")
                           else "PLAINTEXT_IDENTITY_LIST")
    return {partition: {**entry, "forms": sorted(entry["forms"])}
            for partition, entry in out.items()}


def assert_not_holdout(unit_ids: Sequence[str] | set[str], *, what: str,
                       root: str | Path | None = None) -> dict[str, Any]:
    """Refuse if any identifier is a registered holdout unit.

    Both registration forms are checked: plaintext registrations by set
    intersection, blinded registrations by recomputing the commitment of every
    candidate under that partition's salt.  A blinded partition is therefore
    exactly as protected as a plaintext one; the difference is only in what the
    ledger discloses to a reader.

    The error names counts and partitions, never the offending identifiers: a
    refusal message is read by whoever ran the job, and leaking the holdout
    identity set through an exception is its own contamination channel.
    """

    candidates = set(unit_ids)
    croot = custody_root(root)
    registered = holdout_unit_ids(root)
    collisions = {
        partition: len(candidates & ids)
        for partition, ids in registered.items()
        if candidates & ids
    }

    commitments = holdout_commitments(root)
    salts = _load_salts(croot) if commitments else {}
    unresolvable: list[str] = []
    for partition, committed in commitments.items():
        salt = salts.get(partition)
        if salt is None:
            unresolvable.append(partition)
            continue
        hit = sum(1 for uid in candidates if _commit(salt, uid) in committed)
        if hit:
            collisions[partition] = collisions.get(partition, 0) + hit

    if unresolvable:
        raise CustodyViolation(
            f"{what} cannot be cleared: partitions {sorted(unresolvable)!r} "
            "carry blinded holdout registrations whose salt is missing, so "
            "membership cannot be tested.  An untestable register refuses; it "
            "does not pass."
        )
    if collisions:
        raise CustodyViolation(
            f"{what} would handle registered holdout units: {collisions!r}.  "
            "A holdout unit may not be sharded, delivered to a seat, or "
            "released to any process outside open_sealed_partition()."
        )
    return {
        "checked": len(candidates),
        "registered_partitions": sorted(set(registered) | set(commitments)),
        "registered_units": (sum(len(ids) for ids in registered.values())
                             + sum(len(ids) for ids in commitments.values())),
        "blinded_partitions": sorted(commitments),
        "collisions": 0,
    }


def assert_eligible_for_holdout(unit_ids: Sequence[str] | set[str], *,
                                corpus_id: str,
                                root: str | Path | None = None
                                ) -> dict[str, Any]:
    """Refuse to draw a holdout partition from units that were ever exposed.

    Eligibility is NEVER-READ **and** NEVER-SHARDED **and** NEVER-JUDGED **and**
    NEVER-REFERENCED.  Not "has no reference answer" — that definition is what
    produced a partition whose units had already been delivered to blind seats
    and, for some of them, already judged.

    Refuses outright when the corpus has no ledger-scope declaration, because
    an unwatched corpus cannot support a non-exposure claim.

    Both exposure forms are resolved.  A blinded exposure records commitments
    instead of identifiers, so a predicate that consulted
    :func:`exposed_unit_ids` alone would read a blinded ``SHARDED`` record as
    silence and re-draw a spent unit — the blinding would have bought
    disclosure hygiene at the cost of the control it protects.  Each blinded
    record is therefore resolved under its scope's salt, and a scope whose salt
    is missing REFUSES rather than passes, exactly as
    :func:`assert_not_holdout` does.
    """

    if corpus_id not in _ledger_scopes(root):
        raise CustodyViolation(
            f"corpus {corpus_id!r} has no exposure-ledger scope declaration; "
            "an empty ledger is not evidence of non-exposure.  Call "
            "declare_ledger_scope() when the corpus is built, before anything "
            "is allowed to read, shard or judge it."
        )
    candidates = set(unit_ids)
    exposed = exposed_unit_ids(corpus_id=corpus_id, root=root)
    offending = {kind: len(candidates & ids)
                 for kind, ids in exposed.items() if candidates & ids}

    committed = exposed_commitments(corpus_id=corpus_id, root=root)
    salts = _load_salts(custody_root(root))
    unresolvable = sorted({
        scope for scopes in committed.values() for scope in scopes
        if salts.get(scope) is None
    })
    if unresolvable:
        raise CustodyViolation(
            f"corpus {corpus_id!r} carries blinded exposure records for scopes "
            f"{unresolvable!r} whose salt is missing, so exposure cannot be "
            "tested.  An untestable exposure ledger refuses; it does not pass."
        )
    blinded_totals: dict[str, int] = {}
    for kind, scopes in committed.items():
        total = 0
        for scope, commitments in scopes.items():
            salt = salts[scope]
            total += len(commitments)
            hit = sum(1 for uid in candidates
                      if _commit(salt, uid) in commitments)
            if hit:
                offending[kind] = offending.get(kind, 0) + hit
        if total:
            blinded_totals[kind] = total

    if offending:
        raise CustodyViolation(
            f"{sum(offending.values())} candidate units are already exposed "
            f"in corpus {corpus_id!r}: {offending!r}.  They cannot be drawn "
            "into a holdout partition."
        )
    return {
        "corpus_id": corpus_id,
        "candidates": len(candidates),
        "eligible": True,
        "definition": "NEVER_READ_AND_NEVER_SHARDED_AND_NEVER_JUDGED_"
                      "AND_NEVER_REFERENCED",
        "ledger_totals": {kind: len(ids) for kind, ids in exposed.items()},
        "blinded_ledger_totals": blinded_totals,
    }


# ---------------------------------------------------------------------------
# The hardened exposed-population loader
# ---------------------------------------------------------------------------

#: ``unit_id`` as it appears as a top-level JSON key.  The leading ``{`` or
#: ``,`` prevents ``"parent_unit_id"`` and friends from matching.  The value
#: class excludes backslash, so an escaped value never parses "cheaply" and
#: wrong — it fails closed instead.
_UNIT_ID_RE = re.compile(r'[{,]\s*"unit_id"\s*:\s*"([^"\\]*)"')


def _scan_unit_id(line: str, line_number: int) -> str:
    """Extract ``unit_id`` without deserializing the record.

    Nothing in the line is bound to a Python object except the identifier.
    The item text is never parsed, never decoded into a ``str`` field, and
    never reachable by the caller.  Ambiguity fails closed: if the pattern
    matches zero times, or more than once, the loader refuses rather than
    guessing which match is the key.
    """

    found = _UNIT_ID_RE.findall(line)
    if len(found) != 1:
        raise CustodyViolation(
            f"line {line_number}: expected exactly one top-level unit_id, "
            f"found {len(found)}; refusing to deserialize the record to "
            "resolve the ambiguity"
        )
    return found[0]


def load_exposed_population(
    path: str | Path,
    *,
    expected_unit_ids: Sequence[str] | set[str] | None = None,
    expect_sha256: str | None = None,
    root: str | Path | None = None,
) -> dict[str, dict]:
    """Load a population store that a process is entitled to read in full.

    This is the replacement for the ``{row["unit_id"]: row for line in
    path.read_text().splitlines()}`` idiom that destroyed the V2 partitions.
    It differs in three ways, in the order they matter:

    1. **It refuses a sealed store without opening it.**  The registry lookup
       is by resolved path and by ``(device, inode)`` and happens before any
       file is opened, so zero holdout bytes enter the process.  This is the
       load-bearing control: incident 306 counted *bytes read*, and a
       streaming filter over a shared file still reads every byte.
    2. **It refuses a foreign identity before deserializing anything.**  When
       ``expected_unit_ids`` is given, every line's identifier is scanned with
       a regex and checked against the allowance in a first pass.  Not one
       record is deserialized until the whole file is known to contain only
       permitted identities, so a store that silently gained a holdout row
       cannot leak its text through this function.
    3. **It verifies a pinned digest** when one is supplied.

    On success it returns exactly what the legacy idiom returned: a mapping of
    ``unit_id`` to the deserialized row, in file order.
    """

    target = Path(path).resolve()

    sealed = is_sealed_path(target, root=root)
    if sealed is not None:
        raise CustodyViolation(
            f"{target} is the sealed store {sealed.store_id!r} "
            f"({sealed.reason}).  It carries holdout item text and may only be "
            "reached through open_sealed_partition().  No bytes were read."
        )

    if not target.exists():
        raise CustodyViolation(f"population store not found: {target}")

    if expect_sha256 is not None:
        digest = _sha256_file(target)
        if digest != expect_sha256:
            raise CustodyViolation(
                f"population store digest mismatch for {target}: "
                f"{digest} != {expect_sha256}"
            )

    text = target.read_text()
    lines = [line for line in text.splitlines() if line]

    if expected_unit_ids is not None:
        allowed = set(expected_unit_ids)
        seen: set[str] = set()
        for number, line in enumerate(lines, 1):
            unit_id = _scan_unit_id(line, number)
            if unit_id not in allowed:
                raise CustodyViolation(
                    f"{target} contains identity {unit_id!r} at line {number}, "
                    "which is not in the permitted identity set.  This store "
                    "is not exposure-clean; refusing before any record is "
                    "deserialized."
                )
            if unit_id in seen:
                raise CustodyViolation(
                    f"{target} repeats identity {unit_id!r} at line {number}"
                )
            seen.add(unit_id)
        missing = allowed - seen
        if missing:
            raise CustodyViolation(
                f"{target} is missing {len(missing)} permitted identities; "
                "refusing a partial population"
            )

    rows: dict[str, dict] = {}
    for number, line in enumerate(lines, 1):
        row = json.loads(line)
        rows[row["unit_id"]] = row
    return rows


def load_authorised_packets(
    path: str | Path,
    authorised_unit_ids: Sequence[str] | set[str],
    *,
    what: str = "packet load",
    root: str | Path | None = None,
) -> dict[str, dict]:
    """Load only the records a caller is explicitly authorised to handle.

    Unlike :func:`load_exposed_population` this does not require the store to
    contain nothing else — a packet store legitimately outlives the
    authorisation of any one job.  What it guarantees is the other direction:
    a record whose identifier is not on the allowlist is never deserialized,
    so its item text never becomes a Python object and cannot be written
    anywhere.  Selection happens on the identifier, before the record exists.

    It also refuses when the allowlist itself contains a registered holdout
    identifier.  "Load everything and slice" is what delivered 87 of 120
    sealed units to reviewer seats; an allowlist that is allowed to name a
    holdout unit reproduces it with extra steps.
    """

    target = Path(path).resolve()

    sealed = is_sealed_path(target, root=root)
    if sealed is not None:
        raise CustodyViolation(
            f"{target} is the sealed store {sealed.store_id!r} "
            f"({sealed.reason}).  {what} may not read it.  No bytes were read."
        )

    allowed = set(authorised_unit_ids)
    if not allowed:
        raise CustodyViolation(
            f"{what}: an empty authorisation list is refused.  Authorisation "
            "must be positive and explicit."
        )
    assert_not_holdout(allowed, what=what, root=root)

    if not target.exists():
        raise CustodyViolation(f"packet store not found: {target}")

    rows: dict[str, dict] = {}
    with target.open() as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            unit_id = _scan_unit_id(line, number)
            if unit_id not in allowed:
                continue  # never deserialized, never materialized
            if unit_id in rows:
                raise CustodyViolation(
                    f"{what}: {target} repeats identity at line {number}"
                )
            record = json.loads(line)
            if record.get("unit_id") != unit_id:
                raise CustodyViolation(
                    f"{what}: line {number} identifier scan disagrees with the "
                    "deserialized record; refusing"
                )
            rows[unit_id] = record

    missing = allowed - set(rows)
    if missing:
        raise CustodyViolation(
            f"{what}: {len(missing)} authorised identities are absent from "
            f"{target}; refusing a partial authorisation rather than "
            "silently shipping less than was authorised"
        )
    return rows
