"""Filesystem/process transport with access-first bundle construction."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..canonical import canonical_line, parse_time, sha256
from . import POLICY_VERSION, PROTOCOL_VERSION, SCHEMA_VERSION
from .identity import TestSigner, can_receive
from .models import (AccessMarkingV3, AdmissionOutcome, DistributedEventEnvelope, NodeIdentity,
                     SyncBundle, SyncOffer, SyncReceipt, SyncRequest)
from .node import DistributedNode, IntegrityError, NodeError, _event, _read_json, _write_json


class SyncProtocolError(NodeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SyncAuthorizationError(IntegrityError):
    pass


def _peer_identity(node: DistributedNode, peer_id: str) -> NodeIdentity:
    peers = _read_json(node.root / "peer_identities.json", {})
    if peer_id not in peers:
        raise SyncProtocolError("UNAUTHORIZED", "peer identity is not configured")
    return NodeIdentity(**peers[peer_id])


def _scope_key(request: SyncRequest) -> str:
    # Equivalent access contexts share aliases and projected vector clocks;
    # destination node id is intentionally absent for convergence.
    return sha256({
        "requested_scope": sorted(request.requested_scope),
        "access": request.access_and_releasability_context,
        "schemas": sorted(request.supported_schemas),
    })[:24]


def make_offer(source: DistributedNode, destination_id: str,
               access_context: Mapping[str, Any]) -> SyncOffer:
    destination = _peer_identity(source, destination_id)
    visible = [event for event in source.events() if event.originating_node_id == source.node_id
               and can_receive(AccessMarkingV3(**normalized_marking(event.access_marking)), destination, access_context)]
    token = sha256({"scope": sha256(access_context), "visible_hashes": [event.event_hash for event in visible]})[:24]
    return SyncOffer(
        source_node=source.node_id, protocol_version=PROTOCOL_VERSION,
        opaque_state_token=token, schema_versions=source.manifest.schema_versions,
        policy_versions=source.manifest.policy_versions,
        authorized_sharing_scope=tuple(sorted(set(source.manifest.sharing_scopes) &
                                               set(access_context.get("mission_scopes", ())))),
        integrity_metadata={"algorithm": "SHA256+HMAC_TEST", "state": "VALID",
                            "manifest": sha256(source.manifest.to_record())},
    )


def make_request(destination: DistributedNode, source_id: str, access_context: Mapping[str, Any], *,
                 requested_scope: tuple[str, ...], supported_schemas: tuple[str, ...] = (SCHEMA_VERSION,),
                 max_events: int = 100_000, max_bytes: int = 256_000_000) -> SyncRequest:
    state = destination.sync_peer_state(source_id)
    return SyncRequest(
        destination_node=destination.node_id,
        known_causal_context={source_id: state.get("last_received_token", "GENESIS")},
        requested_scope=requested_scope,
        access_and_releasability_context=dict(access_context), supported_schemas=supported_schemas,
        maximum_bundle_constraints={"max_events": max_events, "max_bytes": max_bytes},
    )


def _stable_alias(source: DistributedNode, original_id: str, scope_key: str) -> str:
    signature = TestSigner.sign(source.identity.key_for(source.node_id),
                                {"purpose": "ACCESS_SCOPED_EVENT_ALIAS", "scope": scope_key,
                                 "original_id": original_id})
    return f"shared-event-{signature[:28]}"


def _project_events(source: DistributedNode, visible: list[DistributedEventEnvelope],
                    request: SyncRequest) -> list[DistributedEventEnvelope]:
    scope_key = _scope_key(request)
    aliases = {event.event_id: _stable_alias(source, event.event_id, scope_key) for event in visible}
    destination = _peer_identity(source, request.destination_node)
    # Direct-origin transport does not retransmit peer events, but their
    # authorized dots remain necessary causal context.  Use all locally known,
    # access-visible events to project vector components while serializing only
    # `visible` origin events.  Omitting this made an event that followed a
    # shared baseline appear concurrent with that baseline after rejoin.
    causal_visible = [event for event in source.events()
                      if event.schema_version in request.supported_schemas and
                      can_receive(AccessMarkingV3(**normalized_marking(event.access_marking)), destination,
                                  request.access_and_releasability_context)]
    by_origin: dict[str, list[DistributedEventEnvelope]] = {}
    for event in causal_visible:
        by_origin.setdefault(event.originating_node_id, []).append(event)
    for events in by_origin.values():
        events.sort(key=lambda item: (item.node_local_sequence, item.event_id))
    projected: list[DistributedEventEnvelope] = []
    for event in visible:
        origin_events = by_origin[event.originating_node_id]
        sequence = 1 + next(index for index, candidate in enumerate(origin_events)
                            if candidate.event_id == event.event_id)
        context: dict[str, int] = {}
        for node_id, raw_required in event.causal_context.items():
            visible_required = sum(1 for candidate in by_origin.get(node_id, ())
                                   if candidate.node_local_sequence <= raw_required)
            if visible_required:
                context[node_id] = visible_required
        # The event's own projected dot is always explicit.
        context[event.originating_node_id] = sequence
        parent_aliases = tuple(sorted(aliases[parent] for parent in event.parent_event_ids if parent in aliases))
        shared_payload, redacted = _redact_payload(event.payload, destination,
                                                   request.access_and_releasability_context)
        shared = replace(
            event, event_id=aliases[event.event_id], node_local_sequence=sequence,
            causal_context=context, parent_event_ids=parent_aliases, event_hash="", admitted_time=None,
            payload=shared_payload, payload_hash=sha256(shared_payload),
            action_envelope=None if redacted else event.action_envelope,
            authentication_state=("SOURCE_ATTESTED_ACCESS_PROJECTION_TEST_SIGNER" if redacted
                                  else event.authentication_state))
        projected.append(replace(shared, event_hash=sha256(shared.hash_record())))
    return projected


def _redact_payload(payload: Mapping[str, Any], destination: NodeIdentity,
                    access_context: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Bounded attribute, relationship and provenance filtering.

    Marked substructures are removed at the source. A redacted sharing event
    is authenticated by the source bundle rather than misrepresenting the
    original actor signature as covering a different payload.
    """
    result = dict(payload)
    redacted = False
    attribute_markings = dict(result.pop("attribute_markings", {}))
    for field, marking in attribute_markings.items():
        if not can_receive(AccessMarkingV3(**normalized_marking(marking)), destination, access_context):
            result.pop(field, None); redacted = True
    relationships = []
    for relationship in result.get("relationships", ()):
        item = dict(relationship)
        marking = item.pop("access_marking", None)
        if marking and not can_receive(AccessMarkingV3(**normalized_marking(marking)), destination, access_context):
            redacted = True; continue
        relationships.append(item)
    if "relationships" in result:
        result["relationships"] = relationships
    provenance_marking = result.pop("provenance_marking", None)
    if provenance_marking and not can_receive(AccessMarkingV3(**normalized_marking(provenance_marking)),
                                              destination, access_context):
        result.pop("provenance", None); redacted = True
    conflict_marking = result.pop("conflict_detail_marking", None)
    if conflict_marking and not can_receive(AccessMarkingV3(**normalized_marking(conflict_marking)),
                                            destination, access_context):
        for field in ("conflict", "conflict_id", "competing_values", "hidden_conflict"):
            result.pop(field, None)
        redacted = True
    if redacted:
        result["access_projection_attestation"] = "SOURCE_TEST_SIGNER_REDACTION_APPLIED"
    return result, redacted


def build_bundle(source: DistributedNode, request: SyncRequest, *, creation_time: str,
                 expiry: str | None = None, reverse_delivery_order: bool = False) -> SyncBundle:
    destination = _peer_identity(source, request.destination_node)
    if not source.identity.node_valid_at(source.node_id, creation_time):
        raise SyncProtocolError("UNAUTHORIZED", "source node is revoked or inactive")
    if not source.identity.node_valid_at(destination.node_id, creation_time):
        raise SyncProtocolError("UNAUTHORIZED", "destination node is revoked, inactive or untrusted")
    if PROTOCOL_VERSION not in destination.supported_protocol_versions:
        raise SyncProtocolError("INCOMPATIBLE", "destination protocol incompatible")
    if not set(source.manifest.schema_versions).intersection(request.supported_schemas):
        raise SyncProtocolError("INCOMPATIBLE", "no compatible distributed schema")
    access = request.access_and_releasability_context
    # Each origin distributes its own append-only history.  This avoids
    # access-alias cascades and makes provenance ownership explicit; the
    # bounded scenario uses direct configured peers, not anonymous gossip.
    visible = [event for event in source.events() if event.originating_node_id == source.node_id
               and event.schema_version in request.supported_schemas
               and can_receive(AccessMarkingV3(**normalized_marking(event.access_marking)), destination, access)]
    projected_all = _project_events(source, visible, request)
    scope_key = _scope_key(request)
    peer_state = source.sync_peer_state(request.destination_node)
    scope_state = peer_state.get("outbound_scopes", {}).get(scope_key, {})
    expected_base = scope_state.get("last_result_token", "GENESIS")
    requested_base = request.known_causal_context.get(source.node_id, "GENESIS")
    if requested_base != expected_base:
        code = "MISSING_BASE" if requested_base == "GENESIS" and expected_base != "GENESIS" else "STALE_BASE"
        raise SyncProtocolError(code, "sync base token does not match source peer state")
    sent_hashes = set(scope_state.get("sent_event_hashes", ()))
    delta = [event for event in projected_all if event.event_hash not in sent_hashes]
    max_events = request.maximum_bundle_constraints.get("max_events", 100_000)
    selected = delta[:max_events]
    if reverse_delivery_order:
        selected = list(reversed(selected))
    after_hashes = sorted(sent_hashes | {event.event_hash for event in selected})
    result_token = sha256({"scope": scope_key, "delivered_event_hashes": after_hashes})[:24]
    event_records = tuple(event.to_record() for event in selected)
    events_sha = sha256(event_records)
    bundle_id = f"bundle-{sha256({'source': source.node_id, 'destination': destination.node_id, 'base': expected_base, 'result': result_token, 'events': events_sha})[:24]}"
    unsigned = SyncBundle(
        bundle_id=bundle_id, source_node=source.node_id, destination_node=destination.node_id,
        protocol_version=PROTOCOL_VERSION, base_state_token=expected_base,
        resulting_state_token=result_token, events=event_records,
        causal_metadata={"mechanism": "ACCESS_SCOPED_VERSION_VECTOR",
                         "scope_token": scope_key, "order": "REVERSED_VALID_SET" if reverse_delivery_order else "CAUSAL_SOURCE_ORDER"},
        schema_and_mapping_references=tuple(sorted(set(source.manifest.schema_versions) &
                                                   set(request.supported_schemas))),
        provenance={"originating_authority": source.manifest.authority,
                    "transport": "FILESYSTEM_PROCESS_ISOLATED"},
        access_markings=tuple(sorted({sha256(event.access_marking)[:16] for event in selected})),
        omissions_declaration=("Records, attributes, relationships, provenance and causal parents may be omitted "
                               "by policy. This declaration intentionally discloses no hidden counts or identifiers."),
        integrity_manifest={"events_sha256": events_sha, "scope_sha256": sha256(access),
                            "algorithm": "SHA256+HMAC_SHA256_TEST"},
        creation_time=creation_time, expiry=expiry, signature="",
    )
    signing = unsigned.to_record()
    signing.pop("signature")
    bundle = replace(unsigned, signature=TestSigner.sign(source.identity.key_for(source.node_id), signing))
    return bundle


def acknowledge_receipt(source: DistributedNode, bundle: SyncBundle, receipt: SyncReceipt) -> None:
    """Advance source-side sync state only after an accepted receipt.

    This preserves safe retry after a lost/delayed bundle: bundle construction
    alone never advances the source's peer watermark.
    """
    if receipt.bundle_identifier != bundle.bundle_id or receipt.verification_state not in ("ACCEPTED", "DUPLICATE"):
        return
    peer_state = source.sync_peer_state(bundle.destination_node)
    scope_key = bundle.causal_metadata["scope_token"]
    outbound = dict(peer_state.get("outbound_scopes", {}))
    previous = outbound.get(scope_key, {})
    sent_hashes = set(previous.get("sent_event_hashes", ()))
    sent_hashes.update(_event(record).event_hash for record in bundle.events)
    outbound[scope_key] = {"last_result_token": bundle.resulting_state_token,
                           "sent_event_hashes": sorted(sent_hashes),
                           "last_bundle_id": bundle.bundle_id}
    source.update_sync_state(bundle.destination_node, outbound_scopes=outbound)


def write_bundle(bundle: SyncBundle, path: str | Path) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_line(bundle.to_record()) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": sha256(bundle.to_record()), "bundle_id": bundle.bundle_id}


def read_bundle(path: str | Path) -> SyncBundle:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("events", "schema_and_mapping_references", "access_markings"):
        data[key] = tuple(data.get(key, ()))
    return SyncBundle(**data)


def verify_bundle(destination: DistributedNode, bundle: SyncBundle, *, at_time: str) -> None:
    if bundle.destination_node != destination.node_id:
        raise IntegrityError("bundle addressed to another destination")
    if bundle.protocol_version != PROTOCOL_VERSION:
        raise IntegrityError("bundle protocol incompatible")
    if bundle.expiry and parse_time(bundle.expiry) <= parse_time(at_time):
        raise IntegrityError("bundle expired")
    if not destination.identity.node_valid_at(bundle.source_node, at_time):
        raise SyncAuthorizationError("source node revoked, inactive or untrusted")
    if sha256(bundle.events) != bundle.integrity_manifest.get("events_sha256"):
        raise IntegrityError("bundle event-set integrity mismatch")
    signing = bundle.to_record()
    signing.pop("signature")
    if not TestSigner.verify(destination.identity.key_for(bundle.source_node), signing, bundle.signature):
        raise IntegrityError("bundle signature invalid")
    for record in bundle.events:
        event = _event(record)
        if not event.verify_hashes():
            raise IntegrityError("bundle contains tampered event")


def import_bundle(destination: DistributedNode, bundle: SyncBundle, *, admitted_time: str) -> SyncReceipt:
    state = destination.sync_peer_state(bundle.source_node)
    received_ids = set(state.get("received_bundle_ids", ()))
    if bundle.bundle_id in received_ids:
        return SyncReceipt(
            bundle_identifier=bundle.bundle_id, verification_state="DUPLICATE",
            accepted_events=(), duplicates=(), quarantines=(), causal_gaps=(), conflicts=(),
            resulting_opaque_state_token=state.get("last_received_token", "GENESIS"),
            public_summary={"status": "DUPLICATE", "detail": "previously verified bundle"})
    try:
        verify_bundle(destination, bundle, at_time=admitted_time)
    except SyncAuthorizationError as exc:
        _write_json(destination.root / f"quarantined_bundle_{sha256(bundle.bundle_id)[:12]}.json",
                    {"bundle_id": bundle.bundle_id, "outcome": "UNAUTHORIZED",
                     "reason_category": "source node revoked, inactive or untrusted"})
        return SyncReceipt(
            bundle_identifier=bundle.bundle_id, verification_state="UNAUTHORIZED",
            accepted_events=(), duplicates=(), quarantines=("bundle",), causal_gaps=(), conflicts=(),
            resulting_opaque_state_token=state.get("last_received_token", "GENESIS"),
            public_summary={"status": "REJECTED", "detail": "node authority verification failed"})
    except IntegrityError as exc:
        _write_json(destination.root / f"quarantined_bundle_{sha256(bundle.bundle_id)[:12]}.json",
                    {"bundle_id": bundle.bundle_id, "outcome": "TAMPERED_OR_UNAUTHORIZED",
                     "reason_category": str(exc)})
        return SyncReceipt(
            bundle_identifier=bundle.bundle_id, verification_state="TAMPERED",
            accepted_events=(), duplicates=(), quarantines=("bundle",), causal_gaps=(), conflicts=(),
            resulting_opaque_state_token=state.get("last_received_token", "GENESIS"),
            public_summary={"status": "REJECTED", "detail": "integrity or authority verification failed"})
    expected_base = state.get("last_received_token", "GENESIS")
    if bundle.base_state_token != expected_base:
        outcome = "MISSING_BASE" if expected_base == "GENESIS" else "STALE_BASE"
        return SyncReceipt(
            bundle_identifier=bundle.bundle_id, verification_state=outcome,
            accepted_events=(), duplicates=(), quarantines=(), causal_gaps=(),
            conflicts=("STALE_BASE_CONFLICT",), resulting_opaque_state_token=expected_base,
            public_summary={"status": "CONFLICT", "detail": "sync base unavailable or stale"})
    pending = [_event(record) for record in bundle.events]
    accepted: list[str] = []
    duplicates: list[str] = []
    quarantines: list[str] = []
    gaps: list[str] = []
    conflicts: list[str] = []
    # Retry a reversed but correctly signed event set until dependencies land.
    while pending:
        progress = False
        still_pending: list[DistributedEventEnvelope] = []
        for event in pending:
            outcome, conflict_ids = destination.admit_remote_event(event, admitted_time)
            if outcome == AdmissionOutcome.ACCEPTED.value:
                accepted.append(event.event_id); conflicts.extend(conflict_ids); progress = True
            elif outcome == AdmissionOutcome.DUPLICATE.value:
                duplicates.append(event.event_id); progress = True
            elif outcome == AdmissionOutcome.CAUSAL_GAP.value:
                still_pending.append(event)
            else:
                quarantines.append(event.event_id)
        if not still_pending or not progress:
            gaps.extend(event.event_id for event in still_pending)
            break
        pending = still_pending
    verification_state = "ACCEPTED" if not quarantines and not gaps else "PARTIAL"
    if verification_state == "ACCEPTED":
        received_ids.add(bundle.bundle_id)
        destination.update_sync_state(
            bundle.source_node, last_received_token=bundle.resulting_state_token,
            received_bundle_ids=sorted(received_ids), last_bundle_id=bundle.bundle_id)
    destination.persist_projection()
    return SyncReceipt(
        bundle_identifier=bundle.bundle_id, verification_state=verification_state,
        accepted_events=tuple(accepted), duplicates=tuple(duplicates), quarantines=tuple(quarantines),
        causal_gaps=tuple(gaps), conflicts=tuple(sorted(set(conflicts))),
        resulting_opaque_state_token=(bundle.resulting_state_token if verification_state == "ACCEPTED"
                                      else expected_base),
        public_summary={"status": verification_state,
                        "detail": "authorized event set processed; restricted counts and identifiers withheld"},
    )


def normalized_marking(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owning_authority": record["owning_authority"],
        "compartments": tuple(record.get("compartments", ())),
        "releasability": tuple(record.get("releasability", ())),
        "mission_scopes": tuple(record.get("mission_scopes", ())),
        "min_role": record.get("min_role", "OBSERVER"),
        "originator_controls": tuple(record.get("originator_controls", ())),
        "sanitized": bool(record.get("sanitized", False)),
    }
