"""Authenticated collaborative workflow call paths over distributed events."""
from __future__ import annotations

from typing import Any, Mapping

from .models import (AccessMarkingV3, AnalystHandoff, CollaborativeAnnotation, DecisionRecordV3,
                     ReviewRecord, TaskRecord)
from .node import DistributedNode


REVIEW_TYPES = ("PEER_REVIEW", "EVIDENCE_REVIEW", "SOURCE_ORIGIN_REVIEW", "ENGINEERING_REVIEW",
                "DECISION_REVIEW", "AI_SECONDARY_REVIEW")
TASK_STATES = ("ASSIGNED", "ACCEPTED", "IN_PROGRESS", "BLOCKED", "HANDED_OFF", "COMPLETED",
               "REJECTED", "REOPENED")


class CollaborativeWorkflow:
    def __init__(self, node: DistributedNode):
        self.node = node

    def annotation(self, record: CollaborativeAnnotation, *, actor_id: str, recorded_time: str,
                   nonce: str, mission_scope: str) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="ANNOTATION", event_type="COLLABORATIVE_ANNOTATION",
            payload={"record_type": "annotation", **record.to_record()},
            marking=AccessMarkingV3(**normalize_marking(record.access_marking)),
            recorded_time=recorded_time, nonce=nonce, object_ref=record.subject_id,
            mission_scope=mission_scope)
        return event.event_id

    def handoff(self, record: AnalystHandoff, *, actor_id: str, recorded_time: str,
                nonce: str, mission_scope: str) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="HANDOFF", event_type="ANALYST_HANDOFF",
            payload={"record_type": "handoff", "subject_id": record.handoff_id, **record.to_record()},
            marking=AccessMarkingV3(**normalize_marking(record.access_marking)),
            recorded_time=recorded_time, nonce=nonce, object_ref=record.handoff_id,
            mission_scope=mission_scope)
        return event.event_id

    def review_request(self, record: ReviewRecord, *, actor_id: str, recorded_time: str,
                       nonce: str, marking: AccessMarkingV3, mission_scope: str) -> str:
        if record.review_type not in REVIEW_TYPES:
            raise ValueError(f"unsupported review type: {record.review_type}")
        event = self.node.append_action(
            actor_id=actor_id, action_type="REVIEW", event_type="REVIEW_REQUESTED",
            payload={"record_type": "review_request", "subject_id": record.subject_id, **record.to_record()},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            object_ref=record.subject_id, mission_scope=mission_scope)
        return event.event_id

    def review_response(self, record: ReviewRecord, *, actor_id: str, recorded_time: str,
                        nonce: str, marking: AccessMarkingV3, mission_scope: str,
                        parent_event_id: str) -> str:
        if record.review_type not in REVIEW_TYPES:
            raise ValueError(f"unsupported review type: {record.review_type}")
        event = self.node.append_action(
            actor_id=actor_id, action_type="REVIEW", event_type="REVIEW_RESPONDED",
            payload={"record_type": "review_response", "subject_id": record.subject_id, **record.to_record()},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            parent_event_ids=(parent_event_id,), object_ref=record.subject_id,
            mission_scope=mission_scope)
        return event.event_id

    def assign_task(self, record: TaskRecord, *, actor_id: str, recorded_time: str,
                    nonce: str, mission_scope: str) -> str:
        if record.state != "ASSIGNED":
            raise ValueError("task assignment begins in ASSIGNED state")
        event = self.node.append_action(
            actor_id=actor_id, action_type="TASK_ASSIGNMENT", event_type="TASK_ASSIGNED",
            payload={"record_type": "task_assignment", "subject_id": record.task_id, **record.to_record()},
            marking=AccessMarkingV3(**normalize_marking(record.access_marking)),
            recorded_time=recorded_time, nonce=nonce, object_ref=record.task_id,
            mission_scope=mission_scope)
        return event.event_id

    def transition_task(self, task_id: str, state: str, *, actor_id: str, recorded_time: str,
                        nonce: str, marking: AccessMarkingV3, mission_scope: str,
                        parent_event_ids: tuple[str, ...], evidence_snapshot: tuple[str, ...] = (),
                        blocking_dependencies: tuple[str, ...] = ()) -> str:
        if state not in TASK_STATES or state == "ASSIGNED":
            raise ValueError(f"invalid task transition state: {state}")
        action = "TASK_ACCEPTANCE" if state == "ACCEPTED" else "TASK_COMPLETION"
        record_type = "task_acceptance" if state == "ACCEPTED" else "task_completion" if state == "COMPLETED" \
            else "task_activity"
        event = self.node.append_action(
            actor_id=actor_id, action_type=action, event_type=f"TASK_{state}",
            payload={"record_type": record_type, "subject_id": task_id, "task_id": task_id,
                     "state": state, "blocking_dependencies": list(blocking_dependencies),
                     "evidence_snapshot": list(evidence_snapshot)},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            parent_event_ids=parent_event_ids, object_ref=task_id, mission_scope=mission_scope)
        return event.event_id

    def decision(self, record: DecisionRecordV3, *, actor_id: str, recorded_time: str,
                 nonce: str, mission_scope: str, parent_event_ids: tuple[str, ...] = ()) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="DECISION", event_type="DECISION_DISPOSITIONED",
            payload={"record_type": "decision", **record.to_record()},
            marking=AccessMarkingV3(**normalize_marking(record.access_marking)),
            recorded_time=recorded_time, nonce=nonce, parent_event_ids=parent_event_ids,
            object_ref=record.subject_id, mission_scope=mission_scope)
        return event.event_id

    def recommendation_disposition(self, recommendation_id: str, disposition: str, *, actor_id: str,
                                   recorded_time: str, nonce: str, marking: AccessMarkingV3,
                                   mission_scope: str, evidence_snapshot: tuple[str, ...],
                                   parent_event_ids: tuple[str, ...] = ()) -> str:
        event = self.node.append_action(
            actor_id=actor_id, action_type="RECOMMENDATION_DISPOSITION",
            event_type="RECOMMENDATION_DISPOSITIONED",
            payload={"record_type": "recommendation_disposition", "subject_id": recommendation_id,
                     "recommendation_id": recommendation_id, "disposition": disposition,
                     "evidence_snapshot": list(evidence_snapshot)},
            marking=marking, recorded_time=recorded_time, nonce=nonce,
            parent_event_ids=parent_event_ids, object_ref=recommendation_id,
            mission_scope=mission_scope)
        return event.event_id


def normalize_marking(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "owning_authority": record["owning_authority"],
        "compartments": tuple(record.get("compartments", ())),
        "releasability": tuple(record.get("releasability", ())),
        "mission_scopes": tuple(record.get("mission_scopes", ())),
        "min_role": record.get("min_role", "OBSERVER"),
        "originator_controls": tuple(record.get("originator_controls", ())),
        "sanitized": bool(record.get("sanitized", False)),
    }
