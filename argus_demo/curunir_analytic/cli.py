"""Analytical inspection CLI — typed queries over the analytical layer.

    python -m curunir_analytic.cli --root STORE_ROOT COMMAND [args]

Commands:
    themes                      current themes with status/basis
    narratives                  current narratives (reach vs independence)
    stakeholders [--entity ID]  current assessments
    influence                   current influence assertions
    objectives                  mission objectives with status
    paths [--objective ID]      impact paths
    assumptions                 assumptions with status
    analogues                   retrieved historical analogues
    forecasts                   current forecasts with probability history depth
    indicators                  armed/fired/blocked indicators
    warnings                    standing warnings with tier and components
    calibration                 scoreboard: proper scores, buckets, coverage
    needs                       current analytical collection needs
    explain KIND ID             eight-section structured explanation
    history KIND ID             versions + transitions of one object
    dependents --claim ID       analytical objects resting on a claim
    stale                       analytical objects in degraded states
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .collect import analytic_collection_needs
from .explain import explain_object, render_text
from .store import ANALYTIC_ID_FIELDS, AnalyticStore
from .substrate import DependencyIndex


def _themes(store: AnalyticStore) -> list[dict]:
    return [{"theme_id": t["theme_id"], "title": t["title"], "status": t["status"],
             "authority": t["authority"],
             "supporting": len(t["basis"]["supporting_claim_ids"]),
             "contradicting": len(t["basis"]["contradicting_claim_ids"]),
             "independent_families": len(t["basis"]["origin_families"]),
             "reach": t["basis"]["manifestation_count"],
             "version": t["version"]}
            for t in sorted(store.current_themes().values(),
                            key=lambda t: t["title"])]


def _narratives(store: AnalyticStore) -> list[dict]:
    return [{"narrative_id": n["narrative_id"], "statement": n["statement"][:100],
             "status": n["status"], "reach": n["basis"]["manifestation_count"],
             "independent_families": len(n["basis"]["origin_families"]),
             "origin_status": n["origin_status"],
             "variants": len(n["variant_ids"]),
             "counter_narratives": len(n["counter_narrative_ids"])}
            for n in sorted(store.current_narratives().values(),
                            key=lambda n: n["narrative_id"])]


def _stakeholders(store: AnalyticStore, entity: str = "") -> list[dict]:
    assessments = store.current_stakeholder_assessments().values()
    if entity:
        assessments = [a for a in assessments if a["entity_object_id"] == entity]
    return [{"assessment_id": a["assessment_id"], "entity": a["entity_label"],
             "context": f"{a['context_kind']}:{a['context_id'][:20]}",
             "role": a["role_in_context"],
             "positions": [(p["kind"], p["authority"],
                            "superseded" if p["superseded"] else "current")
                           for p in a["positions"]],
             "identity_caveats": len(a["identity_caveats"]),
             "status": a["status"]}
            for a in sorted(assessments, key=lambda a: a["assessment_id"])]


def _paths(store: AnalyticStore, objective: str = "") -> list[dict]:
    paths = store.current_impact_paths().values()
    if objective:
        paths = [p for p in paths if p["objective_id"] == objective]
    return [{"path_id": p["path_id"], "objective_id": p["objective_id"][:24],
             "summary": p["summary"][:90], "status": p["status"],
             "path_authority": p["path_authority"],
             "edges": [(e["edge_kind"], e["effect_order"], e["authority"])
                       for e in p["edges"]],
             "uncertainty": p["uncertainty_note"][:120]}
            for p in sorted(paths, key=lambda p: p["path_id"])]


def _stale(store: AnalyticStore) -> list[dict]:
    rows = []
    for kind in ("analytic_theme", "analytic_narrative", "impact_path",
                 "analytic_assumption", "mission_objective"):
        for record in store.current_analytics(kind).values():
            _, id_field = ANALYTIC_ID_FIELDS[kind]
            if record.get("status") in ("STALE", "CONTESTED", "UNCERTAIN",
                                        "INVALIDATED", "EXPOSED", "DECLINING"):
                rows.append({"kind": kind, "id": record[id_field],
                             "status": record["status"],
                             "what": (record.get("title") or record.get("statement")
                                      or record.get("summary", ""))[:90]})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("command", choices=(
        "themes", "narratives", "stakeholders", "influence", "objectives",
        "paths", "assumptions", "analogues", "forecasts", "indicators",
        "warnings", "calibration", "needs", "explain", "history",
        "dependents", "stale"))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--entity", default="")
    parser.add_argument("--objective", default="")
    parser.add_argument("--claim", default="")
    parser.add_argument("--text", action="store_true",
                        help="render explain output as text")
    args = parser.parse_args(argv)
    store = AnalyticStore(Path(args.root) / "store"
                          if (Path(args.root) / "store" / "store_meta.json").exists()
                          else Path(args.root))
    command = args.command
    if command == "themes":
        result = _themes(store)
    elif command == "narratives":
        result = _narratives(store)
    elif command == "stakeholders":
        result = _stakeholders(store, args.entity)
    elif command == "influence":
        result = [{"influence_id": i["influence_id"], "kind": i["kind"],
                   "source": i["source_object_id"][:24],
                   "target": i["target_object_id"][:24],
                   "authority": i["authority"], "status": i["status"],
                   "mechanism": i["mechanism"][:100]}
                  for i in store.current_influence_assertions().values()]
    elif command == "objectives":
        result = [{"objective_id": o["objective_id"], "statement": o["statement"][:100],
                   "status": o["status"], "priority": o["priority"],
                   "mission": o["mission_context"]}
                  for o in store.current_objectives().values()]
    elif command == "paths":
        result = _paths(store, args.objective)
    elif command == "assumptions":
        result = [{"assumption_id": a["assumption_id"], "statement": a["statement"][:100],
                   "status": a["status"]}
                  for a in store.current_assumptions().values()]
    elif command == "analogues":
        result = [{"analogue_id": a["analogue_id"],
                   "situation": f"{a['query_kind']}:{a['query_id'][:20]}",
                   "episode_id": a["episode_id"][:24],
                   "matched": [d["dimension"] for d in a["matched"]],
                   "mismatched": [d["dimension"] for d in a["mismatched"]],
                   "status": a["status"]}
                  for a in store.current_analytics("historical_analogue").values()]
    elif command == "forecasts":
        result = [{"forecast_id": f["forecast_id"],
                   "question": f["question"][:100],
                   "probability": f["probability"], "status": f["status"],
                   "author": f["author"], "domain": f["domain"],
                   "horizon": f["horizon_time"][:19],
                   "independent_families": len(f["basis"]["origin_families"]),
                   "versions": len(store.analytic_versions("analytic_forecast",
                                                           f["forecast_id"])),
                   "outcome": f["outcome"]}
                  for f in sorted(store.current_forecasts().values(),
                                  key=lambda f: f["forecast_id"])]
    elif command == "indicators":
        result = [{"indicator_id": i["indicator_id"],
                   "description": i["description"][:100],
                   "kind": i["kind"], "direction": i["direction"],
                   "status": i["status"], "effect": i["effect"]["mode"],
                   "forecasts": len(i["forecast_ids"]),
                   "fired_time": i["fired_time"][:19]}
                  for i in sorted(store.current_indicators().values(),
                                  key=lambda i: i["indicator_id"])]
    elif command == "warnings":
        result = [{"warning_id": w["warning_id"], "tier": w["tier"],
                   "status": w["status"], "rule": w["tier_rule_id"],
                   "probability_band": w["probability_band"],
                   "consequence": w["consequence"],
                   "time_pressure": w["time_pressure"],
                   "evidence_confidence": w["evidence_confidence"],
                   "forecast": w["forecast_id"][:24],
                   "objective": w["objective_id"][:24]}
                  for w in sorted(store.current_warnings().values(),
                                  key=lambda w: w["warning_id"])]
    elif command == "calibration":
        from .calibration import scoreboard
        result = scoreboard(store)
    elif command == "needs":
        result = analytic_collection_needs(store)
    elif command == "explain":
        kind, object_id = args.args[0], args.args[1]
        explanation = explain_object(store, kind, object_id)
        if args.text:
            print(render_text(explanation))
            return 0
        result = explanation
    elif command == "history":
        kind, object_id = args.args[0], args.args[1]
        result = {"versions": [
            {"version": v.get("version", 1), "recorded_time": v["recorded_time"],
             "status": v.get("status", ""), "change_reason": v.get("change_reason", "")}
            for v in store.analytic_versions(kind, object_id)],
            "transitions": [
                {"type": t["transition_type"], "at": t["recorded_time"],
                 "detail": t["detail"][:140], "caused_by": t["caused_by"][:40]}
                for t in store.transitions_for(object_id)]}
    elif command == "dependents":
        index = DependencyIndex(store)
        result = sorted(f"{kind}:{ref}" for kind, ref
                        in index.affected_by(claim_ids=[args.claim]))
    else:  # stale
        result = _stale(store)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
