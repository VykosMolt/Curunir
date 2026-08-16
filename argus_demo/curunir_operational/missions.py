"""Information requirements, evidence requests and analyst tasks.

The auditable mission workflow: Alert → Information Requirement → Analyst Task
or Evidence Request → New Evidence or Explicit Failure → Requirement Closure or
Continued Unknown. Guards enforced here: only a HUMAN actor can move a
requirement to ANSWERED or CLOSED_UNANSWERED (a model-generated answer cannot
close a requirement), answering requires evidence references, explicit failure
requires a stated reason, and every transition is a recorded event.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import AccessContext, Marking, marking_from_record
from .canonical import digest_id
from .contracts import (REQUIREMENT_PRIORITIES, AnalystTask, EvidenceRequest,
                        InformationRequirement, WorkflowTransition)
from .store import MissionDataStore


class MissionWorkflowError(ValueError):
    pass


REQUIREMENT_FLOW = {
    "OPEN": ("EVIDENCE_PENDING", "ANSWERED", "CLOSED_UNANSWERED"),
    "EVIDENCE_PENDING": ("OPEN", "ANSWERED", "CLOSED_UNANSWERED"),
    "ANSWERED": (), "CLOSED_UNANSWERED": (),
}
TASK_FLOW = {
    "ASSIGNED": ("IN_PROGRESS", "BLOCKED", "ABANDONED"),
    "IN_PROGRESS": ("BLOCKED", "DONE", "ABANDONED"),
    "BLOCKED": ("IN_PROGRESS", "ABANDONED"),
    "DONE": (), "ABANDONED": (),
}
EVIDENCE_REQUEST_FLOW = {"OPEN": ("FULFILLED", "FAILED"), "FULFILLED": (), "FAILED": ()}
HUMAN_ONLY_TARGETS = {"requirement": ("ANSWERED", "CLOSED_UNANSWERED"), "analyst_task": ("DONE",),
                      "evidence_request": ()}


class MissionWorkflow:
    def __init__(self, store: MissionDataStore):
        self.store = store

    def _status_of(self, subject_kind: str, subject_id: str) -> tuple[str, Mapping[str, Any]]:
        key = {"requirement": ("information_requirement", "requirement_id"),
               "analyst_task": ("analyst_task", "task_id"),
               "evidence_request": ("evidence_request", "request_id")}[subject_kind]
        record = None
        for row in self.store.records_of(key[0]):
            if row[key[1]] == subject_id:
                record = row
        if record is None:
            raise MissionWorkflowError(f"unknown {subject_kind}: {subject_id}")
        status = record["status"]
        for transition in self.store.records_of("workflow_transition"):
            if transition["subject_kind"] == subject_kind and transition["subject_id"] == subject_id:
                status = transition["to_status"]
        return status, record

    def open_requirement(self, *, mission_context: str, question: str, affected_ids: tuple[str, ...],
                         priority: str, rationale: str, required_evidence_type: str, owning_role: str,
                         closure_criteria: str, due_time: str | None, recorded_time: str,
                         marking: Marking, actor: str, source_alert_id: str | None = None) -> dict[str, Any]:
        requirement = InformationRequirement(
            requirement_id=digest_id("req", question, mission_context),
            mission_context=mission_context, question=question,
            affected_ids=tuple(affected_ids) + ((source_alert_id,) if source_alert_id else ()),
            priority=priority, rationale=rationale, required_evidence_type=required_evidence_type,
            owning_role=owning_role, created_time=recorded_time, due_time=due_time,
            status="OPEN", closure_criteria=closure_criteria, marking=marking,
        )
        latest = None
        for existing in self.store.records_of("information_requirement"):
            if existing["requirement_id"] == requirement.requirement_id:
                latest = existing
        if latest is not None:
            # fold, don't discard: a later caller may escalate priority or
            # widen the affected set — an existing requirement must not
            # silently swallow that epistemic state
            rank = {p: i for i, p in enumerate(REQUIREMENT_PRIORITIES)}
            merged_priority = latest["priority"] \
                if rank[latest["priority"]] >= rank[priority] else priority
            merged_affected = tuple(dict.fromkeys(
                tuple(latest["affected_ids"]) + tuple(requirement.affected_ids)))
            if merged_priority == latest["priority"] \
                    and merged_affected == tuple(latest["affected_ids"]):
                return latest
            updated = InformationRequirement(
                **{**{k: v for k, v in latest.items() if k != "record_type"},
                   "priority": merged_priority, "affected_ids": merged_affected,
                   "version": latest.get("version", 1) + 1,
                   # a fold escalates priority / widens scope; it never
                   # re-classifies: the record keeps its own marking
                   "marking": marking_from_record(latest["marking"])})
            self.store.append("REQUIREMENT_RECORDED", updated,
                              recorded_time=recorded_time, actor=actor)
            return updated.to_record()
        self.store.append("REQUIREMENT_RECORDED", requirement, recorded_time=recorded_time, actor=actor)
        return requirement.to_record()

    def request_evidence(self, requirement_id: str, *, request_kind: str, detail: str,
                         affected_ids: tuple[str, ...], due_time: str | None,
                         recorded_time: str, marking: Marking, actor: str) -> dict[str, Any]:
        self._status_of("requirement", requirement_id)  # must exist
        request = EvidenceRequest(
            request_id=digest_id("evreq", requirement_id, request_kind, detail),
            requirement_id=requirement_id, request_kind=request_kind, detail=detail,
            affected_ids=tuple(affected_ids), status="OPEN", created_time=recorded_time,
            due_time=due_time, marking=marking,
        )
        self.store.append("EVIDENCE_REQUEST_RECORDED", request, recorded_time=recorded_time, actor=actor)
        current, _ = self._status_of("requirement", requirement_id)
        if current == "OPEN":
            self.transition("requirement", requirement_id, "EVIDENCE_PENDING",
                            actor_id=actor, actor_kind="SERVICE", evidence_refs=(request.request_id,),
                            note="evidence requested", recorded_time=recorded_time, marking=marking)
        return request.to_record()

    def assign_task(self, *, assigned_role: str, assigned_actor: str, task_type: str,
                    affected_ids: tuple[str, ...], required_action: str, due_time: str | None,
                    depends_on: tuple[str, ...], recorded_time: str, marking: Marking,
                    actor: str) -> dict[str, Any]:
        task = AnalystTask(
            task_id=digest_id("task", required_action, assigned_actor, recorded_time),
            assigned_role=assigned_role, assigned_actor=assigned_actor, task_type=task_type,
            affected_ids=tuple(affected_ids), required_action=required_action, status="ASSIGNED",
            created_time=recorded_time, due_time=due_time, depends_on=tuple(depends_on),
            evidence_refs=(), completion_result="", marking=marking,
        )
        self.store.append("TASK_RECORDED", task, recorded_time=recorded_time, actor=actor)
        return task.to_record()

    def transition(self, subject_kind: str, subject_id: str, to_status: str, *, actor_id: str,
                   actor_kind: str, evidence_refs: tuple[str, ...], note: str,
                   recorded_time: str, marking: Marking) -> dict[str, Any]:
        current, record = self._status_of(subject_kind, subject_id)
        flow = {"requirement": REQUIREMENT_FLOW, "analyst_task": TASK_FLOW,
                "evidence_request": EVIDENCE_REQUEST_FLOW}[subject_kind]
        if to_status not in flow.get(current, ()):
            raise MissionWorkflowError(f"{subject_kind} {subject_id}: no transition {current} → {to_status}")
        if to_status in HUMAN_ONLY_TARGETS[subject_kind] and actor_kind != "HUMAN":
            raise MissionWorkflowError(
                f"only a human can move a {subject_kind} to {to_status}; a model-generated answer cannot close it")
        if subject_kind == "requirement" and to_status == "ANSWERED" and not evidence_refs:
            raise MissionWorkflowError("answering a requirement requires evidence references")
        if to_status in ("CLOSED_UNANSWERED", "FAILED", "ABANDONED") and not note:
            raise MissionWorkflowError("explicit failure or abandonment requires a stated reason")
        if subject_kind == "analyst_task" and to_status == "DONE" and actor_id != record["assigned_actor"]:
            raise MissionWorkflowError("only the assigned actor can complete the task")
        transition = WorkflowTransition(
            transition_id=digest_id("wft", subject_kind, subject_id, to_status, recorded_time),
            subject_kind=subject_kind, subject_id=subject_id, from_status=current, to_status=to_status,
            actor_id=actor_id, actor_kind=actor_kind, evidence_refs=tuple(evidence_refs), note=note,
            recorded_time=recorded_time, marking=marking,
        )
        self.store.append("WORKFLOW_TRANSITIONED", transition, recorded_time=recorded_time, actor=actor_id)
        return transition.to_record()

    def audit_trail(self, subject_kind: str, subject_id: str) -> list[dict[str, Any]]:
        return [t for t in self.store.records_of("workflow_transition")
                if t["subject_kind"] == subject_kind and t["subject_id"] == subject_id]
