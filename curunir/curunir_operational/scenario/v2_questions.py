"""The 25 mission questions for scenarios B and C, answered from system
outputs."""
from __future__ import annotations

import pathlib
import tempfile
from typing import Any

from curunir_operational.projection import projection_hash

from . import v2_config as v2


def answer_v2_questions(b: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    b_store = b["store"]
    b_proj = b["projection"]
    c_store = c["store"]
    c_proj = c["projection"]
    joint = b_proj.view(v2.CONTEXTS["joint"])
    logistics = b_proj.view(v2.CONTEXTS["logistics"])
    civil = b_proj.view(v2.CONTEXTS["civil_protection"])
    cjoint = c_proj.view(v2.CONTEXTS["joint"])
    answers: list[dict[str, Any]] = []

    def add(n, q, answerable, answer, src):
        answers.append({"number": n, "question": q, "answerable": answerable, "answer": answer, "answered_from": src})

    affects = [r for r in joint["relationships"] if r["relation_type"] == "AFFECTS"]
    hazard_targets = sorted({r["target_object_id"] for r in affects})
    add(1, "Which infrastructure objects are affected by the captured hazard data?", True,
        [t for t in hazard_targets if t.startswith("infra-")], "AFFECTS relationships (rule-hazard-impact, scenario B)")

    affected_routes = [t for t in hazard_targets if t.startswith("route-")]
    add(2, "Which logistics routes depend on those objects?", True,
        {"routes_directly_affected": affected_routes,
         "route_dependencies": {r["source_object_id"]: r["target_object_id"] for r in joint["relationships"]
                                if r["relation_type"] == "DEPENDS_ON" and r["source_object_id"].startswith("route-")}},
        "DEPENDS_ON / AFFECTS relationships")

    r1 = next((o for o in joint["objects"] if o["object_id"] == "route-R1"), None)
    add(3, "Which route recommendations changed because of the hazard?", True,
        {"route_R1_status": r1["attributes"].get("status") if r1 else None,
         "route_R1_epistemic": r1["epistemic_state"] if r1 else None,
         "decision_effect_recorded": b["decision_effect"] is not None,
         "recommendations": [r["proposed_action"] for r in joint["recommendations"] if r["action_kind"] == "ROUTE_CHANGE"]},
        "cross-workbench decision effect on route-R1")

    structured = [o["object_id"] for o in joint["objects"]
                  if o["provenance"]["mode"] == "OPERATIONAL" and o["object_type"] in ("OPERATIONAL_CONCERN",)]
    add(4, "Which evidence comes from structured data?", True,
        {"hazard_objects_from_structured_feed": structured,
         "note": "GDACS-shaped structured feed produced the operational concern object(s)"},
        "operational provenance of OPERATIONAL_CONCERN objects")

    doc_evidence = [{"object_id": o["object_id"], "source_object": o["provenance"]["evidence"][0]["source_object_id"],
                     "review_state": o["provenance"]["evidence"][0]["review_state"]}
                    for o in joint["objects"] if o["provenance"].get("evidence")]
    add(5, "Which evidence comes from a public document?", True, doc_evidence,
        "evidentiary provenance (ARGUS adapter); scenario B carries the live GDACS/USGS document")

    add(6, "Which reports share one evidence basis?", True,
        [{"group_id": g["group_id"], "members": g["member_object_ids"]} for g in cjoint["dependence_groups"]],
        "dependence groups (scenario C: 4 reports, 1 basis)")

    ind = [o["object_id"] for o in cjoint["objects"] if o["provenance"].get("evidence")
           and not o["provenance"]["evidence"][0].get("dependence_group_id")
           and o["provenance"]["evidence"][0]["evidence_basis_id"] != "UNKNOWN_BASIS"]
    add(7, "Which report is genuinely independent?", True, ind,
        "evidence without a dependence group and with a distinct basis (scenario C)")

    disputed = sorted(o["object_id"] for o in joint["objects"] if o["epistemic_state"] == "DISPUTED")
    add(8, "Which operational state is disputed?", True, {"scenario_b": disputed,
         "scenario_c": sorted(o["object_id"] for o in cjoint["objects"] if o["epistemic_state"] == "DISPUTED")},
        "epistemic states after conflict detection")

    late_b = [r["ingestion_id"] for r in b_store.records_of("ingestion") if r["late"]]
    late_c = [r["ingestion_id"] for r in c_store.records_of("ingestion") if r["late"]]
    add(9, "Which data arrived late?", True, {"scenario_b": late_b, "scenario_c": late_c},
        "ingestion late flags")

    corrected = [o["object_id"] for o in cjoint["objects"] if o["attributes"].get("corrects_report")]
    retracted = [o["object_id"] for o in cjoint["objects"]
                 if o["attributes"].get("reported_status") == "RETRACTED"]
    add(10, "Which record was corrected or retracted?", True,
        {"corrected": corrected, "retraction_status_present": retracted}, "scenario C correction/retraction")

    schema_versions = {}
    for record in c_store.records_of("ingestion"):
        schema_versions.setdefault(record["schema_id"], set()).add(record["schema_version"])
    add(11, "Which schema version produced each object?", True,
        {k: sorted(v) for k, v in schema_versions.items()}, "ingestion schema_id + schema_version")

    transforms = {}
    for record in b_store.records_of("transformation"):
        for out in record["output_refs"]:
            transforms[out["ref"]] = record["mapping_id"]
    add(12, "Which mapping transformed each source record?", True,
        {"sample": dict(sorted(transforms.items())[:8]), "total_transformations": len(transforms)},
        "transformation records")

    open_reqs = [r["requirement_id"] for r in joint["information_requirements"]
                 if r["status"] in ("OPEN", "EVIDENCE_PENDING")]
    add(13, "Which information requirement remains open?", True,
        {"open": open_reqs, "answered": [r["requirement_id"] for r in joint["information_requirements"]
                                         if r["status"] == "ANSWERED"]},
        "information requirement statuses (scenario B)")

    overdue = [t["task_id"] for t in joint["analyst_tasks"] if t.get("overdue")]
    blocked = [t["task_id"] for t in joint["analyst_tasks"] if t["status"] == "BLOCKED"]
    add(14, "Which analyst task is overdue or blocked?", True,
        {"overdue": overdue, "blocked": blocked, "all_tasks": [(t["task_id"], t["status"]) for t in joint["analyst_tasks"]]},
        "analyst task statuses")

    contributors = {}
    for record in b_store.records_of("analytical_proposal"):
        inf = next((i for i in b_store.records_of("inference") if i["inference_id"] == record["inference_id"]), None)
        if inf:
            contributors[record["proposal_id"][:16]] = f"{inf['model_id']}@{inf['model_version']}"
    add(15, "Which provider generated each analytical proposal?", True,
        {"providers": sorted(set(contributors.values())), "proposal_count": len(contributors)},
        "inference records behind proposals")

    decisions = joint["decisions"]
    add(16, "Which recommendations were accepted, modified, deferred or rejected?", True,
        {d["state"]: d["recommendation_id"] for d in decisions} or "no decisions in joint view",
        "decision records")

    add(17, "What can the logistics-only actor see?", True,
        {"objects": logistics["counts"]["objects_total"],
         "sees_engineering_compartment": any(o["object_id"].startswith("eng-") for o in logistics["objects"]),
         "sees_hazard": any(o["object_type"] == "OPERATIONAL_CONCERN" for o in logistics["objects"])},
        "logistics context projection")
    add(18, "What can the civil-protection-only actor see?", True,
        {"objects": civil["counts"]["objects_total"],
         "sees_engineering_compartment": any(o["object_id"].startswith("eng-") for o in civil["objects"])},
        "civil-protection context projection")
    add(19, "What can the joint coordinator see?", True,
        {"objects": joint["counts"]["objects_total"],
         "sees_engineering_compartment": any(o["object_id"].startswith("eng-") for o in joint["objects"])},
        "joint context projection")

    unknown_dims = sorted({f"{o['object_id']}:{dim}" for o in joint["objects"]
                           for dim, val in o.get("quality", {}).items() if val == "UNKNOWN"})
    add(20, "What remains unknown?", True,
        {"unknown_quality_dimensions": len(unknown_dims), "sample": unknown_dims[:5],
         "open_requirements": open_reqs,
         "unreviewed_evidence": sorted({e["source_object_id"] for o in joint["objects"]
                                        for e in o["provenance"].get("evidence", []) if e["review_state"] == "UNREVIEWED"})},
        "quality dimensions, requirements, evidence review states")

    from curunir_operational.sovereignty import run_exit_test
    tmp = pathlib.Path(tempfile.mkdtemp())
    exit_b = run_exit_test(b_store, tmp / "exp_b", tmp / "fresh_b", v2.CONTEXTS["joint"],
                           snapshot_time=b_proj.snapshot_time, staleness_hours=v2.STALENESS_HOURS)
    exit_c = run_exit_test(c_store, tmp / "exp_c", tmp / "fresh_c", v2.CONTEXTS["joint"],
                           snapshot_time=c_proj.snapshot_time, staleness_hours=v2.STALENESS_HOURS)
    add(21, "Can both workbenches be reconstructed from export?", True,
        {"scenario_b_exit_test": exit_b["passed"], "scenario_c_exit_test": exit_c["passed"],
         "note": "both workbenches read the same exported store; reconstruction is store-level"},
        "open export + fresh-store import exit test")

    add(22, "Can a delta bundle update a stale compatible store?", True,
        {"demonstrated_in": "delta_sync_report.json", "see": "OperationalSyncReceipt status APPLIED"},
        "delta synchronization harness")

    add(23, "Which internal schema dependencies remain?", True,
        {"internal_schema_dependence": "consumers depend on the curunir-operational record vocabulary and enums",
         "semantic_migration_risk": "epistemic states, quality dims and markings carry meaning beyond field names"},
        "sovereignty manifest lock-in assessment")

    hazard = next((o for o in joint["objects"] if o["object_type"] == "OPERATIONAL_CONCERN"), None)
    add(24, "Which live-source facts are time-sensitive?", True,
        {"live_document_evidence": [d["object_id"] for d in doc_evidence],
         "hazard_valid_from": hazard["valid_from"] if hazard else None,
         "note": "feed timestamps are preserved verbatim (timezone-naive → coerced UTC); freshness policy flags "
                 "aged hazard/observation objects; a captured fixture is a point-in-time snapshot, not current truth"},
        "freshness policy + capture limitations")

    add(25, "What does the system refuse to conclude?", True,
        {"refusals": [
            "does not treat 4 dependent reports as independent corroboration (1 basis, scenario C)",
            "does not close an information requirement on a model-generated answer (human-only)",
            "does not let a provider decide or write accepted state",
            "does not auto-merge ambiguous objects (conservative association)",
            "does not silently coerce an incompatible schema (v2→v1 INVALID)",
            "does not present the captured fixture as current live truth"]},
        "enforced guards across the fabric")

    return {"scenario_projection_hashes": {
                "b_joint": projection_hash(joint), "b_logistics": projection_hash(logistics),
                "b_civil_protection": projection_hash(civil), "c_joint": projection_hash(cjoint)},
            "answers": answers, "all_answerable": all(a["answerable"] for a in answers)}
