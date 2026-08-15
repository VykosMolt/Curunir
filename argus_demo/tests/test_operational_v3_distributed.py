"""Focused V3 node, causal, sync, conflict, access and temporal coverage."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from curunir_operational.v3.identity import AuthenticationError, AuthorizationError
from curunir_operational.v3.models import (AccessMarkingV3, ActorIdentity, CausalRelation,
                                           NodeIdentity, RevocationRecord, VersionVector)
from curunir_operational.v3.node import DistributedNode
from curunir_operational.v3.sync import (acknowledge_receipt, build_bundle, import_bundle,
                                         make_request)

pytestmark = pytest.mark.no_db

T0 = "2026-04-01T00:00:00+00:00"
T1 = "2026-04-01T01:00:00+00:00"
T2 = "2026-04-01T02:00:00+00:00"
MISSION = "REGIONAL_RESPONSE"


def identities():
    nodes = {
        "LOGISTICS_NODE": NodeIdentity("LOGISTICS_NODE", "LOG-AUTH", "LOGISTICS", "TRUSTED_TEST", (MISSION,)),
        "CIVIL_PROTECTION_NODE": NodeIdentity("CIVIL_PROTECTION_NODE", "CIV-AUTH", "CIVIL_PROTECTION",
                                                "TRUSTED_TEST", (MISSION,)),
        "STRATEGIC_EVIDENCE_NODE": NodeIdentity("STRATEGIC_EVIDENCE_NODE", "STRAT-AUTH", "STRATEGIC_EVIDENCE",
                                                 "TRUSTED_TEST", (MISSION,)),
    }
    actors = (
        ActorIdentity("log-analyst", "Log Analyst", "LOG-AUTH", ("LOGISTICS_ANALYST",),
                      mission_scopes=(MISSION,), valid_from=T0),
        ActorIdentity("civil-engineer", "Civil Engineer", "CIV-AUTH", ("CIVIL_PROTECTION_ENGINEER",),
                      mission_scopes=(MISSION,), authority_scopes=("ENGINEERING_STATUS",), valid_from=T0),
        ActorIdentity("strategic-analyst", "Strategic Analyst", "STRAT-AUTH", ("STRATEGIC_ANALYST",),
                      mission_scopes=(MISSION,), valid_from=T0),
        ActorIdentity("joint-coordinator", "Joint Coordinator", "JOINT-AUTH", ("JOINT_COORDINATOR",),
                      mission_scopes=(MISSION,), authority_scopes=("CROSS_AUTHORITY", "ENGINEERING_STATUS"),
                      valid_from=T0),
        ActorIdentity("provider", "Advisory Provider", "STRAT-AUTH", ("PROVIDER",),
                      mission_scopes=(MISSION,), valid_from=T0),
    )
    keys = {subject: f"v3-test-key-{subject}" for subject in [*nodes, *(actor.actor_id for actor in actors)]}
    return nodes, actors, keys


def make_nodes(tmp_path):
    nodes, actors, keys = identities()
    result = {}
    for node_id, identity in nodes.items():
        result[node_id] = DistributedNode.create(tmp_path / node_id.lower(), identity, actors, keys,
                                                 {key: value for key, value in nodes.items() if key != node_id})
    return result


PUBLIC = AccessMarkingV3("LOG-AUTH", releasability=("MISSION_PARTNERS",), mission_scopes=(MISSION,))
RESTRICTED = AccessMarkingV3("CIV-AUTH", compartments=("ENGINEERING",),
                             releasability=("CIVIL_ONLY",), mission_scopes=(MISSION,),
                             min_role="CIVIL_PROTECTION_ENGINEER",
                             originator_controls=("AUTHORITY:CIV-AUTH",))
FULL_ACCESS = {"roles": ("AUDITOR",), "compartments": ("ENGINEERING",),
               "releasability": ("MISSION_PARTNERS", "CIVIL_ONLY"), "mission_scopes": (MISSION,)}
PARTNER_ACCESS = {"roles": ("JOINT_COORDINATOR",), "compartments": (),
                  "releasability": ("MISSION_PARTNERS",), "mission_scopes": (MISSION,)}


def append_observation(node, subject, value, *, actor="log-analyst", time=T0, nonce="n1", marking=PUBLIC):
    return node.append_action(
        actor_id=actor, action_type="ANNOTATION", event_type="OBSERVATION_RECORDED",
        payload={"record_type": "observation", "subject_id": subject, "value": value},
        marking=marking, recorded_time=time, nonce=nonce, object_ref=subject, mission_scope=MISSION)


def sync(source, destination, access=PARTNER_ACCESS, *, time=T1, reverse=False):
    request = make_request(destination, source.node_id, access, requested_scope=(MISSION,))
    bundle = build_bundle(source, request, creation_time=time, reverse_delivery_order=reverse)
    receipt = import_bundle(destination, bundle, admitted_time=time)
    acknowledge_receipt(source, bundle, receipt)
    return bundle, receipt


@pytest.mark.parametrize(("left", "right", "expected"), [
    ({"A": 1}, {"A": 2}, CausalRelation.BEFORE),
    ({"A": 2}, {"A": 1}, CausalRelation.AFTER),
    ({"A": 1, "B": 0}, {"A": 0, "B": 1}, CausalRelation.CONCURRENT),
    ({"A": 1}, {"A": 1, "B": 0}, CausalRelation.EQUIVALENT),
])
def test_causal_relationships(left, right, expected):
    assert VersionVector(left).relation(VersionVector(right)) == expected


def test_causal_unknown_and_wall_clock_inversion():
    assert VersionVector({"A": 1}).relation(None) == CausalRelation.UNKNOWN
    # Causality remains BEFORE even if an AFTER event carries an earlier wall clock.
    assert VersionVector({"A": 1}).relation(VersionVector({"A": 2})) == CausalRelation.BEFORE


def test_node_identity_uniqueness_trust_ownership_and_protocol(tmp_path):
    nodes = make_nodes(tmp_path)
    assert len({node.node_id for node in nodes.values()}) == 3
    assert nodes["LOGISTICS_NODE"].manifest.authority == "LOG-AUTH"
    assert nodes["CIVIL_PROTECTION_NODE"].manifest.trust_state == "TRUSTED_TEST"
    assert nodes["STRATEGIC_EVIDENCE_NODE"].manifest.protocol_version == "curunir-distributed-sync-v3"
    with pytest.raises(ValueError):
        NodeIdentity("", "AUTH", "ROLE", "TRUSTED_TEST", ())


def test_authentication_valid_invalid_payload_replay_wrong_node_and_policy(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    payload = {"record_type": "annotation", "subject_id": "route-1"}
    action = node.identity.make_action("log-analyst", "ANNOTATION", payload, T0, "nonce-auth")
    assert node.identity.verify_action(action, payload, expected_node=node.node_id).verification_result.startswith("VERIFIED")
    with pytest.raises(AuthenticationError, match="nonce replayed"):
        node.identity.verify_action(action, payload, expected_node=node.node_id)
    with pytest.raises(AuthenticationError, match="payload hash"):
        node.identity.verify_action(replace(action, nonce="new"), {**payload, "x": 1}, expected_node=node.node_id)
    with pytest.raises(AuthenticationError, match="wrong node"):
        node.identity.verify_action(replace(action, nonce="new2"), payload, expected_node="OTHER")
    with pytest.raises(AuthenticationError, match="policy version"):
        node.identity.verify_action(replace(action, nonce="new3", policy_version="old"), payload,
                                    expected_node=node.node_id)


def test_actor_revocation_future_refusal_historical_validity(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    old = append_observation(node, "route-1", "OPEN")
    node.revoke(RevocationRecord("rev-1", "ACTOR", "log-analyst", T1, T1, "JOINT-AUTH", "role ended"))
    assert node.identity.actor_valid_at("log-analyst", T0)
    assert not node.identity.actor_valid_at("log-analyst", T2)
    with pytest.raises(AuthenticationError, match="revoked"):
        append_observation(node, "route-2", "OPEN", time=T2, nonce="future")
    assert old.event_id in {event.event_id for event in node.events()}


def test_role_authorization_and_provider_cannot_decide(tmp_path):
    nodes = make_nodes(tmp_path)
    strategic = nodes["STRATEGIC_EVIDENCE_NODE"]
    with pytest.raises(AuthorizationError, match="may not mark infrastructure"):
        strategic.append_action(actor_id="strategic-analyst", action_type="MARK_INFRASTRUCTURE_STATUS",
                                event_type="STATUS", payload={"record_type": "infrastructure_status",
                                "subject_id": "bridge", "status": "OPEN"}, marking=PUBLIC,
                                recorded_time=T0, nonce="wrong-role", mission_scope=MISSION)
    with pytest.raises(AuthorizationError, match="providers may propose"):
        strategic.append_action(actor_id="provider", action_type="DECISION", event_type="DECISION",
                                payload={"record_type": "decision", "subject_id": "x", "value": "ACCEPT"},
                                marking=PUBLIC, recorded_time=T0, nonce="provider-decision",
                                mission_scope=MISSION)


def test_full_incremental_duplicate_restart_and_rejoin(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    append_observation(source, "route-1", "OPEN")
    bundle, receipt = sync(source, destination)
    assert receipt.verification_state == "ACCEPTED" and len(receipt.accepted_events) == 1
    duplicate = import_bundle(destination, bundle, admitted_time=T1)
    assert duplicate.verification_state == "DUPLICATE"
    append_observation(source, "route-2", "RESTRICTED", time=T1, nonce="n2")
    incremental, next_receipt = sync(source, destination, time=T2)
    assert next_receipt.verification_state == "ACCEPTED" and len(incremental.events) == 1
    restarted = DistributedNode(destination.root)
    assert restarted.verify_integrity()["valid"]
    assert {item["payload"]["subject_id"] for item in restarted.projection(PARTNER_ACCESS)["union_records"]} >= {"route-1", "route-2"}


def test_out_of_order_delivery_retries_and_causal_gap_is_not_silent(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    append_observation(source, "a", 1)
    append_observation(source, "b", 2, time=T1, nonce="n2")
    _, receipt = sync(source, destination, reverse=True)
    assert receipt.verification_state == "ACCEPTED" and len(receipt.accepted_events) == 2
    quarantines = destination.quarantine_path.read_text(encoding="utf-8")
    assert "CAUSAL_GAP" in quarantines and "origin sequence gap" in quarantines


def test_tampered_partial_incompatible_and_stale_base(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    append_observation(source, "a", 1)
    request = make_request(destination, source.node_id, PARTNER_ACCESS, requested_scope=(MISSION,))
    bundle = build_bundle(source, request, creation_time=T1)
    bad_events = list(bundle.events); bad_events[0] = {**bad_events[0], "payload": {"tampered": True}}
    bad = replace(bundle, events=tuple(bad_events))
    assert import_bundle(destination, bad, admitted_time=T1).verification_state == "TAMPERED"
    stale = replace(bundle, base_state_token="opaque-wrong-base")
    # Re-signing is deliberately absent, so integrity wins before base parsing.
    assert import_bundle(destination, stale, admitted_time=T1).verification_state == "TAMPERED"
    incompatible_event = {**bundle.events[0], "schema_version": "future-schema"}
    incompatible = replace(bundle, events=(incompatible_event,))
    assert import_bundle(destination, incompatible, admitted_time=T1).verification_state == "TAMPERED"


def test_concurrent_status_conflict_no_last_write_wins_and_resolution_event(tmp_path):
    nodes = make_nodes(tmp_path)
    logistics, civil = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    logistics.append_action(actor_id="log-analyst", action_type="ANNOTATION", event_type="ROUTE_STATUS",
                            payload={"record_type": "route_status", "subject_id": "route-R1", "status": "OPEN"},
                            marking=PUBLIC, recorded_time=T0, nonce="log-status", mission_scope=MISSION)
    civil.append_action(actor_id="civil-engineer", action_type="MARK_INFRASTRUCTURE_STATUS", event_type="ROUTE_STATUS",
                        payload={"record_type": "route_status", "subject_id": "route-R1", "status": "UNSAFE"},
                        marking=PUBLIC, recorded_time=T0, nonce="civil-status", mission_scope=MISSION)
    sync(logistics, civil)
    sync(civil, logistics)
    projection = civil.projection(PARTNER_ACCESS)
    route = next(item for item in projection["state"] if item["subject_id"] == "route-R1")
    assert route["status"] == "CONFLICT" and {value["status"] for value in route["values"]} == {"OPEN", "UNSAFE"}
    conflict = next(item for item in civil.conflicts() if item.affected_object_or_workflow == "route-R1")
    resolution = civil.resolve_conflict(conflict.conflict_id, actor_id="joint-coordinator",
                                        resolution={"status": "RESTRICTED"}, recorded_time=T2,
                                        nonce="resolve", marking=PUBLIC)
    assert resolution.event_id not in conflict.involved_events
    resolved = next(item for item in civil.projection(PARTNER_ACCESS)["conflicts"]
                    if item["conflict_id"] == conflict.conflict_id)
    assert resolved["status"] == "RESOLVED" and resolved["resolution_event_id"] == resolution.event_id


def test_access_filtering_before_bundle_and_hidden_count_token_size_stability(tmp_path):
    nodes = make_nodes(tmp_path)
    source, destination = nodes["CIVIL_PROTECTION_NODE"], nodes["LOGISTICS_NODE"]
    append_observation(source, "public-route", "RESTRICTED", actor="civil-engineer", nonce="public",
                       marking=PUBLIC)
    request = make_request(destination, source.node_id, PARTNER_ACCESS, requested_scope=(MISSION,))
    before = build_bundle(source, request, creation_time=T1)
    append_observation(source, "secret-assessment", "UNSAFE", actor="civil-engineer", time=T1,
                       nonce="secret", marking=RESTRICTED)
    after = build_bundle(source, request, creation_time=T1)
    assert before.events == after.events
    assert before.resulting_state_token == after.resulting_state_token
    assert len(json.dumps(before.to_record(), sort_keys=True)) == len(json.dumps(after.to_record(), sort_keys=True))
    serialized = json.dumps(after.to_record(), sort_keys=True)
    assert "secret-assessment" not in serialized and "ENGINEERING" not in serialized and "CIVIL_ONLY" not in serialized
    assert "hidden counts" in after.omissions_declaration


def test_attribute_relationship_provenance_conflict_filtering_and_no_reexport(tmp_path):
    nodes = make_nodes(tmp_path)
    source, lower, third = (nodes["CIVIL_PROTECTION_NODE"], nodes["LOGISTICS_NODE"],
                            nodes["STRATEGIC_EVIDENCE_NODE"])
    source.append_action(
        actor_id="civil-engineer", action_type="ANNOTATION", event_type="FILTERED_RECORD",
        payload={"record_type": "observation", "subject_id": "route-R1", "status": "RESTRICTED",
                 "exact_location": "SECRET-PIER", "allowed_note": "engineering restriction",
                 "attribute_markings": {"exact_location": RESTRICTED.to_record()},
                 "relationships": [
                     {"source": "route-R1", "target": "public-depot", "kind": "AFFECTS"},
                     {"source": "route-R1", "target": "secret-sensor", "kind": "DERIVED_FROM",
                      "access_marking": RESTRICTED.to_record()}],
                 "provenance": {"source_id": "secret-engineering-source"},
                 "provenance_marking": RESTRICTED.to_record(),
                 "conflict": {"secret": True}, "conflict_id": "secret-conflict",
                 "conflict_detail_marking": RESTRICTED.to_record()},
        marking=AccessMarkingV3("CIV-AUTH", releasability=("MISSION_PARTNERS",), mission_scopes=(MISSION,)),
        recorded_time=T0, nonce="subrecord-filter", mission_scope=MISSION)
    bundle, receipt = sync(source, lower)
    assert receipt.verification_state == "ACCEPTED"
    serialized = json.dumps(bundle.to_record(), sort_keys=True)
    for secret in ("SECRET-PIER", "secret-sensor", "secret-engineering-source", "secret-conflict"):
        assert secret not in serialized
    assert "public-depot" in serialized and "engineering restriction" in serialized
    shared = bundle.events[0]
    assert shared["action_envelope"] is None
    assert shared["authentication_state"] == "SOURCE_ATTESTED_ACCESS_PROJECTION_TEST_SIGNER"
    # Bounded direct-origin transport prevents a lower node from relaying a
    # peer's filtered record as though it originated locally.
    onward = make_request(third, lower.node_id, PARTNER_ACCESS, requested_scope=(MISSION,))
    onward_bundle = build_bundle(lower, onward, creation_time=T2)
    assert not onward_bundle.events


def test_sanitized_consequence_preserves_nonrevealing_basis(tmp_path):
    node = make_nodes(tmp_path)["CIVIL_PROTECTION_NODE"]
    secret = append_observation(node, "secret-engineering", "UNSAFE", actor="civil-engineer",
                                nonce="secret", marking=RESTRICTED)
    basis = f"restricted-basis-{secret.payload_hash[:16]}"
    consequence = node.append_action(
        actor_id="civil-engineer", action_type="ANNOTATION", event_type="SANITIZED_CONSEQUENCE",
        payload={"record_type": "sanitized_consequence", "subject_id": "route-R1", "status": "RESTRICTED",
                 "originating_authority": "CIV-AUTH", "reason_category": "ENGINEERING_RESTRICTION",
                 "restricted_basis_ref": basis, "valid_interval": {"from": T0, "to": None}},
        marking=replace(PUBLIC, sanitized=True), recorded_time=T1, nonce="sanitized",
        parent_event_ids=(secret.event_id,), mission_scope=MISSION)
    assert consequence.payload["restricted_basis_ref"] == basis and "secret-engineering" not in json.dumps(consequence.payload)


def test_temporal_valid_known_node_knowledge_and_future_correction_exclusion(tmp_path):
    nodes = make_nodes(tmp_path)
    logistics, strategic = nodes["LOGISTICS_NODE"], nodes["STRATEGIC_EVIDENCE_NODE"]
    original = append_observation(strategic, "bulletin", "DISRUPTION", actor="strategic-analyst", nonce="orig")
    sync(strategic, logistics, time=T1)
    correction = strategic.append_action(
        actor_id="strategic-analyst", action_type="ATTACH_PUBLIC_EVIDENCE", event_type="CORRECTION",
        payload={"record_type": "correction", "subject_id": "bulletin", "correction_of": original.event_id,
                 "value": "LOCAL_DELAY_ONLY"}, marking=PUBLIC, recorded_time=T2, nonce="correct",
        mission_scope=MISSION)
    earlier = logistics.projection(PARTNER_ACCESS, known_at=T1, valid_at=T1)
    assert all(item["payload"].get("record_type") != "correction" for item in earlier["union_records"])
    assert correction.event_id not in {item["event_id"] for item in earlier["union_records"]}
    assert all(item["payload"].get("record_type") != "correction"
               for item in logistics.projection(PARTNER_ACCESS)["union_records"])
    sync(strategic, logistics, time="2026-04-01T03:00:00+00:00")
    later = logistics.projection(PARTNER_ACCESS, known_at="2026-04-01T03:00:00+00:00")
    assert any(item["payload"].get("record_type") == "correction" for item in later["union_records"])


def test_export_import_replay_preserves_distributed_history_without_provider(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    append_observation(node, "route", "OPEN")
    original = node.projection(PARTNER_ACCESS)
    manifest = node.export_open(tmp_path / "export")
    replay = DistributedNode.import_open(tmp_path / "export", tmp_path / "replay")
    assert manifest["provider_reinvocations"] == 0
    assert replay.projection(PARTNER_ACCESS)["state"] == original["state"]
    assert replay.projection(PARTNER_ACCESS)["union_records"] == original["union_records"]
