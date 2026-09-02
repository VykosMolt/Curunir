"""One access context's view of a whole mission store, filtered before it is
serialized.

It joins the operational world view with the fabric, semantic, analytic and
workbench families from the same store, under three rules: a record the context
cannot see appears in no list, count, graph, search or timeline; any hidden id
appearing inside a visible record, in a reference field or in free text, is
replaced with REDACTED; and a lookup answers the same way for a hidden record as
for one that does not exist.

The source registry families carry no marking, because they say where Curunír
may look and how those lookups went, not what it found. Everything acquired or
derived from them is marking-gated.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from curunir_operational.access import AccessContext, can_view
from curunir_operational.canonical import parse_time
from curunir_operational.projection import Projection

from .store import WorkbenchStore

# Families where a re-append supersedes: the newest record for an id is the
# current one, and earlier versions stay in the log.
LATEST_FAMILIES: dict[str, str] = {
    # fabric
    "fabric_source_descriptor": "source_id",
    "fabric_source_profile": "source_id",
    "fabric_information_need": "need_id",
    "fabric_discovery_plan": "plan_id",
    "fabric_pivot": "pivot_id",
    "fabric_watch": "watch_id",
    # semantic
    "semantic_document": "document_id",
    "semantic_claim": "claim_id",
    "hypothesis": "hypothesis_id",
    "discriminator": "discriminator_id",
    "collection_route": "route_id",
    "review_item": "item_id",
    # operational model proposals (no version field; the last append wins)
    "analytical_proposal": "proposal_id",
    # analytic
    "analytic_theme": "theme_id",
    "analytic_narrative": "narrative_id",
    "narrative_variant": "variant_id",
    "propagation_edge": "edge_id",
    "stakeholder_assessment": "assessment_id",
    "influence_assertion": "influence_id",
    "mission_objective": "objective_id",
    "analytic_assumption": "assumption_id",
    "impact_path": "path_id",
    "response_option": "option_id",
    "historical_episode": "episode_id",
    "historical_analogue": "analogue_id",
    "analytic_forecast": "forecast_id",
    "forecast_indicator": "indicator_id",
    "strategic_warning": "warning_id",
    # workbench
    "workbench_annotation": "annotation_id",
    "workbench_report": "report_id",
    "workbench_saved_view": "view_id",
}

# Append-only families: every record stands on its own.
APPEND_FAMILIES: dict[str, str] = {
    "fabric_source_status": "status_id",
    "fabric_execution": "execution_id",
    "fabric_manifestation": "manifestation_id",
    "fabric_coverage": "coverage_id",
    "fabric_watch_run": "run_id",
    "fabric_change": "change_id",
    "semantic_observation": "observation_id",
    "semantic_claim_state": "state_id",
    "semantic_change": "change_id",
    "analytic_transition": "transition_id",
    "workbench_report_disposition": "disposition_id",
}

ALL_FAMILIES = {**LATEST_FAMILIES, **APPEND_FAMILIES}

# Catalog metadata: unmarked by contract and visible to everyone who is
# authenticated, so it never joins the hidden set.
UNMARKED_REGISTRY_FAMILIES = ("fabric_source_descriptor", "fabric_source_profile",
                              "fabric_source_status")

REDACTED = "REDACTED"


def _base_id(ref: Any) -> str:
    return str(ref).split("@v")[0]


def record_time(record: Mapping[str, Any]) -> str:
    """When a record became known; each family names that field differently."""
    for key in ("recorded_time", "retrieval_time", "assessed_time",
                "observed_time", "created_time", "started_time"):
        value = record.get(key)
        if value:
            return value
    return ""


class MissionProjection:
    """Everything one access context may see in a mission store."""

    def __init__(self, store: WorkbenchStore, context: AccessContext, *,
                 as_of_seq: int | None = None, snapshot_time: str | None = None,
                 valid_at: str | None = None):
        self.store = store
        self.context = context
        self.base = Projection(store, as_of_seq=as_of_seq,
                               snapshot_time=snapshot_time, valid_at=valid_at)
        self.base_view = self.base.view(context)
        self.snapshot_time = self.base.snapshot_time
        self.state_token = self.base.state_token
        self._visible_activity_ids = {a["activity_id"]
                                      for a in self.base_view["activities"]}
        self._visible_ids: set[str] | None = None
        self._family_cache: dict[str, list[dict]] = {}

        # ---- split each family into visible and hidden, once ----
        self._current: dict[str, dict[str, dict]] = {}
        self._history: dict[str, dict[str, list[dict]]] = {}
        hidden: set[str] = {oid for oid in self.base.objects
                            if not can_view(self.base.objects[oid]["current"].get("marking"), context)}
        hidden |= {rid for rid, entry in self.base.relationships.items()
                   if not can_view(entry["current"].get("marking"), context)}
        # A hidden activity, alert, task or decision id has to scrub out of
        # visible records just like a hidden object id does.
        def _op_hidden(records, id_field, unwrap=None):
            for entry in records:
                record = unwrap(entry) if unwrap else entry
                if not can_view(record.get("marking"), context):
                    hidden.add(record[id_field])
        _op_hidden(self.base.activities, "activity_id")
        _op_hidden(self.base.alerts.values(), "alert_id", lambda e: e["record"])
        _op_hidden(self.base.recommendations.values(), "recommendation_id")
        _op_hidden(self.base.decisions, "decision_id")
        _op_hidden(self.base.analyst_actions, "action_id")
        _op_hidden(self.base.association_proposals.values(), "proposal_id",
                   lambda e: e["proposal"])
        _op_hidden(self.base.inferences.values(), "inference_id")
        _op_hidden(self.base.requirements.values(), "requirement_id",
                   lambda e: e["record"])
        _op_hidden(self.base.analyst_tasks.values(), "task_id",
                   lambda e: e["record"])
        _op_hidden(self.base.evidence_requests.values(), "request_id",
                   lambda e: e["record"])
        _op_hidden(self.base.sources.values(), "source_id")
        _op_hidden(self.base.ingestions.values(), "ingestion_id")
        # An activity is also suppressed when a participant is hidden; those
        # count as hidden here too.
        for record in self.base.activities:
            if record["activity_id"] not in self._visible_activity_ids:
                hidden.add(record["activity_id"])
        # A relationship or proposal dropped because an endpoint is hidden is
        # itself hidden: its id names structure the context may not see.
        visible_relationship_ids = {r["relationship_id"]
                                    for r in self.base_view["relationships"]}
        for rid, entry in self.base.relationships.items():
            if rid not in visible_relationship_ids:
                hidden.add(rid)
        visible_proposal_ids = {p["proposal_id"]
                                for p in self.base_view["association_proposals"]}
        for pid in self.base.association_proposals:
            if pid not in visible_proposal_ids:
                hidden.add(pid)
        for record_type, id_field in LATEST_FAMILIES.items():
            visible: dict[str, dict] = {}
            history: dict[str, list[dict]] = {}
            for record in store.records_of(record_type):
                rid = record[id_field]
                history.setdefault(rid, []).append(record)
            for rid, versions in history.items():
                # Highest version wins. Families with no version field all tie
                # at 1, so the last append wins, as the store does it.
                current = max(reversed(versions), key=lambda r: r.get("version", 1))
                if record_type in UNMARKED_REGISTRY_FAMILIES and "marking" not in current:
                    visible[rid] = current
                elif can_view(current.get("marking"), context):
                    visible[rid] = current
                else:
                    hidden.add(rid)
            self._current[record_type] = visible
            self._history[record_type] = history
        self._append: dict[str, list[dict]] = {}
        for record_type, id_field in APPEND_FAMILIES.items():
            visible_records: list[dict] = []
            for record in store.records_of(record_type):
                if record_type in UNMARKED_REGISTRY_FAMILIES and "marking" not in record:
                    visible_records.append(record)
                elif can_view(record.get("marking"), context):
                    visible_records.append(record)
                else:
                    hidden.add(record[id_field])
            self._append[record_type] = visible_records
        self.hidden_ids = hidden
        # Build one pattern that matches any hidden id, with or without an
        # "@vN" suffix, anywhere in a string. Records also embed shortened ids
        # in free text, and a shortened id still identifies the record, so each
        # hidden id contributes its common truncations as well. A truncation is
        # only used when no visible id starts with it, since some families share
        # a long prefix and scrubbing that would damage ids the reader may see.
        # Longest first, so the full id beats its own prefixes.
        visible_ids = self.visible_id_set()
        needles: set[str] = set()
        for h in hidden:
            needles.add(h)
            for cut in (24, 18, 16):
                if len(h) <= cut:
                    continue
                prefix = h[:cut]
                if not any(vid.startswith(prefix) for vid in visible_ids):
                    needles.add(prefix)
        self._scrub_re = re.compile(
            "|".join(re.escape(n) + r"(?:@v\d+)?"
                     for n in sorted(needles, key=len, reverse=True))
        ) if needles else None
        # A cluster's representative may itself be hidden. Name each cluster
        # after its smallest visible member instead, so scrubbing cannot merge
        # unrelated clusters into one called REDACTED.
        groups: dict[str, list[str]] = {}
        for record in self.base_view["objects"]:
            root = self.base.cluster_of.get(record["object_id"], record["object_id"])
            groups.setdefault(root, []).append(record["object_id"])
        visible_root = {root: min(members) for root, members in groups.items()}
        roots_with_hidden = {root for obj, root in self.base.cluster_of.items()
                             if obj in hidden}
        for record in self.base_view["objects"]:
            root = self.base.cluster_of.get(record["object_id"], record["object_id"])
            record["cluster_id"] = visible_root[root]
            if root in roots_with_hidden:
                record["cluster_partially_hidden"] = True
        # The base view redacts its own objects but knows nothing of hidden ids
        # from the other planes; scrub it once here so every reader of
        # base_view gets the same treatment.
        self.base_view = self._scrub(self.base_view)

    def marking_of(self, record_id: str) -> dict | None:
        """The marking of the visible record with this id, in any plane.

        A new record inherits from what it cites, and this resolves the cite.
        None when the id is not visible, since a caller may only anchor to
        records it can see."""
        for record_type in LATEST_FAMILIES:
            record = self._current[record_type].get(record_id)
            if record is not None:
                return record.get("marking")
        for record_type, id_field in APPEND_FAMILIES.items():
            for record in self._append[record_type]:
                if record[id_field] == record_id:
                    return record.get("marking")
        for record in self.base_view["objects"]:
            if record["object_id"] == record_id:
                return record.get("marking")
        for key, id_field in (("relationships", "relationship_id"),
                              ("activities", "activity_id"),
                              ("alerts", "alert_id"),
                              ("recommendations", "recommendation_id"),
                              ("decisions", "decision_id"),
                              ("analyst_actions", "action_id"),
                              ("association_proposals", "proposal_id"),
                              ("information_requirements", "requirement_id"),
                              ("analyst_tasks", "task_id"),
                              ("evidence_requests", "request_id")):
            for record in self.base_view.get(key, []):
                if record.get(id_field) == record_id:
                    return record.get("marking")
        # A section or sentence carries its report's marking.
        for report in self._current["workbench_report"].values():
            if record_id == report["report_id"]:
                return report.get("marking")
            for section in report["sections"]:
                if section["section_id"] == record_id \
                        or any(s["sentence_id"] == record_id for s in section["sentences"]):
                    return report.get("marking")
        return None

    def visible_id_set(self) -> set[str]:
        """Every record id this context can see, in any plane.

        Inbound references are checked against this set. A hidden id is as
        absent as one that never existed, so a refusal says nothing about
        what exists."""
        if self._visible_ids is None:
            ids: set[str] = set()
            for family in self._current.values():
                ids |= set(family)
            for record_type, records in self._append.items():
                id_field = APPEND_FAMILIES[record_type]
                ids |= {r[id_field] for r in records}
            view = self.base_view
            ids |= {o["object_id"] for o in view["objects"]}
            ids |= {r["relationship_id"] for r in view["relationships"]}
            ids |= {a["activity_id"] for a in view["activities"]}
            ids |= {a["alert_id"] for a in view["alerts"]}
            ids |= {r["recommendation_id"] for r in view["recommendations"]}
            ids |= {d["decision_id"] for d in view["decisions"]}
            ids |= {a["action_id"] for a in view["analyst_actions"]}
            ids |= {p["proposal_id"] for p in view["association_proposals"]}
            ids |= {r["requirement_id"] for r in view["information_requirements"]}
            ids |= {t["task_id"] for t in view["analyst_tasks"]}
            ids |= {r["request_id"] for r in view["evidence_requests"]}
            # Sections and sentences are citable in their own right.
            for report in self._current["workbench_report"].values():
                for section in report["sections"]:
                    ids.add(section["section_id"])
                    ids |= {s["sentence_id"] for s in section["sentences"]}
            # Inference ids and anchor targets are citable too.
            for observation in self._append["semantic_observation"]:
                ids |= {a.get("manifestation_id") for a in observation.get("anchors", ())
                        if a.get("manifestation_id")}
                if observation.get("inference_id"):
                    ids.add(observation["inference_id"])
            self._visible_ids = ids
        return self._visible_ids

    # ---- redaction ----

    def _scrub(self, value: Any) -> Any:
        if self._scrub_re is None:
            return value
        if isinstance(value, str):
            return self._scrub_re.sub(REDACTED, value)
        if isinstance(value, (list, tuple)):
            return [self._scrub(v) for v in value]
        if isinstance(value, Mapping):
            return {(self._scrub(k) if isinstance(k, str) else k): self._scrub(v)
                    for k, v in value.items()}
        return value

    def redact(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A copy with every hidden id replaced by REDACTED, wherever it appears.

        The whole record is walked rather than a list of fields, so a field
        added later cannot escape redaction by not being listed."""
        return self._scrub(dict(record))

    # ---- family access ----

    def family(self, record_type: str) -> list[dict]:
        """All visible current records of one family, redacted, in a stable order.

        The records are shared between calls on this projection; do not mutate them.
        """
        cached = self._family_cache.get(record_type)
        if cached is None:
            if record_type in LATEST_FAMILIES:
                records = [self._current[record_type][rid]
                           for rid in sorted(self._current[record_type])]
            elif record_type in APPEND_FAMILIES:
                # A stable sort keeps log order among records with equal times.
                records = sorted(self._append[record_type], key=record_time)
            else:
                raise KeyError(f"unknown record family: {record_type}")
            cached = self._family_cache[record_type] = [self.redact(r) for r in records]
        return list(cached)

    def get(self, record_type: str, record_id: str) -> dict | None:
        """The current record, or None; hidden and missing look the same."""
        if record_type in LATEST_FAMILIES:
            record = self._current[record_type].get(record_id)
            return self.redact(record) if record is not None else None
        if record_type in APPEND_FAMILIES:
            id_field = APPEND_FAMILIES[record_type]
            for record in self._append[record_type]:
                if record[id_field] == record_id:
                    return self.redact(record)
            return None
        raise KeyError(f"unknown record family: {record_type}")

    def raw_current(self, record_type: str, record_id: str) -> dict | None:
        """The current record, unredacted, if this context may see it.

        For commands that re-append a record and must keep its real marking and
        references. Never send the result to a client."""
        if record_type in LATEST_FAMILIES:
            return self._current[record_type].get(record_id)
        raise KeyError(f"not a latest-family record type: {record_type}")

    def versions(self, record_type: str, record_id: str) -> list[dict]:
        """A record's versions, oldest first, dropping any the context may not
        see. Empty when the current version is hidden."""
        if record_type not in LATEST_FAMILIES:
            return []
        if record_id not in self._current[record_type]:
            return []
        return [self.redact(v) for v in
                sorted(self._history[record_type].get(record_id, []),
                       key=lambda r: r.get("version", 1))
                if record_type in UNMARKED_REGISTRY_FAMILIES and "marking" not in v
                or can_view(v.get("marking"), self.context)]

    def transitions(self, subject_id: str) -> list[dict]:
        if subject_id in self.hidden_ids:
            return []
        return [self.redact(t) for t in self._append["analytic_transition"]
                if t["subject_id"] == subject_id]

    def annotations_for(self, target_id: str) -> list[dict]:
        if target_id in self.hidden_ids:
            return []
        return [a for a in self.family("workbench_annotation")
                if a["target_id"] == target_id or a.get("anchor_ref") == target_id]

    # ---- world model ----

    def visible_objects(self) -> list[dict]:
        return self.base_view["objects"]

    def visible_object_ids(self) -> set[str]:
        return {o["object_id"] for o in self.base_view["objects"]}

    def object_history(self, object_id: str) -> list[dict]:
        """The versions of one world object, filtered and scrubbed."""
        return self._scrub(self.base.object_history(object_id, self.context))

    def object_current(self, object_id: str) -> dict | None:
        for record in self.base_view["objects"]:
            if record["object_id"] == object_id:
                return record
        return None

    # ---- overview ----

    def overview(self) -> dict[str, Any]:
        """The mission at a glance: what is happening, what changed, what is
        uncertain and what needs attention, from visible records only."""
        view = self.base_view
        forecasts = self.family("analytic_forecast")
        warnings = [w for w in self.family("strategic_warning") if w["status"] == "ACTIVE"]
        review_items = [r for r in self.family("review_item") if r["status"] == "OPEN"]
        hypotheses = self.family("hypothesis")
        themes = [t for t in self.family("analytic_theme") if t.get("status") != "RETIRED"]
        objectives = self.family("mission_objective")
        requirements = [r for r in view["information_requirements"]
                        if r["status"] in ("OPEN", "EVIDENCE_PENDING")]
        tasks = [t for t in view["analyst_tasks"]
                 if t["status"] in ("ASSIGNED", "IN_PROGRESS", "BLOCKED")]
        routes = [r for r in self.family("collection_route")
                  if r["status"] in ("PROPOSED", "ASSIGNED")]
        watches = [w for w in self.family("fabric_watch") if w.get("active")]
        indicators = [i for i in self.family("forecast_indicator") if i["status"] == "ARMED"]
        coverage = self.family("fabric_coverage")
        gaps = [c for c in coverage if c.get("state") in ("NOT_SEARCHED", "PARTIALLY_COVERED",
                                                          "SOURCE_FAILED", "ACCESS_RESTRICTED")]
        manifestations = self.family("fabric_manifestation")
        changes = self.family("semantic_change")
        snapshot = parse_time(self.snapshot_time)
        open_forecasts = [f for f in forecasts if f["status"] == "OPEN"]

        def _horizon_hours(record):
            return (parse_time(record["horizon_time"]) - snapshot).total_seconds() / 3600

        near_horizon = sorted((f for f in open_forecasts if _horizon_hours(f) <= 24 * 30),
                              key=lambda f: f["horizon_time"])
        recent_changes = sorted(changes, key=record_time, reverse=True)[:10]
        recent_evidence = sorted(manifestations, key=record_time, reverse=True)[:10]
        return {
            "meta": {"store_id": self.base.store_id, "state_token": self.state_token,
                     "snapshot_time": self.snapshot_time,
                     "context_id": self.context.context_id},
            "objectives": objectives,
            "active_warnings": warnings,
            "open_requirements": requirements,
            "open_review_items": review_items,
            "hypotheses": hypotheses,
            "themes": themes,
            "open_forecasts": open_forecasts,
            "forecasts_near_horizon": near_horizon[:5],
            "armed_indicators": indicators,
            "open_tasks": tasks,
            "pending_routes": routes,
            "active_watches": watches,
            "coverage_gaps": gaps,
            "recent_semantic_changes": recent_changes,
            "recent_evidence": recent_evidence,
            "alerts": [a for a in view["alerts"] if a["status"] == "OPEN"],
            "counts": {
                "entities": len(view["objects"]),
                "relationships": len(view["relationships"]),
                "events": len(view["activities"]),
                "claims": len(self._current["semantic_claim"]),
                "manifestations": len(manifestations),
                "sources": len(self._current["fabric_source_descriptor"]),
                "themes": len(themes),
                "narratives": len(self._current["analytic_narrative"]),
                "stakeholder_assessments": len(self._current["stakeholder_assessment"]),
                "impact_paths": len(self._current["impact_path"]),
                "hypotheses": len(hypotheses),
                "forecasts_open": len(open_forecasts),
                "forecasts_resolved": sum(1 for f in forecasts
                                          if f["status"].startswith("RESOLVED")),
                "warnings_active": len(warnings),
                "indicators_armed": len(indicators),
                "requirements_open": len(requirements),
                "tasks_open": len(tasks),
                "review_open": len(review_items),
                "watches_active": len(watches),
                "coverage_gaps": len(gaps),
                "annotations": len(self._current["workbench_annotation"]),
                "reports": len(self._current["workbench_report"]),
            },
        }

    # ---- activity feed ----

    _FEED_LABEL_KEYS = ("object_id", "relationship_id", "alert_id",
                        "requirement_id", "task_id", "decision_id",
                        "activity_id", "proposal_id", "action_id",
                        "recommendation_id", "request_id", "inference_id",
                        "model_id", "source_id", "ingestion_id",
                        "transformation_id")

    def activity_feed(self, limit: int = 100) -> list[dict]:
        """Readable mission history, newest first, over visible records only."""
        feed: list[dict] = []
        for event in self.store.events(self.base.as_of_seq):
            record = event["record"]
            kind = record.get("record_type", "")
            if not (kind in UNMARKED_REGISTRY_FAMILIES and "marking" not in record) \
                    and not can_view(record.get("marking"), self.context):
                continue
            family_id = ALL_FAMILIES.get(kind)
            label = record.get(family_id, "") if family_id else next(
                (record[k] for k in self._FEED_LABEL_KEYS if record.get(k)), "")
            if label and _base_id(label) in self.hidden_ids:
                continue
            # An activity hidden by its subject in the base view stays hidden
            # here too.
            if kind == "activity" and record.get("activity_id") \
                    not in self._visible_activity_ids:
                continue
            feed.append({"time": event["recorded_time"], "actor": event["actor"],
                         "event_type": event["event_type"], "record_type": kind,
                         "record_id": label,
                         "version": record.get("version"),
                         "status": self._scrub(record.get("status", ""))})
        feed.reverse()
        return feed[:limit]
