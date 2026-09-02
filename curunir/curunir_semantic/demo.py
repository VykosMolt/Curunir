"""Live demonstration of the semantic loop over real preserved evidence.

    python -m curunir_semantic.demo --root DIR --phase 1|2|3 [--evidence FABRIC_ROOT]

Phase 1: understand the OSINT foundation's real evidence (GLEIF, EDGAR,
         Wikidata, Wayback 2008, feeds, the real captured content change):
         Cases A–D of the tranche definition.
Phase 2: (fresh process = restart) hypothesis → discriminators → ranked
         collection routes → live execution through the fabric → world-model
         and hypothesis update. The closed loop.
Phase 3: (fresh process) export, replay into a fresh store, recover exact
         evidence anchors from the replayed payloads.

Every acquisition in phase 2 is a real network request. Nothing is mocked.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from curunir_fabric.registry import load_registry
from curunir_operational.access import Marking

from .collection import (assign_human_route, execute_route, plan_collection_routes,
                         requirement_for_discriminator)
from .hypotheses import link_claim, propose_discriminator, record_hypothesis, refresh_hypothesis
from .normalize import load_fields
from .pipeline import SemanticPipeline
from .store import SemanticStore

ACTOR = "semantic-live-demo"
MARK = Marking(owning_authority="curunir-semantic-demo", releasability=("PUBLIC",))
SEVERSTAL_LEI = "213800OKDPTV6K4ONO53"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pipeline(root: Path) -> SemanticPipeline:
    return SemanticPipeline(store=SemanticStore(root / "store"),
                            custody_root=root / "custody", actor=ACTOR, marking=MARK,
                            now_fn=_now)


def phase_1(root: Path, evidence_root: Path) -> dict:
    if not (root / "store" / "store_meta.json").exists():
        shutil.copytree(evidence_root / "store", root / "store")
        shutil.copytree(evidence_root / "custody", root / "custody")
    pipeline = _pipeline(root)
    store = pipeline.store
    understood = pipeline.process_new_evidence()
    historical = pipeline.interpret_historical_discoveries()
    monitored = pipeline.process_fabric_changes()

    # Case A — structured registry: exact field provenance
    claim = next(c for c in store.current_claims().values()
                 if c["subject_ref"] == f"LEI:{SEVERSTAL_LEI}" and c["predicate"] == "legal_name")
    observation = next(o for o in store.records_of("semantic_observation")
                       if o["observation_id"] == claim["observation_ids"][0])
    anchor = observation["anchors"][0]
    case_a = {"claim": claim["statement"][:90],
              "field_path": anchor["field_path"], "exact_value": anchor["exact_value"],
              "manifestation": anchor["manifestation_id"][:24],
              "content_sha256": anchor["content_sha256"][:16],
              "independent_basis": claim["independent_basis_count"]}

    # Case B — filings: events with source-native ids and day-precision time
    filing = next(a for a in store.records_of("activity")
                  if a["activity_type"] == "filing_published")
    case_b = {"event": filing["description"][:40], "valid_from": filing["valid_from"],
              "time_precision": filing["time_precision"],
              "participants": filing["participants"],
              "evidence": filing["evidence_refs"][:1]}

    # Case C — historical manifestation: valid time then, knowledge time now
    historical_claim = next(c for c in store.current_claims().values()
                            if c["valid_from"] and c["valid_from"] < "2010-01-01")
    case_c = {"statement": historical_claim["statement"][:90],
              "valid_from": historical_claim["valid_from"],
              "knowledge_time": historical_claim["recorded_time"],
              "discoveries": sum(len(h["semantic_changes"]) for h in historical)}

    # Case D — the real captured change, semantically interpreted
    semantic_changes = store.records_of("semantic_change")
    real_change = next((c for c in semantic_changes
                        if c["fabric_change_id"] and c["change_class"] != "SEMANTICALLY_UNCHANGED"),
                       None)
    alerts = [a for a in store.records_of("alert") if a["rule_id"] == "semantic-change"]
    case_d = {"change": (real_change or {}).get("detail", "")[:120],
              "change_class": (real_change or {}).get("change_class"),
              "affected_objects": (real_change or {}).get("affected_object_ids", ()),
              "alert_excerpt": alerts[0]["trigger"].split("\n")[0][:140] if alerts else ""}

    return {"understood": len(understood["processed"]),
            "failed": [p["error"] for p in understood["failed"]],
            "observations": len(store.records_of("semantic_observation")),
            "objects": len({v["object_id"] for v in store.records_of("object_version")}),
            "events": len(store.records_of("activity")),
            "claims": len(store.current_claims()),
            "case_a_registry": case_a, "case_b_filing": case_b,
            "case_c_historical": case_c, "case_d_semantic_change": case_d,
            "chain_valid": store.verify_chain()["valid"]}


def phase_2(root: Path) -> dict:
    pipeline = _pipeline(root)
    store = pipeline.store
    claims = store.current_claims()
    status_claim = next(c for c in claims.values()
                        if c["subject_ref"] == f"LEI:{SEVERSTAL_LEI}"
                        and c["predicate"] == "registration_status")
    active_claim = next(c for c in claims.values()
                        if c["subject_ref"] == f"LEI:{SEVERSTAL_LEI}"
                        and c["predicate"] == "entity_status")
    hypothesis = record_hypothesis(
        store, statement="PAO Severstal remains an active, registered Russian company",
        case_id="severstal-status", analyst_or_provider="analyst",
        now=_now(), actor=ACTOR, marking=MARK)
    for claim, why in ((status_claim, "GLEIF registration status"),
                       (active_claim, "GLEIF entity status")):
        hypothesis = link_claim(store, hypothesis["hypothesis_id"], claim["claim_id"],
                                "supporting", rationale=why, now=_now(), actor=ACTOR,
                                marking=MARK)
    hypothesis = refresh_hypothesis(pipeline.context(), hypothesis["hypothesis_id"])
    single_family_status = hypothesis["status"]

    need_id_file = root / "store" / ".." / ".." / "need_id.txt"
    need_id = ""
    for candidate in (root / "need_id.txt", need_id_file):
        if Path(candidate).exists():
            need_id = Path(candidate).read_text().strip()
            break
    if not need_id:
        needs = store.records_of("fabric_information_need")
        need_id = needs[0]["need_id"] if needs else ""

    registry = load_registry(store)

    # discriminator 1: independent corroboration (dependence-aware ranking)
    d1 = propose_discriminator(
        store, question="Does an independent source family corroborate Severstal's "
                        "active registered status?",
        hypothesis_ids=(hypothesis["hypothesis_id"],),
        claim_ids=(status_claim["claim_id"], active_claim["claim_id"]),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=f"LEI:{SEVERSTAL_LEI}", desired_attribute="entity_status",
        independence_required=True, now=_now(), actor=ACTOR, marking=MARK)
    d1 = requirement_for_discriminator(store, d1, mission_context="severstal-status",
                                       need_id=need_id, now=_now(), actor=ACTOR,
                                       marking=MARK)["discriminator"]
    routes_1 = plan_collection_routes(store, registry, d1,
                                      requirement_id=d1["requirement_id"],
                                      need_id=need_id, now=_now(), actor=ACTOR, marking=MARK)
    executed_1 = execute_route(pipeline, registry, routes_1[0]) if routes_1 and \
        routes_1[0]["score"] > 0 else {"execution_outcome": "NO_VIABLE_ROUTE"}

    # discriminator 2: source-family recheck (automatable, hint-weighted)
    d2 = propose_discriminator(
        store, question=f"Does GLEIF still show registration ISSUED for LEI {SEVERSTAL_LEI}?",
        hypothesis_ids=(hypothesis["hypothesis_id"],),
        claim_ids=(status_claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=f"LEI:{SEVERSTAL_LEI}", desired_attribute="registration_status",
        source_family_hints=("gleif",), now=_now(), actor=ACTOR, marking=MARK)
    d2 = requirement_for_discriminator(store, d2, mission_context="severstal-status",
                                       need_id=need_id, now=_now(), actor=ACTOR,
                                       marking=MARK)["discriminator"]
    routes_2 = plan_collection_routes(store, registry, d2,
                                      requirement_id=d2["requirement_id"],
                                      need_id=need_id, now=_now(), actor=ACTOR, marking=MARK)
    executed_2 = execute_route(pipeline, registry, routes_2[0])

    hypothesis = store.current_hypotheses()[hypothesis["hypothesis_id"]]
    return {
        "hypothesis_after_gleif_only": single_family_status,
        "independence_routes": [
            f"rank {r['rank']}: {r['source_id']}/{r['operation']} score={r['score']:.3f}"
            + (" [same-family: zero]" if r["score"] == 0 else "")
            for r in routes_1[:4]],
        "independent_route_executed": {
            "route": f"{routes_1[0]['source_id']}/{routes_1[0]['operation']}"
            if routes_1 else None,
            "outcome": executed_1.get("execution_outcome"),
            "new_manifestations": len(executed_1.get("manifestations", ()))},
        "recheck_routes": [
            f"rank {r['rank']}: {r['source_id']}/{r['operation']} score={r['score']:.3f}"
            for r in routes_2[:3]],
        "recheck_executed": {
            "route": f"{routes_2[0]['source_id']}/{routes_2[0]['operation']}",
            "outcome": executed_2["execution_outcome"],
            "discriminator": executed_2["discriminator_status"],
            "new_satisfying_observations": executed_2["observations_satisfying"],
            "hypotheses_refreshed": executed_2["hypotheses_refreshed"]},
        "final_hypothesis": {"status": hypothesis["status"],
                             "independent_families": hypothesis["independent_evidence_count"],
                             "history_tail": list(hypothesis["history"])[-3:]},
        "open_requirements": sum(
            1 for r in store.records_of("information_requirement")),
        "chain_valid": store.verify_chain()["valid"]}


def phase_3(root: Path) -> dict:
    pipeline = _pipeline(root)
    store = pipeline.store
    export_dir = root / "export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    manifest = store.export_to(export_dir)
    replay_dir = root / "replayed"
    if replay_dir.exists():
        shutil.rmtree(replay_dir)
    replayed = SemanticStore.import_from(export_dir, replay_dir)

    # recover an exact anchor from the replayed store alone
    claim = next(c for c in replayed.current_claims().values()
                 if c["subject_ref"] == f"LEI:{SEVERSTAL_LEI}"
                 and c["predicate"] == "legal_name")
    observation = next(o for o in replayed.records_of("semantic_observation")
                       if o["observation_id"] == claim["observation_ids"][0])
    anchor = observation["anchors"][0]
    document = next(d for d in replayed.records_of("semantic_document")
                    if d["manifestation_id"] == anchor["manifestation_id"])
    fields = dict(load_fields(replayed, document))
    recovered = fields.get(anchor["field_path"], "")
    return {
        "export_events": manifest["event_count"],
        "replayed_chain_valid": replayed.verify_chain()["valid"],
        "replayed_head_matches": replayed.head()["head_hash"] == manifest["head_hash"],
        "records_reconstructed": {
            "semantic_documents": len(replayed.records_of("semantic_document")),
            "observations": len(replayed.records_of("semantic_observation")),
            "claims": len(replayed.records_of("semantic_claim")),
            "object_versions": len(replayed.records_of("object_version")),
            "events": len(replayed.records_of("activity")),
            "semantic_changes": len(replayed.records_of("semantic_change")),
            "hypotheses": len(replayed.records_of("hypothesis")),
            "collection_routes": len(replayed.records_of("collection_route")),
        },
        "anchor_recovered_from_replay": {
            "field_path": anchor["field_path"],
            "value": recovered[:60],
            "matches_anchor": recovered == anchor["exact_value"]}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--phase", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--evidence", default="")
    args = parser.parse_args(argv)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    if args.phase == 1:
        result = phase_1(root, Path(args.evidence))
    elif args.phase == 2:
        result = phase_2(root)
    else:
        result = phase_3(root)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
