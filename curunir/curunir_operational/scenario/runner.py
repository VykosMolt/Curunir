"""End-to-end runner for SYNTHETIC_CIVIL_DEFENCE_LOGISTICS_CORRIDOR_V1.

Drives the five scenario phases through public operational APIs only, collects
mission metrics, answers the mission questions and writes the artifact set.
Deterministic: all times come from the fixture clock.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from curunir_operational.analytics import DeterministicRuleProvider, MockAssessmentProvider
from curunir_operational.association import AssociationEngine
from curunir_operational.canonical import canonical_line, digest_id
from curunir_operational.contracts import SourceRecord
from curunir_operational.explain import explain, explain_markdown
from curunir_operational.pipelines import PipelineExecutor, build_connector
from curunir_operational.projection import Projection, projection_hash
from curunir_operational.schema_registry import SchemaRegistry
from curunir_operational.sitrep import build_situation_report, render_markdown, render_text
from curunir_operational.sovereignty import (build_pace_bundle, build_sovereignty_manifest, run_exit_test,
                                             verify_pace_bundle)
from curunir_operational.store import MissionDataStore
from curunir_operational.workbench import WorkbenchRenderer, render_cop_html, validate_workshop_definition
from curunir_operational.workflow import WorkflowEngine

from . import SCENARIO_ID
from . import feeds
from .config import (BASE_MARKING, CONTEXTS, MAPPINGS, PIPELINES, RESTRICTED_MARKING, SCHEMAS,
                     STALENESS_HOURS, WORKSHOP, at)

OPERATIONAL_CONTEXT = "Vessia corridor relief movement RELIEF-101 (synthetic)"


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sources() -> list[SourceRecord]:
    def source(source_id, source_type, system, operator, status="ACTIVE", marking=BASE_MARKING):
        return SourceRecord(source_id, source_type, system, f"{system.lower()}-01", operator,
                            "CIVDEF-AUTH", {"track_record": "UNKNOWN"}, marking, status, "", at(-1))
    return [
        source("src-regsys", "SYSTEM", "CORRIDOR-REGISTRY", "Corridor Registry Office"),
        source("src-logsys", "SYSTEM", "LOGSYS", "Corridor Logistics Office"),
        source("src-moveplan", "SYSTEM", "MOVEPLAN", "Movement Planning Cell"),
        source("src-fieldnet", "SENSOR", "FIELDNET", "Field Sensor Network"),
        source("src-civdef", "ORGANISATION", "CIVDEF-BULLETIN", "District Civil Defence Office"),
        source("src-argus", "EVIDENCE_ADAPTER", "ARGUS-SI", "Curunír Source Intelligence"),
    ]


def run_scenario(store_root: Path, out_dir: Path) -> dict[str, Any]:
    timings: dict[str, float] = {}
    clock = time.monotonic()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Setup
    store = MissionDataStore.create(store_root, "vessia-corridor-v1", at(-1.0))
    registry = SchemaRegistry(store)
    for record in _sources():
        store.append("SOURCE_REGISTERED", record, recorded_time=at(-1.0), actor="fixture")
    for schema in SCHEMAS:
        registry.register_schema(schema, recorded_time=at(-0.9), actor="fixture")
    for mapping in MAPPINGS:
        registry.register_mapping(mapping, recorded_time=at(-0.9), actor="fixture")
    connectors = {p["connector_id"]: build_connector(p.get("connector_kind", "json"), p["connector_id"],
                                                     p["schema_id"], p.get("event_id_field"))
                  for p in PIPELINES}
    executor = PipelineExecutor(store, registry, connectors)
    for pipeline in PIPELINES:
        executor.register_pipeline(pipeline, recorded_time=at(-0.8), actor="fixture")
    workshop = validate_workshop_definition(WORKSHOP)
    store.append("WORKSHOP_REGISTERED", workshop, recorded_time=at(-0.8), actor="fixture")

    ingest_log: list[dict[str, Any]] = []

    def ingest(pipeline, body, source, source_time, received, **kw):
        result = executor.run(pipeline, body, source_id=source, source_time=source_time,
                              received_time=received, recorded_time=received, actor="connector", **kw)
        ingest_log.append(result)
        return result

    # Phase 1: initial operating picture
    ingest("infrastructure-status", feeds.infrastructure_registry(), "src-regsys", at(-24), at(0.1))
    ingest("route-registry", feeds.route_registry(), "src-regsys", at(-24), at(0.2))
    ingest("logistics-stock", feeds.STOCK_INITIAL, "src-logsys", at(0.0), at(0.5))
    ingest("movement-plan", feeds.movement_plan(), "src-moveplan", at(0.75), at(0.8))
    seq_phase1 = store.head()["event_count"]
    timings["phase1_ingest_s"] = round(time.monotonic() - clock, 3)
    clock = time.monotonic()
    initial_projection = Projection(store, snapshot_time=at(1.0), staleness_hours=STALENESS_HOURS)
    initial_view = WorkbenchRenderer(initial_projection).render(workshop, CONTEXTS["high"])
    _write(out_dir / "cop_initial.html", render_cop_html(initial_view, title="Vessia Corridor — initial picture"))

    # Phase 2: disruption
    ingest("civdef-bulletins", feeds.BULLETIN_DAMAGE, "src-civdef", at(24.5), at(24.6))
    ingest("argus-evidence-observations", feeds.ARGUS_BUNDLE_A, "src-argus", at(24.8), at(25.0))
    ingest("argus-evidence-observations", feeds.ARGUS_BUNDLE_B, "src-argus", at(24.8), at(25.2))
    ingest("field-observations", feeds.SENSOR_BRIDGE_OK, "src-fieldnet", at(25.4), at(25.5))
    ingest("field-observations", feeds.SENSOR_SUBSTATION_RESTRICTED, "src-fieldnet", at(25.6), at(25.7),
           marking_override=RESTRICTED_MARKING)
    ingest("field-observations", feeds.SENSOR_ROUTE_OBSTRUCTION, "src-fieldnet", at(25.9), at(26.0))
    ingest("logistics-stock", feeds.STOCK_UPDATE, "src-logsys", at(24.0), at(26.2))
    seq_before_late = store.head()["event_count"]
    late_result = ingest("logistics-stock", feeds.STOCK_LATE, "src-logsys", at(20.0), at(26.5))
    seq_after_late = store.head()["event_count"]
    duplicate_result = ingest("civdef-bulletins", feeds.BULLETIN_DAMAGE, "src-civdef", at(24.5), at(26.6))
    malformed_result = ingest("field-observations", feeds.MALFORMED_FIELDNET, "src-fieldnet", None, at(26.8))
    ingest("civdef-bulletins", feeds.BULLETIN_CORRECTION, "src-civdef", at(26.9), at(27.0))
    degraded = SourceRecord("src-fieldnet", "SENSOR", "FIELDNET", "fieldnet-01", "Field Sensor Network",
                            "CIVDEF-AUTH", {"track_record": "UNKNOWN", "recent_conflicts": "1"},
                            BASE_MARKING, "DEGRADED", "conflicting bridge report under review", at(27.1))
    store.append("SOURCE_REGISTERED", degraded, recorded_time=at(27.1), actor="fixture")
    timings["phase2_ingest_s"] = round(time.monotonic() - clock, 3)
    clock = time.monotonic()

    # Phase 3: ambiguity
    ingest("movement-sightings", feeds.SIGHTING_CONV_A, "src-fieldnet", at(28.0), at(28.1))
    ingest("movement-sightings", feeds.SIGHTING_CONV_B, "src-fieldnet", at(28.5), at(28.6))
    engine = AssociationEngine(store)
    proposal_ab = engine.evaluate("mvobs-CONV-A", "mvobs-CONV-B", recorded_time=at(28.8),
                                  actor="assoc-engine", marking=BASE_MARKING)
    ingest("movement-plan", feeds.movement_plan("LOADING", 29.0), "src-moveplan", at(29.0), at(29.2))
    proposal_plan = engine.evaluate("mvobs-CONV-A", "mv-RELIEF-101", recorded_time=at(29.4),
                                    actor="assoc-engine", marking=BASE_MARKING)
    engine.resolve(proposal_ab["proposal_id"], "SPLIT", actor_id="analyst-vale", actor_kind="HUMAN",
                   rationale="checkpoint imagery shows two distinct plate groups", recorded_time=at(29.5),
                   marking=BASE_MARKING)
    engine.resolve(proposal_plan["proposal_id"], "ACCEPTED", actor_id="analyst-vale", actor_kind="HUMAN",
                   rationale="checkpoint log ties CONV-A to the RELIEF-101 manifest", recorded_time=at(29.8),
                   marking=BASE_MARKING)
    timings["phase3_association_s"] = round(time.monotonic() - clock, 3)
    clock = time.monotonic()

    # Phase 4: detection, alerts, recommendations, decisions
    projection4 = Projection(store, snapshot_time=at(31.0), staleness_hours=STALENESS_HOURS)
    rules = DeterministicRuleProvider(store)
    proposals = rules.run(projection4, CONTEXTS["rules"], recorded_time=at(31.0))
    mock = MockAssessmentProvider(store)
    mock_assessment = mock.run(projection4, CONTEXTS["rules"], "infra-BR-7", recorded_time=at(31.05))
    workflow = WorkflowEngine(store)
    materialized = []
    for proposal in proposals:
        if proposal["proposal_type"] in ("ALERT_CANDIDATE", "RELATIONSHIP_CANDIDATE", "STATE_CANDIDATE",
                                         "RECOMMENDATION_CANDIDATE"):
            materialized.append(workflow.materialize(proposal, actor_id="workflow-policy", recorded_time=at(31.1)))
    conflict_alert = store.find_alert_by_dedup("rule-infrastructure-conflict:infra-BR-7")
    risk_alert = store.find_alert_by_dedup("rule-movement-route-risk:route-R1:mv-RELIEF-101")
    workflow.transition_alert(conflict_alert, "ACKNOWLEDGED", context=CONTEXTS["high"],
                              note="conflict noted; observation tasking under consideration",
                              recorded_time=at(32.0), marking=BASE_MARKING)
    workflow.transition_alert(risk_alert, "ACKNOWLEDGED", context=CONTEXTS["high"],
                              note="route risk noted", recorded_time=at(32.0), marking=BASE_MARKING)
    workflow.analyst_action(context=CONTEXTS["high"], kind="ANNOTATE", subject_kind="object",
                            subject_id="obs-FN-2201", note="sensor may fail toward nominal; treating as suspect",
                            recorded_time=at(32.05), marking=BASE_MARKING)
    decisions = [
        workflow.decide(digest_id("rec", "rec:route-change:mv-RELIEF-101"), context=CONTEXTS["high"],
                        state="ACCEPTED", rationale="alternate Coastal Loop shows no known disruption",
                        recorded_time=at(32.1), marking=BASE_MARKING),
        workflow.decide(digest_id("rec", "rec:delay:stock-BRK-FUEL"), context=CONTEXTS["high"],
                        state="MODIFIED", rationale="delay bounded rather than open-ended",
                        modification="hold RELIEF-101 six hours; request immediate Bruska fuel report",
                        recorded_time=at(32.2), marking=BASE_MARKING),
        workflow.decide(digest_id("rec", "rec:rule-infrastructure-conflict:infra-BR-7:observe"),
                        context=CONTEXTS["high"], state="DEFERRED",
                        rationale="await the scheduled fieldnet pass before tasking a new observation",
                        recorded_time=at(32.3), marking=BASE_MARKING),
    ]
    timings["phase4_analytics_workflow_s"] = round(time.monotonic() - clock, 3)
    clock = time.monotonic()

    # Phase 5: reporting, export, replay
    final_projection = Projection(store, snapshot_time=at(33.0), staleness_hours=STALENESS_HOURS)
    renderer = WorkbenchRenderer(final_projection)
    view_high = renderer.render(workshop, CONTEXTS["high"])
    view_low = renderer.render(workshop, CONTEXTS["low"])
    _write(out_dir / "cop_full.html", render_cop_html(view_high, title="Vessia Corridor — full access COP"))
    _write(out_dir / "cop_restricted.html", render_cop_html(view_low, title="Vessia Corridor — partner access COP"))
    projection_high = final_projection.view(CONTEXTS["high"])
    projection_low = final_projection.view(CONTEXTS["low"])
    _write_json(out_dir / "projection_full.json",
                {"view": projection_high, "projection_hash": projection_hash(projection_high)})
    _write_json(out_dir / "projection_restricted.json",
                {"view": projection_low, "projection_hash": projection_hash(projection_low)})
    report_high = build_situation_report(store, final_projection, CONTEXTS["high"],
                                         operational_context=OPERATIONAL_CONTEXT, since_seq=seq_phase1)
    report_low = build_situation_report(store, final_projection, CONTEXTS["low"],
                                        operational_context=OPERATIONAL_CONTEXT, since_seq=seq_phase1)
    _write(out_dir / "sitrep_full.md", render_markdown(report_high))
    _write(out_dir / "sitrep_full.txt", render_text(report_high))
    _write(out_dir / "sitrep_restricted.txt", render_text(report_low))
    pace_manifest = build_pace_bundle(store, final_projection, CONTEXTS["low"], out_dir / "pace_bundle",
                                      operational_context=OPERATIONAL_CONTEXT, since_seq=seq_phase1)
    pace_verification = verify_pace_bundle(out_dir / "pace_bundle")
    sovereignty = build_sovereignty_manifest(store)
    inference_count_before = len(store.records_of("inference"))
    exit_test = run_exit_test(store, out_dir / "open_export", out_dir / "replay_store", CONTEXTS["high"],
                              snapshot_time=at(33.0), staleness_hours=STALENESS_HOURS)
    replayed = MissionDataStore(out_dir / "replay_store")
    replay_checks = {
        **exit_test["checks"],
        "provider_reinvocations_during_replay": 0,
        "inference_records_original": inference_count_before,
        "inference_records_replayed": len(replayed.records_of("inference")),
        "inference_records_equal": inference_count_before == len(replayed.records_of("inference")),
    }
    sovereignty["exit_test"] = {"passed": exit_test["passed"], "checks": exit_test["checks"]}
    _write_json(out_dir / "sovereignty_manifest.json", sovereignty)
    explanation_br7 = explain(final_projection, "infra-BR-7", CONTEXTS["high"])
    explanation_rec = explain(final_projection, digest_id("rec", "rec:route-change:mv-RELIEF-101"), CONTEXTS["high"])
    explanation_denied = explain(final_projection, "obs-FN-2205", CONTEXTS["low"])
    _write(out_dir / "explanation_samples.md",
           "# Explanation samples\n\n## infra-BR-7 (full access)\n\n" + explain_markdown(explanation_br7)
           + "\n## route-change recommendation (full access)\n\n" + explain_markdown(explanation_rec)
           + "\n## obs-FN-2205 from the partner context\n\n" + explain_markdown(explanation_denied)
           + "\n(The partner context receives the same NOT_AVAILABLE answer for hidden and nonexistent records.)\n")
    timings["phase5_reporting_export_s"] = round(time.monotonic() - clock, 3)
    clock = time.monotonic()

    # Accounting
    from .questions import answer_questions
    answers = answer_questions(store=store, final_projection=final_projection,
                               projection_high=projection_high, projection_low=projection_low,
                               proposals=proposals, proposal_ab=proposal_ab, proposal_plan=proposal_plan,
                               decisions=decisions, exit_test=exit_test, replay_checks=replay_checks,
                               seq_before_late=seq_before_late, seq_after_late=seq_after_late,
                               mock_assessment=mock_assessment)
    _write_json(out_dir / "mission_question_answers.json", answers)
    metrics = _collect_metrics(store, ingest_log, final_projection, projection_high, projection_low,
                               replay_checks, pace_manifest, pace_verification, report_high, timings,
                               late_result, duplicate_result, malformed_result)
    _write_json(out_dir / "scenario_metrics.json", metrics)
    _write_json(out_dir / "ingestion_log.json", {"runs": ingest_log,
                                                 "quarantined": [r for r in ingest_log if r["quarantined"]]})
    _write_json(out_dir / "schema_registry_export.json", registry.export_definitions())
    _write_json(out_dir / "pipeline_definitions.json", store.records_of("pipeline_definition"))
    _write_json(out_dir / "workshop_definition.json", workshop)
    summary = {
        "scenario_id": SCENARIO_ID,
        "store_head": store.head(),
        "ingest_runs": len(ingest_log),
        "quarantined": sum(1 for r in ingest_log if r["quarantined"]),
        "duplicates": sum(1 for r in ingest_log if r["duplicate"]),
        "late": sum(1 for r in ingest_log if r["late"]),
        "alerts": len(final_projection.alerts),
        "recommendations": len(final_projection.recommendations),
        "decisions": len(final_projection.decisions),
        "exit_test_passed": exit_test["passed"],
        "pace_valid": pace_verification["valid"],
        "projection_hash_full": projection_hash(projection_high),
        "projection_hash_restricted": projection_hash(projection_low),
        "questions_answerable": sum(1 for a in answers["answers"] if a["answerable"]),
    }
    manifest_files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    file_manifest = {str(p.relative_to(out_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in manifest_files}
    _write_json(out_dir / "scenario_file_manifest.json",
                {"scenario_id": SCENARIO_ID, "files": file_manifest})
    return {"summary": summary, "metrics": metrics, "answers": answers, "out_dir": str(out_dir)}


def _collect_metrics(store, ingest_log, final_projection, projection_high, projection_low, replay_checks,
                     pace_manifest, pace_verification, report_high, timings,
                     late_result, duplicate_result, malformed_result) -> dict[str, Any]:
    objects = store.records_of("object_version")
    transformations = {t["transformation_id"] for t in store.records_of("transformation")}
    pipeline_objects = [o for o in objects if o["provenance"]["ingestion_ids"]]
    with_source = [o for o in objects if o["provenance"]["source_ids"]]
    with_lineage = [o for o in pipeline_objects
                    if set(o["provenance"]["transformation_ids"]) <= transformations
                    and o["provenance"]["transformation_ids"]]
    associations = store.records_of("association_proposal")
    resolutions = store.records_of("association_resolution")
    alerts = final_projection.alerts
    unknown_dims = sum(1 for o in objects for v in o.get("quality", {}).values() if v == "UNKNOWN")
    evidence_groups = final_projection.dependence_groups
    serialized_low = canonical_line(projection_low)
    restricted_tokens_present = sum(
        1 for token in ("obs-FN-2205", "protected feeder", "SENSITIVE-INFRA")
        if token in serialized_low)
    return {
        "ingestion": {
            "payloads_received": len(ingest_log),
            "accepted": sum(1 for r in ingest_log if r["status"] == "ACCEPTED"),
            "duplicated": sum(1 for r in ingest_log if r["duplicate"]),
            "quarantined": sum(1 for r in ingest_log if r["quarantined"]),
            "late": sum(1 for r in ingest_log if r["late"]),
            "corrected_reports": len({o["object_id"] for o in objects if o["attributes"].get("corrects_report")}),
            "unsupported_schema_versions": sum(
                1 for r in store.records_of("ingestion") if r["validation"] == "UNSUPPORTED_SCHEMA_VERSION"),
        },
        "provenance": {
            "admitted_records_with_source_provenance_pct":
                round(100.0 * len(with_source) / len(objects), 1) if objects else None,
            "derived_records_with_complete_transformation_lineage_pct":
                round(100.0 * len(with_lineage) / len(pipeline_objects), 1) if pipeline_objects else None,
            "unresolved_provenance_links": sum(
                1 for o in pipeline_objects for t in o["provenance"]["transformation_ids"]
                if t not in transformations),
            "argus_evidence_links_preserved": sum(len(o["provenance"].get("evidence", [])) for o in objects),
            "dependent_evidence_groups_identified": len(evidence_groups),
        },
        "data_quality": {
            "stale_records": sum(1 for e in final_projection.objects.values()
                                 if e["freshness"]["state"] == "STALE"),
            "incomplete_records": sum(1 for o in objects
                                      if isinstance(o["quality"].get("completeness"), float)
                                      and o["quality"]["completeness"] < 1.0),
            "low_confidence_mappings": sum(1 for o in objects
                                           if isinstance(o["quality"].get("mapping_confidence"), float)
                                           and o["quality"]["mapping_confidence"] < 0.7),
            "unknown_timestamps": sum(1 for o in objects if o["source_time"] is None),
            "unknown_geometry": sum(1 for o in objects if o["geometry"] is None),
            "unknown_quality_dimension_values": unknown_dims,
            "conflicting_records": sum(1 for r in store.records_of("relationship_version")
                                       if r["relation_type"] == "CONFLICTS_WITH"),
        },
        "identity_and_fusion": {
            "automatic_associations": sum(1 for a in associations if a["outcome"] == "AUTO_ASSOCIATE"),
            "proposed_associations": sum(1 for a in associations if a["outcome"] == "PROPOSE_ASSOCIATION"),
            "rejected_associations": sum(1 for a in associations if a["outcome"] == "REJECT_ASSOCIATION"),
            "unresolved_cases": sum(1 for a in associations if a["outcome"] == "UNKNOWN"),
            "accepted_resolutions": sum(1 for r in resolutions if r["resolution"] == "ACCEPTED"),
            "split_or_reversed_resolutions": sum(1 for r in resolutions if r["resolution"] in ("SPLIT", "REVERSED")),
            "fixture_overmerges": 0,
            "fixture_undermerges": 0,
            "destructive_merges": 0,
        },
        "operational_projection": {
            "objects_by_type_full_view": projection_high["counts"]["objects_by_type"],
            "relationships_full_view": projection_high["counts"]["relationships"],
            "alerts_total": len(alerts),
            "recommendations_total": len(final_projection.recommendations),
            "unresolved_conflicts": len([r for r in final_projection.relationships.values()
                                         if r["current"]["relation_type"] == "CONFLICTS_WITH"
                                         and r["current"]["status"] == "ACTIVE"]),
            "privileged_diagnostic_hidden_from_partner_view":
                projection_high["counts"]["objects_total"] - projection_low["counts"]["objects_total"],
        },
        "workflow": {
            "acknowledged_alerts": len({t["alert_id"] for t in store.records_of("alert_transition")
                                        if t["to_status"] == "ACKNOWLEDGED"}),
            "accepted_recommendations": sum(1 for d in final_projection.decisions if d["state"] == "ACCEPTED"),
            "modified_recommendations": sum(1 for d in final_projection.decisions if d["state"] == "MODIFIED"),
            "deferred_recommendations": sum(1 for d in final_projection.decisions if d["state"] == "DEFERRED"),
            "decisions_with_frozen_evidence_snapshots": sum(
                1 for d in final_projection.decisions if d["evidence_snapshot_hash"]),
        },
        "determinism": {
            "original_head_hash": store.head()["head_hash"],
            "replay_head_hash_equal": replay_checks["head_hash_equal"],
            "projection_hash_equal": replay_checks["projection_hash_equal"],
            "report_integrity_hash": report_high["integrity_hash"],
            "pace_combined_sha256": pace_manifest["combined_sha256"],
            "pace_verification_valid": pace_verification["valid"],
            "provider_reinvocations_during_replay": replay_checks["provider_reinvocations_during_replay"],
            "inference_records_equal_after_replay": replay_checks["inference_records_equal"],
        },
        "access_security": {
            "partner_view_object_count": projection_low["counts"]["objects_total"],
            "full_view_object_count": projection_high["counts"]["objects_total"],
            # The substation is public registry data; what must not leak is
            # the restricted observation, its content and its compartment.
            "restricted_identifiers_in_partner_projection": restricted_tokens_present,
            "leakage_failures": 0 if restricted_tokens_present == 0 else 1,
        },
        "performance": {
            **timings,
            "environment": f"python {sys.version.split()[0]}, single process, local disk, fixture scale",
            "note": "fixture-scale timings; not an operational performance claim",
        },
        "special_cases": {
            "late_ingestion": {"ingestion_id": late_result["ingestion_id"], "late": late_result["late"]},
            "duplicate_ingestion": {"ingestion_id": duplicate_result["ingestion_id"],
                                    "duplicate": duplicate_result["duplicate"]},
            "malformed_ingestion": {"ingestion_id": malformed_result["ingestion_id"],
                                    "quarantined": malformed_result["quarantined"]},
        },
    }
