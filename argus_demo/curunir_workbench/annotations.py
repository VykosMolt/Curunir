"""Collaborative annotations and dissent over canonical mission objects.

An annotation binds to an existing record the author can see; dissent never
overwrites the assessment it disagrees with, and resolution keeps the note.
Strict next-version enforcement in the store surfaces concurrent edits as
conflicts instead of losing an analyst's update.
"""
from __future__ import annotations

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking

from curunir_operational.access import marking_from_record

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
    """The target must exist and be visible to the author — annotating hidden
    or imaginary state is refused (unknown and forbidden indistinguishable)."""
    if not _target_visible(projection, target_kind, target_id):
        raise NotFound(f"unknown target: {target_kind}/{target_id}")
    if reply_to and projection.get("workbench_annotation", reply_to) is None:
        # gate on the AUTHOR's view: replying into a thread they cannot see is
        # refused identically to a nonexistent parent (no existence oracle,
        # no writing into a compartmented discussion)
        raise NotFound(f"unknown parent annotation: {reply_to}")
    record = AnnotationRecord(
        annotation_id=digest_id("annotation", actor, target_kind, target_id, now, text[:64]),
        target_kind=target_kind, target_id=target_id, author=actor,
        kind=kind, text=text, status="OPEN", resolution_note="",
        reply_to=reply_to, anchor_ref=anchor_ref,
        recorded_time=now, marking=marking, version=1)
    store.append("WORKBENCH_ANNOTATION_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


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
        # dissent blocks approvals; letting the blocked party resolve it
        # would let an approver clear their own path. Only the dissenting
        # analyst closes their dissent — approvers carry it visibly instead
        # (APPROVED_WITH_DISSENT).
        raise PermissionError("only the dissenting author may resolve their "
                              "dissent; approve with acknowledge_dissent to "
                              "carry it visibly")
    record = AnnotationRecord(
        annotation_id=annotation_id, target_kind=current["target_kind"],
        target_id=current["target_id"], author=current["author"],
        kind=current["kind"], text=current["text"], status=status,
        resolution_note=note, reply_to=current.get("reply_to", ""),
        anchor_ref=current.get("anchor_ref", ""), recorded_time=now,
        # a resolution never re-classifies the annotation
        marking=marking_from_record(current["marking"]),
        version=current["version"] + 1)
    try:
        store.append("WORKBENCH_ANNOTATION_RECORDED", record, recorded_time=now, actor=actor)
    except ValueError as error:
        raise AnnotationConflict(str(error)) from error
    return record.to_record()


# operational base-view families: target kind -> (view key, id field)
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
    """The marking of the record an annotation binds to, or None when the
    target family is unmarked (registry metadata) or unresolvable. Resolution
    is by the target's declared KIND, never by an id search across families."""
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
        # an anchor is embedded in an observation; it inherits the marking of
        # the manifestation it addresses (target_id is that manifestation id)
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
        # an anchor is addressed by the manifestation it belongs to; visible
        # iff a visible observation carries an anchor into that manifestation
        return any(a.get("manifestation_id") == target_id
                   for o in projection.family("semantic_observation")
                   for a in o.get("anchors", ()))
    try:
        return projection.get(target_kind, target_id) is not None
    except KeyError:
        return False
