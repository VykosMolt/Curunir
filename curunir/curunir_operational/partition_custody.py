"""Fail-closed custody for held-out evidence partitions: the read boundary.

Access markings decide who may see a record a process already holds. They do
not stop a process from reading bytes it should never touch, and that is how
two holdout generations were lost. This module makes the read impossible
rather than discouraged, through four mechanisms:

sealed store
    A holdout file is held at mode 0o000, so even its owner gets
    PermissionError from the kernel, whatever the calling code does.

one-shot lock
    ``os.open(O_CREAT | O_EXCL)``. The first opener wins; every later attempt
    is refused. The lock file is never removed here.

first-access log
    A hash-chained append-only ledger. The intent to open is written and
    fsynced before the store is unsealed, so an unlogged read cannot happen
    silently — an interrupted open shows up as an unterminated one.

adjudication read
    A second gated path for the one lawful use that is not an opening:
    handing the evidence for an explicitly named identity list to a packet
    builder. It writes its own event class and leaves the one-shot lock alone,
    which is reserved for a sealed opening.

:class:`AccessObserver` answers "has this partition been read, and by whom"
from the ledger, keeping openings and adjudication reads distinguishable. It
never answers a bare no when the ledger does not verify; a broken chain
reports UNKNOWN_LOG_TAMPERED.

Nothing here is advisory. A store stays unreadable until a gate unseals it, no
gate unseals before taking the lock and writing the log, and the adjudication
gate needs an identity list provably inside a registered partition.

Research shadow only. Nothing here claims human validation.
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


#: Mode a holdout store is held at. ``0o000`` denies the owner too, so an
#: unprivileged read fails whoever runs it; ``0o400`` would not, because the
#: campaign runs as the file's owner.
SEALED_MODE = 0o000

#: Directories seal the same way: ``0o000`` removes search permission, so one
#: call closes everything beneath.
SEALED_DIR_MODE = 0o000

#: Mode granted for the duration of one gated open.  Read-only, owner-only.
OPEN_MODE = 0o400

#: Mode granted to a directory for the duration of one gated open.
OPEN_DIR_MODE = 0o500

#: Mode for the custody ledger, its heads file and the seal registry.
#:
#: The ledger names unit identities, so a world-readable one would publish the
#: membership of a sealed partition without unsealing anything. It cannot be
#: ``0o000`` either: the custody layer appends to it on every gated open, and a
#: sealed ledger could not record the access it exists to record.
LEDGER_MODE = 0o600

#: Genesis of the first-access chain. Sixty-four zeros is never a real record
#: hash, so "chains from genesis" is decidable.
GENESIS_HASH = "0" * 64

#: The product root.
_REPO = Path(__file__).resolve().parent.parent


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


#: The historical holdout stores, declared in code rather than a data file: a
#: data file can be deleted or renamed, this tuple needs a reviewed edit. It is
#: the floor of the registry, not the whole of it — later generations register
#: through :func:`seal_store`.
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


# ---- custody root: ledger, seal registry and locks ----


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


# ---- small helpers ----


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
    """Append one line and fsync it.

    ``O_APPEND`` only, with no truncation and no seeking, so this writer cannot
    rewrite history even by mistake. ``mode`` is applied on creation and
    enforced on an existing file, because ``O_CREAT`` ignores its mode once the
    file exists; it is required and has no default, so no caller can leave a
    file behind at the wrong permissions.
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


# ---- first-access log ----


class FirstAccessLog:
    """Append-only, hash-chained ledger of every custody event.

    Three separate controls, since any one alone would only be a promise: the
    writer opens with ``O_APPEND`` and never truncates; every record names the
    hash of the one before it, so an interior edit breaks the chain; and every
    append also writes to a separate heads ledger, so dropping the last record
    leaves the chain head disagreeing with the recorded head.

    :meth:`verify` reports; it never repairs.
    """

    def __init__(self, root: CustodyRoot) -> None:
        self.root = root

    # ---- writing ----

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

    # ---- reading ----

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

    # ---- queries ----

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


# ---- one-shot lock ----


class OneShotLock:
    """A partition may be opened for scoring exactly once.

    ``O_CREAT | O_EXCL`` is atomic in the kernel, so exactly one caller wins
    and everyone else is refused. The lock is never removed here: releasing it
    takes a human with ``rm``, which leaves a trace, and there is deliberately
    no API for it.
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


# ---- seal registry and sealing ----


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

    Matching is by resolved path and by (device, inode), so a rename, a hard
    link or a symlink still resolves to the same answer. Nothing is opened.
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

    Only the permission bits change; contents are untouched.

    ``compute_digest`` defaults to False because hashing reads every byte,
    which is the very act sealing exists to prevent. Pass a digest you already
    hold as ``known_sha256`` instead; only the process that wrote the store
    should ask for one to be computed.
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


# ---- the gate ----

#: Stores this process unsealed and has not resealed. A crash in between must
#: not leave one readable.
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

    The order is the control: nothing that could fail after the store becomes
    readable runs before it.

    1. confirm the path is a registered sealed store;
    2. verify the first-access log from genesis, because an open that cannot
       be recorded truthfully must not happen;
    3. require every prerequisite to hold; a failure is appended as ``DENIED``
       and does not consume the one-shot lock;
    4. take the one-shot lock;
    5. append and fsync the ``OPEN`` record while the store is still sealed,
       recording the mode observed, so the ledger proves the log came first;
    6. only then unseal the store;
    7. reseal and append ``CLOSE`` on the way out, whatever happened.

    Steps 4 and 5 are ordered so a failure to write the ledger burns the lock.
    Burning a partition is recoverable; reading one unrecorded is not.
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
        # Arm the reseal before the store becomes readable, so an interrupt in
        # between cannot leave it open.
        _UNSEALED[store_id] = (path, store.sealed_mode)
        os.chmod(path, store.open_mode)
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


# ---- the adjudication read: the second gate, which is not an opening ----

#: The event class :func:`open_sealed_partition` writes. Named here so that
#: "the partition was opened" and "evidence for named units was handed over"
#: stay greppably distinct.
SEALED_OPENING_EVENT = "OPEN"

#: The event class :func:`read_units_for_adjudication` writes.
ADJUDICATION_READ_EVENT = "ADJUDICATION_READ"

#: Appended on the way out of an adjudication read, whatever happened.
ADJUDICATION_READ_CLOSE_EVENT = "ADJUDICATION_READ_CLOSE"

#: Appended before an adjudication read is refused, so a refusal is evidence
#: rather than an absence. Not ``DENIED``: refusing an opening and refusing an
#: adjudication are different facts.
ADJUDICATION_DENIED_EVENT = "ADJUDICATION_DENIED"


def partition_identity_sha256(unit_ids: Sequence[str]) -> str:
    """Commitment over an identity list: ``sha256(join(sorted(ids), '|'))``.

    The packet builder pins the same definition, so a custody record and a
    builder record commit to the same object and a third party can recompute
    either. The ledger carries the digest and a count, never the list.
    """

    return hashlib.sha256(
        "|".join(sorted(unit_ids)).encode("utf-8")).hexdigest()


def assert_partition_membership(partition_id: str,
                                unit_ids: Sequence[str] | set[str], *,
                                what: str,
                                root: str | Path | None = None
                                ) -> dict[str, Any]:
    """Refuse unless every identifier is a member of the named partition.

    The inverse of :func:`assert_not_holdout`, over the same two registration
    forms: a plaintext registration is tested by set membership, a blinded one
    by recomputing each candidate's commitment under that partition's salt.
    Neither answer needs the partition's identity store unsealed.

    Three refusals, all fail-closed: an unregistered partition cannot
    authorise anything; a blinded partition whose salt is missing is
    untestable, and untestable refuses; and a non-member is refused by count.

    The message names counts and partitions, never identifiers, so a refusal
    is not itself a way out of the partition it just protected.
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

    A ``str`` is a sequence of strings and would quietly authorise its own
    characters, so it is refused by type, and ``None`` is refused rather than
    read as "everything". Only a materialised collection is accepted: the
    authorisation has to be a list somebody wrote down, not a stream that
    produces one on demand. Duplicates are refused so the recorded commitment
    is the commitment of exactly the list passed.
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
    """Hand the evidence for an explicitly named identity list to a builder.

    This is narrower than an opening, not looser:

    1. ``identity_list`` is the only unit-selecting input. It is positional and
       required, and there is no partition-wide or "all units" call shape
       anywhere on this path. ``None``, a bare string, an empty list and a list
       with duplicates are all refused.
    2. Every identity must belong to ``partition_id``, tested in whichever form
       it was registered. One call therefore cannot span two partitions.
    3. The store must be a registered sealed store.
    4. The one-shot lock is read and never taken, and its observed state goes
       into the record as evidence that the opening was not consumed.
    5. The ``ADJUDICATION_READ`` record is appended and fsynced while the store
       is still sealed, carrying the time, the partition, the identity count
       and the identity commitment — never the list itself.
    6. Only then is the store unsealed, and selection happens on the
       identifier, so a record outside the list is never deserialized.
    7. The store is resealed and a close record appended on the way out.

    ``prerequisites`` is the adjudication's own checklist, deliberately not the
    opening's: gating this read on the opening verifier would make lawful
    adjudication impossible. A refused prerequisite is appended as
    ``ADJUDICATION_DENIED`` and consumes nothing.

    What this does not enforce, said plainly: whether the sealed reference was
    frozen before the partition was opened. That is not a custody event and
    this layer never sees a reference. What it does provide is the evidence a
    verifier needs — every read and every opening is a sequenced, hash-chained
    record naming its partition and its time.

    Returns ``unit_id`` to record, the same shape
    :func:`load_authorised_packets` returns.
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
        _UNSEALED[store_id] = (path, store.sealed_mode)
        os.chmod(path, store.open_mode)
        with path.open() as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                unit_id = _scan_unit_id(line, number)
                if unit_id not in allowed:
                    continue
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
    """Every adjudication-read record, optionally filtered.

    It reports what the ledger says and nothing more. Whether the chain
    verifies is a separate question for :meth:`FirstAccessLog.verify` or
    :class:`AccessObserver`.
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


# ---- access observer ----


class AccessObserver:
    """Answers "has this partition been read, and by whom" with evidence.

    The answer is three-valued rather than a boolean, because the interesting
    failure is a tampered ledger: that reports UNKNOWN_LOG_TAMPERED and never
    a bare no.

    Openings and adjudication reads are counted separately, since they are
    different facts about a partition, and both are gated on the same chain
    verification.
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


# ---- exposure ledger, holdout register and eligibility ----

#: The ways a unit stops being a lawful holdout candidate. "It has a reference
#: answer" is only the weakest of them: a partition drawn on that alone once
#: turned out to have been sharded, judged and read already.
EXPOSURE_KINDS: tuple[str, ...] = ("READ", "SHARDED", "JUDGED", "REFERENCED")


def declare_ledger_scope(corpus_id: str, *, note: str,
                         root: str | Path | None = None) -> dict[str, Any]:
    """Declare that the exposure ledger covers a corpus from now on.

    An empty ledger is not evidence of non-exposure; it usually means nobody
    was recording. Requiring this declaration turns "we have been watching
    since it was built" into a dated, hash-chained claim.
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

    Identifiers only; this ledger never carries item text, which is why code
    with no right to read a store may still consult it.

    ``blinded`` mirrors :func:`register_holdout_units`. Once adjudication
    starts, the units being exposed are a holdout partition's units, so a
    plaintext record would republish the membership that blinding the
    registration was meant to stop publishing. A blinded exposure writes a
    count and salted commitments under the same salt as the registration, so
    the two commit to the same identities and eligibility still resolves.

    ``partition_id`` names the commitment scope: required when blinded, since
    the salt is per partition, and refused when not, because a record naming a
    partition and listing its identities is the disclosure to avoid.

    Blinding hides membership from a reader of the ledger. It does not hide it
    from someone who can enumerate the identity space and read the salt.
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
    """Every unit the ledger has seen exposed, grouped by kind.

    A blinded exposure contributes no identifiers here by construction, so
    "absent from this list" does not mean "not exposed". Use
    :func:`exposed_commitments` to see that they exist, and
    :func:`assert_eligible_for_holdout`, which resolves both forms, to test a
    candidate.
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


#: How a blinded registration commits to an identity, named here so a reader
#: of one ledger record can recompute it without reading this module.
HOLDOUT_COMMITMENT_SCHEME = "sha256(salt + '|' + unit_id)"

#: Where per-partition commitment salts live, kept out of the ledger records
#: on purpose. A ledger gets copied into reports and handed to auditors; a salt
#: travelling with the commitment would turn every copy into a membership
#: oracle. Split, publishing the ledger publishes a count and opaque digests.
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
    sharded or delivered by code that is not going through the gate. This is
    the register the shard builder consults.

    ``blinded`` decides what the ledger record carries. False writes the sorted
    identity list, which makes the partition's membership readable without
    unsealing anything. True writes a count and salted commitments instead, so
    the identity list lives only in the partition's own separate store; the
    ledger stops disclosing membership while :func:`assert_not_holdout`, which
    holds the salt, can still test it. It does not hide membership from someone
    who can enumerate the identity space and read the salt.

    ``identity_store`` records where the identity list does live, so a blinded
    record is self-describing.
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

    A blinded registration contributes none here. Use
    :func:`holdout_registrations` to see that it exists and
    :func:`assert_not_holdout` to test membership against it.
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

    Both forms are checked: plaintext by set intersection, blinded by
    recomputing each candidate's commitment under that partition's salt, so a
    blinded partition is as protected as a plaintext one.

    The error names counts and partitions, never the offending identifiers —
    an exception that echoed them would be its own leak.
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

    Eligible means never read, never sharded, never judged and never
    referenced — all four. "Has no reference answer" alone once produced a
    partition whose units had already been delivered and judged.

    A corpus with no ledger-scope declaration is refused outright, since an
    unwatched corpus cannot support a claim of non-exposure.

    Both exposure forms are resolved: a blinded record is checked under its
    scope's salt, because reading it as silence would re-draw a spent unit. A
    scope whose salt is missing refuses rather than passes.
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


# ---- the hardened exposed-population loader ----

#: ``unit_id`` as a top-level JSON key. The leading ``{`` or ``,`` stops
#: ``parent_unit_id`` and friends matching, and the value class excludes
#: backslash so an escaped value fails closed rather than parsing wrongly.
_UNIT_ID_RE = re.compile(r'[{,]\s*"unit_id"\s*:\s*"([^"\\]*)"')


def _scan_unit_id(line: str, line_number: int) -> str:
    """Extract ``unit_id`` without deserializing the record.

    Nothing but the identifier becomes a Python object, so the item text is
    never reachable. Zero matches or more than one is a refusal, not a guess.
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
    """Load a population store a process is entitled to read in full.

    Three controls, in the order they matter:

    1. A sealed store is refused before anything is opened. The registry lookup
       is by resolved path and by (device, inode), so no holdout bytes enter
       the process — a streaming filter over a shared file still reads them.
    2. A foreign identity is refused before anything is deserialized. With
       ``expected_unit_ids``, every line's identifier is scanned and checked
       first, so a store that quietly gained a holdout row cannot leak its text
       through here.
    3. A pinned digest is verified when one is supplied.

    Returns ``unit_id`` to row, in file order.
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

    Unlike :func:`load_exposed_population`, the store may contain other things;
    a packet store outlives any one job's authorisation. The guarantee runs the
    other way: a record not on the allowlist is never deserialized, so its item
    text never becomes a Python object. Selection happens on the identifier,
    before the record exists.

    An allowlist naming a registered holdout identifier is refused, since that
    would be "load everything and slice" with extra steps.
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
                continue
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
