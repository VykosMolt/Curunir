"""Independently persisted V3 node histories and conflict-aware projections."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..canonical import canonical_line, parse_time, sha256, utc_now
from . import AUTHENTICATION_STATUS, POLICY_VERSION, PROTOCOL_VERSION, SCHEMA_VERSION
from .identity import (AuthenticationError, AuthorizationError, IdentityProvider, can_receive,
                       authorize)
from .merge import MergePolicyRegistry, detect_conflicts, materialize_conflict_status, semantic_subject
from .models import (AccessMarkingV3, ActorIdentity, AdmissionOutcome, AuthenticatedActionEnvelope,
                     DistributedConflictRecord, DistributedEventEnvelope, NodeIdentity, NodeManifest,
                     RevocationRecord, VersionVector)


class NodeError(ValueError):
    pass


class IntegrityError(NodeError):
    pass


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_line(value) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_line(value) + "\n")
        handle.flush()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.exists() else []


def _marking(record: Mapping[str, Any]) -> AccessMarkingV3:
    return AccessMarkingV3(
        owning_authority=str(record["owning_authority"]),
        compartments=tuple(record.get("compartments", ())),
        releasability=tuple(record.get("releasability", ())),
        mission_scopes=tuple(record.get("mission_scopes", ())),
        min_role=str(record.get("min_role", "OBSERVER")),
        originator_controls=tuple(record.get("originator_controls", ())),
        sanitized=bool(record.get("sanitized", False)),
    )


def _event(record: Mapping[str, Any]) -> DistributedEventEnvelope:
    data = dict(record)
    data["parent_event_ids"] = tuple(data.get("parent_event_ids", ()))
    return DistributedEventEnvelope(**data)


class DistributedNode:
    """One node store.  No object or memory is shared with peer processes."""

    REQUIRED_FILES = (
        "node_manifest.json", "events.jsonl", "conflicts.jsonl", "quarantine.jsonl",
        "schema_registry.json", "access_policy.json", "sync_state.json", "peer_identities.json",
    )

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not (self.root / "node_manifest.json").exists():
            raise NodeError(f"not a distributed node store: {self.root}")
        self.manifest = NodeManifest(**_read_json(self.root / "node_manifest.json", {}))
        if Path(self.manifest.store_path).resolve() != self.root.resolve():
            raise IntegrityError("node manifest store path does not identify this store")
        self.identity = IdentityProvider(self.root / "identity")
        self.merge_registry = MergePolicyRegistry()
        self.events_path = self.root / "events.jsonl"
        self.conflicts_path = self.root / "conflicts.jsonl"
        self.quarantine_path = self.root / "quarantine.jsonl"
        self.sync_state_path = self.root / "sync_state.json"
        self.projection_path = self.root / "projection_state.json"
        self._events = [_event(record) for record in _load_jsonl(self.events_path)]
        self._validate_store()
        self._event_ids = {event.event_id for event in self._events}
        self._local_sequence = sum(1 for event in self._events if event.originating_node_id == self.node_id)
        known: dict[str, int] = {}
        self._subject_index: dict[tuple[str, str], list[DistributedEventEnvelope]] = {}
        for event in self._events:
            for node_id, seq in event.causal_context.items():
                known[node_id] = max(known.get(node_id, 0), seq)
            key = (str(event.payload.get("record_type", "")), semantic_subject(event))
            self._subject_index.setdefault(key, []).append(event)
        self._known_vector_cache = VersionVector(known)

    @classmethod
    def create(cls, root: str | Path, identity: NodeIdentity, actors: tuple[ActorIdentity, ...],
               keys: Mapping[str, str], peers: Mapping[str, NodeIdentity], *,
               schema_versions: tuple[str, ...] = (SCHEMA_VERSION,),
               policy_versions: tuple[str, ...] = (POLICY_VERSION,)) -> "DistributedNode":
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise NodeError(f"refusing to initialize non-empty node store: {root}")
        root.mkdir(parents=True, exist_ok=True)
        manifest = NodeManifest(
            node_identifier=identity.node_id, authority=identity.owning_authority,
            role=identity.role, store_path=str(root.resolve()), protocol_version=PROTOCOL_VERSION,
            schema_versions=schema_versions, policy_versions=policy_versions,
            trust_state=identity.trust_state, sharing_scopes=identity.sharing_scopes,
            integrity_state="VALID",
            peer_configuration={node_id: {"authority": peer.owning_authority, "role": peer.role,
                                           "trust_state": peer.trust_state}
                                for node_id, peer in sorted(peers.items())},
        )
        _write_json(root / "node_manifest.json", manifest.to_record())
        _write_json(root / "schema_registry.json", {version: {"status": "ACTIVE"} for version in schema_versions})
        _write_json(root / "access_policy.json", {"current": POLICY_VERSION, "versions": list(policy_versions),
                                                   "merge_policy_registry": MergePolicyRegistry().manifest()})
        _write_json(root / "sync_state.json", {"peers": {}, "quarantine_state": "EMPTY"})
        _write_json(root / "peer_identities.json", {key: value.to_record() for key, value in peers.items()})
        for name in ("events.jsonl", "conflicts.jsonl", "quarantine.jsonl"):
            (root / name).touch()
        IdentityProvider.initialize(root / "identity", identity, actors, keys)
        node = cls(root)
        node.persist_projection()
        return node

    @property
    def node_id(self) -> str:
        return self.manifest.node_identifier

    def _validate_store(self) -> None:
        seen: set[str] = set()
        local_sequences: list[int] = []
        for envelope in self._events:
            if envelope.event_id in seen:
                raise IntegrityError(f"duplicate event id in node history: {envelope.event_id}")
            if not envelope.verify_hashes():
                raise IntegrityError(f"event integrity invalid: {envelope.event_id}")
            seen.add(envelope.event_id)
            if envelope.originating_node_id == self.node_id:
                local_sequences.append(envelope.node_local_sequence)
        if local_sequences != list(range(1, len(local_sequences) + 1)):
            raise IntegrityError("local sequence history is not contiguous")

    def events(self) -> list[DistributedEventEnvelope]:
        return list(self._events)

    def conflicts(self) -> list[DistributedConflictRecord]:
        return [DistributedConflictRecord(**{**record,
                                             "involved_events": tuple(record["involved_events"]),
                                             "involved_nodes": tuple(record["involved_nodes"]),
                                             "competing_values": tuple(record["competing_values"]),
                                             "evidence_snapshot": tuple(record["evidence_snapshot"]),
                                             "audit_history": tuple(record.get("audit_history", ()))})
                for record in _load_jsonl(self.conflicts_path)]

    def local_head(self) -> dict[str, Any]:
        local = [event for event in self._events if event.originating_node_id == self.node_id]
        return {
            "node_id": self.node_id,
            "opaque_state_token": self.opaque_state_token(),
            "integrity_state": "VALID",
            "local_event_count_privileged": len(local),
            "admitted_event_count_privileged": len(self._events),
        }

    def _known_vector(self) -> VersionVector:
        return self._known_vector_cache

    def _next_local_sequence(self) -> int:
        return self._local_sequence + 1

    def opaque_state_token(self, access_context: Mapping[str, Any] | None = None) -> str:
        visible = self.visible_events(access_context) if access_context is not None else self._events
        # Access-scoped: hidden events do not perturb a lower-access token.
        return sha256({"node": self.node_id, "visible_hashes": [event.event_hash for event in visible]})[:24]

    def visible_events(self, access_context: Mapping[str, Any] | None) -> list[DistributedEventEnvelope]:
        if access_context is None:
            return list(self._events)
        destination = self.identity.node
        return [event for event in self._events if can_receive(_marking(event.access_marking), destination, access_context)]

    def append_action(self, *, actor_id: str, action_type: str, event_type: str,
                      payload: Mapping[str, Any], marking: AccessMarkingV3,
                      recorded_time: str, nonce: str, valid_from: str | None = None,
                      valid_to: str | None = None, parent_event_ids: tuple[str, ...] = (),
                      object_ref: str = "", object_owner: str = "", mission_scope: str = "",
                      conflict_type: str = "", schema_version: str = SCHEMA_VERSION) -> DistributedEventEnvelope:
        data = dict(payload)
        record_type = str(data.get("record_type", ""))
        self.merge_registry.policy_for(record_type)
        if schema_version not in self.manifest.schema_versions:
            raise NodeError(f"local schema version is not registered: {schema_version}")
        action = self.identity.make_action(actor_id, action_type, data, recorded_time, nonce)
        verified = self.identity.verify_action(action, data, expected_node=self.node_id)
        actor = self.identity.actor(actor_id)
        decision = authorize(actor, action_type, object_ref or semantic_subject_payload(data), recorded_time,
                             object_owner=object_owner, mission_scope=mission_scope,
                             conflict_type=conflict_type, is_provider="PROVIDER" in actor.roles)
        self.identity.record_decision(decision)
        if decision.result != "ALLOW":
            raise AuthorizationError(decision.rationale)
        missing = set(parent_event_ids) - self._event_ids
        if missing:
            raise NodeError("local append has missing causal parents")
        sequence = self._next_local_sequence()
        vector = self._known_vector().increment(self.node_id)
        seed = {"node": self.node_id, "sequence": sequence, "action": verified.action_id,
                "payload_hash": sha256(data)}
        event_id = f"event-{self.node_id.lower()}-{sha256(seed)[:24]}"
        envelope = DistributedEventEnvelope(
            event_id=event_id, originating_node_id=self.node_id,
            node_local_sequence=sequence, causal_context=vector.values,
            parent_event_ids=parent_event_ids, event_type=event_type,
            valid_time={"from": valid_from or recorded_time, "to": valid_to},
            recorded_time=recorded_time, actor_id=actor_id,
            access_marking=marking.to_record(), schema_version=schema_version,
            payload=data, payload_hash=sha256(data), event_hash="",
            authentication_state=verified.verification_result,
            action_envelope=verified.to_record(), admitted_time=recorded_time,
        )
        envelope = replace(envelope, event_hash=sha256(envelope.hash_record()))
        self._append_admitted(envelope, detect=True)
        return envelope

    def append_batch(self, specs: Iterable[Mapping[str, Any]]) -> list[DistributedEventEnvelope]:
        """Efficient production append path used by bounded stress execution."""
        appended: list[DistributedEventEnvelope] = []
        for spec in specs:
            appended.append(self.append_action(**dict(spec)))
        return appended

    def _append_admitted(self, envelope: DistributedEventEnvelope, *, detect: bool) -> list[str]:
        conflict_ids: list[str] = []
        if detect:
            key = (str(envelope.payload.get("record_type", "")), semantic_subject(envelope))
            conflicts = detect_conflicts(envelope, self._subject_index.get(key, ()), self.merge_registry,
                                         envelope.admitted_time or envelope.recorded_time)
            existing_conflicts = {item.conflict_id for item in self.conflicts()} if conflicts else set()
            for conflict in conflicts:
                if conflict.conflict_id not in existing_conflicts:
                    _append_jsonl(self.conflicts_path, conflict.to_record())
                    conflict_ids.append(conflict.conflict_id)
                    existing_conflicts.add(conflict.conflict_id)
        _append_jsonl(self.events_path, envelope.to_record())
        self._events.append(envelope)
        self._event_ids.add(envelope.event_id)
        if envelope.originating_node_id == self.node_id:
            self._local_sequence = max(self._local_sequence, envelope.node_local_sequence)
        self._known_vector_cache = self._known_vector_cache.merged(VersionVector(envelope.causal_context))
        key = (str(envelope.payload.get("record_type", "")), semantic_subject(envelope))
        self._subject_index.setdefault(key, []).append(envelope)
        return conflict_ids

    def admit_remote_event(self, envelope: DistributedEventEnvelope, admitted_time: str) -> tuple[str, list[str]]:
        if envelope.event_id in self._event_ids:
            existing = next(event for event in self._events if event.event_id == envelope.event_id)
            if existing.event_hash != envelope.event_hash:
                self._quarantine(envelope, AdmissionOutcome.TAMPERED.value, "identifier collision")
                return AdmissionOutcome.TAMPERED.value, []
            return AdmissionOutcome.DUPLICATE.value, []
        if envelope.schema_version not in self.manifest.schema_versions:
            self._quarantine(envelope, AdmissionOutcome.INCOMPATIBLE.value, "schema version unsupported")
            return AdmissionOutcome.INCOMPATIBLE.value, []
        if not envelope.verify_hashes():
            self._quarantine(envelope, AdmissionOutcome.TAMPERED.value, "event hash invalid")
            return AdmissionOutcome.TAMPERED.value, []
        if set(envelope.parent_event_ids) - self._event_ids:
            self._quarantine(envelope, AdmissionOutcome.CAUSAL_GAP.value, "missing visible parent")
            return AdmissionOutcome.CAUSAL_GAP.value, []
        known = self._known_vector().values
        for node_id, required in envelope.causal_context.items():
            if node_id == envelope.originating_node_id:
                if required > known.get(node_id, 0) + 1:
                    self._quarantine(envelope, AdmissionOutcome.CAUSAL_GAP.value, "origin sequence gap")
                    return AdmissionOutcome.CAUSAL_GAP.value, []
            elif required > known.get(node_id, 0):
                self._quarantine(envelope, AdmissionOutcome.CAUSAL_GAP.value, "causal context gap")
                return AdmissionOutcome.CAUSAL_GAP.value, []
        if envelope.action_envelope:
            action_data = dict(envelope.action_envelope)
            action = AuthenticatedActionEnvelope(**action_data)
            try:
                self.identity.verify_action(action, envelope.payload, expected_node=envelope.originating_node_id,
                                            consume_nonce=False, historical=True)
            except AuthenticationError as exc:
                self._quarantine(envelope, AdmissionOutcome.UNAUTHORIZED.value, str(exc))
                return AdmissionOutcome.UNAUTHORIZED.value, []
        admitted = replace(envelope, admitted_time=admitted_time)
        return AdmissionOutcome.ACCEPTED.value, self._append_admitted(admitted, detect=True)

    def _quarantine(self, envelope: DistributedEventEnvelope, outcome: str, reason: str) -> None:
        _append_jsonl(self.quarantine_path, {"outcome": outcome, "reason_category": reason,
                                             "event": envelope.to_record()})
        state = _read_json(self.sync_state_path, {"peers": {}})
        state["quarantine_state"] = "NON_EMPTY"
        _write_json(self.sync_state_path, state)

    def revoke(self, record: RevocationRecord) -> None:
        self.identity.revoke(record)

    def resolve_conflict(self, conflict_id: str, *, actor_id: str, resolution: Mapping[str, Any],
                         recorded_time: str, nonce: str, marking: AccessMarkingV3) -> DistributedEventEnvelope:
        conflicts = {conflict.conflict_id: conflict for conflict in self.conflicts()}
        if conflict_id not in conflicts:
            raise NodeError(f"unknown conflict: {conflict_id}")
        conflict = conflicts[conflict_id]
        payload = {"record_type": "conflict_resolution", "conflict_id": conflict_id,
                   "subject_id": conflict.affected_object_or_workflow, "status": "RESOLVED",
                   "selected_value": dict(resolution), "evidence_snapshot": list(conflict.evidence_snapshot)}
        return self.append_action(
            actor_id=actor_id, action_type="CONFLICT_RESOLUTION", event_type="CONFLICT_RESOLVED",
            payload=payload, marking=marking, recorded_time=recorded_time, nonce=nonce,
            parent_event_ids=conflict.involved_events, object_ref=conflict.affected_object_or_workflow,
            conflict_type=conflict.conflict_type)

    def projection(self, access_context: Mapping[str, Any], *, valid_at: str | None = None,
                   known_at: str | None = None, privileged_audit: bool = False) -> dict[str, Any]:
        # Current authorization applies even for history unless explicit audit
        # is requested by an AUDITOR context.
        if privileged_audit and "AUDITOR" not in access_context.get("roles", ()):
            raise AuthorizationError("privileged temporal audit requires AUDITOR role")
        events = self.visible_events(access_context)
        if known_at:
            point = parse_time(known_at)
            events = [event for event in events if event.admitted_time and parse_time(event.admitted_time) <= point]
        if valid_at:
            point = parse_time(valid_at)
            events = [event for event in events if
                      (not event.valid_time.get("from") or parse_time(str(event.valid_time["from"])) <= point) and
                      (not event.valid_time.get("to") or point < parse_time(str(event.valid_time["to"])))]
        groups: dict[tuple[str, str], list[DistributedEventEnvelope]] = {}
        union: list[dict[str, Any]] = []
        for event in events:
            record_type = str(event.payload.get("record_type", ""))
            policy = self.merge_registry.policy_for(record_type)
            if policy.category in ("UNION", "CORRECTION", "IDENTITY") or record_type == "conflict_resolution":
                union.append(event.to_record())
            else:
                groups.setdefault((record_type, semantic_subject(event)), []).append(event)
        state: list[dict[str, Any]] = []
        for (record_type, subject), candidates in sorted(groups.items()):
            maximal: list[DistributedEventEnvelope] = []
            for candidate in candidates:
                relation_to_any_after = any(
                    VersionVector(candidate.causal_context).relation(VersionVector(other.causal_context)).value == "BEFORE"
                    for other in candidates if other.event_id != candidate.event_id)
                if not relation_to_any_after:
                    maximal.append(candidate)
            policy = self.merge_registry.policy_for(record_type)
            state.append({
                "record_type": record_type, "subject_id": subject,
                "merge_policy": policy.name,
                "status": "CONFLICT" if len({sha256(item.payload) for item in maximal}) > 1 else "MATERIALIZED",
                "values": [item.payload for item in sorted(maximal, key=lambda item: item.event_id)],
                "event_ids": [item.event_id for item in sorted(maximal, key=lambda item: item.event_id)],
                "fail_closed": policy.fail_closed and len(maximal) > 1,
            })
        visible_ids = {event.event_id for event in events}
        conflicts = [materialize_conflict_status(conflict, events) for conflict in self.conflicts()
                     if set(conflict.involved_events) <= visible_ids]
        result = {
            "node_id": self.node_id,
            "node_authority": self.manifest.authority,
            "valid_at": valid_at,
            "known_at": known_at,
            "authorization_mode": "PRIVILEGED_AUDIT" if privileged_audit else "CURRENT_POLICY",
            "opaque_state_token": sha256({"node": self.node_id,
                                            "visible": [event.event_hash for event in events]})[:24],
            "state": state,
            "union_records": union,
            "conflicts": conflicts,
            "visible_summary": {
                "state": len(state), "union_records": len(union),
                "open_conflicts": sum(1 for conflict in conflicts if conflict["status"] != "RESOLVED"),
            },
            "limitations": ["current authorization applied to historical query",
                            "absence of a visible record does not disclose whether a restricted record exists"],
        }
        return result

    def persist_projection(self, access_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        access_context = access_context or {
            "roles": ("AUDITOR",), "compartments": tuple(sorted({item for event in self._events
                                                                    for item in event.access_marking.get("compartments", ())})),
            "releasability": tuple(sorted({item for event in self._events
                                            for item in event.access_marking.get("releasability", ())})),
            "mission_scopes": tuple(self.manifest.sharing_scopes),
        }
        projection = self.projection(access_context)
        _write_json(self.projection_path, projection)
        return projection

    def semantic_projection_hash(self, access_context: Mapping[str, Any], *,
                                 valid_at: str | None = None, known_at: str | None = None) -> str:
        """Convergence hash over authorized semantics, not transport aliases."""
        projection = self.projection(access_context, valid_at=valid_at, known_at=known_at)
        semantic_state = [{"record_type": item["record_type"], "subject_id": item["subject_id"],
                           "merge_policy": item["merge_policy"], "status": item["status"],
                           "values": sorted(item["values"], key=sha256), "fail_closed": item["fail_closed"]}
                          for item in projection["state"]]
        semantic_union = sorted((item["payload"] for item in projection["union_records"]), key=sha256)
        semantic_conflicts = sorted(({
            "conflict_id": item["conflict_id"], "conflict_type": item["conflict_type"],
            "subject": item["affected_object_or_workflow"], "status": item["status"],
            "values": sorted(item["competing_values"], key=sha256),
            "resolution_policy": item["resolution_policy"],
            "resolved": bool(item.get("resolution_event_id")),
        } for item in projection["conflicts"]), key=lambda item: item["conflict_id"])
        return sha256({"state": semantic_state, "union": semantic_union, "conflicts": semantic_conflicts})

    def update_sync_state(self, peer_id: str, **updates: Any) -> None:
        state = _read_json(self.sync_state_path, {"peers": {}})
        state.setdefault("peers", {}).setdefault(peer_id, {}).update(updates)
        _write_json(self.sync_state_path, state)

    def sync_peer_state(self, peer_id: str) -> dict[str, Any]:
        return dict(_read_json(self.sync_state_path, {"peers": {}}).get("peers", {}).get(peer_id, {}))

    def export_open(self, directory: str | Path) -> dict[str, Any]:
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()):
            raise NodeError(f"refusing to overwrite non-empty export: {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            files[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest = {
            "format": "curunir-distributed-node-open-export-v3", "node_id": self.node_id,
            "files": files, "source_integrity": self.verify_integrity(),
            "provider_reinvocations": 0,
        }
        _write_json(directory / "export_manifest.json", manifest)
        return manifest

    @classmethod
    def import_open(cls, source: str | Path, new_root: str | Path) -> "DistributedNode":
        source, new_root = Path(source), Path(new_root)
        manifest = _read_json(source / "export_manifest.json", {})
        if manifest.get("format") != "curunir-distributed-node-open-export-v3":
            raise IntegrityError("unsupported distributed node export")
        if new_root.exists() and any(new_root.iterdir()):
            raise NodeError(f"refusing to import over non-empty path: {new_root}")
        new_root.mkdir(parents=True, exist_ok=True)
        for relative, expected in manifest["files"].items():
            body = (source / relative).read_bytes()
            if hashlib.sha256(body).hexdigest() != expected:
                raise IntegrityError(f"export file tampered: {relative}")
            target = new_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        node_manifest_path = new_root / "node_manifest.json"
        node_manifest = _read_json(node_manifest_path, {})
        node_manifest["store_path"] = str(new_root.resolve())
        _write_json(node_manifest_path, node_manifest)
        node = cls(new_root)
        if not node.verify_integrity()["valid"]:
            raise IntegrityError("imported distributed history invalid")
        return node

    def verify_integrity(self) -> dict[str, Any]:
        try:
            self._validate_store()
        except IntegrityError as exc:
            return {"valid": False, "reason": str(exc)}
        return {"valid": True, "node_id": self.node_id,
                "opaque_state_token": self.opaque_state_token(), "event_count_privileged": len(self._events)}


def semantic_subject_payload(payload: Mapping[str, Any]) -> str:
    return str(payload.get("subject_id") or payload.get("object_id") or payload.get("workflow_id") or
               payload.get("task_id") or payload.get("policy_id") or payload.get("association_id") or "UNSPECIFIED")


def joint_knowledge(nodes: Iterable[DistributedNode], access_context: Mapping[str, Any], *,
                    valid_at: str | None = None, known_at: str | None = None) -> dict[str, Any]:
    projections = [node.projection(access_context, valid_at=valid_at, known_at=known_at) for node in nodes]
    canonical_states = sorted((state for projection in projections for state in projection["state"]),
                              key=lambda state: (state["record_type"], state["subject_id"], sha256(state)))
    conflicts = sorted((conflict for projection in projections for conflict in projection["conflicts"]),
                       key=lambda conflict: conflict["conflict_id"])
    return {
        "node_context": "JOINT_SYNCHRONIZED_SYSTEM",
        "valid_at": valid_at, "known_at": known_at,
        "contributing_nodes": sorted(node.node_id for node in nodes),
        "state": canonical_states, "conflicts": conflicts,
        "integrity_hash": sha256({"state": canonical_states, "conflicts": conflicts}),
        "authorization_mode": "CURRENT_POLICY",
    }
