"""Inspectable, versioned merge policies and explicit conflict construction."""
from __future__ import annotations

from typing import Any, Iterable

from ..canonical import sha256
from .models import (CausalRelation, DistributedConflictRecord, DistributedEventEnvelope,
                     MergePolicy, VersionVector)


UNION_RECORDS = (
    "observation", "evidence_reference", "comment", "annotation", "review_note",
    "task_activity", "indicator", "evidence_for", "evidence_against", "evidence_handoff",
    "conflict_resolution", "handoff", "review_request", "review_response", "task_acceptance",
    "task_completion", "task_progress", "information_requirement", "warning_assessment",
    "strategic_hypothesis", "source_dependence_group", "operational_implication",
    "sanitized_consequence", "alert", "revocation", "public_evidence_reference",
)
PROPOSAL_RECORDS = (
    "route_status", "infrastructure_status", "stock_availability", "object_identity",
    "hazard_impact", "planning_assumption", "recommendation",
)
SECURITY_RECORDS = ("access_policy", "originator_restriction", "actor_role", "node_trust")
WORKFLOW_RECORDS = ("task_assignment", "recommendation_disposition", "decision", "review_state")
CORRECTION_RECORDS = ("correction", "retraction")
IDENTITY_RECORDS = ("identity_association", "identity_disassociation")


class MergePolicyRegistry:
    def __init__(self) -> None:
        policies = (
            MergePolicy("UNION_PRESERVING", "3.0", "UNION", UNION_RECORDS),
            MergePolicy("VERSIONED_PROPOSAL_CONFLICT", "3.0", "PROPOSAL", PROPOSAL_RECORDS),
            MergePolicy("SECURITY_FAIL_CLOSED", "3.0", "SECURITY", SECURITY_RECORDS, fail_closed=True),
            MergePolicy("WORKFLOW_TRANSITION_CONFLICT", "3.0", "WORKFLOW", WORKFLOW_RECORDS),
            MergePolicy("CORRECTION_RETRACTION_PRESERVE", "3.0", "CORRECTION", CORRECTION_RECORDS),
            MergePolicy("IDENTITY_ASSOCIATION_EXPLICIT", "3.0", "IDENTITY", IDENTITY_RECORDS),
        )
        self._by_record = {record_type: policy for policy in policies for record_type in policy.record_types}
        self._policies = {policy.name: policy for policy in policies}

    def policy_for(self, record_type: str) -> MergePolicy:
        try:
            return self._by_record[record_type]
        except KeyError as exc:
            raise ValueError(f"distributed record type has no registered merge policy: {record_type}") from exc

    def manifest(self) -> dict[str, Any]:
        return {name: policy.to_record() for name, policy in sorted(self._policies.items())}


def semantic_subject(event: DistributedEventEnvelope) -> str:
    payload = event.payload
    return str(payload.get("subject_id") or payload.get("object_id") or payload.get("workflow_id") or
               payload.get("task_id") or payload.get("policy_id") or payload.get("association_id") or event.event_id)


def semantic_value(event: DistributedEventEnvelope) -> dict[str, Any]:
    payload = event.payload
    ignored = {"record_type", "subject_id", "object_id", "workflow_id", "task_id", "policy_id",
               "association_id", "evidence_snapshot", "note", "rationale"}
    return {key: value for key, value in payload.items() if key not in ignored}


def conflict_type_for(record_type: str, left: DistributedEventEnvelope,
                      right: DistributedEventEnvelope) -> str:
    if record_type in SECURITY_RECORDS:
        return "ACCESS_POLICY_CONFLICT" if record_type in ("access_policy", "originator_restriction") \
            else "ORIGINATOR_AUTHORITY_CONFLICT"
    if record_type == "task_assignment":
        return "TASK_ASSIGNMENT_CONFLICT"
    if record_type in ("recommendation", "recommendation_disposition"):
        return "RECOMMENDATION_CONFLICT"
    if record_type == "decision":
        return "DECISION_CONFLICT"
    if record_type in IDENTITY_RECORDS:
        return "IDENTITY_ASSOCIATION_CONFLICT"
    if record_type in CORRECTION_RECORDS:
        return "CORRECTION_CONFLICT"
    if record_type == "schema_mapping":
        return "SCHEMA_MAPPING_CONFLICT"
    if record_type in ("route_status", "infrastructure_status"):
        return "CONTRADICTORY_OPERATIONAL_STATUS"
    return "CONCURRENT_ATTRIBUTE_CHANGE"


def most_restrictive_marking(*events: DistributedEventEnvelope) -> dict[str, Any]:
    markings = [event.access_marking for event in events]
    ranks = {"OBSERVER": 0, "LOGISTICS_ANALYST": 1, "STRATEGIC_ANALYST": 1,
             "CIVIL_PROTECTION_ENGINEER": 2, "JOINT_COORDINATOR": 3, "AUDITOR": 4}
    min_role = max((marking.get("min_role", "OBSERVER") for marking in markings), key=lambda role: ranks.get(role, 99))
    return {
        "owning_authority": markings[0].get("owning_authority", "UNKNOWN"),
        "compartments": sorted({item for marking in markings for item in marking.get("compartments", ())}),
        "releasability": sorted(set.intersection(*[set(marking.get("releasability", ())) for marking in markings])
                               if markings and all(marking.get("releasability") for marking in markings) else set()),
        "mission_scopes": sorted({item for marking in markings for item in marking.get("mission_scopes", ())}),
        "min_role": min_role,
        "originator_controls": sorted({item for marking in markings
                                        for item in marking.get("originator_controls", ())}),
        "sanitized": all(marking.get("sanitized", False) for marking in markings),
    }


def detect_conflict(incoming: DistributedEventEnvelope,
                    existing: Iterable[DistributedEventEnvelope], registry: MergePolicyRegistry,
                    detected_at: str) -> DistributedConflictRecord | None:
    conflicts = detect_conflicts(incoming, existing, registry, detected_at)
    return conflicts[0] if conflicts else None


def detect_conflicts(incoming: DistributedEventEnvelope,
                     existing: Iterable[DistributedEventEnvelope], registry: MergePolicyRegistry,
                     detected_at: str) -> list[DistributedConflictRecord]:
    record_type = str(incoming.payload.get("record_type", ""))
    policy = registry.policy_for(record_type)
    if policy.category == "UNION":
        return []
    subject = semantic_subject(incoming)
    incoming_vector = VersionVector(dict(incoming.causal_context))
    conflicts: list[DistributedConflictRecord] = []
    for prior in reversed(tuple(existing)):
        if prior.event_id == incoming.event_id or prior.payload.get("record_type") != record_type:
            continue
        if semantic_subject(prior) != subject:
            continue
        relation = VersionVector(dict(prior.causal_context)).relation(incoming_vector)
        if relation != CausalRelation.CONCURRENT:
            continue
        left_value, right_value = semantic_value(prior), semantic_value(incoming)
        if left_value == right_value:
            continue
        conflict_type = conflict_type_for(record_type, prior, incoming)
        identity = {
            "type": conflict_type, "subject": subject,
            # Payload hashes and origin nodes survive access-scoped event-id
            # aliasing, so equivalent authorized nodes derive one conflict id.
            "payloads": sorted((prior.payload_hash, incoming.payload_hash)),
            "nodes": sorted((prior.originating_node_id, incoming.originating_node_id)),
        }
        conflicts.append(DistributedConflictRecord(
            conflict_id=f"conflict-{sha256(identity)[:20]}", conflict_type=conflict_type,
            affected_object_or_workflow=subject,
            involved_events=tuple(sorted((prior.event_id, incoming.event_id))),
            involved_nodes=tuple(sorted((prior.originating_node_id, incoming.originating_node_id))),
            causal_relationship=CausalRelation.CONCURRENT.value,
            competing_values=(left_value, right_value), detected_at=detected_at,
            access_marking=most_restrictive_marking(prior, incoming), status="OPEN",
            resolver_role="JOINT_COORDINATOR" if policy.category != "PROPOSAL" else "AUTHORIZED_DOMAIN_RESOLVER",
            resolution_policy=policy.name, resolution_event_id=None,
            evidence_snapshot=tuple(sorted(set(prior.payload.get("evidence_snapshot", ())) |
                                           set(incoming.payload.get("evidence_snapshot", ())))),
            audit_history=({"time": detected_at, "action": "DETECTED", "policy": policy.name},),
        ))
    return conflicts


def materialize_conflict_status(conflict: DistributedConflictRecord,
                                resolution_events: Iterable[DistributedEventEnvelope]) -> dict[str, Any]:
    record = conflict.to_record()
    for event in resolution_events:
        if event.payload.get("record_type") != "conflict_resolution":
            continue
        if event.payload.get("conflict_id") != conflict.conflict_id:
            continue
        record["status"] = event.payload.get("status", "RESOLVED")
        record["resolution_event_id"] = event.event_id
        record["audit_history"] = [*record.get("audit_history", ()), {
            "time": event.recorded_time, "action": "RESOLVED_BY_NEW_EVENT",
            "event_id": event.event_id, "actor_id": event.actor_id,
        }]
    return record
