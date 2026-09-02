"""Bridge between the fabric and the mission workflow, on one shared store.

A FabricStore accepts every mission event type, so one store root carries the
whole lineage: requirement → need → plan → execution → manifestation →
coverage → watch → change → alert. The bridge keeps the boundaries intact:
the fabric records collection facts and evidence-bound alerts; only a HUMAN
actor can ever answer or close the requirement (enforced by the mission
workflow itself).
"""
from __future__ import annotations

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking
from curunir_operational.missions import MissionWorkflow
from curunir_operational.workflow import WorkflowEngine

from .contracts import InformationNeed
from .store import FabricStore


def open_requirement_with_need(store: FabricStore, *, mission_context: str, question: str,
                               entities: tuple[str, ...] = (),
                               identifiers: tuple[tuple[str, str], ...] = (),
                               time_bounds: tuple[str | None, str | None] = (None, None),
                               geography: tuple[str, ...] = (), languages: tuple[str, ...] = (),
                               scripts: tuple[str, ...] = (), hypotheses: tuple[str, ...] = (),
                               urgency: str = "ROUTINE", priority: str = "MEDIUM",
                               owning_role: str = "ANALYST",
                               closure_criteria: str = "", rationale: str = "",
                               now: str, actor: str, marking: Marking) -> InformationNeed:
    """Open a mission InformationRequirement and its fabric-side need together."""
    workflow = MissionWorkflow(store)
    requirement = workflow.open_requirement(
        mission_context=mission_context, question=question, affected_ids=entities,
        priority=priority, rationale=rationale or "opened by fabric collection planning",
        required_evidence_type="PUBLIC_SOURCE_EVIDENCE", owning_role=owning_role,
        closure_criteria=closure_criteria or "human review of collected evidence",
        due_time=None, recorded_time=now, marking=marking, actor=actor,
    )
    need = InformationNeed(
        need_id=digest_id("need", requirement["requirement_id"]),
        requirement_id=requirement["requirement_id"],
        mission_context=mission_context, question=question,
        entities=entities, identifiers=identifiers, time_bounds=time_bounds,
        geography=geography, languages=languages or ("en",), scripts=scripts,
        hypotheses=hypotheses, urgency=urgency,
        created_by=actor, created_time=now, marking=marking,
    )
    store.append("FABRIC_NEED_RECORDED", need, recorded_time=now, actor=actor)
    return need


def alert_from_change(store: FabricStore, change_record: dict, *, severity: str = "WARNING",
                      now: str, actor: str, marking: Marking) -> tuple[str, bool]:
    """Raise an evidence-bound mission alert for a watch change observation."""
    if not change_record["evidence_manifestation_ids"] and change_record["change_type"] != "RETRIEVAL_FAILURE":
        raise ValueError("change alerts must cite manifestation evidence")
    evidence = tuple(change_record["evidence_manifestation_ids"]) or (change_record["run_id"],)
    engine = WorkflowEngine(store)
    return engine.raise_alert({
        "rule_id": "fabric-watch-change", "rule_version": "0.1",
        "trigger": f"{change_record['change_type']}: {change_record['detail'][:200]}",
        "affected_ids": (change_record["watch_id"],),
        "evidence_refs": evidence,
        "severity": severity,
        "severity_rationale": "watched source changed; analyst review required",
        "dedup_key": digest_id("fabric-alert", change_record["change_id"]),
    }, marking=marking, recorded_time=now, actor=actor)
