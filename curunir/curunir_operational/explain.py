"""What a record is, where it came from, what supports or contradicts it, who
touched it, and what stays unknown.

A hidden record answers exactly as a missing one, so an explanation cannot probe
for what the reader may not see. Provenance walks carry a visited set.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import AccessContext, can_view
from .projection import Projection

NOT_AVAILABLE = {"status": "NOT_AVAILABLE_IN_CONTEXT"}


def _sources_for(projection: Projection, provenance: Mapping[str, Any], context: AccessContext) -> list[dict[str, Any]]:
    result = []
    for source_id in provenance.get("source_ids", []):
        source = projection.sources.get(source_id)
        if source and can_view(source.get("marking"), context):
            result.append({"source_id": source_id, "source_type": source["source_type"],
                           "source_system": source["source_system"], "status": source["status"],
                           "reliability": source.get("reliability", {})})
    return result


def _transformations_for(projection: Projection, provenance: Mapping[str, Any], context: AccessContext,
                         visited: set[str]) -> list[dict[str, Any]]:
    result = []
    for transformation_id in provenance.get("transformation_ids", []):
        if transformation_id in visited:
            continue
        visited.add(transformation_id)
        transformation = projection.transformations.get(transformation_id)
        if transformation is None:
            result.append({"transformation_id": transformation_id, "status": "UNRESOLVED_LINK"})
            continue
        entry = {"transformation_id": transformation_id, "mapping_id": transformation["mapping_id"],
                 "implementation": f"{transformation['implementation_id']}@{transformation['implementation_version']}",
                 "warnings": transformation["warnings"], "lossy_operations": transformation["lossy_operations"],
                 "inputs": []}
        for ref in transformation.get("input_refs", []):
            if ref["kind"] == "ingestion" and ref["ref"] not in visited:
                visited.add(ref["ref"])
                ingestion = projection.ingestions.get(ref["ref"])
                if ingestion and can_view(ingestion.get("marking"), context):
                    entry["inputs"].append({"ingestion_id": ref["ref"], "connector": ingestion["connector_id"],
                                            "source_id": ingestion["source_id"], "received_time": ingestion["received_time"],
                                            "source_time": ingestion["source_time"], "late": ingestion["late"],
                                            "validation": ingestion["validation"]})
        result.append(entry)
    return result


def _find_record(projection: Projection, record_id: str) -> tuple[str, Any] | None:
    if record_id in projection.objects:
        return "object", projection.objects[record_id]
    if record_id in projection.relationships:
        return "relationship", projection.relationships[record_id]
    if record_id in projection.alerts:
        return "alert", projection.alerts[record_id]
    if record_id in projection.recommendations:
        return "recommendation", projection.recommendations[record_id]
    if record_id in projection.sources:
        return "source", projection.sources[record_id]
    if record_id in projection.ingestions:
        return "ingestion", projection.ingestions[record_id]
    for decision in projection.decisions:
        if decision["decision_id"] == record_id:
            return "decision", decision
    for activity in projection.activities:
        if activity["activity_id"] == record_id:
            return "activity", activity
    return None


def explain(projection: Projection, record_id: str, context: AccessContext) -> dict[str, Any]:
    found = _find_record(projection, record_id)
    if found is None:
        return dict(NOT_AVAILABLE)
    kind, entry = found
    record = entry["current"] if kind in ("object", "relationship") else entry["record"] if kind == "alert" else entry
    if not can_view(record.get("marking"), context):
        return dict(NOT_AVAILABLE)
    visited: set[str] = {record_id}
    explanation: dict[str, Any] = {
        "status": "AVAILABLE", "record_id": record_id, "kind": kind,
        "what": _describe(kind, record, entry),
        "epistemic_state": record.get("epistemic_state", "N/A"),
        "access_marking": record.get("marking"),
    }
    provenance = record.get("provenance", {})
    explanation["sources"] = _sources_for(projection, provenance, context)
    explanation["transformations"] = _transformations_for(projection, provenance, context, visited)
    explanation["evidence"] = [dict(e) for e in provenance.get("evidence", [])]
    # Same rule as Projection.view: members the context cannot view are dropped,
    # and a group with fewer than two survivors is not reported at all.
    visible_ids = {object_id for object_id, entry in projection.objects.items()
                   if can_view(entry["current"].get("marking"), context)}
    dependent_sources = []
    for group, members in projection.dependence_groups.items():
        if record_id not in members \
                and not any(e.get("dependence_group_id") == group for e in provenance.get("evidence", [])):
            continue
        visible_members = [m for m in members if m in visible_ids]
        if len(visible_members) >= 2:
            dependent_sources.append({"group_id": group, "member_object_ids": visible_members})
    explanation["dependent_sources"] = dependent_sources
    conflicts = []
    for relationship in projection.relationships.values():
        current = relationship["current"]
        if current["relation_type"] == "CONFLICTS_WITH" and current["status"] == "ACTIVE" \
                and record_id in (current["source_object_id"], current["target_object_id"]) \
                and can_view(current.get("marking"), context):
            other = current["target_object_id"] if current["source_object_id"] == record_id else current["source_object_id"]
            conflicts.append({"relationship_id": current["relationship_id"], "conflicting_object_id": other,
                              "rationale": current.get("rationale", "")})
    explanation["conflicts"] = conflicts
    if kind == "object":
        explanation["quality"] = entry["quality_summary"]
        explanation["freshness"] = entry["freshness"]
        history = []
        previous_attributes: dict[str, Any] = {}
        for version in entry["versions"]:
            if not can_view(version.get("marking"), context):
                continue
            changed = sorted(k for k in version.get("attributes", {})
                             if previous_attributes.get(k) != version["attributes"][k])
            history.append({"version": version["version"], "recorded_time": version["recorded_time"],
                            "epistemic_state": version["epistemic_state"],
                            "correction_of": version.get("correction_of"),
                            "changed_attributes": changed,
                            "recorded_by": projection.event_actor.get(f"{record_id}@v{version['version']}", "UNKNOWN")})
            previous_attributes = version.get("attributes", {})
        explanation["history"] = history
    contributors = []
    for inference in projection.inferences.values():
        if record_id in inference.get("downstream_use", ()) and can_view(inference.get("marking"), context):
            contributors.append({"inference_id": inference["inference_id"], "model_id": inference["model_id"],
                                 "model_version": inference["model_version"], "validation": inference["validation"]})
    if kind == "alert":
        contributors.append({"rule_id": record["rule_id"], "rule_version": record["rule_version"]})
    explanation["rule_and_model_contributors"] = contributors
    human_actions = [a for a in projection.analyst_actions
                     if a["subject_id"] == record_id and can_view(a.get("marking"), context)]
    if kind == "recommendation":
        human_actions.extend(d for d in projection.decisions
                             if d["recommendation_id"] == record_id and can_view(d.get("marking"), context))
    explanation["human_actions"] = human_actions
    unknowns = []
    if kind == "object":
        unknowns = sorted(k for k, v in record.get("quality", {}).items() if v == "UNKNOWN")
        if record.get("source_time") is None:
            unknowns.append("source_time")
        if record.get("geometry") is None:
            unknowns.append("geometry")
    explanation["weak_or_unknown"] = unknowns
    return explanation


def _describe(kind: str, record: Mapping[str, Any], entry: Any) -> str:
    if kind == "object":
        labels = ", ".join(record.get("labels", ())) or record["object_id"]
        return f"{record['object_type']} '{labels}' (version {record['version']}, {record['lifecycle']})"
    if kind == "relationship":
        return f"{record['relation_type']} from {record['source_object_id']} to {record['target_object_id']} ({record['status']})"
    if kind == "alert":
        return f"Alert [{record['severity']}] {record['trigger']}"
    if kind == "recommendation":
        return f"Recommendation ({record['action_kind']}): {record['proposed_action']}"
    if kind == "decision":
        return f"Decision {record['state']} on {record['recommendation_id']} by {record['actor_id']}"
    if kind == "source":
        return f"Source {record['source_system']} ({record['source_type']}, {record['status']})"
    if kind == "ingestion":
        return f"Ingestion via {record['connector_id']} ({record['validation']})"
    return f"Activity: {record.get('description', '')}"


def explain_markdown(explanation: Mapping[str, Any]) -> str:
    if explanation.get("status") != "AVAILABLE":
        return "Not available in this access context.\n"
    lines = [f"# Explanation: {explanation['record_id']}", "",
             f"**What:** {explanation['what']}",
             f"**Epistemic state:** {explanation['epistemic_state']}", ""]
    if explanation.get("sources"):
        lines.append("## Supplying systems")
        lines.extend(f"- {s['source_system']} ({s['source_type']}, status {s['status']})" for s in explanation["sources"])
    if explanation.get("transformations"):
        lines.append("## Transformations")
        for transformation in explanation["transformations"]:
            lines.append(f"- {transformation.get('mapping_id', transformation['transformation_id'])}"
                         f" — warnings: {len(transformation.get('warnings', ()))},"
                         f" lossy: {len(transformation.get('lossy_operations', ()))}")
    if explanation.get("evidence"):
        lines.append("## Evidence (ARGUS)")
        for evidence in explanation["evidence"]:
            lines.append(f"- {evidence['source_object_id']} basis={evidence['evidence_basis_id']}"
                         f" review={evidence['review_state']} independence={evidence['independence_status']}")
    if explanation.get("dependent_sources"):
        lines.append("## Dependent reporting")
        for group in explanation["dependent_sources"]:
            lines.append(f"- group {group['group_id']}: {', '.join(group['member_object_ids'])} share one underlying basis")
    if explanation.get("conflicts"):
        lines.append("## Conflicts")
        lines.extend(f"- conflicts with {c['conflicting_object_id']}: {c['rationale']}" for c in explanation["conflicts"])
    if explanation.get("history"):
        lines.append("## History")
        for item in explanation["history"]:
            marker = f" (corrects {item['correction_of']})" if item.get("correction_of") else ""
            lines.append(f"- v{item['version']} at {item['recorded_time']} [{item['epistemic_state']}]"
                         f" changed: {', '.join(item['changed_attributes']) or 'nothing'}{marker}")
    if explanation.get("rule_and_model_contributors"):
        lines.append("## Rule / model contributors")
        for contributor in explanation["rule_and_model_contributors"]:
            name = contributor.get("model_id") or contributor.get("rule_id")
            lines.append(f"- {name}")
    if explanation.get("human_actions"):
        lines.append("## Human actions")
        for action in explanation["human_actions"]:
            verb = action.get("kind") or action.get("state")
            lines.append(f"- {verb} by {action['actor_id']} at {action['recorded_time']}")
    if explanation.get("weak_or_unknown"):
        lines.append(f"\n**Weak or unknown:** {', '.join(explanation['weak_or_unknown'])}")
    return "\n".join(lines) + "\n"
