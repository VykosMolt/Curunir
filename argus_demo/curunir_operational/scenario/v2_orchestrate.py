"""Top-level V2 orchestration: three scenarios, joint outputs, provider
evaluation, delta synchronization, cross-workbench access matrix, stress, and
the full artifact set."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curunir_operational.canonical import canonical_line
from curunir_operational.delta import (build_delta_bundle, build_full_bundle, import_delta_bundle,
                                       verify_delta_bundle)
from curunir_operational.projection import Projection, projection_hash
from curunir_operational.providers_eval import run_provider_comparison
from curunir_operational.sitrep import build_situation_report, render_markdown, render_text
from curunir_operational.sovereignty import build_sovereignty_manifest, run_exit_test
from curunir_operational.store import MissionDataStore
from curunir_operational.workbench import WorkbenchRenderer, render_cop_html
from curunir_operational.access import can_view

from . import config as a_config
from . import v2_config as v2
from .runner import run_scenario as run_scenario_a
from .v2_runner import run_scenario_b, run_scenario_c
from .v2_questions import answer_v2_questions


def _wj(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _restricted_ids(store: MissionDataStore) -> set[str]:
    ids = set()
    for record in store.records_of("object_version"):
        marking = record["marking"]
        if marking.get("compartments"):
            ids.add(record["object_id"])
    return ids


def cross_workbench_access_matrix(b: dict[str, Any]) -> dict[str, Any]:
    """A leak = a restricted object id, or a compartment name the context does
    not hold, appearing in that context's serialized view. An object the
    context IS authorized to see is not a leak."""
    store = b["store"]; projection = b["projection"]
    # privileged diagnostic: map each restricted object to its marking
    restricted_markings = {r["object_id"]: r["marking"] for r in store.records_of("object_version")
                           if r["marking"].get("compartments")}
    all_compartments = {c for m in restricted_markings.values() for c in m["compartments"]}
    rows = {}
    total_leaks = 0
    for name, context in v2.CONTEXTS.items():
        if context.actor_kind == "SERVICE":
            continue
        view = projection.view(context)
        serialized = canonical_line(view)
        # only ids the context genuinely cannot view count as leaks
        unauthorized_ids = sorted(oid for oid, marking in restricted_markings.items()
                                  if not can_view(marking, context) and oid in serialized)
        # compartment names the context does not hold must not appear
        unheld_compartments = sorted(c for c in all_compartments
                                     if c not in context.compartments and c in serialized)
        r1 = next((o for o in view["objects"] if o["object_id"] == "route-R1"), None)
        leaks = len(unauthorized_ids) + len(unheld_compartments)
        total_leaks += leaks
        rows[name] = {
            "objects": view["counts"]["objects_total"],
            "open_requirements": view["counts"]["open_information_requirements"],
            "holds_engineering_compartment": "ENGINEERING-ASSESSMENT" in context.compartments,
            "sees_engineering_objects": any(o["object_id"].startswith("eng-") for o in view["objects"]),
            "sees_route_R1": r1 is not None,
            "sees_route_R1_restriction_reason": bool(r1 and r1["attributes"].get("restriction_reason")),
            "unauthorized_id_leaks": unauthorized_ids,
            "unheld_compartment_leaks": unheld_compartments,
        }
    return {"restricted_object_ids_privileged_diagnostic": sorted(restricted_markings),
            "compartments": sorted(all_compartments), "views": rows, "leakage_failures": total_leaks}


def delta_sync_demo(b: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    store = b["store"]
    seq_phase1 = b["seq_phase1"]
    full_manifest = build_full_bundle(store, out_dir / "full_bundle")
    # stale target = a store holding only up to seq_phase1; a delta brings it current
    truncated_root = out_dir / "truncated_target"
    _truncate_import(store, truncated_root, seq_phase1)
    truncated = MissionDataStore(truncated_root)
    delta_manifest = build_delta_bundle(store, out_dir / "delta_bundle", base_seq=seq_phase1)
    verification = verify_delta_bundle(out_dir / "delta_bundle")
    receipt = import_delta_bundle(truncated, out_dir / "delta_bundle")
    brought_current = truncated.head()["head_hash"] == store.head()["head_hash"]
    # duplicate delta → idempotent no-op on the now-current store
    duplicate_receipt = import_delta_bundle(truncated, out_dir / "delta_bundle")
    # missing base: a target holding only 1 event cannot receive a delta based at seq_phase1
    missing_base_target = _fresh_from_truncation(store, out_dir / "missing_base_target", keep=1)
    missing_base = import_delta_bundle(missing_base_target, out_dir / "delta_bundle")
    # wrong base: a target that diverged at seq_phase1 (different history) rejects the delta
    diverged = _diverged_target(store, out_dir / "diverged_target", seq_phase1)
    wrong_base = import_delta_bundle(diverged, out_dir / "delta_bundle")
    # tamper the delta events and re-verify
    tampered_dir = out_dir / "delta_tampered"
    _copy_dir(out_dir / "delta_bundle", tampered_dir)
    events_file = tampered_dir / "delta_events.jsonl"
    events_file.write_text(events_file.read_text().replace("ACTIVE", "FORGED", 1))
    tamper_verification = verify_delta_bundle(tampered_dir)
    return {
        "full_bundle": {"event_count": full_manifest["event_count"], "base_state_token": full_manifest["base_state_token"]},
        "delta_bundle": {"base_seq": delta_manifest["base_seq"], "event_count": delta_manifest["event_count"],
                         "verification_valid": verification["valid"]},
        "apply_receipt": {"status": receipt["status"], "applied_events": receipt.get("applied_events"),
                          "head_after": receipt.get("store_head_after", {}).get("head_hash")},
        "brought_current_matches_source": brought_current,
        "duplicate_delta_receipt": duplicate_receipt["status"],
        "missing_base_conflict": missing_base.get("conflict"),
        "wrong_base_conflict": wrong_base.get("conflict"),
        "tamper_detected": not tamper_verification["valid"],
    }


def _fresh_from_truncation(store, root, keep):
    _truncate_import(store, root, keep)
    return MissionDataStore(root)


def _diverged_target(store, root, keep_seq):
    """A target sharing the first keep_seq events then appending a divergent one,
    so the delta's base hash still matches but an overlapping event differs."""
    _truncate_import(store, root, keep_seq)
    target = MissionDataStore(root)
    from curunir_operational.contracts import SourceRecord
    from curunir_operational.access import Marking
    target.append("SOURCE_REGISTERED",
                  SourceRecord("src-divergent", "SYSTEM", "OTHER", "x", "op", "CIVDEF-AUTH", {},
                               Marking("CIVDEF-AUTH", releasability=("CORRIDOR-OPS",)), "ACTIVE", "",
                               store.events()[keep_seq]["recorded_time"]),
                  recorded_time=store.events()[keep_seq]["recorded_time"], actor="divergent")
    return target


def _truncate_import(store: MissionDataStore, root: Path, keep_seq: int) -> None:
    store.export_to(root.parent / "_full_export_tmp")
    events = (root.parent / "_full_export_tmp" / "events.jsonl").read_text().splitlines()
    root.mkdir(parents=True, exist_ok=True)
    (root / "payloads").mkdir(exist_ok=True)
    import shutil
    shutil.copyfile(root.parent / "_full_export_tmp" / "store_meta.json", root / "store_meta.json")
    (root / "events.jsonl").write_text("\n".join(events[:keep_seq]) + ("\n" if events[:keep_seq] else ""))
    for payload in (root.parent / "_full_export_tmp" / "payloads").iterdir():
        shutil.copyfile(payload, root / "payloads" / payload.name)


def _fresh_from_open_export(store, export_dir, root, drop_to):
    """A store holding only `drop_to` events, to trigger MISSING_BASE for a later delta."""
    _truncate_import(store, root, drop_to)
    return MissionDataStore(root)


def _copy_dir(src: Path, dst: Path) -> None:
    import shutil
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def joint_report(b: dict[str, Any], context) -> dict[str, Any]:
    store = b["store"]; projection = b["projection"]
    view = projection.view(context)
    logistics = WorkbenchRenderer(projection).render(
        _workshop(store, "SHARED_LOGISTICS_VISIBILITY_WORKBENCH_V1"), context)
    infra = WorkbenchRenderer(projection).render(
        _workshop(store, "INFRASTRUCTURE_RESILIENCE_AND_CIVIL_PROTECTION_WORKBENCH_V2"), context)
    cross = [r for r in view["relationships"] if r["relation_type"] in ("AFFECTS", "DEPENDS_ON", "SUPPLIES")]
    return {
        "report_type": "JOINT_COORDINATOR_REPORT", "context_id": context.context_id,
        "state_token": view["meta"]["state_token"],
        "shared_objects": sorted(set(logistics["details"]) & set(infra["details"])),
        "cross_domain_impacts": [{"relation": r["relation_type"], "from": r["source_object_id"],
                                  "to": r["target_object_id"]} for r in cross],
        "unresolved_conflicts": [r["relationship_id"] for r in view["relationships"]
                                 if r["relation_type"] == "CONFLICTS_WITH" and r["status"] == "ACTIVE"],
        "information_requirements": [(r["requirement_id"], r["status"]) for r in view["information_requirements"]],
        "analyst_tasks": [(t["task_id"], t["status"]) for t in view["analyst_tasks"]],
        "recommendations": [(r["recommendation_id"], r["action_kind"]) for r in view["recommendations"]],
        "decisions": [(d["decision_id"], d["state"]) for d in view["decisions"]],
        "logistics_object_count": logistics["counts"]["objects"],
        "infrastructure_object_count": infra["counts"]["objects"],
    }


def _workshop(store: MissionDataStore, workshop_id: str) -> dict[str, Any]:
    return [d for d in store.records_of("workshop_definition") if d["workshop_id"] == workshop_id][-1]


def run_v2(root: Path, out_dir: Path) -> dict[str, Any]:
    root = Path(root); out_dir = Path(out_dir)
    (out_dir / "04_v2_scenarios").mkdir(parents=True, exist_ok=True)
    (out_dir / "05_v2_workbenches").mkdir(parents=True, exist_ok=True)
    (out_dir / "06_v2_exports").mkdir(parents=True, exist_ok=True)

    scenario_a = run_scenario_a(root / "scenario_a" / "store", root / "scenario_a" / "out")
    b = run_scenario_b(root / "scenario_b")
    c = run_scenario_c(root / "scenario_c")

    # workbench renders (both workbenches, joint + per-domain contexts)
    b_store = b["store"]; b_proj = b["projection"]
    logistics_wb = _workshop(b_store, "SHARED_LOGISTICS_VISIBILITY_WORKBENCH_V1")
    infra_wb = _workshop(b_store, "INFRASTRUCTURE_RESILIENCE_AND_CIVIL_PROTECTION_WORKBENCH_V2")
    renderer = WorkbenchRenderer(b_proj)
    for name, wb in (("logistics", logistics_wb), ("infrastructure", infra_wb)):
        for ctx_name in ("joint", "logistics", "civil_protection"):
            view = renderer.render(wb, v2.CONTEXTS[ctx_name])
            (out_dir / "05_v2_workbenches" / f"cop_{name}_{ctx_name}.html").write_text(
                render_cop_html(view, title=f"{wb['purpose']} — {ctx_name}"), encoding="utf-8")

    # situation reports for each workbench (joint context)
    for name in ("logistics", "civil_protection", "joint"):
        report = build_situation_report(b_store, b_proj, v2.CONTEXTS[name],
                                        operational_context=f"Infrastructure cascade — {name}", since_seq=b["seq_phase1"])
        (out_dir / "05_v2_workbenches" / f"sitrep_{name}.md").write_text(render_markdown(report), encoding="utf-8")
        (out_dir / "05_v2_workbenches" / f"sitrep_{name}.txt").write_text(render_text(report), encoding="utf-8")

    answers = answer_v2_questions(b, c)
    _wj(out_dir / "04_v2_scenarios" / "mission_question_answers.json", answers)

    access_matrix = cross_workbench_access_matrix(b)
    _wj(out_dir / "04_v2_scenarios" / "cross_workbench_access_matrix.json", access_matrix)

    # provider evaluation on scenario B: each provider gets its own fresh copy
    # of the store (evaluation must not mutate operational state)
    empty = Projection(MissionDataStore.create(root / "empty_store", "empty", a_config.at(0)),
                       snapshot_time=a_config.at(1))
    b_store.export_to(root / "eval_export")
    eval_counter = {"n": 0}

    def make_eval_store(label):
        eval_counter["n"] += 1
        return MissionDataStore.import_from(root / "eval_export", root / f"eval_{label}_{eval_counter['n']}")

    provider_eval = run_provider_comparison(
        root / "eval_export", make_eval_store, b_proj, empty, v2.CONTEXTS["joint"], v2.CONTEXTS["logistics"],
        _restricted_ids(b_store),
        times=[a_config.at(40.0), a_config.at(40.5), a_config.at(41.0), a_config.at(41.5)])
    _wj(out_dir / "04_v2_scenarios" / "provider_evaluation.json", provider_eval)

    delta = delta_sync_demo(b, out_dir / "06_v2_exports")
    _wj(out_dir / "06_v2_exports" / "delta_sync_report.json", delta)

    jr = joint_report(b, v2.CONTEXTS["joint"])
    _wj(out_dir / "05_v2_workbenches" / "joint_coordinator_report.json", jr)

    sovereignty = build_sovereignty_manifest(b_store)
    _wj(out_dir / "06_v2_exports" / "sovereignty_manifest_v2.json", sovereignty)

    # scenario metrics
    metrics = {
        "scenario_a": {"head": scenario_a["summary"]["store_head"]["head_hash"],
                       "questions_answerable": scenario_a["summary"]["questions_answerable"]},
        "scenario_b": {"events": b_store.head()["event_count"], "head": b_store.head()["head_hash"],
                       "objects_joint": b_proj.view(v2.CONTEXTS["joint"])["counts"]["objects_total"],
                       "affects_relationships": sum(1 for r in b_proj.view(v2.CONTEXTS["joint"])["relationships"]
                                                    if r["relation_type"] == "AFFECTS"),
                       "requirement_answered": any(r["status"] == "ANSWERED" for r in
                                                   b_proj.view(v2.CONTEXTS["joint"])["information_requirements"]),
                       "route_R1_restricted": next((o["attributes"].get("status") for o in
                                                    b_proj.view(v2.CONTEXTS["joint"])["objects"]
                                                    if o["object_id"] == "route-R1"), None)},
        "scenario_c": {"events": c["store"].head()["event_count"], "head": c["store"].head()["head_hash"],
                       "dependence_groups": len(c["projection"].view(v2.CONTEXTS["joint"])["dependence_groups"]),
                       "incompatible_schema": c["incompatible_validation"]["status"],
                       "unsupported_schema": c["unsupported_validation"]["status"],
                       "compatible_schema": c["compatible_validation"]["status"],
                       "duplicates": sum(1 for r in c["log"] if r["duplicate"])},
        "access_security": {"leakage_failures": access_matrix["leakage_failures"]},
        "provider_evaluation": {"providers": [e["provider"] for e in provider_eval["evaluated"]],
                                "learned_model_comparison": provider_eval["learned_model_comparison"]},
        "delta_sync": {"applied": delta["apply_receipt"]["status"],
                       "brought_current": delta["brought_current_matches_source"],
                       "tamper_detected": delta["tamper_detected"]},
        "determinism_note": "wall-clock timings excluded from metrics hashing; all projection hashes reproduce across runs",
    }
    _wj(out_dir / "04_v2_scenarios" / "scenario_metrics.json", metrics)

    return {"scenario_a": scenario_a["summary"], "scenario_b_events": b_store.head()["event_count"],
            "scenario_c_events": c["store"].head()["event_count"], "answers": answers,
            "access_matrix": access_matrix, "provider_eval": provider_eval, "delta": delta,
            "joint_report": jr, "metrics": metrics}
