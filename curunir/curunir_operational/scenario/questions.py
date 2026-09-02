"""Answers to the fifteen mission questions, computed from system outputs
rather than written by hand."""
from __future__ import annotations

from typing import Any

from curunir_operational.canonical import canonical_line
from curunir_operational.projection import Projection, projection_hash
from curunir_operational.store import MissionDataStore


def answer_questions(*, store: MissionDataStore, final_projection: Projection,
                     projection_high: dict[str, Any], projection_low: dict[str, Any],
                     proposals: list[dict[str, Any]], proposal_ab: dict[str, Any],
                     proposal_plan: dict[str, Any], decisions: list[dict[str, Any]],
                     exit_test: dict[str, Any], replay_checks: dict[str, Any],
                     seq_before_late: int, seq_after_late: int,
                     mock_assessment: dict[str, Any] | None) -> dict[str, Any]:
    answers: list[dict[str, Any]] = []

    def add(number, question, answer, source, answerable=True):
        answers.append({"number": number, "question": question, "answerable": answerable,
                        "answer": answer, "answered_from": source})

    exposure = next((p["content"] for p in proposals
                     if p["proposal_type"] == "ASSESSMENT" and p["content"].get("kind") == "route-exposure"), {})
    add(1, "Which route is currently least exposed to known disruption?",
        {"least_exposed": exposure.get("least_exposed"),
         "exposure_counts": {r: len(f) for r, f in exposure.get("exposure", {}).items()}},
        "rule-route-exposure ASSESSMENT proposal")

    damage_observations = [o["current"] for o in final_projection.objects.values()
                           if o["current"]["object_type"] == "OBSERVATION"
                           and o["current"]["attributes"].get("subject_ref") == "BR-7"
                           and o["current"]["attributes"].get("reported_status") == "DAMAGED"]
    add(2, "Which evidence supports the bridge-damage concern?",
        [{"object_id": o["object_id"], "epistemic_state": o["epistemic_state"],
          "sources": o["provenance"]["source_ids"],
          "evidence_bases": [e["evidence_basis_id"] for e in o["provenance"].get("evidence", [])]}
         for o in damage_observations],
        "observation objects + evidentiary provenance")

    groups = final_projection.dependence_groups
    add(3, "How many apparently separate reports share the same underlying evidence basis?",
        {"groups": [{"group_id": g, "members": m, "member_count": len(m)} for g, m in groups.items()],
         "note": "dependent members are one basis, not corroboration"},
        "argus adapter dependence groups")

    stale = [{"object_id": oid, "age_hours": e["freshness"]["age_hours"]}
             for oid, e in final_projection.objects.items() if e["freshness"]["state"] == "STALE"]
    add(4, "Which resource records are stale?",
        [s for s in stale if s["object_id"].startswith("stock-")], "projection freshness policy")

    ab_resolutions = final_projection.association_proposals[proposal_ab["proposal_id"]]["resolutions"]
    add(5, "Which movement identity remains disputed?",
        {"disputed_during_phase3": {"pair": [proposal_ab["left_object_id"], proposal_ab["right_object_id"]],
                                    "outcome": proposal_ab["outcome"]},
         "resolution": ab_resolutions[-1]["resolution"] if ab_resolutions else "OPEN",
         "currently_open_proposals": [p["proposal"]["proposal_id"]
                                      for p in final_projection.association_proposals.values()
                                      if p["proposal"]["outcome"] == "PROPOSE_ASSOCIATION" and not p["resolutions"]],
         "accepted_association": {"pair": [proposal_plan["left_object_id"], proposal_plan["right_object_id"]],
                                  "cluster_id": final_projection.cluster_of.get(proposal_plan["left_object_id"])}},
        "association proposals and resolutions")

    before = Projection(store, as_of_seq=seq_before_late, snapshot_time=projection_high["meta"]["snapshot_time"])
    after = Projection(store, as_of_seq=seq_after_late, snapshot_time=projection_high["meta"]["snapshot_time"])
    fuel_before = before.objects["stock-ALD-FUEL"]
    fuel_after = after.objects["stock-ALD-FUEL"]
    add(6, "What changed after the late observation arrived?",
        {"object_id": "stock-ALD-FUEL",
         "history_count": {"before": fuel_before["history_count"], "after": fuel_after["history_count"]},
         "current_quantity": {"before": fuel_before["current"]["attributes"]["quantity"],
                              "after": fuel_after["current"]["attributes"]["quantity"]},
         "explanation": "the late report entered history but its older validity did not displace current state"},
        "historical as-of projections around the late ingestion")

    high_alert_ids = {a["alert_id"] for a in projection_high["alerts"]}
    low_alert_ids = {a["alert_id"] for a in projection_low["alerts"]}
    only_high = sorted(high_alert_ids - low_alert_ids)
    add(7, "Which alert is visible only to the higher-access user?",
        {"alert_ids": only_high,
         "triggers": [a["trigger"] for a in projection_high["alerts"] if a["alert_id"] in only_high],
         "note": "recorded in privileged diagnostics only; the partner view itself contains no trace"},
        "access-differentiated projections (privileged diagnostic)")

    route_rec = next((r for r in final_projection.recommendations.values()
                      if r["action_kind"] == "ROUTE_CHANGE"), None)
    add(8, "Why did the alternate-route recommendation fire?",
        None if route_rec is None else
        {"rationale": route_rec["rationale"], "evidence_refs": list(route_rec["evidence_refs"]),
         "assumptions": list(route_rec["assumptions"]), "provider": route_rec["provider_id"]},
        "recommendation record", answerable=route_rec is not None)

    open_recommendations = [r for r in final_projection.recommendations.values()
                            if not any(d["recommendation_id"] == r["recommendation_id"]
                                       for d in final_projection.decisions)]
    add(9, "Which assumptions remain unresolved?",
        {"undecided_recommendations": [{"recommendation_id": r["recommendation_id"],
                                        "assumptions": list(r["assumptions"]),
                                        "uncertainty": r["uncertainty"]} for r in open_recommendations],
         "decided_but_conditional": [{"recommendation_id": d["recommendation_id"], "state": d["state"],
                                      "modification": d["modification"]} for d in final_projection.decisions
                                     if d["state"] in ("MODIFIED", "DEFERRED")],
         "disputed_objects": sorted(o for o, e in final_projection.objects.items()
                                    if e["current"]["epistemic_state"] == "DISPUTED")},
        "recommendations, decisions and epistemic states")

    add(10, "What did the analyst decide?",
        [{"decision_id": d["decision_id"], "recommendation_id": d["recommendation_id"], "state": d["state"],
          "actor": d["actor_id"], "role": d["actor_role"], "rationale": d["rationale"],
          "modification": d["modification"]} for d in decisions],
        "decision records")

    unknown_dims = sorted({f"{oid}:{dim}" for oid, e in final_projection.objects.items()
                           for dim, value in e["current"].get("quality", {}).items() if value == "UNKNOWN"})
    add(11, "What data would be required to make a stronger decision?",
        {"open_information_requests": [r["proposed_action"] for r in open_recommendations
                                       if r["action_kind"] in ("INFORMATION_REQUEST", "SOURCE_INSPECTION")],
         "stale_needing_refresh": [s["object_id"] for s in stale],
         "unknown_quality_dimensions": unknown_dims,
         "unreviewed_evidence": sorted({e["source_object_id"] for o in final_projection.objects.values()
                                        for e in o["current"]["provenance"].get("evidence", [])
                                        if e["review_state"] == "UNREVIEWED"})},
        "open recommendations, freshness and quality dimensions")

    add(12, "Can the complete state be reproduced from exported data?",
        {"exit_test_passed": exit_test["passed"], "checks": exit_test["checks"],
         "provider_reinvocations_during_replay": replay_checks["provider_reinvocations_during_replay"]},
        "open export + fresh-store import + projection hash comparison")

    contributors = []
    for entry in final_projection.alerts.values():
        contributors.append({"derived_record": entry["record"]["alert_id"], "kind": "alert",
                             "contributor": f"{entry['record']['rule_id']}@{entry['record']['rule_version']}"})
    for record in final_projection.recommendations.values():
        contributors.append({"derived_record": record["recommendation_id"], "kind": "recommendation",
                             "contributor": record["provider_id"]})
    for oid, entry in final_projection.objects.items():
        if entry["current"]["epistemic_state"] == "DISPUTED":
            contributors.append({"derived_record": f"{oid}@v{entry['current']['version']}", "kind": "disputed_state",
                                 "contributor": "curunir-deterministic-rules (materialized by workflow-policy)"})
    if mock_assessment:
        contributors.append({"derived_record": mock_assessment["proposal_id"], "kind": "assessment_proposal",
                             "contributor": "mock-damage-assessment@1.0 (UNACCREDITED; proposal only)"})
    add(13, "Which model or rule contributed to each derived result?", contributors,
        "alert/recommendation/inference records")

    systems = sorted({ref["system"] for o in store.records_of("object_version")
                      for ref in o.get("external_refs", [])})
    add(14, "Which original sources remain authoritative systems of record?",
        {"external_systems": systems,
         "statement": "Curunír operational is SYSTEM_OF_ENGAGEMENT_AND_ANALYSIS; every imported identifier keeps "
                      "its originating system, identifier, imported version, ingestion id and sync status; "
                      "authoritative records remain with the originating systems"},
        "external references + engagement boundary")

    low_json = canonical_line(projection_low)
    add(15, "What information is unavailable to the current (partner) access context?",
        {"policy": "the partner context cannot learn what is hidden: no hidden ids, labels, counts or provenance "
                   "appear in its projection",
         "leakage_scan": {"restricted_tokens_found_in_partner_view": [t for t in
                          ("obs-FN-2205", "SENSITIVE-INFRA", "Substation Four") if t in low_json]},
         "privileged_diagnostic_hidden_object_count":
             projection_high["counts"]["objects_total"] - projection_low["counts"]["objects_total"]},
        "leakage scan of the partner projection + privileged diagnostics")

    return {"scenario_projection_hashes": {"full": projection_hash(projection_high),
                                           "restricted": projection_hash(projection_low)},
            "answers": answers}
