"""What the stored records say about whether a report may be approved.

A view answers what an actor may read; this module answers whether an approval
may commit, so it reads the raw store. A record the approver cannot see blocks
the approval instead of counting as absent.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from curunir_operational.access import AccessContext, can_view
from curunir_operational.security import (
    MaterialReference,
    PRIMARY_ID_FIELDS,
    material_references,
    resolve_reference_records,
)
from curunir_analytic.contracts import SETTLED_SUPPORT_STATUSES
from curunir_semantic.contracts import HYPOTHESIS_SETTLED_SUPPORT_STATUSES

from .store import WorkbenchStore


def report_part_ids(store: WorkbenchStore, report_id: str) -> set[str]:
    parts = {report_id}
    for version in store.report_versions(report_id):
        for section in version.get("sections", ()):
            parts.add(section["section_id"])
            parts.update(
                sentence["sentence_id"]
                for sentence in section.get("sentences", ())
            )
    return parts


def report_basis_ids(report: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for section in report.get("sections", ()):
        refs.update(section.get("option_ids", ()))
        for sentence in section.get("sentences", ()):
            refs.update(sentence.get("basis_refs", ()))
            refs.update(sentence.get("assumption_ids", ()))
    return {ref for ref in refs if isinstance(ref, str) and ref}


def _record_identity(record: Mapping[str, Any]) -> tuple[str, str, int]:
    kind = str(record.get("record_type", ""))
    field = PRIMARY_ID_FIELDS.get(kind, "")
    return kind, str(record.get(field, "")), int(record.get("version", 1))


def _basis_closure(
    store: WorkbenchStore,
    direct_refs: Iterable[str],
) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """Follow every reference from the report until nothing new turns up.

    Two of the steps run backwards: citing an observation or a manifestation
    also pulls in the claims drawn from it, and citing an objective pulls in the
    assumptions and impact paths that name it. Both belong to what a report
    rests on, so the approval gate follows them even though the marking registry
    records the dependency the other way round.
    """
    queue = [MaterialReference("*", ref) for ref in direct_refs]
    records: list[dict] = []
    seen_records: set[tuple[str, str, int]] = set()
    seen_refs: set[MaterialReference] = set()
    unresolved: set[str] = set()
    direct = set(direct_refs)
    while queue:
        reference = queue.pop()
        if reference in seen_refs:
            continue
        seen_refs.add(reference)
        resolved = resolve_reference_records(store, (reference,))
        if not resolved and reference.record_id in direct:
            unresolved.add(reference.record_id)
        for record in resolved:
            identity = _record_identity(record)
            if identity in seen_records:
                continue
            seen_records.add(identity)
            records.append(record)
            queue.extend(material_references(record))
            kind, record_id, _version = identity
            if kind == "semantic_observation":
                claims = (
                    store.claims_referencing_observation(record_id)
                    if hasattr(store, "claims_referencing_observation") else ()
                )
                queue.extend(MaterialReference("semantic_claim", claim["claim_id"])
                             for claim in claims)
            elif kind == "fabric_manifestation" and hasattr(
                    store, "observations_for_manifestation"):
                queue.extend(MaterialReference(
                    "semantic_observation", observation["observation_id"])
                    for observation in store.observations_for_manifestation(record_id))
            elif kind == "mission_objective" and hasattr(store, "current_analytics"):
                queue.extend(MaterialReference("analytic_assumption", assumption_id)
                             for assumption_id, assumption
                             in store.current_analytics("analytic_assumption").items()
                             if record_id in (assumption.get("objective_ids") or ()))
                queue.extend(MaterialReference("impact_path", path_id)
                             for path_id, path
                             in store.current_analytics("impact_path").items()
                             if path.get("objective_id") == record_id)
    return tuple(records), tuple(sorted(unresolved))


_SETTLED_STATUS = {
    **SETTLED_SUPPORT_STATUSES,
    "hypothesis": HYPOTHESIS_SETTLED_SUPPORT_STATUSES,
}


def _status_concern(record: Mapping[str, Any]) -> dict[str, str] | None:
    kind, record_id, _version = _record_identity(record)
    status = record.get("status")
    settled = _SETTLED_STATUS.get(kind)
    if isinstance(status, str) and status and settled is not None \
            and status not in settled:
        return {
            "code": "CONTESTED_AS_SETTLED",
            "kind": kind,
            "subject_id": record_id,
            "reason": f"current {kind} status is {status}",
        }
    if kind == "semantic_claim" and record.get("epistemic_state") == "DISPUTED":
        return {
            "code": "CONTESTED_AS_SETTLED",
            "kind": kind,
            "subject_id": record_id,
            "reason": "current semantic claim is DISPUTED",
        }
    return None


def _submission_actors(store: WorkbenchStore, report_id: str) -> set[str]:
    actors = {
        disposition["actor_id"]
        for disposition in store.report_dispositions(report_id)
        if disposition["disposition"] == "SUBMITTED"
    }
    # Submitting writes the IN_REVIEW version first. If the disposition after
    # it is lost, that event's actor still names the submitter, so they still
    # cannot approve their own report.
    actors.update(
        event["actor"]
        for event in store.events()
        if event["record"].get("record_type") == "workbench_report"
        and event["record"].get("report_id") == report_id
        and event["record"].get("status") == "IN_REVIEW"
    )
    return actors


def authoritative_approval_state(
    store: WorkbenchStore,
    report: Mapping[str, Any],
    *,
    context: AccessContext,
    actor: str,
    acknowledged_dissent: Iterable[str] = (),
) -> dict[str, Any]:
    """Return every raw-state fact that can block this approval."""
    report_id = report["report_id"]
    direct_refs = report_basis_ids(report)
    closure, unresolved = _basis_closure(store, direct_refs)
    related_ids = set(direct_refs)
    claim_ids: set[str] = set()
    hidden_basis: list[dict[str, str]] = []
    blocking_basis: list[dict[str, str]] = []
    for record in closure:
        kind, record_id, _version = _record_identity(record)
        if record_id:
            related_ids.add(record_id)
        if kind == "semantic_claim":
            claim_ids.add(record["claim_id"])
            related_ids.add(record["claim_id"])

        concern = _status_concern(record)
        if concern is not None:
            if can_view(record.get("marking"), context):
                blocking_basis.append(concern)
            else:
                hidden_basis.append(concern)

        # A dependency the approver cannot see still counts; the filtered view
        # must not make it look like nothing is there.
        if not can_view(record.get("marking"), context):
            hidden_basis.append({
                "code": "HIDDEN_BASIS_STATE",
                "kind": kind,
                "subject_id": record_id,
                "reason": "material basis is outside approving context",
            })

    latest_states = store.latest_by_id("semantic_claim_state", "claim_id")
    for claim_id in sorted(claim_ids):
        state = latest_states.get(claim_id)
        if state is None:
            continue
        if not can_view(state.get("marking"), context):
            hidden_basis.append({
                "code": "HIDDEN_BASIS_STATE",
                "kind": "semantic_claim_state",
                "subject_id": claim_id,
                "reason": "current claim state is outside approving context",
            })
        elif state.get("state") in {"RETRACTED", "SUPERSEDED", "CORRECTED", "STALE"}:
            blocking_basis.append({
                "code": "STALE_BASIS",
                "kind": "semantic_claim_state",
                "subject_id": claim_id,
                "reason": f"current claim state is {state.get('state')}",
            })

    for item in store.latest_by_id("review_item", "item_id").values():
        if item.get("status") != "OPEN" or item.get("subject_id") not in related_ids:
            continue
        concern = {
            "code": "CONTESTED_AS_SETTLED",
            "kind": "review_item",
            "subject_id": item["subject_id"],
            "reason": "open review exists on report basis",
        }
        if can_view(item.get("marking"), context):
            blocking_basis.append(concern)
        else:
            hidden_basis.append(concern)

    parts = report_part_ids(store, report_id)
    raw_dissent = [
        annotation
        for annotation in store.current_annotations().values()
        if annotation.get("kind") == "DISSENT"
        and annotation.get("status") == "OPEN"
        and (
            annotation.get("target_id") in parts
            or annotation.get("anchor_ref") in parts
        )
    ]
    acknowledged = set(acknowledged_dissent)
    hidden_dissent = [
        annotation["annotation_id"]
        for annotation in raw_dissent
        if not can_view(annotation.get("marking"), context)
    ]
    unacknowledged_dissent = [
        annotation["annotation_id"]
        for annotation in raw_dissent
        if can_view(annotation.get("marking"), context)
        and annotation["annotation_id"] not in acknowledged
    ]
    visible_dissent = [
        annotation["annotation_id"]
        for annotation in raw_dissent
        if can_view(annotation.get("marking"), context)
    ]

    content_authors = {
        version["author"] for version in store.report_versions(report_id)
    }
    submitters = _submission_actors(store, report_id)
    return {
        "report_id": report_id,
        "unresolved_basis": list(unresolved),
        "hidden_basis_concerns": hidden_basis,
        "blocking_basis_concerns": blocking_basis,
        "hidden_dissent_ids": sorted(hidden_dissent),
        "visible_dissent_ids": sorted(visible_dissent),
        "unacknowledged_dissent_ids": sorted(unacknowledged_dissent),
        "content_authors": sorted(content_authors),
        "submitters": sorted(submitters),
        "separation_of_duties_violation": actor in content_authors | submitters,
    }
