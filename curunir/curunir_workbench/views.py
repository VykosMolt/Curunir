"""The workbench screens, each built from one filtered projection.

A view can only narrow or rearrange what the context may already see, never
widen it. Current and historical state stay apart, so do observed and inferred,
and "not searched yet" never renders as "nothing there".
"""
from __future__ import annotations

from typing import Any, Mapping

from curunir_analytic.substrate import CANDIDATE_BINDING_KEYS
from curunir_operational.canonical import parse_time

from .projections import MissionProjection, _base_id, record_time

ENTITY_OBJECT_TYPES = ("PERSON", "GROUP", "ORGANISATION", "WEB_DOMAIN",
                       "INTELLECTUAL_WORK", "PUBLIC_IDENTIFIER", "LOCATION",
                       "INFRASTRUCTURE", "ASSET", "ROUTE", "RESOURCE_STOCK")


def _claims_about(projection: MissionProjection, object_id: str) -> list[dict]:
    return [c for c in projection.family("semantic_claim")
            if object_id in (c.get("subject_object_id"), c.get("object_object_id"))
            or any(_base_id(ref) == object_id for ref in c.get("world_refs", ()))]


def _latest_claim_states(projection: MissionProjection) -> dict[str, dict]:
    """The newest visible state record for each claim."""
    latest: dict[str, dict] = {}
    for state in projection.family("semantic_claim_state"):
        known = latest.get(state["claim_id"])
        if known is None or state["recorded_time"] >= known["recorded_time"]:
            latest[state["claim_id"]] = state
    return latest


# Entity / event dossiers

def entity_list(projection: MissionProjection) -> list[dict]:
    rows = []
    for record in projection.visible_objects():
        if record["object_type"] not in ENTITY_OBJECT_TYPES:
            continue
        rows.append({
            "object_id": record["object_id"], "object_type": record["object_type"],
            "labels": record.get("labels", []),
            "epistemic_state": record["epistemic_state"],
            "lifecycle": record.get("lifecycle"),
            "history_count": record.get("history_count", 1),
            "freshness": record.get("freshness", {}).get("state", "UNKNOWN"),
            "cluster_id": record.get("cluster_id", record["object_id"]),
            "cluster_partially_hidden": bool(record.get("cluster_partially_hidden")),
            "has_geometry": bool(record.get("geometry")),
        })
    return rows


def entity_dossier(projection: MissionProjection, object_id: str) -> dict | None:
    current = projection.object_current(object_id)
    if current is None:
        return None
    history = projection.object_history(object_id)
    relationships = [r for r in projection.base_view["relationships"]
                     if object_id in (r["source_object_id"], r["target_object_id"])]
    events = [a for a in projection.base_view["activities"]
              if object_id in a.get("subject_ids", ())]
    claims = _claims_about(projection, object_id)
    claim_ids = {c["claim_id"] for c in claims}
    identity_proposals = [p for p in projection.base_view["association_proposals"]
                          if object_id in (p["left_object_id"], p["right_object_id"])]
    contradictions = [r for r in projection.family("review_item")
                      if r["subject_id"] in claim_ids or r["subject_id"] == object_id]
    claim_states = _latest_claim_states(projection)

    def _touches(record: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
        for key in keys:
            for ref in record.get(key, ()):
                if _base_id(ref if isinstance(ref, str) else ref[-1]) == object_id:
                    return True
        return False

    def _theme_claims(t):
        basis = t.get("basis", {}) or {}
        return set(basis.get("supporting_claim_ids", ())) \
            | set(basis.get("contradicting_claim_ids", ()))

    themes = [t for t in projection.family("analytic_theme")
              if _touches(t, ("entity_ids", "event_ids", "relation_ids"))
              or _theme_claims(t) & claim_ids]
    stakeholder = [s for s in projection.family("stakeholder_assessment")
                   if s.get("entity_object_id") == object_id]
    forecasts = [f for f in projection.family("analytic_forecast")
                 if any(_base_id(ref[1] if isinstance(ref, (list, tuple)) else ref) == object_id
                        for ref in f.get("proposition_refs", ()))
                 or any(c in claim_ids
                        for c in tuple(f.get("basis", {}).get("supporting_claim_ids", ()))
                        + tuple(f.get("basis", {}).get("contradicting_claim_ids", ())))]
    return {
        "current": current,
        "history": history,          # every version this context may see
        "relationships": relationships,
        "events": events,
        "claims": [{**c, "state": claim_states.get(c["claim_id"], {}).get("state", "ACTIVE")}
                   for c in claims],
        "identity_proposals": identity_proposals,   # ambiguity stays visible
        "contradictions": contradictions,
        "themes": [{"theme_id": t["theme_id"], "title": t.get("title", ""),
                    "status": t.get("status")} for t in themes],
        "stakeholder_assessments": stakeholder,
        "forecasts": [{"forecast_id": f["forecast_id"], "question": f["question"],
                       "probability": f["probability"], "status": f["status"]}
                      for f in forecasts],
        "annotations": projection.annotations_for(object_id),
    }


def event_list(projection: MissionProjection) -> list[dict]:
    return [{
        "activity_id": a["activity_id"], "activity_type": a["activity_type"],
        "epistemic_state": a["epistemic_state"],
        "description": a.get("description", ""),
        "valid_from": a.get("valid_from"), "source_time": a.get("source_time"),
        "recorded_time": a["recorded_time"],
        "time_precision": a.get("time_precision", "UNKNOWN"),
        "subject_ids": a.get("subject_ids", ()),
    } for a in projection.base_view["activities"]]


def event_dossier(projection: MissionProjection, activity_id: str) -> dict | None:
    record = next((a for a in projection.base_view["activities"]
                   if a["activity_id"] == activity_id), None)
    if record is None:
        return None
    observation_ids = tuple(record.get("evidence_refs", ()))
    observations = [o for o in projection.family("semantic_observation")
                    if o["observation_id"] in observation_ids]
    claims = [c for c in projection.family("semantic_claim")
              if any(_base_id(ref) == activity_id for ref in c.get("world_refs", ()))]
    return {
        "current": record,
        "participants": record.get("participants", ()),
        "observations": observations,
        "claims": claims,
        "annotations": projection.annotations_for(activity_id),
    }


# Timeline

def timeline(projection: MissionProjection, *, axis: str = "valid",
             kinds: tuple[str, ...] = (), t_from: str | None = None,
             t_to: str | None = None) -> dict[str, Any]:
    """One timeline for the mission.

    The "valid" axis places an item when it happened in the world, the
    "knowledge" axis when Curunír learned of it. An item with no world time
    appears only on the knowledge axis; a missing time is shown, not guessed."""
    if axis not in ("valid", "knowledge"):
        raise ValueError(f"unknown timeline axis: {axis}")
    entries: list[dict] = []

    def add(kind: str, ref: str, label: str, *, valid: str | None,
            recorded: str, precision: str = "", status: str = ""):
        time = valid if axis == "valid" else recorded
        if time is None:
            return
        entries.append({"time": time, "kind": kind, "ref": ref, "label": label,
                        "valid_time": valid, "recorded_time": recorded,
                        "time_precision": precision, "status": status})

    want = set(kinds) if kinds else None

    def wants(kind: str) -> bool:
        return want is None or kind in want

    if wants("object_version"):
        for record in projection.visible_objects():
            add("object_version", record["object_id"],
                f"{record['object_type']} {(record.get('labels') or [record['object_id']])[0]} "
                f"[{record['epistemic_state']}]",
                valid=record.get("valid_from") or record.get("source_time"),
                recorded=record["recorded_time"],
                precision=record.get("time_precision", ""))
    if wants("event"):
        for record in projection.base_view["activities"]:
            add("event", record["activity_id"],
                f"{record['activity_type']}: {record.get('description', '')[:80]} "
                f"[{record['epistemic_state']}]",
                valid=record.get("valid_from") or record.get("source_time"),
                recorded=record["recorded_time"],
                precision=record.get("time_precision", ""))
    if wants("manifestation"):
        for record in projection.family("fabric_manifestation"):
            add("manifestation", record["manifestation_id"],
                f"evidence from {record['source_id']}",
                valid=record.get("archive_capture_time") or record.get("source_time"),
                recorded=record_time(record))
    if wants("semantic_change"):
        for record in projection.family("semantic_change"):
            add("semantic_change", record["change_id"],
                f"{record['change_class']}: {record.get('detail', '')[:80]}",
                valid=None, recorded=record["recorded_time"],
                status=record["change_class"])
    if wants("claim"):
        for record in projection.family("semantic_claim"):
            add("claim", record["claim_id"],
                f"claim v{record['version']}: {record['statement'][:80]}",
                valid=record.get("valid_from"), recorded=record["recorded_time"],
                precision=record.get("time_precision", ""))
    if wants("forecast"):
        for record in projection.family("analytic_forecast"):
            for version in projection.versions("analytic_forecast", record["forecast_id"]):
                add("forecast", record["forecast_id"],
                    f"forecast p={version['probability']:.2f} v{version['version']} "
                    f"by {version['author']}",
                    valid=None, recorded=version["recorded_time"],
                    status=version["status"])
    if wants("indicator"):
        for record in projection.family("forecast_indicator"):
            add("indicator", record["indicator_id"],
                f"indicator {record['status']}: {record.get('description', '')[:60]}",
                valid=None, recorded=record["recorded_time"], status=record["status"])
    if wants("warning"):
        for record in projection.family("strategic_warning"):
            for version in projection.versions("strategic_warning", record["warning_id"]):
                add("warning", record["warning_id"],
                    f"warning {version['tier']} v{version['version']}",
                    valid=None, recorded=version["recorded_time"],
                    status=version["tier"])
    if wants("collection"):
        for record in projection.family("fabric_execution"):
            add("collection", record["execution_id"],
                f"collection via {record['source_id']} [{record.get('outcome', '')}]",
                valid=None, recorded=record_time(record),
                status=record.get("outcome", ""))
    if wants("watch_run"):
        for record in projection.family("fabric_watch_run"):
            add("watch_run", record["run_id"],
                f"watch {record['watch_id']} run [{record.get('outcome', '')}]",
                valid=None, recorded=record_time(record))
    if wants("decision"):
        for record in projection.base_view["decisions"]:
            add("decision", record["decision_id"],
                f"decision {record['state']} by {record['actor_id']}",
                valid=None, recorded=record["recorded_time"], status=record["state"])
    if wants("analyst_action"):
        for record in projection.base_view["analyst_actions"]:
            add("analyst_action", record["action_id"],
                f"{record['kind']} by {record['actor_id']}",
                valid=None, recorded=record["recorded_time"])
    if wants("annotation"):
        for record in projection.family("workbench_annotation"):
            add("annotation", record["annotation_id"],
                f"{record['kind']} by {record['author']}: {record['text'][:60]}",
                valid=None, recorded=record["recorded_time"], status=record["status"])
    if wants("report"):
        for record in projection.family("workbench_report"):
            add("report", record["report_id"],
                f"report v{record['version']} [{record['status']}]",
                valid=None, recorded=record["recorded_time"], status=record["status"])

    lo = parse_time(t_from) if t_from else None
    hi = parse_time(t_to) if t_to else None
    if lo or hi:
        entries = [e for e in entries
                   if (lo is None or parse_time(e["time"]) >= lo)
                   and (hi is None or parse_time(e["time"]) <= hi)]
    entries.sort(key=lambda e: (e["time"], e["kind"], e["ref"]))
    return {"axis": axis, "entries": entries}


# Relationship graph

def graph(projection: MissionProjection, *, focus: str | None = None,
          depth: int = 2, relation_types: tuple[str, ...] = (),
          include_proposals: bool = True) -> dict[str, Any]:
    """A graph of the visible entities and their relations, drawing observed
    links, inferred links and open proposals differently so that unsettled
    identity stays visibly unsettled."""
    objects = {o["object_id"]: o for o in projection.visible_objects()}
    edges = []
    for record in projection.base_view["relationships"]:
        if relation_types and record["relation_type"] not in relation_types:
            continue
        derivation = (record.get("provenance") or {}).get("mode", "")
        edges.append({
            "relationship_id": record["relationship_id"],
            "relation_type": record["relation_type"],
            "source": record["source_object_id"], "target": record["target_object_id"],
            "status": record["status"],
            "basis": "OBSERVED" if derivation == "EVIDENTIARY" else "ASSERTED",
            "rationale": record.get("rationale", ""),
        })
    proposals = []
    if include_proposals:
        for record in projection.base_view["association_proposals"]:
            open_proposal = not any(r.get("resolution") == "ACCEPTED"
                                    for r in record.get("resolutions", []))
            proposals.append({
                "proposal_id": record["proposal_id"],
                "source": record["left_object_id"], "target": record["right_object_id"],
                "outcome": record["outcome"], "open": open_proposal,
            })
    if focus:
        if focus not in objects:
            return {"nodes": [], "edges": [], "association_proposals": [], "focus": focus}
        keep = {focus}
        frontier = {focus}
        for _ in range(max(depth, 0)):
            grown: set[str] = set()
            for edge in edges:
                if edge["source"] in frontier:
                    grown.add(edge["target"])
                if edge["target"] in frontier:
                    grown.add(edge["source"])
            for proposal in proposals:
                if proposal["source"] in frontier:
                    grown.add(proposal["target"])
                if proposal["target"] in frontier:
                    grown.add(proposal["source"])
            frontier = grown - keep
            keep |= grown
            if not frontier:
                break
        objects = {k: v for k, v in objects.items() if k in keep}
        edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
        proposals = [p for p in proposals if p["source"] in keep and p["target"] in keep]
    nodes = [{
        "object_id": o["object_id"], "object_type": o["object_type"],
        "label": (o.get("labels") or [o["object_id"]])[0],
        "epistemic_state": o["epistemic_state"],
        "cluster_id": o.get("cluster_id", o["object_id"]),
        "cluster_partially_hidden": bool(o.get("cluster_partially_hidden")),
    } for o in objects.values()]
    return {"nodes": sorted(nodes, key=lambda n: n["object_id"]),
            "edges": sorted(edges, key=lambda e: e["relationship_id"]),
            "association_proposals": proposals, "focus": focus}


# Map

def map_view(projection: MissionProjection) -> dict[str, Any]:
    """Only geography that is in the records: an object without coordinates is
    listed as unlocated rather than placed by guesswork."""
    features = []
    unlocated = []
    for record in projection.visible_objects():
        geometry = record.get("geometry")
        label = (record.get("labels") or [record["object_id"]])[0]
        if not geometry:
            if record["object_type"] in ("LOCATION", "INFRASTRUCTURE", "ASSET", "ROUTE"):
                unlocated.append({"object_id": record["object_id"], "label": label,
                                  "object_type": record["object_type"]})
            continue
        kind = geometry.get("kind", "POINT")
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString" if kind == "LINESTRING" else kind.capitalize(),
                         "coordinates": geometry["coordinates"]},
            "properties": {"object_id": record["object_id"], "label": label,
                           "object_type": record["object_type"],
                           "epistemic_state": record["epistemic_state"],
                           "uncertainty_m": geometry.get("uncertainty_m")},
        })
    return {"feature_collection": {"type": "FeatureCollection", "features": features},
            "unlocated": unlocated}


# Source independence

def source_independence(projection: MissionProjection, *, claim_ids: tuple[str, ...]) -> dict[str, Any]:
    """How much independent support a set of claims really has: documents,
    distinct sources and independent origins counted separately, so 47 copies of
    one story cannot look like 47 sources."""
    claims = [c for c in projection.family("semantic_claim") if c["claim_id"] in set(claim_ids)]
    observation_ids: set[str] = set()
    dependence_groups: set[str] = set()
    independent_counts: dict[str, int] = {}
    for claim in claims:
        observation_ids |= {o for o in claim.get("observation_ids", ()) if o != "REDACTED"}
        dependence_groups |= {g for g in claim.get("dependence_group_ids", ()) if g != "REDACTED"}
        independent_counts[claim["claim_id"]] = claim.get("independent_basis_count", 0)
    observations = [o for o in projection.family("semantic_observation")
                    if o["observation_id"] in observation_ids]
    manifestation_ids = {o["manifestation_id"] for o in observations}
    source_ids = {o["source_id"] for o in observations}
    sources = {s["source_id"]: s for s in projection.family("fabric_source_descriptor")
               if s["source_id"] in source_ids}
    languages = sorted({o.get("language", "") for o in observations if o.get("language")})
    times = sorted(t for t in (o.get("source_time") for o in observations) if t)
    return {
        "claims": [{"claim_id": c["claim_id"], "statement": c["statement"],
                    "independent_basis_count": independent_counts[c["claim_id"]],
                    "basis_note": c.get("basis_note", "")} for c in claims],
        "manifestation_count": len(manifestation_ids),
        "unique_sources": sorted(source_ids),
        "source_families": sorted({f"{s.get('source_type','')}/{s.get('source_subtype','')}".strip("/")
                                   for s in sources.values()
                                   if s.get("source_type") or s.get("source_subtype")}),
        "dependence_groups": sorted(dependence_groups),
        "independent_origin_count": len(dependence_groups) if dependence_groups else 0,
        "languages": languages,
        "temporal_spread": {"earliest": times[0], "latest": times[-1]} if times else None,
    }


# Hypothesis comparison / evidence matrix

def hypothesis_matrix(projection: MissionProjection, *,
                      hypothesis_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """Claims against hypotheses, computed from the recorded links rather than
    kept by hand. One claim may support one hypothesis and contradict another,
    and unresolved stays "?" instead of drifting into faint support."""
    hypotheses = [h for h in projection.family("hypothesis")
                  if not hypothesis_ids or h["hypothesis_id"] in set(hypothesis_ids)]
    claim_rows: dict[str, dict[str, str]] = {}
    for hypothesis in hypotheses:
        for claim_id in hypothesis.get("supporting_claim_ids", ()):
            claim_rows.setdefault(claim_id, {})[hypothesis["hypothesis_id"]] = "SUPPORTS"
        for claim_id in hypothesis.get("contradicting_claim_ids", ()):
            claim_rows.setdefault(claim_id, {})[hypothesis["hypothesis_id"]] = "CONTRADICTS"
    claims = {c["claim_id"]: c for c in projection.family("semantic_claim")}
    claim_states = _latest_claim_states(projection)
    rows = []
    for claim_id in sorted(claim_rows):
        claim = claims.get(claim_id)
        if claim is None and claim_id != "REDACTED":
            continue  # hidden support never renders
        rows.append({
            "claim_id": claim_id,
            "statement": claim["statement"] if claim else "",
            "independent_basis_count": claim.get("independent_basis_count", 0) if claim else 0,
            "dependence_group_ids": claim.get("dependence_group_ids", ()) if claim else (),
            "state": claim_states.get(claim_id, {}).get("state", "ACTIVE"),
            "cells": {h["hypothesis_id"]: claim_rows[claim_id].get(h["hypothesis_id"], "UNRESOLVED")
                      for h in hypotheses},
        })
    discriminators = [d for d in projection.family("discriminator")
                      if not hypothesis_ids
                      or set(d.get("hypothesis_ids", ())) & set(hypothesis_ids)]
    return {
        "hypotheses": [{
            "hypothesis_id": h["hypothesis_id"], "statement": h["statement"],
            "status": h["status"], "assumptions": h.get("assumptions", ()),
            "unknowns": h.get("unknowns", ()), "history": h.get("history", ()),
            "supporting_count": len(h.get("supporting_claim_ids", ())),
            "contradicting_count": len(h.get("contradicting_claim_ids", ())),
        } for h in hypotheses],
        "rows": rows,
        "discriminators": discriminators,
    }


# Coverage matrix

def coverage_matrix(projection: MissionProjection, *,
                    need_id: str | None = None) -> dict[str, Any]:
    """Which source families have been searched for each information need;
    "not searched" is a state of its own, never an absent gap."""
    assessments = projection.family("fabric_coverage")
    if need_id:
        assessments = [a for a in assessments if a["need_id"] == need_id]
    by_need: dict[str, dict[str, dict]] = {}
    for record in assessments:
        cell = by_need.setdefault(record["need_id"], {})
        known = cell.get(record["source_family"])
        if known is None or record["assessed_time"] >= known["assessed_time"]:
            cell[record["source_family"]] = record
    needs = {n["need_id"]: n for n in projection.family("fabric_information_need")}
    rows = []
    for nid in sorted(by_need):
        need = needs.get(nid)
        rows.append({
            "need_id": nid,
            "question": need.get("question", "") if need else "",
            "families": {family: {"state": cell["state"],
                                  "gaps": cell.get("gaps", ()),
                                  "assessed_time": cell["assessed_time"],
                                  "assessor": cell.get("assessor", "")}
                         for family, cell in sorted(by_need[nid].items())},
        })
    return {"rows": rows}


# Unified review queue

def review_queue(projection: MissionProjection) -> dict[str, Any]:
    """Everything waiting on a person: review items, model proposals, identity
    proposals and reports in review."""
    items = []
    for record in projection.family("review_item"):
        items.append({"queue": "SEMANTIC", "kind": record["kind"], "id": record["item_id"],
                      "subject_kind": record["subject_kind"], "subject_id": record["subject_id"],
                      "detail": record["detail"], "status": record["status"],
                      "evidence_refs": record.get("evidence_refs", ()),
                      "resolution_note": record.get("resolution_note", ""),
                      "recorded_time": record["recorded_time"],
                      "version": record.get("version", 1)})
    for record in projection.base_view["association_proposals"]:
        resolved = any(r.get("resolution") in ("ACCEPTED", "REJECTED", "SPLIT", "REVERSED")
                       for r in record.get("resolutions", []))
        items.append({"queue": "IDENTITY", "kind": "ASSOCIATION",
                      "id": record["proposal_id"],
                      "subject_kind": "object",
                      "subject_id": record["left_object_id"],
                      "detail": f"{record['left_object_id']} ~ {record['right_object_id']} "
                                f"({record['outcome']})",
                      "status": "RESOLVED" if resolved else "OPEN",
                      "evidence_refs": (), "resolution_note": "",
                      "recorded_time": record["recorded_time"], "version": 1})
    resolved_analyst_actions = {a.get("subject_id"): a for a in projection.base_view["analyst_actions"]
                                if a.get("kind") in ("ACCEPT", "REJECT")}
    for record in projection.family("analytical_proposal"):
        action = resolved_analyst_actions.get(record["proposal_id"])
        status = record.get("status", "PROPOSED")
        content = record.get("content", {}) or {}
        detail = "; ".join(f"{k}={str(v)[:60]}" for k, v in sorted(content.items())
                           if k != "target_kind")[:300]
        target_kind = content.get("target_kind", "")
        subject_id = ""
        if target_kind:
            for key in CANDIDATE_BINDING_KEYS.get(target_kind, ()):
                if content.get(key):
                    subject_id = str(content[key])
                    break
        items.append({"queue": "MODEL_PROPOSAL", "kind": record.get("proposal_type", ""),
                      "id": record["proposal_id"],
                      "subject_kind": target_kind,
                      "subject_id": subject_id,
                      "detail": detail,
                      "status": "OPEN" if status == "PROPOSED" else status,
                      "evidence_refs": (), "resolution_note": action.get("note", "") if action else "",
                      "recorded_time": record["recorded_time"], "version": 1})
    for record in projection.family("workbench_report"):
        if record["status"] == "IN_REVIEW":
            items.append({"queue": "REPORT", "kind": "REPORT_APPROVAL",
                          "id": record["report_id"], "subject_kind": "workbench_report",
                          "subject_id": record["report_id"],
                          "detail": f"report v{record['version']}: {record['title']}",
                          "status": "OPEN", "evidence_refs": (),
                          "resolution_note": "",
                          "recorded_time": record["recorded_time"],
                          "version": record["version"]})
    items.sort(key=lambda i: (0 if i["status"] == "OPEN" else 1, i["recorded_time"]))
    return {"items": items,
            "open_count": sum(1 for i in items if i["status"] == "OPEN")}
