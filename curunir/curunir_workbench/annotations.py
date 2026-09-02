"""Annotations and dissent attached to canonical mission records.

An annotation binds to a record its author can see. Dissent never overwrites
what it disagrees with, and resolving it keeps the note.
"""
from __future__ import annotations

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, marking_from_record

from .contracts import AnnotationRecord
from .errors import NotFound
from .projections import MissionProjection
from .store import WorkbenchStore


class AnnotationConflict(Exception):
    pass


def create_annotation(store: WorkbenchStore, projection: MissionProjection, *,
                      actor: str, marking: Marking, now: str,
                      target_kind: str, target_id: str, kind: str, text: str,
                      reply_to: str = "", anchor_ref: str = "") -> dict:
    """Attach a note to a record the author can see; anything else is unknown."""
    if not _target_visible(projection, target_kind, target_id):
        raise NotFound(f"unknown target: {target_kind}/{target_id}")
    if reply_to and projection.get("workbench_annotation", reply_to) is None:
        # Judge by the author's view: a parent they cannot see is refused
        # exactly like one that does not exist.
        raise NotFound(f"unknown parent annotation: {reply_to}")
    record = AnnotationRecord(
        annotation_id=digest_id("annotation", actor, target_kind, target_id, now, text[:64]),
        target_kind=target_kind, target_id=target_id, author=actor,
        kind=kind, text=text, status="OPEN", resolution_note="",
        reply_to=reply_to, anchor_ref=anchor_ref,
        recorded_time=now, marking=marking, version=1)
    event = store.append(
        "WORKBENCH_ANNOTATION_RECORDED", record,
        recorded_time=now, actor=actor)
    return event["record"]


def resolve_annotation(store: WorkbenchStore, annotation_id: str, *, actor: str,
                       marking: Marking, now: str, expected_version: int,
                       status: str, note: str) -> dict:
    current = store.current_annotations().get(annotation_id)
    if current is None:
        raise NotFound(f"unknown annotation: {annotation_id}")
    if current["version"] != expected_version:
        raise AnnotationConflict(
            f"annotation is at version {current['version']}, you saw {expected_version}")
    if status == "WITHDRAWN" and actor != current["author"]:
        raise PermissionError("only the author may withdraw an annotation")
    if current["kind"] == "DISSENT" and actor != current["author"]:
        # Dissent blocks approval, so only its author may close it; otherwise
        # an approver could clear their own path. Approvers carry it visibly.
        raise PermissionError("only the dissenting author may resolve their "
                              "dissent; approve with acknowledge_dissent to "
                              "carry it visibly")
    record = AnnotationRecord(
        annotation_id=annotation_id, target_kind=current["target_kind"],
        target_id=current["target_id"], author=current["author"],
        kind=current["kind"], text=current["text"], status=status,
        resolution_note=note, reply_to=current.get("reply_to", ""),
        anchor_ref=current.get("anchor_ref", ""), recorded_time=now,
        # Resolving never re-marks the annotation.
        marking=marking_from_record(current["marking"]),
        version=current["version"] + 1)
    try:
        event = store.append(
            "WORKBENCH_ANNOTATION_RECORDED", record,
            recorded_time=now, actor=actor)
    except ValueError as error:
        raise AnnotationConflict(str(error)) from error
    return event["record"]


# target kind -> (base-view key, id field)
_BASE_VIEW_TARGETS = {
    "relationship": ("relationships", "relationship_id"),
    "alert": ("alerts", "alert_id"),
    "recommendation": ("recommendations", "recommendation_id"),
    "decision": ("decisions", "decision_id"),
    "information_requirement": ("information_requirements", "requirement_id"),
    "analyst_task": ("analyst_tasks", "task_id"),
}


def target_marking(projection: MissionProjection, target_kind: str,
                   target_id: str):
    """The marking of the annotated record, or None if it carries none or is
    unresolvable. The declared kind decides where to look; ids are never
    searched across families."""
    if target_kind == "object":
        record = projection.object_current(target_id)
        return record.get("marking") if record else None
    if target_kind in _BASE_VIEW_TARGETS:
        key, id_field = _BASE_VIEW_TARGETS[target_kind]
        for record in projection.base_view.get(key, []):
            if record.get(id_field) == target_id:
                return record.get("marking")
        return None
    if target_kind == "report_sentence":
        for report in projection.family("workbench_report"):
            for section in report["sections"]:
                if any(x["sentence_id"] == target_id for x in section["sentences"]):
                    return report.get("marking")
        return None
    if target_kind == "evidence_anchor":
        # An anchor lives inside an observation and takes the marking of the
        # manifestation it points at, which is what target_id names.
        manifestation = projection.get("fabric_manifestation", target_id)
        return manifestation.get("marking") if manifestation else None
    try:
        record = projection.get(target_kind, target_id)
    except KeyError:
        return None
    return record.get("marking") if record else None


def _target_visible(projection: MissionProjection, target_kind: str,
                    target_id: str) -> bool:
    if target_kind == "object":
        return projection.object_current(target_id) is not None
    if target_kind == "relationship":
        return any(r["relationship_id"] == target_id
                   for r in projection.base_view["relationships"])
    if target_kind in ("alert", "recommendation", "decision"):
        key = {"alert": "alerts", "recommendation": "recommendations",
               "decision": "decisions"}[target_kind]
        id_field = f"{target_kind}_id"
        return any(r[id_field] == target_id for r in projection.base_view[key])
    if target_kind == "information_requirement":
        return any(r["requirement_id"] == target_id
                   for r in projection.base_view["information_requirements"])
    if target_kind == "analyst_task":
        return any(t["task_id"] == target_id
                   for t in projection.base_view["analyst_tasks"])
    if target_kind == "report_sentence":
        for report in projection.family("workbench_report"):
            for section in report["sections"]:
                if any(s["sentence_id"] == target_id for s in section["sentences"]):
                    return True
        return False
    if target_kind == "evidence_anchor":
        # An anchor is visible when a visible observation points into that
        # manifestation.
        return any(a.get("manifestation_id") == target_id
                   for o in projection.family("semantic_observation")
                   for a in o.get("anchors", ()))
    try:
        return projection.get(target_kind, target_id) is not None
    except KeyError:
        return False
