"""Situation reports in JSON, Markdown and plain text.

A report is built from a frozen access-filtered view and carries an integrity
hash. It always says whether an item is observed, reported, extracted, inferred,
disputed or unknown, and reports dependent publications as one shared basis
rather than as corroboration.
"""
from __future__ import annotations

from typing import Any, Mapping

from .access import AccessContext, can_view
from .canonical import sha256
from .projection import Projection
from .store import MissionDataStore

REPORT_VERSION = "curunir-operational-sitrep-v1"


def _route_exposure(projection: Projection, context: AccessContext) -> dict[str, Any] | None:
    latest = None
    for proposal in projection.analytical_proposals.values():
        if proposal["proposal_type"] == "ASSESSMENT" and proposal["content"].get("kind") == "route-exposure" \
                and can_view(proposal.get("marking"), context):
            latest = proposal
    return latest["content"] if latest else None


def build_situation_report(store: MissionDataStore, projection: Projection, context: AccessContext, *,
                           operational_context: str, since_seq: int = 0) -> dict[str, Any]:
    view = projection.view(context)
    changes = projection.changes_since(since_seq, context, store)
    objects = view["objects"]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for record in objects:
        by_type.setdefault(record["object_type"], []).append(record)

    def line(record: Mapping[str, Any], detail: str) -> dict[str, Any]:
        return {"epistemic_state": record["epistemic_state"], "object_id": record["object_id"],
                "label": (record.get("labels") or [record["object_id"]])[0], "detail": detail,
                "freshness": record["freshness"]["state"], "age_hours": record["freshness"]["age_hours"],
                "quality_state": record["quality_summary"]["state"],
                "provenance_sources": list(record.get("provenance", {}).get("source_ids", []))}

    resources = [line(r, f"{r['attributes'].get('commodity', 'stock')}: "
                         f"{r['attributes'].get('quantity', 'UNKNOWN')} {r['attributes'].get('unit', '')}".strip())
                 for r in by_type.get("RESOURCE_STOCK", [])]
    infrastructure = [line(r, f"status: {r['attributes'].get('status', 'UNKNOWN')}"
                              + (f" — DISPUTED: {r['attributes'].get('dispute_note', '')}"
                                 if r["epistemic_state"] == "DISPUTED" else ""))
                      for r in by_type.get("INFRASTRUCTURE", [])]
    exposure = _route_exposure(projection, context)
    visible_ids = {record["object_id"] for record in objects}
    routes = []
    for record in by_type.get("ROUTE", []):
        if exposure and record["object_id"] in exposure.get("exposure", {}):
            # Never name an object this context cannot view, whatever the
            # assessment happens to carry.
            findings = [f for f in exposure["exposure"][record["object_id"]]
                        if f["disruption_id"] in visible_ids
                        and f.get("dependency", f["disruption_id"]) in visible_ids]
            detail = "no known disruption" if not findings else \
                f"{len(findings)} known disruption(s): " + "; ".join(
                    f"{f['disruption_id']} ({f['condition']})" for f in findings)
            state = "INFERRED"
        else:
            detail = "exposure not assessed"
            state = "UNKNOWN"
        routes.append({**line(record, detail), "exposure_state": state})
    movements = [line(r, f"cargo: {r['attributes'].get('cargo', 'UNKNOWN')}; "
                          f"status: {r['attributes'].get('status', r['epistemic_state'])}")
                 for r in by_type.get("MOVEMENT", [])]
    conflicts = [{"relationship_id": r["relationship_id"], "left": r["source_object_id"],
                  "right": r["target_object_id"], "rationale": r.get("rationale", "")}
                 for r in view["relationships"]
                 if r["relation_type"] == "CONFLICTS_WITH" and r["status"] == "ACTIVE"]
    stale = [{"object_id": o["object_id"], "age_hours": o["freshness"]["age_hours"],
              "threshold_hours": o["freshness"].get("threshold_hours")}
             for o in objects if o["freshness"]["state"] == "STALE"]
    dependence = [{"group_id": g["group_id"], "member_object_ids": g["member_object_ids"],
                   "note": f"{len(g['member_object_ids'])} reports share one underlying basis; "
                           "they are not independent corroboration"}
                  for g in view["dependence_groups"]]
    alerts = [{"alert_id": a["alert_id"], "severity": a["severity"], "status": a["status"],
               "trigger": a["trigger"], "evidence_refs": list(a["evidence_refs"])} for a in view["alerts"]]
    recommendations = [{"recommendation_id": r["recommendation_id"], "action_kind": r["action_kind"],
                        "proposed_action": r["proposed_action"], "rationale": r["rationale"],
                        "uncertainty": r["uncertainty"], "required_role": r["required_role"],
                        "evidence_snapshot_hash": r["evidence_snapshot_hash"]}
                       for r in view["recommendations"]]
    decisions = [{"decision_id": d["decision_id"], "recommendation_id": d["recommendation_id"],
                  "state": d["state"], "actor_id": d["actor_id"], "actor_role": d["actor_role"],
                  "rationale": d["rationale"], "modification": d["modification"],
                  "evidence_snapshot_hash": d["evidence_snapshot_hash"]} for d in view["decisions"]]
    unresolved = {
        "disputed_objects": sorted(o["object_id"] for o in objects if o["epistemic_state"] == "DISPUTED"),
        "open_association_proposals": sorted(
            p["proposal_id"] for p in view["association_proposals"]
            if p["outcome"] == "PROPOSE_ASSOCIATION"
            and not any(r["resolution"] in ("ACCEPTED", "REJECTED", "SPLIT") for r in p["resolutions"])),
        "unknown_quality_dimensions": sorted({f"{o['object_id']}:{dim}" for o in objects
                                              for dim, value in o.get("quality", {}).items()
                                              if value == "UNKNOWN"}),
    }
    provenance_refs = {
        "source_systems": sorted({s for o in objects for s in o.get("provenance", {}).get("source_ids", [])}),
        "ingestions_referenced": sorted({i for o in objects for i in o.get("provenance", {}).get("ingestion_ids", [])}),
        "evidence_bases": sorted({e["evidence_basis_id"] for o in objects
                                  for e in o.get("provenance", {}).get("evidence", [])}),
    }
    body = {
        "report_version": REPORT_VERSION,
        "snapshot_time": view["meta"]["snapshot_time"],
        "state_token": view["meta"]["state_token"],
        "operational_context": operational_context,
        "access_context": {"context_id": context.context_id,
                           "statement": "This report contains only content releasable to this access context. "
                                        "Content outside its releasability, if any exists, is omitted and is "
                                        "not represented by counts or placeholders."},
        "epistemic_note": "Items are tagged OBSERVED / REPORTED / EXTRACTED / INFERRED / PLANNED / DISPUTED / "
                          "UNKNOWN; tags are load-bearing, not decoration.",
        "significant_changes": changes,
        "resources": resources, "infrastructure": infrastructure, "routes": routes, "movements": movements,
        "conflicts": conflicts, "stale_information": stale, "dependent_reporting": dependence,
        "alerts": alerts, "recommendations": recommendations, "decisions": decisions,
        "unresolved": unresolved, "provenance_references": provenance_refs,
        "counts": view["counts"],
    }
    return {"report": body, "integrity_hash": sha256(body)}


def render_markdown(report: Mapping[str, Any]) -> str:
    body = report["report"]
    lines = [f"# Operational situation report — {body['operational_context']}", "",
             f"Snapshot: {body['snapshot_time']} (state {body['state_token']})  ",
             f"Access: {body['access_context']['context_id']}  ",
             f"Integrity: `{report['integrity_hash']}`", "",
             f"> {body['access_context']['statement']}", "",
             f"_{body['epistemic_note']}_", ""]

    def section(title: str, rows: list[dict[str, Any]], render) -> None:
        lines.append(f"## {title}")
        if not rows:
            lines.append("- none in this view")
        lines.extend(render(row) for row in rows)
        lines.append("")

    section("Resources", body["resources"],
            lambda r: f"- **{r['epistemic_state']}** {r['label']}: {r['detail']} ({r['freshness']}"
                      + ("" if r["age_hours"] is None else f", {r['age_hours']}h old") + ")")
    section("Infrastructure", body["infrastructure"],
            lambda r: f"- **{r['epistemic_state']}** {r['label']}: {r['detail']}")
    section("Routes", body["routes"],
            lambda r: f"- **{r['exposure_state']}** {r['label']}: {r['detail']}")
    section("Movements", body["movements"],
            lambda r: f"- **{r['epistemic_state']}** {r['label']}: {r['detail']}")
    section("Unresolved conflicts", body["conflicts"],
            lambda c: f"- {c['left']} vs {c['right']}: {c['rationale']}")
    section("Stale information", body["stale_information"],
            lambda s: f"- {s['object_id']}: {s['age_hours']}h old (threshold {s['threshold_hours']}h)")
    section("Dependent reporting", body["dependent_reporting"], lambda d: f"- {d['note']}")
    section("Alerts", body["alerts"],
            lambda a: f"- [{a['severity']}] {a['trigger']} — {a['status']}")
    section("Recommendations", body["recommendations"],
            lambda r: f"- ({r['action_kind']}) {r['proposed_action']} — uncertainty: {r['uncertainty']}")
    section("Decisions", body["decisions"],
            lambda d: f"- {d['state']} by {d['actor_id']} ({d['actor_role']}): {d['rationale']}"
                      + (f" — modification: {d['modification']}" if d["modification"] else ""))
    lines.append("## Unresolved")
    lines.append(f"- disputed objects: {', '.join(body['unresolved']['disputed_objects']) or 'none'}")
    lines.append(f"- open association proposals: {', '.join(body['unresolved']['open_association_proposals']) or 'none'}")
    lines.append(f"- unknown quality dimensions: {len(body['unresolved']['unknown_quality_dimensions'])}")
    lines.append("")
    lines.append("## Provenance")
    lines.append(f"- source systems: {', '.join(body['provenance_references']['source_systems']) or 'none'}")
    lines.append(f"- evidence bases: {', '.join(body['provenance_references']['evidence_bases']) or 'none'}")
    return "\n".join(lines) + "\n"


def render_text(report: Mapping[str, Any]) -> str:
    body = report["report"]
    width = 72
    rule = "-" * width
    out = [
        "OPERATIONAL SITUATION REPORT (RESEARCH SHADOW / SYNTHETIC)",
        f"CONTEXT : {body['operational_context']}"[:width],
        f"SNAPSHOT: {body['snapshot_time']}  STATE {body['state_token']}",
        f"ACCESS  : {body['access_context']['context_id']}",
        "INTEGRITY SHA256:",
        f"  {report['integrity_hash']}",
        rule,
        "TAGS: OBSERVED/REPORTED/EXTRACTED/INFERRED/PLANNED/DISPUTED/UNKNOWN",
        rule,
    ]

    def block(title: str, rows: list[str]) -> None:
        out.append(title)
        out.extend(f"  {row}"[:width] for row in (rows or ["none in this view"]))
        out.append(rule)

    block("RESOURCES", [f"[{r['epistemic_state']}] {r['label']}: {r['detail']} ({r['freshness']})"
                        for r in body["resources"]])
    block("INFRASTRUCTURE", [f"[{r['epistemic_state']}] {r['label']}: {r['detail']}" for r in body["infrastructure"]])
    block("ROUTES", [f"[{r['exposure_state']}] {r['label']}: {r['detail']}" for r in body["routes"]])
    block("MOVEMENTS", [f"[{r['epistemic_state']}] {r['label']}: {r['detail']}" for r in body["movements"]])
    block("CONFLICTS", [f"{c['left']} vs {c['right']}: {c['rationale']}" for c in body["conflicts"]])
    block("STALE", [f"{s['object_id']}: {s['age_hours']}h old" for s in body["stale_information"]])
    block("DEPENDENT REPORTING", [d["note"] for d in body["dependent_reporting"]])
    block("ALERTS", [f"[{a['severity']}] {a['trigger']} - {a['status']}" for a in body["alerts"]])
    block("RECOMMENDATIONS", [f"({r['action_kind']}) {r['proposed_action']}" for r in body["recommendations"]])
    block("DECISIONS", [f"{d['state']} by {d['actor_id']}: {d['rationale']}" for d in body["decisions"]])
    unresolved = body["unresolved"]
    block("UNRESOLVED", [f"disputed: {', '.join(unresolved['disputed_objects']) or 'none'}",
                         f"open associations: {', '.join(unresolved['open_association_proposals']) or 'none'}",
                         f"unknown quality dims: {len(unresolved['unknown_quality_dimensions'])}"])
    out.append("END OF REPORT")
    return "\n".join(out) + "\n"
