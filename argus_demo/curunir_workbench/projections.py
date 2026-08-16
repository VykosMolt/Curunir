"""Mission-wide authorized projection: every plane, one context, filtered
before serialization.

The projection composes the operational world view (objects, relationships,
activities, alerts, workflow) with the fabric, semantic, analytic and
workbench record families from the same store. Rules, inherited from the
operational plane and applied uniformly:

  * a record the context cannot view does not exist for that view — it
    appears in no list, no count, no graph, no search result, no timeline;
  * every serialized value of a visible record is scrubbed: any occurrence
    of a hidden record's id — in a reference field OR in free text such as
    detail/explanation strings — is replaced with REDACTED, so a
    cross-compartment id never leaks through a record the context is
    otherwise entitled to see;
  * counts are computed from the filtered set only;
  * unknown and forbidden are indistinguishable in every lookup.

Source registry families (`fabric_source_descriptor`, `fabric_source_profile`,
`fabric_source_status`) carry no marking by contract: they describe where
Curunír CAN look and how those lookups went, not what it found. They are
visible to every authenticated context; all acquired/derived records are
marking-gated.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from curunir_operational.access import AccessContext, can_view
from curunir_operational.canonical import parse_time
from curunir_operational.projection import Projection

from .store import WorkbenchStore

# Families where re-appends supersede: the latest record per id is current
# state, every prior version stays in the log and remains inspectable.
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
    # operational-plane model proposals (versionless; latest append wins)
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

# Append-only families: each record is one immutable fact.
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

# registry/catalog metadata: no marking by contract, visible to every
# authenticated context (never added to the hidden set)
UNMARKED_REGISTRY_FAMILIES = ("fabric_source_descriptor", "fabric_source_profile",
                              "fabric_source_status")

REDACTED = "REDACTED"


def _base_id(ref: Any) -> str:
    return str(ref).split("@v")[0]


def record_time(record: Mapping[str, Any]) -> str:
    """The knowledge-time of a record; families name it differently."""
    for key in ("recorded_time", "retrieval_time", "assessed_time",
                "observed_time", "created_time", "started_time"):
        value = record.get(key)
        if value:
            return value
    return ""


class MissionProjection:
    """One access context's complete authorized view of a mission store."""

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

        # --- visibility partition, per family, computed once ---------------
        self._current: dict[str, dict[str, dict]] = {}
        self._history: dict[str, dict[str, list[dict]]] = {}
        hidden: set[str] = {oid for oid in self.base.objects
                            if not can_view(self.base.objects[oid]["current"].get("marking"), context)}
        hidden |= {rid for rid, entry in self.base.relationships.items()
                   if not can_view(entry["current"].get("marking"), context)}
        # every access-filtered OPERATIONAL family joins the hidden set too:
        # a hidden activity/alert/task/decision id must scrub out of visible
        # records exactly like a hidden object id
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
        # base-view activities additionally suppress on hidden participants;
        # treat those as hidden for scrub purposes as well
        for record in self.base.activities:
            if record["activity_id"] not in self._visible_activity_ids:
                hidden.add(record["activity_id"])
        for record_type, id_field in LATEST_FAMILIES.items():
            visible: dict[str, dict] = {}
            history: dict[str, list[dict]] = {}
            for record in store.records_of(record_type):
                rid = record[id_field]
                history.setdefault(rid, []).append(record)
            for rid, versions in history.items():
                # current = highest version; families without a version field
                # tie at 1 and resolve by LOG ORDER (last append wins), which
                # is the store's own latest_by_id semantics
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
        # one scrub pattern for the whole projection: any occurrence of a
        # hidden id (optionally with an @vN suffix) anywhere in a string
        self._scrub_re = re.compile(
            "|".join(re.escape(h) + r"(?:@v\d+)?" for h in sorted(hidden, key=len,
                                                                  reverse=True))
        ) if hidden else None
        # the operational base view carries its own (object-level) redaction
        # but not the cross-plane hidden-id scrub — apply it here, once, so
        # every consumer of base_view serializes scrubbed state
        self.base_view = self._scrub(self.base_view)

    # ---- redaction ---------------------------------------------------------

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
        """Return a copy safe for this context: every occurrence of a hidden
        record's id — reference fields, nested provenance, free text — is
        replaced with REDACTED. Uniform by construction, so a new contract
        field can never dodge redaction by not being on a list."""
        return self._scrub(dict(record))

    # ---- family access ------------------------------------------------------

    def family(self, record_type: str) -> list[dict]:
        """All visible current records of one family, redacted, stable order."""
        if record_type in LATEST_FAMILIES:
            records = [self._current[record_type][rid]
                       for rid in sorted(self._current[record_type])]
        elif record_type in APPEND_FAMILIES:
            # stable sort: ties on recorded time keep log order, so the last
            # append genuinely is the latest fact
            records = sorted(self._append[record_type], key=record_time)
        else:
            raise KeyError(f"unknown record family: {record_type}")
        return [self.redact(r) for r in records]

    def get(self, record_type: str, record_id: str) -> dict | None:
        """One visible current record, or None — unknown and forbidden are
        indistinguishable."""
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
        """UNREDACTED current record, only if visible to this context.

        For command layers that must re-append a record (preserving its true
        marking and references) — never for serialization to a client."""
        if record_type in LATEST_FAMILIES:
            return self._current[record_type].get(record_id)
        raise KeyError(f"not a latest-family record type: {record_type}")

    def versions(self, record_type: str, record_id: str) -> list[dict]:
        """Version history of one visible record, oldest first. Versions the
        context cannot view individually are omitted; if the current version
        is hidden the whole history is empty."""
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

    # ---- world model (operational base) -------------------------------------

    def visible_objects(self) -> list[dict]:
        return self.base_view["objects"]

    def visible_object_ids(self) -> set[str]:
        return {o["object_id"] for o in self.base_view["objects"]}

    def object_history(self, object_id: str) -> list[dict]:
        """Access-filtered, scrubbed version history of one world object."""
        return self._scrub(self.base.object_history(object_id, self.context))

    def object_current(self, object_id: str) -> dict | None:
        for record in self.base_view["objects"]:
            if record["object_id"] == object_id:
                return record
        return None

    # ---- overview -----------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """The common operating picture: what is happening, what matters,
        what changed, what is uncertain, what needs attention — every element
        computed from this context's visible records only."""
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

    # ---- activity feed ------------------------------------------------------

    _FEED_LABEL_KEYS = ("object_id", "relationship_id", "alert_id",
                        "requirement_id", "task_id", "decision_id",
                        "activity_id", "proposal_id", "action_id",
                        "recommendation_id", "request_id", "inference_id",
                        "model_id", "source_id", "ingestion_id",
                        "transformation_id")

    def activity_feed(self, limit: int = 100) -> list[dict]:
        """Operator-friendly attributable mission history, newest first —
        a view over the event log restricted to visible records."""
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
            # world activities carry subject-based suppression in the base
            # projection; the feed honours the same rule
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
