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
import shutil
from pathlib import Path
from typing import Any, Mapping

from . import PACKAGE_VERSION
from .canonical import CONTRACT_VERSION, canonical_line, parse_time, require_aware, sha256
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


def _entry_hash(seq: int, event_type: str, recorded_time: str, actor: str, record: Mapping[str, Any], prev_hash: str) -> str:
    return sha256({"seq": seq, "event_type": event_type, "recorded_time": recorded_time,
                   "actor": actor, "record": record, "prev_hash": prev_hash})


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
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
            lines = raw.decode("utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    if number == len(lines):
                        raise StoreError(
                            f"event log ends with a torn line ({self.events_path}:{number}); the final append "
                            f"did not complete. Recover by truncating the incomplete last line; all prior "
                            f"events remain valid under the hash chain.") from exc
                    raise StoreError(f"event log corrupt at line {number} ({self.events_path})") from exc
                self._verify_link(event, at=f"{self.events_path}:{number}")
                self._index(event)

    @classmethod
    def create(cls, root: str | Path, store_id: str, created_time: str) -> "MissionDataStore":
        require_aware(created_time)
        root = Path(root)
        if (root / "store_meta.json").exists():
            raise StoreError(f"store already exists: {root}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "payloads").mkdir(exist_ok=True)
        meta = {"store_id": store_id, "created_time": created_time,
                "contract_version": CONTRACT_VERSION, "package_version": PACKAGE_VERSION}
        (root / "store_meta.json").write_text(canonical_line(meta) + "\n", encoding="utf-8")
        (root / "events.jsonl").touch()
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
        root = Path(root)
        if not (root / "store_meta.json").exists():
            raise StoreError(f"not a mission data store: {root}")
        events_path = root / "events.jsonl"
        try:
            cls(root)  # opens cleanly → nothing to recover
            return {"recovered": False, "truncated_bytes": 0}
        except StoreError as open_error:
            first_error = open_error
        raw = events_path.read_bytes() if events_path.exists() else b""
        segments = raw.split(b"\n")
        last = len(segments) - 1
        while last >= 0 and segments[last] == b"":
            last -= 1
        if last < 0:
            raise first_error  # empty/whitespace-only: not a torn tail
        # every line before the last non-empty one must be complete JSON; if an
        # earlier line is torn, this is mid-file damage, not a crashed tail.
        for i in range(last):
            if segments[i].strip() == b"":
                continue
            try:
                json.loads(segments[i])
            except json.JSONDecodeError as exc:
                raise StoreError(
                    f"event log has damage at line {i + 1} that is not a torn "
                    f"tail (an earlier line is incomplete); refusing to "
                    f"auto-recover — this is not a crash signature") from exc
        # the last non-empty line must be the torn one; if it parses, the open
        # failure is a chain break among complete lines = tampering.
        try:
            json.loads(segments[last])
            raise StoreError(
                "the final line is complete JSON, so the store's open failure is "
                "not a torn-tail crash (possible tampering or a diverged chain); "
                "refusing to auto-recover") from first_error
        except json.JSONDecodeError:
            pass  # confirmed torn tail
        # byte-exact truncation: keep everything up to the start of the torn line
        offset = sum(len(segments[i]) + 1 for i in range(last))
        kept, torn_bytes = raw[:offset], len(raw) - sum(len(segments[i]) + 1 for i in range(last))
        backup = events_path.with_name(events_path.name + ".torn")
        tmp = events_path.with_name(events_path.name + ".recovering")
        tmp.write_bytes(kept)
        events_path.replace(backup)   # move the crashed original aside (forensics)
        tmp.replace(events_path)      # install the truncated log
        try:
            cls(root)                 # verify the recovered store opens cleanly
        except StoreError as exc:
            backup.replace(events_path)  # roll back; original is preserved
            raise StoreError(
                "torn-tail truncation did not yield a clean store (damage is "
                "deeper than the last line); original restored, not modified") from exc
        return {"recovered": True, "truncated_bytes": torn_bytes}

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
        lines = tail.decode("utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                if number == len(lines):
                    raise StoreError(
                        f"event log ends with a torn line ({self.events_path}); another "
                        f"writer's final append did not complete. Recover by truncating "
                        f"the incomplete last line; all prior events remain valid under "
                        f"the hash chain.") from exc
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
        event = dict(envelope)
        with self._append_lock():
            self._catch_up()
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
        if not path.exists():
            path.write_bytes(body)
        return digest

    def get_payload(self, digest: str) -> bytes:
        return (self.payload_dir / digest).read_bytes()

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
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "payloads").mkdir(exist_ok=True)
        shutil.copyfile(self.events_path, directory / "events.jsonl")
        shutil.copyfile(self.root / "store_meta.json", directory / "store_meta.json")
        payload_hashes: dict[str, str] = {}
        for path in sorted(self.payload_dir.iterdir()):
            shutil.copyfile(path, directory / "payloads" / path.name)
            payload_hashes[path.name] = path.name  # content-addressed: name is the sha256
        manifest = {
            "export_format": "curunir-operational-open-export-v1",
            "store_id": self.meta["store_id"],
            "contract_version": self.meta["contract_version"],
            "event_count": len(self._events),
            "head_hash": self._head_hash,
            "events_sha256": hashlib.sha256((directory / "events.jsonl").read_bytes()).hexdigest(),
            "store_meta_sha256": hashlib.sha256((directory / "store_meta.json").read_bytes()).hexdigest(),
            "payloads": payload_hashes,
        }
        (directory / "export_manifest.json").write_text(canonical_line(manifest) + "\n", encoding="utf-8")
        return manifest

    @classmethod
    def import_from(cls, source_dir: str | Path, new_root: str | Path) -> "MissionDataStore":
        source_dir = Path(source_dir)
        manifest_path = source_dir / "export_manifest.json"
        if not manifest_path.exists():
            raise StoreError(f"not an open export (export_manifest.json missing): {source_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        imported_contract = json.loads(store_meta_bytes.decode("utf-8")).get("contract_version")
        if imported_contract not in COMPATIBLE_CONTRACT_VERSIONS:
            raise StoreError(
                f"backup was written under contract version {imported_contract!r}, "
                f"which this code ({CONTRACT_VERSION}) cannot interpret; refusing to "
                f"import rather than silently misreading the log. Restore with "
                f"matching code (rollback), or run a migration.")
        new_root = Path(new_root)
        if (new_root / "store_meta.json").exists():
            raise StoreError(f"refusing to import over an existing store: {new_root}")
        new_root.mkdir(parents=True, exist_ok=True)
        (new_root / "payloads").mkdir(exist_ok=True)
        # the verified bytes are the bytes installed, not a fresh read of the source
        (new_root / "store_meta.json").write_bytes(store_meta_bytes)
        (new_root / "events.jsonl").write_bytes(events_bytes)
        for name in manifest["payloads"]:
            body = (source_dir / "payloads" / name).read_bytes()
            if hashlib.sha256(body).hexdigest() != name:
                raise StoreError(f"export tampered: payload {name} hash mismatch")
            (new_root / "payloads" / name).write_bytes(body)
        store = cls(new_root)
        check = store.verify_chain()
        if not check["valid"]:
            raise StoreError(f"imported chain invalid at seq {check['failed_at_seq']}")
        if store.head()["head_hash"] != manifest["head_hash"]:
            raise StoreError("imported head hash does not match manifest")
        return store
