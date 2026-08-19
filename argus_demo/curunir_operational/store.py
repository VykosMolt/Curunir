"""Append-only mission-data event store.

One JSONL event log, hash-chained per entry, plus content-addressed payload
files. All state (current, historical, workflow) is derived by replaying the
log; corrections and supersessions are new events, so prior state and
provenance remain reconstructable and history is never rewritten. The raw
store is the privileged substrate: access filtering happens in projections,
never here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Mapping

from . import PACKAGE_VERSION
from .canonical import CONTRACT_VERSION, canonical_line, parse_time, reject_non_finite, require_aware, sha256
from .contracts import Record

CHAIN_GENESIS = "0" * 64

# Contract versions this code can correctly interpret. A store's contract_version
# (in store_meta.json, authenticated on import) declares which contract its log
# is written under; importing a store whose version is not here would risk
# silently misreading records, so it is refused rather than migrated implicitly.
# Add older versions here only alongside code that reads them correctly.
COMPATIBLE_CONTRACT_VERSIONS = frozenset({CONTRACT_VERSION})

EVENT_TYPES = {
    "SOURCE_REGISTERED": "source",
    "SCHEMA_REGISTERED": "schema_definition",
    "MAPPING_REGISTERED": "mapping_definition",
    "PIPELINE_REGISTERED": "pipeline_definition",
    "WORKSHOP_REGISTERED": "workshop_definition",
    "MODEL_REGISTERED": "model_package",
    "ACCREDITATION_RECORDED": "accreditation",
    "INGESTION_RECORDED": "ingestion",
    "TRANSFORMATION_RECORDED": "transformation",
    "OBJECT_VERSION_APPENDED": "object_version",
    "RELATIONSHIP_VERSION_APPENDED": "relationship_version",
    "ACTIVITY_RECORDED": "activity",
    "ASSOCIATION_PROPOSED": "association_proposal",
    "ASSOCIATION_RESOLVED": "association_resolution",
    "INFERENCE_RECORDED": "inference",
    "ANALYTICAL_PROPOSAL_RECORDED": "analytical_proposal",
    "ALERT_RAISED": "alert",
    "ALERT_TRANSITIONED": "alert_transition",
    "RECOMMENDATION_RECORDED": "recommendation",
    "ANALYST_ACTION_RECORDED": "analyst_action",
    "DECISION_RECORDED": "decision",
    "REQUIREMENT_RECORDED": "information_requirement",
    "EVIDENCE_REQUEST_RECORDED": "evidence_request",
    "TASK_RECORDED": "analyst_task",
    "WORKFLOW_TRANSITIONED": "workflow_transition",
}


class StoreError(ValueError):
    pass


def _no_duplicate_keys(pairs):
    """json object_pairs_hook that REFUSES a duplicate key. A by-value non-finite
    check cannot catch a duplicate-key token (`"score":NaN,"score":1.0` parses to
    a finite 1.0 but leaves a bare `NaN` in the archived bytes, and first/last-wins
    is itself an ambiguity a strict auditor rejects), so an imported log is parsed
    strictly (review N-4). Legitimate exports never carry a duplicate key —
    canonical_line emits sorted, unique keys."""
    seen: set = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate JSON key {key!r}")
        seen.add(key)
    return dict(pairs)


def _entry_hash(seq: int, event_type: str, recorded_time: str, actor: str, record: Mapping[str, Any], prev_hash: str) -> str:
    return sha256({"seq": seq, "event_type": event_type, "recorded_time": recorded_time,
                   "actor": actor, "record": record, "prev_hash": prev_hash})


def _is_regular_readable(path: Path) -> bool:
    """True for a regular file, or a symlink whose target is a regular file.

    FIFO/socket/dir must not be read (review R35B-2 hang)."""
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        try:
            info = path.stat()
        except OSError:
            return False
    return stat.S_ISREG(info.st_mode)


def _is_live_store_root(root: Path) -> bool:
    """create()/import plant `.append.lock`; treat a symlink (incl. dangling)
    as a live marker too so exists()-first cannot hide it (review R35B-1)."""
    lock = Path(root) / ".append.lock"
    return lock.is_symlink() or lock.exists()


def _looks_like_complete_store(root: Path) -> bool:
    """True when store_meta.json parses as a store identity.

    Torn/empty/unreadable meta is not a store (review R26B-4) — import may
    overwrite it rather than refuse retry. A symlink to valid identity
    still names a store (review R32B-5): treating it as leftover would
    rmtree a live root. A FIFO meta is not a store and must not hang."""
    meta = Path(root) / "store_meta.json"
    if meta.is_dir() or not _is_regular_readable(meta):
        return False
    try:
        data = json.loads(meta.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    return isinstance(data, dict) and "store_id" in data


def _looks_like_open_export(root: Path) -> bool:
    """True only for a consistent previous open export.

    A dest-side plant of an empty/`{}`/symlink `export_manifest.json` on a
    live store must not make export_to treat it as replaceable backup
    bait (review R33B-1)."""
    manifest_path = Path(root) / "export_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("export_format") != "curunir-operational-open-export-v1":
        return False
    events = Path(root) / "events.jsonl"
    meta = Path(root) / "store_meta.json"
    if events.is_symlink() or meta.is_symlink():
        return False
    if not events.is_file() or not meta.is_file():
        return False
    try:
        if hashlib.sha256(events.read_bytes()).hexdigest() != manifest.get("events_sha256"):
            return False
        if hashlib.sha256(meta.read_bytes()).hexdigest() != manifest.get("store_meta_sha256"):
            return False
    except OSError:
        return False
    listed = manifest.get("payloads")
    if isinstance(listed, dict):
        listed_names = set(listed)
    elif isinstance(listed, list):
        listed_names = set(listed)
    else:
        return False
    payloads_dir = Path(root) / "payloads"
    if payloads_dir.is_dir() and not payloads_dir.is_symlink():
        present = {
            child.name for child in payloads_dir.iterdir()
            if len(child.name) == 64
            and all(c in "0123456789abcdef" for c in child.name)
        }
        if present != listed_names:
            return False
    elif listed_names:
        return False
    return True


def _refuse_dest_store_overlap(
        dest: Path, *, source_root: Path | None = None,
        replace_dest: bool = False,
        allow_export_replace: bool = False) -> None:
    """Refuse dest that would destroy or pollute a live store.

    Shared by export_to / import_from / build_delta_bundle / PACE so the
    next dest-writer cannot skip the class (review R31B-1 / R32B-1 /
    R33B-2). A previous *consistent* open export may be replaced by
    export_to; a live store (even with a planted manifest name) may not."""
    dest = Path(dest).resolve()
    if source_root is not None:
        root = Path(source_root).resolve()
        if dest == root or dest.is_relative_to(root):
            raise StoreError(
                "dest overlaps the source store; refusing"
            )
        if replace_dest and (dest == root.parent or root.is_relative_to(dest)):
            raise StoreError(
                "dest is an ancestor of the source store; refusing"
            )
    if _looks_like_complete_store(dest):
        if not (allow_export_replace and _looks_like_open_export(dest)):
            raise StoreError("dest is an existing store; refusing")
        if _is_live_store_root(dest):
            raise StoreError("dest is a live store; refusing")
        if source_root is not None:
            try:
                src_id = json.loads(
                    (Path(source_root) / "store_meta.json").read_bytes()
                    .decode("utf-8")).get("store_id")
                dst_id = json.loads(
                    (dest / "store_meta.json").read_bytes()
                    .decode("utf-8")).get("store_id")
            except (ValueError, UnicodeDecodeError, OSError):
                raise StoreError("dest is an existing store; refusing")
            if src_id != dst_id:
                raise StoreError(
                    "dest is an export of a different store; refusing"
                )
    for ancestor in dest.parents:
        if _looks_like_complete_store(ancestor):
            raise StoreError("dest is inside an existing store; refusing")
    if dest.exists() and replace_dest:
        for meta in dest.rglob("store_meta.json"):
            child = meta.parent
            if child != dest and _looks_like_complete_store(child):
                raise StoreError("dest contains an existing store; refusing")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _export_write_bytes(path: Path, body: bytes) -> None:
    """Write an export member without following a dest-side symlink
    (review R26B-2). A pre-planted symlink at events.jsonl / store_meta /
    a payload digest would otherwise clobber an arbitrary file the
    export process can write."""
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise StoreError(
            f"export dest {path.name} is not a regular file; refusing"
        )
    _write_file_atomic(path, body, tmp_dir=path.parent)


def _write_file_atomic(path: Path, body: bytes, *, tmp_dir: Path) -> None:
    """fsync + replace, with the temp living in `tmp_dir` (NOT necessarily
    next to `path`). put_payload's temp must live outside payload_dir so
    export_to cannot sweep a leftover tmp into a backup (review B-1)."""
    path = Path(path)
    tmp = Path(tmp_dir) / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with tmp.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class MissionDataStore:
    EVENT_TYPES = EVENT_TYPES
    # subclasses may declare additional versioned record families
    # (record_type → id field); append then enforces the same next-version
    # discipline object_version/relationship_version get, so a concurrent
    # writer's stale version raises instead of silently shadowing current
    # state under the latest-version replay rule
    VERSIONED_RECORD_TYPES: dict[str, str] = {}

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.payload_dir = self.root / "payloads"
        meta_path = self.root / "store_meta.json"
        if not meta_path.exists():
            raise StoreError(f"not a mission data store: {self.root}")
        try:
            self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoreError(
                f"store_meta.json is not valid JSON ({self.root}); refusing to "
                f"open rather than misread the store"
            ) from exc
        # migration safety at EVERY open (not only import_from): a store written
        # under a contract this code cannot interpret is refused rather than
        # silently misread — closes the directory-copy / snapshot-restore path
        # that bypasses import_from's guard.
        if self.meta.get("contract_version") not in COMPATIBLE_CONTRACT_VERSIONS:
            raise StoreError(
                f"store contract version {self.meta.get('contract_version')!r} is "
                f"not compatible with this code ({CONTRACT_VERSION}); refusing to "
                f"open rather than misread the log")
        self._events: list[dict[str, Any]] = []
        self._head_hash = CHAIN_GENESIS
        self._last_recorded: str | None = None
        self._object_versions: dict[str, int] = {}
        self._relationship_versions: dict[str, int] = {}
        self._generic_versions: dict[tuple[str, str], int] = {}
        self._idempotency: dict[str, str] = {}
        self._alert_dedup: dict[str, str] = {}
        self._records_by_type: dict[str, list[dict[str, Any]]] = {}
        self._source_watermarks: dict[str, str] = {}
        self._file_offset = 0
        if self.events_path.exists():
            raw = self.events_path.read_bytes()
            self._file_offset = len(raw)
            complete, torn = self._split_log(raw)
            # a healthy log ends in \n; a non-empty trailing segment is an
            # incomplete final write (a torn tail) — fail loud so recover_torn_tail
            # handles it, never accept it as committed state.
            if torn.strip():
                raise StoreError(
                    f"event log ends with a torn line ({self.events_path}); the "
                    f"final append did not complete (no terminating newline). "
                    f"Recover with recover_torn_tail(); all prior events remain "
                    f"valid under the hash chain.")
            for number, line in enumerate(complete, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)  # bytes → UTF-8 handled by json
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise StoreError(
                        f"event log corrupt at line {number} ({self.events_path})") from exc
                self._verify_link(event, at=f"{self.events_path}:{number}")
                self._index(event)

    @staticmethod
    def _split_log(raw: bytes) -> tuple[list[bytes], bytes]:
        """Split a raw event log into complete (\n-terminated) line-bytes and a
        trailing torn remainder. Splits on b"\\n" ONLY — never str.splitlines(),
        which ALSO breaks on U+2028/U+2029/U+0085 that appear raw in canonical
        JSON string values (ensure_ascii=False), which would otherwise split one
        committed event into two unparseable pieces. A healthy log ends in \\n,
        so the trailing segment is b"" when intact and the incomplete final
        write when torn."""
        if not raw:
            return [], b""
        parts = raw.split(b"\n")
        return parts[:-1], parts[-1]

    @classmethod
    def create(cls, root: str | Path, store_id: str, created_time: str) -> "MissionDataStore":
        require_aware(created_time)
        root = Path(root)
        if (root / "store_meta.json").exists():
            raise StoreError(f"store already exists: {root}")
        leftover_events = root / "events.jsonl"
        if leftover_events.exists():
            if leftover_events.is_symlink() or not leftover_events.is_file():
                raise StoreError(
                    "events.jsonl dest is not a regular file; refusing"
                )
            leftover_stat = leftover_events.stat()
            if leftover_stat.st_nlink > 1:
                raise StoreError(
                    "refusing to create a store over a hardlinked events.jsonl"
                )
            if leftover_stat.st_size > 0:
                raise StoreError(
                    f"refusing to create a store over leftover events.jsonl: {root}"
                )
        root.mkdir(parents=True, exist_ok=True)
        payloads = root / "payloads"
        if payloads.exists() and (payloads.is_symlink() or not payloads.is_dir()):
            raise StoreError("payloads dest is not a regular directory; refusing")
        if payloads.exists() and payloads.is_dir():
            for child in payloads.iterdir():
                digestish = (
                    len(child.name) == 64
                    and all(c in "0123456789abcdef" for c in child.name)
                )
                if child.is_symlink() or child.is_dir() \
                        or not child.is_file() or digestish:
                    raise StoreError(
                        "refusing to create a store over leftover payload slots"
                    )
        payloads.mkdir(exist_ok=True)
        events = root / "events.jsonl"
        if events.is_symlink() or (events.exists() and not events.is_file()):
            raise StoreError("events.jsonl dest is not a regular file; refusing")
        # store_meta is the commit marker — write it LAST (review R29B-1)
        events.touch()
        meta = {"store_id": store_id, "created_time": created_time,
                "contract_version": CONTRACT_VERSION, "package_version": PACKAGE_VERSION}
        _export_write_bytes(root / "store_meta.json",
                            (canonical_line(meta) + "\n").encode("utf-8"))
        (root / ".append.lock").touch()
        return cls(root)

    @classmethod
    def recover_torn_tail(cls, root: str | Path) -> dict[str, Any]:
        """Deterministically recover a store whose FINAL append was interrupted
        by a crash, leaving a torn (incomplete) last line.

        This is the ONLY corruption the append path can produce: a writer holds
        the exclusive lock and does a single write+flush of one line, so a kill
        mid-write can damage only the trailing bytes of the last line — every
        prior event is complete and chain-valid. Recovery truncates exactly that
        torn tail (atomically, preserving the crashed remainder as
        ``events.jsonl.torn`` for forensics) and verifies the result opens
        cleanly.

        It REFUSES anything that is not a torn-tail crash — a complete but
        chain-broken last line (tampering), or an unparseable line that is not
        last (mid-file damage) — because silently altering those would hide
        corruption. Safe and idempotent on a healthy store (recovered=False).

        Returns {"recovered": bool, "truncated_bytes": int}.
        """
        import fcntl
        root = Path(root)
        meta_path = root / "store_meta.json"
        if not meta_path.exists():
            raise StoreError(f"not a mission data store: {root}")
        # A version-incompatible store is not a recoverable torn-tail situation:
        # refuse up front so recovery never reports success on a store that would
        # still refuse to open (the chain-verify below cannot see this — review
        # finding 1 residual).
        if json.loads(meta_path.read_text(encoding="utf-8")).get("contract_version") \
                not in COMPATIBLE_CONTRACT_VERSIONS:
            raise StoreError(
                "store contract version is not compatible with this code; not a "
                "recoverable torn-tail crash — refusing to auto-recover")
        events_path = root / "events.jsonl"
        # Hold the SAME exclusive lock the append path uses, for the whole
        # read-decide-install: a live writer (a concurrent self-healer IS a
        # process) cannot then be mid-append while we truncate/rename, which
        # would orphan its committed bytes. A crashed writer's lock was released
        # on death, so recovery still proceeds.
        with (root / ".append.lock").open("w") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                cls(root)  # opens cleanly under the lock → nothing to recover
                return {"recovered": False, "truncated_bytes": 0}
            except Exception:  # noqa: BLE001 — any open failure (incl. a serialization
                pass           # crash from a surrogate-bearing record) → try recovery
            raw = events_path.read_bytes() if events_path.exists() else b""
            complete, torn = cls._split_log(raw)
            # the recoverable case is EXACTLY a non-empty torn tail (an incomplete
            # final write, i.e. a log not ending in \n). Anything else — a
            # complete-but-unparseable line, a chain break among complete lines,
            # an incompatible contract version — is not a torn-tail crash.
            if not torn.strip():
                raise StoreError(
                    "store does not open and has no torn tail (no incomplete "
                    "final write) to recover; this is not a crash signature "
                    "(corruption, tampering, or an incompatible version) — "
                    "refusing to auto-recover")
            # every complete line must parse; an earlier unparseable line is
            # mid-file damage beyond the torn tail.
            for i, line in enumerate(complete):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise StoreError(
                        f"damage at line {i + 1} beyond the torn tail; refusing "
                        f"to auto-recover — this is not a crash signature") from exc
            # byte-exact: keep everything up to and including the last \n
            kept = raw[: len(raw) - len(torn)]
            # Validate the truncated content BEFORE touching the live file, so the
            # swap always yields a clean store and NO rollback is ever needed
            # (a rollback would itself be a non-atomic rewrite of the live log —
            # the exact silent-loss window we are closing). If deeper damage is
            # found, refuse with events.jsonl completely untouched.
            try:
                cls._verify_kept(kept)
            except Exception as exc:
                raise StoreError(
                    "torn-tail truncation would not yield a clean store (damage "
                    "is deeper than the last line); events.jsonl left untouched") from exc
            backup = events_path.with_name(events_path.name + ".torn")
            if backup.is_symlink() or (backup.exists() and not backup.is_file()):
                raise StoreError("recovery forensic dest is not a regular file; refusing")
            if backup.exists():  # never clobber an earlier crash's forensic remainder
                backup = events_path.with_name(f"{events_path.name}.torn.{int(time.time_ns())}")
                if backup.is_symlink() or (backup.exists() and not backup.is_file()):
                    raise StoreError("recovery forensic dest is not a regular file; refusing")
            tmp = events_path.with_name(events_path.name + ".recovering")
            if tmp.is_symlink() or (tmp.exists() and not tmp.is_file()):
                raise StoreError("recovery temp is not a regular file; refusing")
            try:
                _export_write_bytes(tmp, kept)
                _export_write_bytes(backup, events_path.read_bytes())
                tmp.replace(events_path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                backup.unlink(missing_ok=True)
                raise
            return {"recovered": True, "truncated_bytes": len(torn)}

    @classmethod
    def _verify_kept(cls, raw: bytes) -> None:
        """Validate a candidate truncated log (chain-linkage + entry hashes) in
        memory, without touching any file or needing the payload store. Raises on
        any torn tail, unparseable line, broken link or hash mismatch."""
        complete, torn = cls._split_log(raw)
        if torn.strip():
            raise StoreError("candidate log still ends in a torn line")
        head = CHAIN_GENESIS
        for number, line in enumerate(complete, start=1):
            if not line.strip():
                continue
            event = json.loads(line)  # JSONDecodeError/UnicodeDecodeError propagate
            if event.get("prev_hash") != head:
                raise StoreError(f"candidate log chain broken at line {number}")
            recomputed = _entry_hash(event["seq"], event["event_type"],
                                     event["recorded_time"], event["actor"],
                                     event["record"], event["prev_hash"])
            if event.get("entry_hash") != recomputed:
                raise StoreError(f"candidate log entry hash mismatch at line {number}")
            head = event["entry_hash"]

    def _verify_link(self, event: Mapping[str, Any], *, at: str) -> None:
        """Fail closed on load and catch-up: a broken chain refuses service
        instead of silently continuing onto corrupt history."""
        if event.get("prev_hash") != self._head_hash:
            raise StoreError(f"hash chain broken at {at}: prev_hash does not link to head")
        recomputed = _entry_hash(event["seq"], event["event_type"], event["recorded_time"],
                                 event["actor"], event["record"], event["prev_hash"])
        if event.get("entry_hash") != recomputed:
            raise StoreError(f"hash chain broken at {at}: entry hash fails recomputation")

    def _catch_up(self) -> None:
        """Under the append lock: index any events another writer appended
        since this instance last read the log, so concurrent writers extend
        one chain instead of forking it."""
        size = self.events_path.stat().st_size
        if size == self._file_offset:
            return
        with self.events_path.open("rb") as handle:
            handle.seek(self._file_offset)
            tail = handle.read()
        complete, torn = self._split_log(tail)
        if torn.strip():
            raise StoreError(
                f"event log ends with a torn line ({self.events_path}); another "
                f"writer's final append did not complete. Recover with "
                f"recover_torn_tail(); all prior events remain valid under the "
                f"hash chain.")
        for line in complete:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise StoreError(f"event log corrupt during catch-up ({self.events_path})") from exc
            self._verify_link(event, at=f"catch-up seq {event.get('seq')}")
            if event.get("seq") != len(self._events) + 1:
                raise StoreError(f"catch-up event out of sequence: {event.get('seq')}")
            self._index(event)
        self._file_offset = size

    def _locked_write(self, event: Mapping[str, Any]) -> None:
        line = canonical_line(event) + "\n"
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
        self._file_offset += len(line.encode("utf-8"))

    def _append_lock(self):
        import fcntl
        from contextlib import contextmanager

        @contextmanager
        def held():
            with (self.root / ".append.lock").open("w") as lock_handle:
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle, fcntl.LOCK_UN)
        return held()

    def _index(self, event: dict[str, Any]) -> None:
        record = event["record"]
        record_type = record.get("record_type")
        if record_type == "object_version":
            self._object_versions[record["object_id"]] = record["version"]
        elif record_type == "relationship_version":
            self._relationship_versions[record["relationship_id"]] = record["version"]
        elif record_type == "ingestion":
            if record["validation"] != "DUPLICATE":
                self._idempotency.setdefault(record["idempotency_key"], record["ingestion_id"])
            source_time = record.get("source_time")
            if source_time:
                current = self._source_watermarks.get(record["source_id"])
                if current is None or parse_time(source_time) > parse_time(current):
                    self._source_watermarks[record["source_id"]] = source_time
        elif record_type == "alert":
            self._alert_dedup.setdefault(record["dedup_key"], record["alert_id"])
        if record_type in self.VERSIONED_RECORD_TYPES:
            key = (record_type, record[self.VERSIONED_RECORD_TYPES[record_type]])
            version = record.get("version", 1)
            if version > self._generic_versions.get(key, 0):
                self._generic_versions[key] = version
        self._records_by_type.setdefault(record_type, []).append(record)
        self._events.append(event)
        self._head_hash = event["entry_hash"]
        self._last_recorded = event["recorded_time"]

    def append(self, event_type: str, record: Record | Mapping[str, Any], *, recorded_time: str, actor: str) -> dict[str, Any]:
        if event_type not in self.EVENT_TYPES:
            raise StoreError(f"unknown event type: {event_type}")
        data = record.to_record() if isinstance(record, Record) else dict(record)
        if data.get("record_type") != self.EVENT_TYPES[event_type]:
            raise StoreError(f"{event_type} requires record_type {self.EVENT_TYPES[event_type]!r}, got {data.get('record_type')!r}")
        require_aware(recorded_time)
        with self._append_lock():
            # another writer on the same root may have advanced the log:
            # index its events first so this append extends one chain
            self._catch_up()
            if self._last_recorded is not None and parse_time(recorded_time) < parse_time(self._last_recorded):
                raise StoreError("recorded_time must be non-decreasing (late data keeps its source_time; it is still recorded now)")
            if data["record_type"] == "object_version":
                expected = self._object_versions.get(data["object_id"], 0) + 1
                if data["version"] != expected:
                    raise StoreError(f"object {data['object_id']} next version is {expected}, got {data['version']}")
            if data["record_type"] == "relationship_version":
                expected = self._relationship_versions.get(data["relationship_id"], 0) + 1
                if data["version"] != expected:
                    raise StoreError(f"relationship {data['relationship_id']} next version is {expected}, got {data['version']}")
            if data["record_type"] in self.VERSIONED_RECORD_TYPES:
                key = (data["record_type"],
                       data[self.VERSIONED_RECORD_TYPES[data["record_type"]]])
                expected = self._generic_versions.get(key, 0) + 1
                if data.get("version", 1) != expected:
                    raise StoreError(
                        f"{data['record_type']} {key[1]} next version is {expected}, "
                        f"got {data.get('version', 1)} (another writer advanced it; "
                        f"re-derive from current state instead of overwriting)")
            if data["record_type"] == "ingestion" and data["validation"] != "DUPLICATE" \
                    and data["idempotency_key"] in self._idempotency:
                raise StoreError(f"idempotency key already ingested: {data['idempotency_key']} (record a DUPLICATE instead)")
            seq = len(self._events) + 1
            entry_hash = _entry_hash(seq, event_type, recorded_time, actor, data, self._head_hash)
            event = {"seq": seq, "event_id": f"evt-{seq:06d}-{entry_hash[:8]}", "event_type": event_type,
                     "recorded_time": recorded_time, "actor": actor, "record": data,
                     "prev_hash": self._head_hash, "entry_hash": entry_hash}
            self._locked_write(event)
            self._index(event)
        return event

    def events(self, until_seq: int | None = None) -> list[dict[str, Any]]:
        if until_seq is None:
            return list(self._events)
        return [e for e in self._events if e["seq"] <= until_seq]

    def records_of(self, record_type: str, until_seq: int | None = None) -> list[dict[str, Any]]:
        if until_seq is None:
            return list(self._records_by_type.get(record_type, ()))
        return [e["record"] for e in self.events(until_seq) if e["record"].get("record_type") == record_type]

    def source_watermark(self, source_id: str) -> str | None:
        return self._source_watermarks.get(source_id)

    def entry_hash_at(self, seq: int) -> str:
        if seq == 0:
            return CHAIN_GENESIS
        if not 1 <= seq <= len(self._events):
            raise StoreError(f"no event at seq {seq}")
        return self._events[seq - 1]["entry_hash"]

    def state_token_at(self, seq: int) -> str:
        """Opaque 16-hex state identifier safe for non-privileged outputs: a
        prefix of the chain entry hash, revealing no counts."""
        return self.entry_hash_at(seq)[:16]

    def append_imported_event(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        """Append a fully-formed event envelope from a trusted delta/export
        source, re-verifying continuity, linkage and content hash."""
        with self._append_lock():
            self._catch_up()
            return self._commit_imported_event(envelope)

    def apply_imported_events_locked(self, events: list[Mapping[str, Any]],
                                     load_payloads) -> int:
        """Apply a pre-checked suffix under ONE append lock so a concurrent
        local writer cannot steal a seq slot mid-bundle (review R27B-2).
        `load_payloads(event)` yields payload bodies to install first."""
        with self._append_lock():
            self._catch_up()
            if self.payload_dir.is_symlink() or not self.payload_dir.is_dir():
                raise StoreError(
                    "payload_dir is not a regular directory; refusing the "
                    "bundle rather than applying a prefix"
                )
            self.preflight_imported_events(events)
            bodies_by_event = [list(load_payloads(event)) for event in events]
            for bodies in bodies_by_event:
                for body in bodies:
                    digest = hashlib.sha256(body).hexdigest()
                    slot = self.payload_dir / digest
                    if slot.is_symlink() or slot.is_dir() \
                            or (slot.exists() and not slot.is_file()):
                        raise StoreError(
                            f"payload slot {digest[:12]} is not a regular file; "
                            f"refusing the bundle rather than applying a prefix"
                        )
            applied = 0
            for event, bodies in zip(events, bodies_by_event):
                for body in bodies:
                    self._install_payload_body(body)
                self._commit_imported_event(event)
                applied += 1
            return applied

    def _commit_imported_event(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        event = dict(envelope)
        expected_seq = len(self._events) + 1
        if event.get("seq") != expected_seq:
            raise StoreError(f"imported event seq {event.get('seq')} does not continue the log (next is {expected_seq})")
        if event.get("prev_hash") != self._head_hash:
            raise StoreError("imported event does not link to this store's head (wrong or diverged base)")
        if event.get("event_type") not in self.EVENT_TYPES:
            raise StoreError(f"imported event has unknown type: {event.get('event_type')}")
        recomputed = _entry_hash(event["seq"], event["event_type"], event["recorded_time"],
                                 event["actor"], event["record"], event["prev_hash"])
        if event.get("entry_hash") != recomputed:
            raise StoreError(f"imported event {event.get('seq')} fails content-hash verification")
        if self._last_recorded is not None and parse_time(event["recorded_time"]) < parse_time(self._last_recorded):
            raise StoreError("imported event violates recorded-time monotonicity")
        record = event["record"]
        if record.get("record_type") == "object_version":
            expected = self._object_versions.get(record["object_id"], 0) + 1
            if record["version"] != expected:
                raise StoreError(
                    f"imported object {record['object_id']} next version is "
                    f"{expected}, got {record['version']}: an import may not "
                    f"shadow local versioned state")
        if record.get("record_type") == "relationship_version":
            expected = self._relationship_versions.get(
                record["relationship_id"], 0) + 1
            if record["version"] != expected:
                raise StoreError(
                    f"imported relationship {record['relationship_id']} next "
                    f"version is {expected}, got {record['version']}: an import "
                    f"may not shadow local versioned state")
        if record.get("record_type") in self.VERSIONED_RECORD_TYPES:
            key = (record["record_type"],
                   record[self.VERSIONED_RECORD_TYPES[record["record_type"]]])
            expected = self._generic_versions.get(key, 0) + 1
            if record.get("version", 1) != expected:
                raise StoreError(
                    f"imported {record['record_type']} {key[1]} next version is "
                    f"{expected}, got {record.get('version', 1)}: an import may "
                    f"not shadow local versioned state")
        self._locked_write(event)
        self._index(event)
        return event

    def preflight_imported_events(self, events: list[Mapping[str, Any]]) -> None:
        """Validate a sequence of NEW events would all append, without
        writing. import_delta_bundle must refuse a hostile suffix BEFORE
        planting any payload or applying a prefix (review R26B-3)."""
        seq = len(self._events)
        head_hash = self._head_hash
        last_recorded = self._last_recorded
        object_versions = dict(self._object_versions)
        relationship_versions = dict(self._relationship_versions)
        generic_versions = dict(self._generic_versions)
        for event in events:
            expected_seq = seq + 1
            if event.get("seq") != expected_seq:
                raise StoreError(
                    f"imported event seq {event.get('seq')} does not continue "
                    f"the log (next is {expected_seq})"
                )
            if event.get("prev_hash") != head_hash:
                raise StoreError(
                    "imported event does not link to this store's head "
                    "(wrong or diverged base)"
                )
            if event.get("event_type") not in self.EVENT_TYPES:
                raise StoreError(
                    f"imported event has unknown type: {event.get('event_type')}"
                )
            recomputed = _entry_hash(
                event["seq"], event["event_type"], event["recorded_time"],
                event["actor"], event["record"], event["prev_hash"],
            )
            if event.get("entry_hash") != recomputed:
                raise StoreError(
                    f"imported event {event.get('seq')} fails content-hash "
                    f"verification"
                )
            if last_recorded is not None \
                    and parse_time(event["recorded_time"]) < parse_time(last_recorded):
                raise StoreError("imported event violates recorded-time monotonicity")
            record = event["record"]
            if record.get("record_type") == "object_version":
                expected = object_versions.get(record["object_id"], 0) + 1
                if record["version"] != expected:
                    raise StoreError(
                        f"imported object {record['object_id']} next version is "
                        f"{expected}, got {record['version']}: an import may not "
                        f"shadow local versioned state"
                    )
                object_versions[record["object_id"]] = record["version"]
            if record.get("record_type") == "relationship_version":
                expected = relationship_versions.get(record["relationship_id"], 0) + 1
                if record["version"] != expected:
                    raise StoreError(
                        f"imported relationship {record['relationship_id']} next "
                        f"version is {expected}, got {record['version']}: an import "
                        f"may not shadow local versioned state"
                    )
                relationship_versions[record["relationship_id"]] = record["version"]
            if record.get("record_type") in self.VERSIONED_RECORD_TYPES:
                key = (record["record_type"],
                       record[self.VERSIONED_RECORD_TYPES[record["record_type"]]])
                expected = generic_versions.get(key, 0) + 1
                if record.get("version", 1) != expected:
                    raise StoreError(
                        f"imported {record['record_type']} {key[1]} next version is "
                        f"{expected}, got {record.get('version', 1)}: an import may "
                        f"not shadow local versioned state"
                    )
                generic_versions[key] = record.get("version", 1)
            seq = expected_seq
            head_hash = event["entry_hash"]
            last_recorded = event["recorded_time"]

    def head(self) -> dict[str, Any]:
        return {"store_id": self.meta["store_id"], "event_count": len(self._events), "head_hash": self._head_hash}

    def next_object_version(self, object_id: str) -> int:
        return self._object_versions.get(object_id, 0) + 1

    def next_relationship_version(self, relationship_id: str) -> int:
        return self._relationship_versions.get(relationship_id, 0) + 1

    def find_ingestion_by_key(self, idempotency_key: str) -> str | None:
        return self._idempotency.get(idempotency_key)

    def find_alert_by_dedup(self, dedup_key: str) -> str | None:
        return self._alert_dedup.get(dedup_key)

    def put_payload(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        path = self.payload_dir / digest
        # content-addressed + crash-atomic + SELF-REPAIRING: an existing file whose
        # bytes do NOT hash to its name is a prior torn write (SIGKILL / OOM /
        # ENOSPC mid-write) — treat it as ABSENT and rewrite, never trust a bare
        # existence gate that would serve wrong evidence forever and make every
        # later backup unrestorable with no signal at backup time (review A-F2).
        # Hold the append lock so a concurrent export_to cannot copy a torn
        # slot while we rewrite it (review R25B-1).
        with self._append_lock():
            return self._install_payload_body(body)

    def _install_payload_body(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        path = self.payload_dir / digest
        if path.is_dir() and not path.is_symlink():
            raise StoreError(f"payload slot {digest[:12]} is a directory; refusing")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise StoreError(
                f"payload slot {digest[:12]} is not a regular file; refusing"
            )
        if path.exists():
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                    return digest
            except OSError:
                pass
        _write_file_atomic(path, body, tmp_dir=self.root)
        return digest

    def get_payload(self, digest: str) -> bytes:
        path = self.payload_dir / digest
        if path.is_dir():                                 # a directory-named slot (B-6)
            raise StoreError(f"payload {digest[:12]} is a directory; refusing to serve it")
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise StoreError(
                f"payload {digest[:12]} is not a regular file; refusing to serve it"
            )
        body = path.read_bytes()                          # FileNotFoundError if genuinely absent
        if hashlib.sha256(body).hexdigest() != digest:
            # a torn/corrupt payload must fail LOUDLY, never be served as silent
            # wrong evidence to extraction/claims (review A-F2). Callers that want
            # graceful degradation (provenance) catch StoreError and fall back to
            # the verified custody copy (review B-2).
            raise StoreError(f"payload {digest[:12]} is corrupt (content does not "
                             "match its content-address); refusing to serve it")
        return body

    def verify_chain(self) -> dict[str, Any]:
        prev = CHAIN_GENESIS
        for event in self._events:
            expected = _entry_hash(event["seq"], event["event_type"], event["recorded_time"],
                                   event["actor"], event["record"], prev)
            if event["prev_hash"] != prev or event["entry_hash"] != expected:
                return {"valid": False, "failed_at_seq": event["seq"]}
            prev = event["entry_hash"]
        return {"valid": True, "event_count": len(self._events), "head_hash": prev}

    def export_to(self, directory: str | Path) -> dict[str, Any]:
        directory = Path(directory)
        dest = directory.resolve()
        _refuse_dest_store_overlap(
            dest, source_root=self.root, replace_dest=True,
            allow_export_replace=True)
        # Staging-atomic (review R30B-1): never mutate dest until the
        # backup is complete. In-place rewrite left dest looking like a
        # store after a dest-member refusal or crash mid-payload.
        staging = directory.parent / (
            f".{directory.name}.exporting.{os.getpid()}.{time.time_ns()}"
        )
        renamed = False
        replaced = None
        try:
            staging.mkdir(parents=True)
            (staging / "payloads").mkdir()
            with self._append_lock():
                self._catch_up()
                _export_write_bytes(staging / "events.jsonl",
                                    self.events_path.read_bytes())
                payload_hashes: dict[str, str] = {}
                for path in sorted(self.payload_dir.iterdir()):
                    name = path.name
                    if not (len(name) == 64
                            and all(c in "0123456789abcdef" for c in name)):
                        continue
                    if path.is_symlink() or path.is_dir() or not path.is_file():
                        raise StoreError(
                            f"export: payload slot {name[:12]} is not a regular "
                            f"file; refusing rather than omit it from the backup"
                        )
                    try:
                        body = self.get_payload(name)
                    except FileNotFoundError as exc:
                        raise StoreError(
                            f"export: payload {name[:12]} disappeared during copy"
                        ) from exc
                    _export_write_bytes(staging / "payloads" / name, body)
                    payload_hashes[name] = name
                _export_write_bytes(staging / "store_meta.json",
                                    (self.root / "store_meta.json").read_bytes())
                manifest = {
                    "export_format": "curunir-operational-open-export-v1",
                    "store_id": self.meta["store_id"],
                    "contract_version": self.meta["contract_version"],
                    "event_count": len(self._events),
                    "head_hash": self._head_hash,
                    "events_sha256": hashlib.sha256(
                        (staging / "events.jsonl").read_bytes()).hexdigest(),
                    "store_meta_sha256": hashlib.sha256(
                        (staging / "store_meta.json").read_bytes()).hexdigest(),
                    "payloads": payload_hashes,
                }
                _export_write_bytes(
                    staging / "export_manifest.json",
                    (canonical_line(manifest) + "\n").encode("utf-8"))
            if directory.exists():
                replaced = directory.parent / (
                    f".{directory.name}.replaced.{os.getpid()}.{time.time_ns()}"
                )
                directory.rename(replaced)
            staging.rename(directory)
            renamed = True
            if replaced is not None:
                shutil.rmtree(replaced, ignore_errors=True)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            if replaced is not None and replaced.exists() and not directory.exists():
                replaced.rename(directory)
            raise
        return manifest

    @classmethod
    def import_from(cls, source_dir: str | Path, new_root: str | Path) -> "MissionDataStore":
        source_dir = Path(source_dir)
        manifest_path = source_dir / "export_manifest.json"
        if not manifest_path.exists():
            raise StoreError(f"not an open export (export_manifest.json missing): {source_dir}")
        # the manifest is the FIRST untrusted file read on the restore path — parse
        # it as strictly as events/store_meta (typed errors, no dup keys, must be an
        # object with the keys the rest of this method indexes) rather than letting
        # a hostile manifest raise an untyped UnicodeDecodeError / AttributeError /
        # KeyError / RecursionError before any guard (review NEW-A1)
        try:
            manifest = json.loads(manifest_path.read_bytes().decode("utf-8"),
                                  object_pairs_hook=_no_duplicate_keys)
        except RecursionError as exc:
            raise StoreError("export_manifest.json is nested too deeply; refusing") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise StoreError("export_manifest.json is not valid interchange JSON; refusing") from exc
        if not isinstance(manifest, dict) or not {"events_sha256",
                                                  "head_hash", "payloads"} <= manifest.keys():
            # NB: store_meta_sha256 is intentionally NOT required here — it has a
            # dedicated, more-specific "manifest records no store_meta_sha256"
            # check downstream that must remain reachable
            raise StoreError("export_manifest.json is missing required fields; refusing")
        events_bytes = (source_dir / "events.jsonl").read_bytes()
        if hashlib.sha256(events_bytes).hexdigest() != manifest["events_sha256"]:
            raise StoreError("export tampered: events.jsonl hash mismatch")
        # store_meta.json carries store identity, contract_version, package_version
        # and created_time: the fields that tell a reader which contract the log is
        # to be parsed under. export_to hashes it, so import authenticates it here
        # like every other export member; an export whose manifest cannot vouch for
        # it is unauthenticated and is refused rather than silently trusted.
        if "store_meta_sha256" not in manifest:
            raise StoreError("export unauthenticated: manifest records no store_meta_sha256")
        store_meta_bytes = (source_dir / "store_meta.json").read_bytes()
        if hashlib.sha256(store_meta_bytes).hexdigest() != manifest["store_meta_sha256"]:
            raise StoreError("export tampered: store_meta.json hash mismatch")
        # migration safety: refuse a backup written under a contract version this
        # code cannot interpret, rather than importing and silently misreading it.
        # (The store_meta is authenticated above, so its version is trustworthy.)
        try:
            imported_meta = json.loads(store_meta_bytes.decode("utf-8"),
                                       object_pairs_hook=_no_duplicate_keys)
            reject_non_finite(imported_meta)      # store_meta is byte-copied too (MAJOR-1/N-4)
        except RecursionError as exc:
            raise StoreError("export store_meta is nested too deeply; refusing") from exc
        except ValueError as exc:
            raise StoreError("export store_meta is not valid interchange JSON "
                             "(a non-finite float or a duplicate key); refusing") from exc
        if not isinstance(imported_meta, dict):
            # a hostile export (hash-matched) could make store_meta a scalar/list;
            # .get() below would then AttributeError untyped, before the install try
            raise StoreError("export store_meta is not an object; refusing")
        imported_contract = imported_meta.get("contract_version")
        if imported_contract not in COMPATIBLE_CONTRACT_VERSIONS:
            raise StoreError(
                f"backup was written under contract version {imported_contract!r}, "
                f"which this code ({CONTRACT_VERSION}) cannot interpret; refusing to "
                f"import rather than silently misreading the log. Restore with "
                f"matching code (rollback), or run a migration.")
        # every event must be valid interchange JSON BY VALUE, not just by token.
        # import installs the export bytes VERBATIM (a raw copy — unlike the delta
        # path, which re-serializes each event through canonical_line), so a
        # hostile export whose manifest hashes match, or a backup written by older
        # code that allowed a non-finite float, would otherwise smuggle a bare
        # NaN/Infinity — or a perfectly legal "1e400" that json.loads returns as
        # inf — straight past the canonical_line seam and PERMANENTLY poison the
        # restored store (a chain-valid log the record projection 500s on forever,
        # replicated by every later export, with delta sync broken). Refuse it
        # here, matching "a tampered backup is refused, never silently
        # reconstructed" (review MAJOR-1). Split on \n only (the C-1 doctrine).
        for line in events_bytes.split(b"\n"):
            if not line.strip():
                continue
            try:
                parsed_event = json.loads(line, object_pairs_hook=_no_duplicate_keys)
                reject_non_finite(parsed_event)
            except json.JSONDecodeError as exc:
                raise StoreError("export events.jsonl has a malformed line; refusing to import") from exc
            except RecursionError as exc:
                raise StoreError("export event is nested too deeply; refusing to import") from exc
            except ValueError as exc:
                # a non-finite float (incl. 1e400->inf) or a duplicate JSON key —
                # a poisoned/tampered log the by-hash gates cannot catch
                raise StoreError(
                    "export event log is not valid interchange JSON (a non-finite float "
                    "or a duplicate key); refusing to reconstruct a poisoned store") from exc
        new_root = Path(new_root)
        if new_root.exists() and not new_root.is_dir():
            # a file/symlink-to-file target would make mkdir + the rollback unlink
            # raise NotADirectoryError untyped; refuse it up front as a StoreError (M1)
            raise StoreError(f"import target exists and is not a directory: {new_root}")
        dest = new_root.resolve()
        if dest == Path(source_dir).resolve():
            raise StoreError("import dest must not be the export source")
        _refuse_dest_store_overlap(dest, replace_dest=True)
        if _looks_like_complete_store(new_root):
            raise StoreError(f"refusing to import over an existing store: {new_root}")
        # Crash-atomic install (review R25B-2): write into a sibling staging
        # directory (payloads FIRST, then events, then store_meta as the
        # commit marker) and rename onto new_root. store_meta is what
        # __init__ and the retry gate treat as "this is a store", so a
        # SIGKILL mid-payload can never leave a dest that OPENS, verifies
        # the chain, refuses retry, and whose later export drops the
        # missing payload with no signal.
        staging = new_root.parent / (
            f".{new_root.name}.importing.{os.getpid()}.{time.time_ns()}"
        )
        renamed = False
        try:
            staging.mkdir(parents=True)
            (staging / "payloads").mkdir()
            for name in manifest["payloads"]:
                # the manifest is untrusted: a payload name MUST be a content-
                # address (64-hex sha256) before we read it, or a hostile export
                # naming "../../../etc/passwd" turns install into an arbitrary-file
                # read (content discarded, but a read + existence oracle) (M4).
                if not (isinstance(name, str) and len(name) == 64
                        and all(c in "0123456789abcdef" for c in name)):
                    raise StoreError(f"export manifest names a non-digest payload {name!r}; refusing")
                body = (source_dir / "payloads" / name).read_bytes()
                if hashlib.sha256(body).hexdigest() != name:
                    raise StoreError(f"export tampered: payload {name} hash mismatch")
                _write_file_atomic(staging / "payloads" / name, body, tmp_dir=staging)
            _export_write_bytes(staging / "events.jsonl", events_bytes)
            # commit marker LAST — a dest without store_meta is not a store
            _export_write_bytes(staging / "store_meta.json", store_meta_bytes)
            _fsync_dir(staging)
            _fsync_dir(staging / "payloads")
            if new_root.exists():
                # leftover without a complete store (no/invalid store_meta)
                if _looks_like_complete_store(new_root):
                    raise StoreError(f"refusing to import over an existing store: {new_root}")
                shutil.rmtree(new_root)
            staging.rename(new_root)
            renamed = True
            (Path(new_root) / ".append.lock").touch()
            _fsync_dir(new_root.parent)
            store = cls(new_root)
            check = store.verify_chain()
            if not check["valid"]:
                raise StoreError(f"imported chain invalid at seq {check['failed_at_seq']}")
            if store.head()["head_hash"] != manifest["head_hash"]:
                raise StoreError("imported head hash does not match manifest")
        except BaseException as error:
            # roll back staging always; if we already renamed, roll back the
            # dest too so a failed verify cannot occupy the name and block retry
            shutil.rmtree(staging, ignore_errors=True)
            if renamed:
                shutil.rmtree(new_root, ignore_errors=True)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise                 # never suppress an interrupt as a StoreError
            if isinstance(error, StoreError):
                raise
            raise StoreError(
                f"import failed and was rolled back: {type(error).__name__}: {error}") from error
        return store
