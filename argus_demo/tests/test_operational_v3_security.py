from __future__ import annotations

from dataclasses import replace

import pytest

from curunir_operational.canonical import sha256
from curunir_operational.v3.identity import AuthorizationError, TestSigner as V3TestSigner
from curunir_operational.v3.models import AdmissionOutcome
from curunir_operational.v3.sync import build_bundle, import_bundle, make_request

from test_operational_v3_distributed import (MISSION, PARTNER_ACCESS, PUBLIC, T0, T1, T2,
                                             append_observation, make_nodes)

pytestmark = pytest.mark.no_db


def test_spoofed_node_bundle_truncation_and_replayed_bundle(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    append_observation(source, "a", 1); append_observation(source, "b", 2, time=T1, nonce="b")
    request = make_request(destination, source.node_id, PARTNER_ACCESS, requested_scope=(MISSION,))
    bundle = build_bundle(source, request, creation_time=T1)
    spoofed = replace(bundle, signature="0" * 64)
    assert import_bundle(destination, spoofed, admitted_time=T1).verification_state == "TAMPERED"
    truncated = replace(bundle, events=bundle.events[:-1])
    assert import_bundle(destination, truncated, admitted_time=T1).verification_state == "TAMPERED"
    accepted = import_bundle(destination, bundle, admitted_time=T1)
    assert accepted.verification_state == "ACCEPTED"
    assert import_bundle(destination, bundle, admitted_time=T2).verification_state == "DUPLICATE"


def test_causal_forgery_and_identifier_collision_quarantined(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    event = append_observation(source, "a", 1)
    request = make_request(destination, source.node_id, PARTNER_ACCESS, requested_scope=(MISSION,))
    bundle = build_bundle(source, request, creation_time=T1)
    shared = bundle.events[0]
    from curunir_operational.v3.node import _event
    admitted = _event(shared)
    assert destination.admit_remote_event(admitted, T1)[0] == AdmissionOutcome.ACCEPTED.value
    collision_payload = {**admitted.payload, "value": 999}
    collision = replace(admitted, payload=collision_payload, payload_hash=sha256(collision_payload),
                        event_hash="", action_envelope=None)
    collision = replace(collision, event_hash=sha256(collision.hash_record()))
    assert destination.admit_remote_event(collision, T2)[0] == AdmissionOutcome.TAMPERED.value
    forged = replace(admitted, event_id="forged-event", node_local_sequence=9,
                     causal_context={admitted.originating_node_id: 9}, event_hash="", action_envelope=None)
    forged = replace(forged, event_hash=sha256(forged.hash_record()))
    assert destination.admit_remote_event(forged, T2)[0] == AdmissionOutcome.CAUSAL_GAP.value


def test_unauthorized_resolution_task_hijack_and_annotation_laundering_refused(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    with pytest.raises(AuthorizationError):
        node.append_action(actor_id="log-analyst", action_type="CONFLICT_RESOLUTION",
                           event_type="RESOLVE", payload={"record_type": "conflict_resolution",
                           "subject_id": "bridge", "conflict_id": "x", "status": "RESOLVED"},
                           marking=PUBLIC, recorded_time=T0, nonce="unauthorized-resolution",
                           conflict_type="CONTRADICTORY_OPERATIONAL_STATUS", mission_scope=MISSION)
    with pytest.raises(AuthorizationError, match="ownership"):
        node.append_action(actor_id="log-analyst", action_type="TASK_ASSIGNMENT", event_type="TASK",
                           payload={"record_type": "task_assignment", "subject_id": "civil-task",
                                    "assigned_to": "log-team"}, marking=PUBLIC, recorded_time=T0,
                           nonce="task-hijack", object_owner="CIV-AUTH", mission_scope=MISSION)
    annotation = node.append_action(actor_id="log-analyst", action_type="ANNOTATION", event_type="ANNOTATION",
                                    payload={"record_type": "annotation", "subject_id": "bridge",
                                             "state": "ACCEPTED_ANALYTICAL_STATE"}, marking=PUBLIC,
                                    recorded_time=T0, nonce="annotation", mission_scope=MISSION)
    projection = node.projection(PARTNER_ACCESS)
    assert annotation.event_id in {item["event_id"] for item in projection["union_records"]}
    assert not projection["state"]


def test_current_policy_applies_to_history_without_audit_role(tmp_path):
    node = make_nodes(tmp_path)["CIVIL_PROTECTION_NODE"]
    from test_operational_v3_distributed import RESTRICTED
    append_observation(node, "historically-secret", "UNSAFE", actor="civil-engineer",
                       nonce="secret-history", marking=RESTRICTED)
    view = node.projection(PARTNER_ACCESS, known_at=T1, valid_at=T0)
    assert "historically-secret" not in str(view)
    with pytest.raises(AuthorizationError, match="AUDITOR"):
        node.projection(PARTNER_ACCESS, known_at=T1, privileged_audit=True)
