"""Current-state and historical-as-of projections with fail-closed access
filtering.

A projection is derived purely from the event log up to a cutoff, plus a
snapshot time for freshness. Access-filtered views compute every list and
every count from the filtered set only, so nothing about hidden records —
existence, counts, labels, provenance or alert state — leaks into a lower
view. The quality summary is a versioned convenience policy over visible
dimensions; unknown never becomes zero or certainty.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import AccessContext, can_view
from .canonical import parse_time, sha256
from .store import MissionDataStore

QUALITY_SUMMARY_POLICY = "curunir-operational-quality-summary-v1"
DEFAULT_STALENESS_HOURS = 24.0


def _freshness(version: Mapping[str, Any], snapshot_time: str, staleness_hours: Mapping[str, float]) -> dict[str, Any]:
    basis = version.get("source_time") or version.get("valid_from")
    if not basis:
        return {"state": "UNKNOWN", "age_hours": None, "basis": None}
    age = (parse_time(snapshot_time) - parse_time(basis)).total_seconds() / 3600
    threshold = staleness_hours.get(version["object_type"], DEFAULT_STALENESS_HOURS)
    return {"state": "STALE" if age > threshold else "FRESH", "age_hours": round(age, 2),
            "basis": basis, "threshold_hours": threshold}


def quality_summary(quality: Mapping[str, Any], freshness_state: str) -> dict[str, Any]:
    """Versioned convenience roll-up. Components stay visible; UNKNOWN is
    reported as UNKNOWN, never treated as zero or as certainty."""
    weak = sorted(k for k, v in quality.items()
                  if v == "UNKNOWN" or (isinstance(v, (int, float)) and v < 0.5)
                  or v in ("CONFLICTING_REPORTS", "DEPENDENT_GROUP", "UNREVIEWED"))
    if freshness_state == "STALE":
        weak.append("freshness")
    state = "DEGRADED" if weak else "NOMINAL"
    return {"policy": QUALITY_SUMMARY_POLICY, "state": state, "weak_or_unknown_dimensions": weak,
            "components": dict(quality)}


class _ClusterIndex:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        self.parent.setdefault(node, node)
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            low, high = sorted((ra, rb))
            self.parent[high] = low


class Projection:
    def __init__(self, store: MissionDataStore, *, as_of_seq: int | None = None,
                 snapshot_time: str | None = None, staleness_hours: Mapping[str, float] | None = None,
                 valid_at: str | None = None):
        events = store.events(as_of_seq)
        self.store_id = store.meta["store_id"]
        self.as_of_seq = events[-1]["seq"] if events else 0  # privileged diagnostic; never emitted in views
        self.state_token = events[-1]["entry_hash"][:16] if events else "genesis"
        self.snapshot_time = snapshot_time or (events[-1]["recorded_time"] if events else store.meta["created_time"])
        self.staleness_hours = dict(staleness_hours or {})
        # Bounded valid-time as-of: only versions whose valid point is at or
        # before `valid_at` participate; the current-state rule then applies
        # within that subset. Combined with `as_of_seq` this answers both
        # knowledge-as-of and (bounded) validity-as-of; ranged valid intervals
        # and full bitemporal joins remain out of scope (BITEMPORAL_LITE).
        self.valid_at = valid_at
        self.event_actor: dict[str, str] = {}
        self.objects: dict[str, dict[str, Any]] = {}
        self.relationships: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        self.ingestions: dict[str, dict[str, Any]] = {}
        self.transformations: dict[str, dict[str, Any]] = {}
        self.activities: list[dict[str, Any]] = []
        self.alerts: dict[str, dict[str, Any]] = {}
        self.recommendations: dict[str, dict[str, Any]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.analyst_actions: list[dict[str, Any]] = []
        self.association_proposals: dict[str, dict[str, Any]] = {}
        self.inferences: dict[str, dict[str, Any]] = {}
        self.analytical_proposals: dict[str, dict[str, Any]] = {}
        self.model_packages: dict[str, dict[str, Any]] = {}
        self.accreditations: list[dict[str, Any]] = []
        self.requirements: dict[str, dict[str, Any]] = {}
        self.analyst_tasks: dict[str, dict[str, Any]] = {}
        self.evidence_requests: dict[str, dict[str, Any]] = {}
        self.definitions: dict[str, list[dict[str, Any]]] = {"schema_definition": [], "mapping_definition": [],
                                                             "pipeline_definition": [], "workshop_definition": []}
        for event in events:
            self._apply(event)
        self._finalize()

    def _apply(self, event: Mapping[str, Any]) -> None:
        record = event["record"]
        kind = record.get("record_type")
        if kind == "object_version":
            entry = self.objects.setdefault(record["object_id"], {"versions": []})
            entry["versions"].append(record)
            self.event_actor[f"{record['object_id']}@v{record['version']}"] = event["actor"]
        elif kind == "relationship_version":
            entry = self.relationships.setdefault(record["relationship_id"], {"versions": []})
            entry["versions"].append(record)
        elif kind == "source":
            self.sources[record["source_id"]] = record
        elif kind == "ingestion":
            self.ingestions[record["ingestion_id"]] = record
        elif kind == "transformation":
            self.transformations[record["transformation_id"]] = record
        elif kind == "activity":
            self.activities.append(record)
        elif kind == "alert":
            self.alerts[record["alert_id"]] = {"record": record, "status": record["status"], "transitions": []}
        elif kind == "alert_transition":
            entry = self.alerts.get(record["alert_id"])
            if entry is not None:
                entry["transitions"].append(record)
                entry["status"] = record["to_status"]
        elif kind == "recommendation":
            self.recommendations[record["recommendation_id"]] = record
        elif kind == "decision":
            self.decisions.append(record)
        elif kind == "analyst_action":
            self.analyst_actions.append(record)
        elif kind == "association_proposal":
            self.association_proposals[record["proposal_id"]] = {"proposal": record, "resolutions": []}
        elif kind == "association_resolution":
            entry = self.association_proposals.get(record["proposal_id"])
            if entry is not None:
                entry["resolutions"].append(record)
        elif kind == "inference":
            self.inferences[record["inference_id"]] = record
        elif kind == "analytical_proposal":
            self.analytical_proposals[record["proposal_id"]] = record
        elif kind == "model_package":
            self.model_packages[record["model_id"]] = record
        elif kind == "accreditation":
            self.accreditations.append(record)
        elif kind == "information_requirement":
            known = self.requirements.get(record["requirement_id"])
            if known is None:
                self.requirements[record["requirement_id"]] = {
                    "record": record, "status": record["status"], "transitions": []}
            else:
                # a re-appended requirement (priority escalation / affected-id
                # fold) updates the record but must not reset the workflow
                # status derived from transitions, nor wipe the audit trail
                known["record"] = record
        elif kind == "analyst_task":
            self.analyst_tasks[record["task_id"]] = {"record": record, "status": record["status"], "transitions": []}
        elif kind == "evidence_request":
            self.evidence_requests[record["request_id"]] = {"record": record, "status": record["status"],
                                                            "transitions": []}
        elif kind == "workflow_transition":
            registry = {"requirement": self.requirements, "analyst_task": self.analyst_tasks,
                        "evidence_request": self.evidence_requests}[record["subject_kind"]]
            entry = registry.get(record["subject_id"])
            if entry is not None:
                entry["transitions"].append(record)
                entry["status"] = record["to_status"]
        elif kind in self.definitions:
            self.definitions[kind].append(record)

    @staticmethod
    def _valid_point(version: Mapping[str, Any]) -> str:
        return version.get("valid_from") or version.get("source_time") or version["recorded_time"]

    def _finalize(self) -> None:
        if self.valid_at is not None:
            cutoff = parse_time(self.valid_at)
            for object_id in list(self.objects):
                entry = self.objects[object_id]
                qualifying = [v for v in entry["versions"] if parse_time(self._valid_point(v)) <= cutoff]
                if not qualifying:
                    del self.objects[object_id]
                    continue
                entry["versions_all_recorded"] = entry["versions"]
                entry["versions"] = qualifying
        for object_id, entry in self.objects.items():
            # Current = latest validity; ties resolved by version number, so a
            # late-arriving older report never displaces newer state, while a
            # correction (same validity, higher version) supersedes.
            current = max(entry["versions"], key=lambda v: (parse_time(self._valid_point(v)), v["version"]))
            entry["current"] = current
            entry["history_count"] = len(entry["versions"])
            entry["freshness"] = _freshness(current, self.snapshot_time, self.staleness_hours)
            entry["quality_summary"] = quality_summary(current.get("quality", {}), entry["freshness"]["state"])
            corrected = {v["correction_of"] for v in entry["versions"] if v.get("correction_of")}
            entry["corrected_version_ids"] = sorted(corrected)
        for entry in self.relationships.values():
            entry["current"] = entry["versions"][-1]
        clusters = _ClusterIndex()
        for entry in self.relationships.values():
            current = entry["current"]
            if current["relation_type"] == "SAME_AS" and current["status"] == "ACTIVE":
                clusters.union(current["source_object_id"], current["target_object_id"])
        self.cluster_of = {object_id: clusters.find(object_id) for object_id in self.objects}
        groups: dict[str, set[str]] = {}
        for object_id, entry in self.objects.items():
            for evidence in entry["current"].get("provenance", {}).get("evidence", []):
                group = evidence.get("dependence_group_id")
                if group:
                    groups.setdefault(group, set()).add(object_id)
        self.dependence_groups = {k: sorted(v) for k, v in sorted(groups.items())}

    # ---- access-aware views -------------------------------------------------

    def view(self, context: AccessContext) -> dict[str, Any]:
        objects = []
        visible_ids = set()
        for object_id in sorted(self.objects):
            entry = self.objects[object_id]
            current = entry["current"]
            if not can_view(current.get("marking"), context):
                continue
            visible_ids.add(object_id)
            objects.append({**current, "history_count": entry["history_count"],
                            "freshness": entry["freshness"], "quality_summary": entry["quality_summary"],
                            "cluster_id": self.cluster_of.get(object_id, object_id),
                            "corrected_version_ids": entry["corrected_version_ids"]})
        relationships = []
        for relationship_id in sorted(self.relationships):
            current = self.relationships[relationship_id]["current"]
            if current["source_object_id"] in visible_ids and current["target_object_id"] in visible_ids \
                    and can_view(current.get("marking"), context):
                relationships.append(current)
        activities = [a for a in self.activities if can_view(a.get("marking"), context)
                      and set(a.get("subject_ids", ())) <= visible_ids]
        alerts = []
        for alert_id in sorted(self.alerts):
            entry = self.alerts[alert_id]
            if can_view(entry["record"].get("marking"), context):
                alerts.append({**entry["record"], "status": entry["status"],
                               "transitions": entry["transitions"]})
        recommendations = [r for r in (self.recommendations[k] for k in sorted(self.recommendations))
                           if can_view(r.get("marking"), context)]
        decisions = [d for d in self.decisions if can_view(d.get("marking"), context)]
        analyst_actions = [a for a in self.analyst_actions if can_view(a.get("marking"), context)]
        proposals = []
        for proposal_id in sorted(self.association_proposals):
            entry = self.association_proposals[proposal_id]
            proposal = entry["proposal"]
            if proposal["left_object_id"] in visible_ids and proposal["right_object_id"] in visible_ids \
                    and can_view(proposal.get("marking"), context):
                proposals.append({**proposal, "resolutions": entry["resolutions"]})
        dependence_groups = [{"group_id": group, "member_object_ids": [m for m in members if m in visible_ids]}
                             for group, members in self.dependence_groups.items()]
        dependence_groups = [g for g in dependence_groups if len(g["member_object_ids"]) >= 2]
        # A workflow record (requirement/task/evidence request) may be visible
        # while some object it references is not: redact object references the
        # context cannot view so a cross-compartment id never leaks through a
        # record the context is otherwise entitled to see.
        hidden_object_ids = {oid for oid in self.objects if oid not in visible_ids}

        def _redact_refs(refs):
            return [("REDACTED" if str(r).split("@v")[0] in hidden_object_ids else r) for r in refs]

        def _redact_record(record):
            out = dict(record)
            for key in ("affected_ids", "evidence_refs", "depends_on"):
                if key in out:
                    out[key] = _redact_refs(out[key])
            out["transitions"] = [{**t, "evidence_refs": _redact_refs(t.get("evidence_refs", []))}
                                  for t in record.get("transitions", [])]
            return out

        requirements = []
        for requirement_id in sorted(self.requirements):
            entry = self.requirements[requirement_id]
            if can_view(entry["record"].get("marking"), context):
                requirements.append(_redact_record({**entry["record"], "status": entry["status"],
                                                    "transitions": entry["transitions"]}))
        analyst_tasks = []
        for task_id in sorted(self.analyst_tasks):
            entry = self.analyst_tasks[task_id]
            if can_view(entry["record"].get("marking"), context):
                overdue = bool(entry["record"].get("due_time")
                               and entry["status"] in ("ASSIGNED", "IN_PROGRESS", "BLOCKED")
                               and parse_time(entry["record"]["due_time"]) < parse_time(self.snapshot_time))
                analyst_tasks.append(_redact_record({**entry["record"], "status": entry["status"],
                                                    "overdue": overdue, "transitions": entry["transitions"]}))
        evidence_requests = []
        for request_id in sorted(self.evidence_requests):
            entry = self.evidence_requests[request_id]
            if can_view(entry["record"].get("marking"), context):
                evidence_requests.append(_redact_record({**entry["record"], "status": entry["status"],
                                                         "transitions": entry["transitions"]}))
        view = {
            # meta carries an opaque state token, never the raw event sequence:
            # a global seq is a hidden-activity side channel for lower contexts.
            "meta": {"store_id": self.store_id, "state_token": self.state_token,
                     "snapshot_time": self.snapshot_time, "valid_at": self.valid_at,
                     "context_id": context.context_id, "quality_summary_policy": QUALITY_SUMMARY_POLICY},
            "objects": objects, "relationships": relationships, "activities": activities,
            "alerts": alerts, "recommendations": recommendations, "decisions": decisions,
            "analyst_actions": analyst_actions, "association_proposals": proposals,
            "dependence_groups": dependence_groups, "information_requirements": requirements,
            "analyst_tasks": analyst_tasks, "evidence_requests": evidence_requests,
        }
        by_type: dict[str, int] = {}
        for item in objects:
            by_type[item["object_type"]] = by_type.get(item["object_type"], 0) + 1
        view["counts"] = {
            "objects_total": len(objects), "objects_by_type": dict(sorted(by_type.items())),
            "relationships": len(relationships), "activities": len(activities), "alerts": len(alerts),
            "open_alerts": sum(1 for a in alerts if a["status"] == "OPEN"),
            "recommendations": len(recommendations), "decisions": len(decisions),
            "association_proposals": len(proposals),
            "stale_objects": sum(1 for o in objects if o["freshness"]["state"] == "STALE"),
            "disputed_objects": sum(1 for o in objects if o["epistemic_state"] == "DISPUTED"),
            "open_information_requirements": sum(1 for r in requirements
                                                 if r["status"] in ("OPEN", "EVIDENCE_PENDING")),
            "open_analyst_tasks": sum(1 for t in analyst_tasks
                                      if t["status"] in ("ASSIGNED", "IN_PROGRESS", "BLOCKED")),
        }
        return view

    def object_history(self, object_id: str, context: AccessContext) -> list[dict[str, Any]]:
        entry = self.objects.get(object_id)
        if entry is None or not can_view(entry["current"].get("marking"), context):
            return []  # unknown and forbidden are indistinguishable
        return [v for v in entry["versions"] if can_view(v.get("marking"), context)]

    def changes_since(self, seq: int, context: AccessContext, store: MissionDataStore) -> dict[str, Any]:
        changed: dict[str, list[str]] = {}
        for event in store.events(self.as_of_seq):
            if event["seq"] <= seq:
                continue
            record = event["record"]
            if not can_view(record.get("marking"), context):
                continue
            key = record["record_type"]
            label = record.get("object_id") or record.get("relationship_id") or record.get("alert_id") \
                or record.get("recommendation_id") or record.get("ingestion_id") or record.get("record_type")
            changed.setdefault(key, []).append(str(label))
        return {"since_state": store.state_token_at(seq), "until_state": self.state_token,
                "changes": {k: sorted(set(v)) for k, v in sorted(changed.items())}}


def projection_hash(view: Mapping[str, Any]) -> str:
    return sha256(view)
