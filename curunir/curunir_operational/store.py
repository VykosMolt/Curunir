"""Durable append-only mission-data store.

The trusted core is small on purpose: strict regular-file reads, atomic
replacement, one lock serializing every mutation, one validator, one staged
installer. The raw log is authoritative; projections filter it for readers and
never decide what may be committed.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import PACKAGE_VERSION
from .canonical import (
    CONTRACT_VERSION,
    canonical_line,
    parse_json_strict,
    parse_time,
    require_aware,
    sha256,
    validate_interchange,
)
from .contracts import Record
from .security import PRIMARY_ID_FIELDS, admit_marking

CHAIN_GENESIS = "0" * 64
EXPORT_FORMAT = "curunir-operational-open-export-v1"
_DIGEST_LENGTH = 64

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


def _entry_hash(
    seq: int,
    event_type: str,
    recorded_time: str,
    actor: str,
    record: Mapping[str, Any],
    prev_hash: str,
) -> str:
    return sha256({
        "seq": seq,
        "event_type": event_type,
        "recorded_time": recorded_time,
        "actor": actor,
        "record": record,
        "prev_hash": prev_hash,
    })


def _is_digest(value: str) -> bool:
    return len(value) == _DIGEST_LENGTH and all(c in "0123456789abcdef" for c in value)


def _require_real_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise StoreError(f"{label} missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise StoreError(f"{label} must be a real directory: {path}")


def _ensure_real_directory(path: Path, *, label: str) -> None:
    """Create missing parents, then refuse a symlink anywhere in the path."""
    absolute = path.absolute()
    absolute.mkdir(parents=True, exist_ok=True)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        _require_real_directory(current, label=label)


def refuse_output_overlap(
    destination: str | Path,
    *,
    source_root: str | Path,
    label: str,
) -> Path:
    """Check that an output root is absent, without following a planted path."""
    destination = Path(destination)
    source = Path(source_root).absolute()
    target = destination.absolute()
    if source == target or source in target.parents or target in source.parents:
        raise StoreError(f"{label} destination overlaps the source store")
    if destination.exists() or destination.is_symlink():
        raise StoreError(f"refusing to replace existing {label} destination: {destination}")
    _ensure_real_directory(destination.parent, label=f"{label} parent")
    return destination


def _open_regular(path: Path, *, label: str) -> tuple[int, os.stat_result]:
    """Open a real single-link regular file; refuse links, swaps and specials."""
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise StoreError(f"{label} missing or unsafe: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) \
            or before.st_nlink != 1:
        raise StoreError(
            f"{label} is not a regular file, is hardlinked, or is otherwise "
            f"unsafe (single-link required): {path}")
    # O_NONBLOCK does nothing to a regular file but stops a swap to a FIFO
    # from hanging before fstat can reject it.
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except (FileNotFoundError, OSError) as exc:
        raise StoreError(f"{label} missing or unsafe: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 \
                or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise StoreError(
                f"{label} is not a regular file, is hardlinked, or changed "
                f"during open (single-link required): {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def _read_regular(path: Path, *, label: str) -> bytes:
    fd, _ = _open_regular(path, label=label)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_regular_tail(path: Path, offset: int, *, label: str) -> bytes:
    """Read only the bytes past ``offset``, under the same file checks."""
    fd, info = _open_regular(path, label=label)
    try:
        size = info.st_size
        if size < offset:
            raise StoreError("event log shrank while the store was open")
        chunks: list[bytes] = []
        position = offset
        while position < size:
            chunk = os.pread(fd, min(1024 * 1024, size - position), position)
            if not chunk:
                break
            chunks.append(chunk)
            position += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, body: bytes, *, mode: int = 0o600) -> None:
    """Publish bytes with no window where the destination is missing or
    half-written."""
    _require_real_directory(path.parent, label="destination parent")
    temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, mode)
    try:
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise StoreError(f"short write while publishing {path}")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise
    else:
        os.close(fd)
    try:
        os.replace(temp, path)
        _fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise


def _exclusive_write(path: Path, body: bytes) -> None:
    """Write one file in a private staging tree; refuse accidental overwrite."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(body)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise StoreError(f"short write while staging {path}")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _log_lines(raw: bytes, *, label: str) -> list[bytes]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise StoreError(
            f"event log ends with a torn line ({label}); the final append is "
            "uncommitted. Recover with recover_torn_tail().")
    if b"\r" in raw:
        raise StoreError(f"event log uses non-canonical line endings: {label}")
    lines = raw[:-1].split(b"\n")
    if any(not line for line in lines):
        raise StoreError(f"event log contains an empty committed line: {label}")
    return lines


class MissionDataStore:
    EVENT_TYPES = EVENT_TYPES
    VERSIONED_RECORD_TYPES: dict[str, str] = {}

    def __init__(self, root: str | Path):
        self.root = Path(root)
        _require_real_directory(self.root, label="store root")
        self.events_path = self.root / "events.jsonl"
        self.payload_dir = self.root / "payloads"
        _require_real_directory(self.payload_dir, label="payload directory")
        self.meta = self._read_meta(self.root)
        self._reset_indexes()
        with self._append_lock():
            raw = _read_regular(self.events_path, label="event log")
            self._load_event_bytes(raw, label=str(self.events_path))
            self._file_offset = len(raw)

    @classmethod
    def _read_meta(cls, root: Path) -> dict[str, Any]:
        try:
            value = parse_json_strict(
                _read_regular(root / "store_meta.json", label="store metadata"),
                label="store metadata",
            )
        except ValueError as exc:
            if isinstance(exc, StoreError):
                raise
            raise StoreError(str(exc)) from exc
        if not isinstance(value, dict):
            raise StoreError("store metadata must be a JSON object")
        required = {"store_id", "created_time", "contract_version", "package_version"}
        if not required <= value.keys():
            raise StoreError(f"store metadata missing fields: {sorted(required - value.keys())}")
        if value["contract_version"] != CONTRACT_VERSION:
            raise StoreError(
                f"store contract version {value['contract_version']!r} is not "
                f"compatible with {CONTRACT_VERSION!r}")
        try:
            require_aware(value["created_time"])
        except (TypeError, ValueError) as exc:
            raise StoreError(f"invalid store created_time: {exc}") from exc
        if not isinstance(value["store_id"], str) or not value["store_id"]:
            raise StoreError("store_id must be a non-empty string")
        return value

    @classmethod
    def create(cls, root: str | Path, store_id: str, created_time: str) -> "MissionDataStore":
        try:
            require_aware(created_time)
        except (TypeError, ValueError) as exc:
            raise StoreError(f"invalid created_time: {exc}") from exc
        if not isinstance(store_id, str) or not store_id:
            raise StoreError("store_id must be a non-empty string")
        root = Path(root)
        if root.exists() or root.is_symlink():
            raise StoreError(f"refusing to create over an existing path: {root}")
        _ensure_real_directory(root.parent, label="store parent")
        stage = Path(tempfile.mkdtemp(prefix=f".{root.name}.create.", dir=root.parent))
        try:
            (stage / "payloads").mkdir(mode=0o700)
            _exclusive_write(stage / "events.jsonl", b"")
            _exclusive_write(stage / ".append.lock", b"")
            meta = {
                "store_id": store_id,
                "created_time": created_time,
                "contract_version": CONTRACT_VERSION,
                "package_version": PACKAGE_VERSION,
            }
            _exclusive_write(
                stage / "store_meta.json",
                (canonical_line(meta) + "\n").encode("utf-8"),
            )
            _fsync_directory(stage / "payloads")
            _fsync_directory(stage)
            os.rename(stage, root)
            _fsync_directory(root.parent)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return cls(root)

    def _reset_indexes(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._head_hash = CHAIN_GENESIS
        self._last_recorded: str | None = None
        self._object_versions: dict[str, int] = {}
        self._relationship_versions: dict[str, int] = {}
        self._generic_versions: dict[tuple[str, str], int] = {}
        self._idempotency: dict[str, str] = {}
        self._alert_dedup: dict[str, str] = {}
        self._records_by_type: dict[str, list[dict[str, Any]]] = {}
        self._records_by_id: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._source_watermarks: dict[str, str] = {}
        self._file_offset = 0

    @contextlib.contextmanager
    def _append_lock(self):
        path = self.root / ".append.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except OSError as exc:
            raise StoreError(f"append lock is missing or unsafe: {path}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StoreError(
                    f"append lock is hardlinked or not a single-link regular file: {path}")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load_event_bytes(self, raw: bytes, *, label: str) -> None:
        for number, line in enumerate(_log_lines(raw, label=label), start=1):
            try:
                event = parse_json_strict(line, label=f"event {number}")
            except ValueError as exc:
                raise StoreError(f"event log corrupt at {label}:{number}: {exc}") from exc
            self._validate_envelope(event, at=f"{label}:{number}")
            self._validate_progression(event["record"])
            self._index(event)

    def _validate_envelope(self, event: Any, *, at: str) -> None:
        if not isinstance(event, dict):
            raise StoreError(f"event envelope is not an object at {at}")
        required = {
            "seq", "event_id", "event_type", "recorded_time", "actor",
            "record", "prev_hash", "entry_hash",
        }
        if set(event) != required:
            raise StoreError(
                f"event envelope fields differ at {at}: "
                f"missing={sorted(required - set(event))}, extra={sorted(set(event) - required)}")
        expected_seq = len(self._events) + 1
        if type(event["seq"]) is not int or event["seq"] != expected_seq:
            raise StoreError(f"event out of sequence at {at}: expected {expected_seq}, got {event['seq']!r}")
        event_type = event["event_type"]
        if not isinstance(event_type, str):
            raise StoreError(f"event_type is not a string at {at}")
        record = event["record"]
        if not isinstance(record, dict):
            raise StoreError(f"event record is not an object at {at}")
        if not isinstance(event["actor"], str) or not event["actor"]:
            raise StoreError(f"event actor must be a non-empty string at {at}")
        try:
            require_aware(event["recorded_time"])
        except (TypeError, ValueError) as exc:
            raise StoreError(f"invalid recorded_time at {at}: {exc}") from exc
        if self._last_recorded is not None \
                and parse_time(event["recorded_time"]) < parse_time(self._last_recorded):
            raise StoreError(f"recorded_time decreases at {at}")
        if event["prev_hash"] != self._head_hash:
            raise StoreError(f"hash chain broken at {at}: prev_hash does not link to head")
        expected_hash = _entry_hash(
            event["seq"], event_type, event["recorded_time"], event["actor"],
            record, event["prev_hash"],
        )
        if event["entry_hash"] != expected_hash:
            raise StoreError(f"hash chain broken at {at}: entry hash fails recomputation")
        expected_id = f"evt-{event['seq']:06d}-{expected_hash[:8]}"
        if event["event_id"] != expected_id:
            raise StoreError(f"event_id does not bind the envelope at {at}")
        # Integrity comes before semantic dispatch, so editing record_type is
        # reported as the chain corruption it is.
        if event_type not in self.EVENT_TYPES:
            raise StoreError(f"unknown event type at {at}: {event_type!r}")
        if record.get("record_type") != self.EVENT_TYPES[event_type]:
            raise StoreError(
                f"{event_type} requires record_type {self.EVENT_TYPES[event_type]!r}, "
                f"got {record.get('record_type')!r} at {at}")

    def _validate_progression(self, record: Mapping[str, Any]) -> None:
        record_type = record.get("record_type")
        if record_type == "object_version":
            expected = self._object_versions.get(record.get("object_id"), 0) + 1
            if type(record.get("version")) is not int or record["version"] != expected:
                raise StoreError(
                    f"object {record.get('object_id')} next version is {expected}, "
                    f"got {record.get('version')!r}")
        elif record_type == "relationship_version":
            expected = self._relationship_versions.get(record.get("relationship_id"), 0) + 1
            if type(record.get("version")) is not int or record["version"] != expected:
                raise StoreError(
                    f"relationship {record.get('relationship_id')} next version is "
                    f"{expected}, got {record.get('version')!r}")
        if record_type in self.VERSIONED_RECORD_TYPES:
            id_field = self.VERSIONED_RECORD_TYPES[record_type]
            record_id = record.get(id_field)
            if not isinstance(record_id, str) or not record_id:
                raise StoreError(f"{record_type} requires non-empty {id_field}")
            key = (record_type, record_id)
            expected = self._generic_versions.get(key, 0) + 1
            if type(record.get("version", 1)) is not int or record.get("version", 1) != expected:
                raise StoreError(
                    f"{record_type} {record_id} next version is {expected}, "
                    f"got {record.get('version', 1)!r}")
        if record_type == "ingestion" and record.get("validation") != "DUPLICATE":
            key = record.get("idempotency_key")
            if not isinstance(key, str) or not key:
                raise StoreError("ingestion requires a non-empty idempotency_key")
            if key in self._idempotency:
                raise StoreError(
                    f"idempotency key already ingested: {key} "
                    "(record a DUPLICATE instead)")

    def _index(self, event: dict[str, Any]) -> None:
        # A tampered log can carry a coherently re-hashed record that is missing
        # the id this index needs; report that as chain damage, not a KeyError.
        try:
            self._index_record(event)
        except KeyError as exc:
            raise StoreError(f"record is missing the index field {exc}") from exc

    def _index_record(self, event: dict[str, Any]) -> None:
        record = event["record"]
        record_type = record["record_type"]
        if record_type == "object_version":
            self._object_versions[record["object_id"]] = record["version"]
        elif record_type == "relationship_version":
            self._relationship_versions[record["relationship_id"]] = record["version"]
        elif record_type == "ingestion":
            if record.get("validation") != "DUPLICATE":
                self._idempotency.setdefault(record["idempotency_key"], record["ingestion_id"])
            source_time = record.get("source_time")
            source_id = record.get("source_id")
            if source_time and source_id:
                current = self._source_watermarks.get(source_id)
                if current is None or parse_time(source_time) > parse_time(current):
                    self._source_watermarks[source_id] = source_time
        elif record_type == "alert":
            self._alert_dedup.setdefault(record["dedup_key"], record["alert_id"])
        if record_type in self.VERSIONED_RECORD_TYPES:
            id_field = self.VERSIONED_RECORD_TYPES[record_type]
            key = (record_type, record[id_field])
            self._generic_versions[key] = record.get("version", 1)
        self._records_by_type.setdefault(record_type, []).append(record)
        primary_id = PRIMARY_ID_FIELDS.get(record_type)
        if primary_id:
            record_id = record.get(primary_id)
            if isinstance(record_id, str) and record_id:
                self._records_by_id.setdefault(record_type, {}).setdefault(record_id, []).append(record)
        self._events.append(event)
        self._head_hash = event["entry_hash"]
        self._last_recorded = event["recorded_time"]

    def _catch_up(self) -> None:
        tail = _read_regular_tail(self.events_path, self._file_offset, label="event log")
        if not tail:
            return
        self._load_event_bytes(tail, label=f"{self.events_path} catch-up")
        self._file_offset += len(tail)

    def refresh(self) -> str:
        """Read whatever another writer appended since this instance last looked.

        A long-lived store (one per server process) calls this before serving
        a request instead of re-reading the whole log. Returns the head hash.
        """
        with self._append_lock():
            self._catch_up()
        return self._head_hash

    def _locked_write(self, event: Mapping[str, Any]) -> None:
        body = (canonical_line(event) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.events_path, flags)
        except OSError as exc:
            raise StoreError(f"event log is missing or unsafe: {self.events_path}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StoreError("event log must be a single-link regular file")
            view = memoryview(body)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise StoreError("short event-log append")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self._file_offset += len(body)

    def append(
        self,
        event_type: str,
        record: Record | Mapping[str, Any],
        *,
        recorded_time: str,
        actor: str,
        condition: Callable[["MissionDataStore"], None] | None = None,
    ) -> dict[str, Any]:
        """Append one event under the store mutation lock.

        ``condition`` runs after catch-up, still under that lock. It is how a
        caller re-checks an invariant that depends on current state, closing
        the gap between a check and the append across store instances.
        """
        if event_type not in self.EVENT_TYPES:
            raise StoreError(f"unknown event type: {event_type}")
        try:
            require_aware(recorded_time)
        except (TypeError, ValueError) as exc:
            raise StoreError(f"invalid recorded_time: {exc}") from exc
        if not isinstance(actor, str) or not actor:
            raise StoreError("actor must be a non-empty string")
        raw = record.to_record() if isinstance(record, Record) else dict(record)
        try:
            validate_interchange(raw)
        except ValueError as exc:
            raise StoreError(f"record is not strict interchange data: {exc}") from exc
        if raw.get("record_type") != self.EVENT_TYPES[event_type]:
            raise StoreError(
                f"{event_type} requires record_type {self.EVENT_TYPES[event_type]!r}, "
                f"got {raw.get('record_type')!r}")
        with self._append_lock():
            self._catch_up()
            if condition is not None:
                condition(self)
            data = admit_marking(self, raw)
            self._validate_progression(data)
            if self._last_recorded is not None \
                    and parse_time(recorded_time) < parse_time(self._last_recorded):
                raise StoreError(
                    "recorded_time must be non-decreasing "
                    "(late data keeps source_time but is recorded now)")
            seq = len(self._events) + 1
            entry_hash = _entry_hash(
                seq, event_type, recorded_time, actor, data, self._head_hash)
            event = {
                "seq": seq,
                "event_id": f"evt-{seq:06d}-{entry_hash[:8]}",
                "event_type": event_type,
                "recorded_time": recorded_time,
                "actor": actor,
                "record": data,
                "prev_hash": self._head_hash,
                "entry_hash": entry_hash,
            }
            self._locked_write(event)
            self._index(event)
            return event

    def events(self, until_seq: int | None = None) -> list[dict[str, Any]]:
        if until_seq is None:
            return list(self._events)
        return [event for event in self._events if event["seq"] <= until_seq]

    def records_of(self, record_type: str, until_seq: int | None = None) -> list[dict[str, Any]]:
        if until_seq is None:
            return list(self._records_by_type.get(record_type, ()))
        return [
            event["record"] for event in self.events(until_seq)
            if event["record"].get("record_type") == record_type
        ]

    def source_watermark(self, source_id: str) -> str | None:
        return self._source_watermarks.get(source_id)

    def entry_hash_at(self, seq: int) -> str:
        if seq == 0:
            return CHAIN_GENESIS
        if not 1 <= seq <= len(self._events):
            raise StoreError(f"no event at seq {seq}")
        return self._events[seq - 1]["entry_hash"]

    def state_token_at(self, seq: int) -> str:
        return self.entry_hash_at(seq)[:16]

    def head(self) -> dict[str, Any]:
        return {
            "store_id": self.meta["store_id"],
            "event_count": len(self._events),
            "head_hash": self._head_hash,
        }

    def next_object_version(self, object_id: str) -> int:
        return self._object_versions.get(object_id, 0) + 1

    def next_relationship_version(self, relationship_id: str) -> int:
        return self._relationship_versions.get(relationship_id, 0) + 1

    def find_ingestion_by_key(self, idempotency_key: str) -> str | None:
        return self._idempotency.get(idempotency_key)

    def find_alert_by_dedup(self, dedup_key: str) -> str | None:
        return self._alert_dedup.get(dedup_key)

    def verify_chain(self) -> dict[str, Any]:
        probe = self.__class__.__new__(self.__class__)
        probe.root = self.root
        probe.meta = dict(self.meta)
        probe._reset_indexes()
        try:
            raw = _read_regular(self.events_path, label="event log")
            probe._load_event_bytes(raw, label=str(self.events_path))
        except StoreError as exc:
            return {"valid": False, "reason": str(exc)}
        return {
            "valid": True,
            "event_count": len(probe._events),
            "head_hash": probe._head_hash,
        }

    def _validate_imported_events(self, events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        probe = self.__class__.__new__(self.__class__)
        probe.root = self.root
        probe.meta = dict(self.meta)
        probe._reset_indexes()
        for event in self._events:
            probe._validate_envelope(event, at=f"existing seq {event['seq']}")
            probe._validate_progression(event["record"])
            probe._index(dict(event))
        accepted: list[dict[str, Any]] = []
        for offset, envelope in enumerate(events, start=1):
            try:
                validate_interchange(envelope)
            except ValueError as exc:
                raise StoreError(f"imported event {offset} is invalid: {exc}") from exc
            event = dict(envelope)
            probe._validate_envelope(event, at=f"imported event {offset}")
            try:
                probe._validate_progression(event["record"])
            except StoreError as exc:
                raise StoreError(
                    f"{exc}: an import may not shadow local versioned state") from exc
            probe._index(event)
            accepted.append(event)
        return accepted

    def apply_imported_events_locked(
        self,
        events: list[Mapping[str, Any]],
        load_payloads: Callable[[Mapping[str, Any]], Iterable[tuple[str, bytes]]] | None = None,
    ) -> int:
        """Validate the complete suffix before installing any payload or event."""
        with self._append_lock():
            self._catch_up()
            accepted = self._validate_imported_events(events)
            pending_payloads: list[tuple[str, bytes]] = []
            if load_payloads is not None:
                for event in accepted:
                    for digest, body in load_payloads(event):
                        if hashlib.sha256(body).hexdigest() != digest:
                            raise StoreError(f"imported payload {digest!r} fails content hash")
                        pending_payloads.append((digest, body))
            for digest, body in pending_payloads:
                self._put_payload_unlocked(body, expected_digest=digest)
            if accepted:
                current = _read_regular(self.events_path, label="event log")
                suffix = b"".join(
                    (canonical_line(event) + "\n").encode("utf-8")
                    for event in accepted
                )
                # A delta is one transaction; replacing the file atomically
                # stops a crash committing only part of it.
                _atomic_write(self.events_path, current + suffix)
                self._file_offset = len(current) + len(suffix)
            for event in accepted:
                self._index(event)
            return len(accepted)

    def append_imported_event(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        event = dict(envelope)
        self.apply_imported_events_locked([event])
        return event

    def _payload_path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not _is_digest(digest):
            raise StoreError(f"invalid payload digest: {digest!r}")
        return self.payload_dir / digest

    def _put_payload_unlocked(self, body: bytes, *, expected_digest: str | None = None) -> str:
        if not isinstance(body, bytes):
            raise StoreError("payload body must be bytes")
        digest = hashlib.sha256(body).hexdigest()
        if expected_digest is not None and digest != expected_digest:
            raise StoreError(f"payload does not match expected digest {expected_digest}")
        path = self._payload_path(digest)
        if path.exists() or path.is_symlink():
            try:
                current = _read_regular(path, label="payload")
            except StoreError as exc:
                raise StoreError(f"refusing to replace unsafe payload slot {digest}: {exc}") from exc
            if hashlib.sha256(current).hexdigest() == digest:
                return digest
        _atomic_write(path, body)
        return digest

    def put_payload(self, body: bytes) -> str:
        with self._append_lock():
            return self._put_payload_unlocked(body)

    def get_payload(self, digest: str) -> bytes:
        body = _read_regular(self._payload_path(digest), label="payload")
        if hashlib.sha256(body).hexdigest() != digest:
            raise StoreError(f"payload {digest} is corrupt")
        return body

    def export_to(self, directory: str | Path) -> dict[str, Any]:
        directory = Path(directory)
        if directory.exists() or directory.is_symlink():
            raise StoreError(
                f"refusing to export over an existing path or live store: {directory}")
        _require_real_directory(directory.parent, label="export parent")
        if self.root.resolve() == directory.resolve() \
                or self.root.resolve() in directory.resolve().parents:
            raise StoreError("export destination must not be the store or inside it")
        stage = Path(tempfile.mkdtemp(prefix=f".{directory.name}.export.", dir=directory.parent))
        try:
            (stage / "payloads").mkdir(mode=0o700)
            with self._append_lock():
                self._catch_up()
                events_bytes = _read_regular(self.events_path, label="event log")
                meta_bytes = _read_regular(self.root / "store_meta.json", label="store metadata")
                payloads: dict[str, str] = {}
                for path in sorted(self.payload_dir.iterdir(), key=lambda item: item.name):
                    if not _is_digest(path.name):
                        continue
                    body = _read_regular(path, label="payload")
                    if hashlib.sha256(body).hexdigest() != path.name:
                        raise StoreError(f"payload {path.name} is corrupt; backup refused")
                    _exclusive_write(stage / "payloads" / path.name, body)
                    payloads[path.name] = path.name
                manifest = {
                    "export_format": EXPORT_FORMAT,
                    "store_id": self.meta["store_id"],
                    "contract_version": self.meta["contract_version"],
                    "event_count": len(self._events),
                    "head_hash": self._head_hash,
                    "events_sha256": hashlib.sha256(events_bytes).hexdigest(),
                    "store_meta_sha256": hashlib.sha256(meta_bytes).hexdigest(),
                    "payloads": payloads,
                }
                _exclusive_write(stage / "events.jsonl", events_bytes)
                _exclusive_write(stage / "store_meta.json", meta_bytes)
                _exclusive_write(
                    stage / "export_manifest.json",
                    (canonical_line(manifest) + "\n").encode("utf-8"),
                )
            _fsync_directory(stage / "payloads")
            _fsync_directory(stage)
            os.rename(stage, directory)
            _fsync_directory(directory.parent)
            return manifest
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    @classmethod
    def import_from(cls, source_dir: str | Path, new_root: str | Path) -> "MissionDataStore":
        source_dir = Path(source_dir)
        new_root = Path(new_root)
        _require_real_directory(source_dir, label="export directory")
        _require_real_directory(source_dir / "payloads", label="export payload directory")
        if new_root.exists() or new_root.is_symlink():
            raise StoreError(
                "refusing to import over an existing path; it may be inside an "
                f"existing store or contain an existing store: {new_root}")
        _ensure_real_directory(new_root.parent, label="import parent")
        source_resolved = source_dir.resolve()
        destination_resolved = new_root.resolve()
        if source_resolved == destination_resolved \
                or source_resolved in destination_resolved.parents \
                or destination_resolved in source_resolved.parents:
            raise StoreError("import source and destination must not overlap")
        try:
            manifest = parse_json_strict(
                _read_regular(source_dir / "export_manifest.json", label="export manifest"),
                label="export manifest",
            )
        except ValueError as exc:
            if isinstance(exc, StoreError):
                raise
            raise StoreError(str(exc)) from exc
        if not isinstance(manifest, dict) or manifest.get("export_format") != EXPORT_FORMAT:
            raise StoreError(f"not a supported open export: {source_dir}")
        if manifest.get("contract_version") != CONTRACT_VERSION:
            raise StoreError(
                f"export contract version {manifest.get('contract_version')!r} is not compatible")
        events_bytes = _read_regular(source_dir / "events.jsonl", label="export event log")
        meta_bytes = _read_regular(source_dir / "store_meta.json", label="export store metadata")
        if hashlib.sha256(events_bytes).hexdigest() != manifest.get("events_sha256"):
            raise StoreError("export tampered: events.jsonl hash mismatch")
        if hashlib.sha256(meta_bytes).hexdigest() != manifest.get("store_meta_sha256"):
            raise StoreError("export tampered: store_meta.json hash mismatch")
        payload_manifest = manifest.get("payloads")
        if not isinstance(payload_manifest, dict):
            raise StoreError("export manifest payloads must be an object")
        payload_bodies: dict[str, bytes] = {}
        for name, claimed in payload_manifest.items():
            if not isinstance(name, str) or claimed != name or not _is_digest(name):
                raise StoreError(f"invalid payload manifest entry: {name!r}")
            body = _read_regular(source_dir / "payloads" / name, label="export payload")
            if hashlib.sha256(body).hexdigest() != name:
                raise StoreError(f"export tampered: payload {name} hash mismatch")
            payload_bodies[name] = body

        stage = Path(tempfile.mkdtemp(prefix=f".{new_root.name}.import.", dir=new_root.parent))
        try:
            (stage / "payloads").mkdir(mode=0o700)
            for name, body in payload_bodies.items():
                _exclusive_write(stage / "payloads" / name, body)
            _exclusive_write(stage / "events.jsonl", events_bytes)
            _exclusive_write(stage / ".append.lock", b"")
            _exclusive_write(stage / "store_meta.json", meta_bytes)
            _fsync_directory(stage / "payloads")
            _fsync_directory(stage)
            candidate = cls(stage)
            head = candidate.head()
            if manifest.get("store_id") != candidate.meta["store_id"]:
                raise StoreError("export manifest store_id does not match authenticated metadata")
            if type(manifest.get("event_count")) is not int \
                    or manifest["event_count"] != head["event_count"]:
                raise StoreError("export event count does not match validated log")
            if manifest.get("head_hash") != head["head_hash"]:
                raise StoreError("export head hash does not match validated log")
            os.rename(stage, new_root)
            _fsync_directory(new_root.parent)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return cls(new_root)

    @classmethod
    def recover_torn_tail(cls, root: str | Path) -> dict[str, Any]:
        root = Path(root)
        _require_real_directory(root, label="store root")
        probe = cls.__new__(cls)
        probe.root = root
        probe.events_path = root / "events.jsonl"
        probe.payload_dir = root / "payloads"
        _require_real_directory(probe.payload_dir, label="payload directory")
        probe.meta = cls._read_meta(root)
        probe._reset_indexes()
        with probe._append_lock():
            raw = _read_regular(probe.events_path, label="event log")
            if not raw or raw.endswith(b"\n"):
                try:
                    probe._load_event_bytes(raw, label=str(probe.events_path))
                except StoreError as exc:
                    raise StoreError(
                        "event log damage is not a crash signature or torn tail; "
                        "committed history shows tampering or has diverged") from exc
                return {"recovered": False, "truncated_bytes": 0}
            split = raw.rfind(b"\n")
            kept = raw[:split + 1] if split >= 0 else b""
            tail = raw[split + 1:]
            # Check every committed line before touching any bytes.
            validator = cls.__new__(cls)
            validator.root = root
            validator.meta = dict(probe.meta)
            validator._reset_indexes()
            try:
                validator._load_event_bytes(kept, label=f"{probe.events_path} retained prefix")
            except StoreError as exc:
                raise StoreError(
                    "event log damage is not a torn-tail crash signature; "
                    "earlier committed history is corrupt") from exc
            if not tail:
                raise StoreError("recovery found no torn bytes")
            forensic = probe.events_path.with_name("events.jsonl.torn")
            suffix = 0
            while forensic.exists() or forensic.is_symlink():
                suffix += 1
                forensic = probe.events_path.with_name(f"events.jsonl.torn.{suffix}")
            _exclusive_write(forensic, raw)
            _fsync_directory(root)
            _atomic_write(probe.events_path, kept)
            return {"recovered": True, "truncated_bytes": len(tail)}
