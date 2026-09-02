"""Scenarios B and C over one shared fabric and two workbenches.

Live and synthetic content stay distinguishable: a captured public feed schema
and one real public document are marked as live in origin, while every placed
corridor object is synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curunir_operational.analytics import DeterministicRuleProvider, MockAssessmentProvider
from curunir_operational.canonical import sha256
from curunir_operational.missions import MissionWorkflow
from curunir_operational.projection import Projection
from curunir_operational.schema_registry import SchemaRegistry
from curunir_operational.store import MissionDataStore
from curunir_operational.workflow import WorkflowEngine

from . import config as a_config
from . import v2_config as v2
from . import v2_feeds as feeds
from .common import register_bundle, register_sources, source

INFRA_MARK = v2.RESTRICTED_INFRA


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sources(created: str):
    return [
        source("src-regsys", "SYSTEM", "CORRIDOR-REGISTRY", "Registry Office", created, v2.BASE_MARKING),
        source("src-logsys", "SYSTEM", "LOGSYS", "Logistics Office", created, v2.BASE_MARKING),
        source("src-gdacs", "SYSTEM", "GDACS", "GDACS (EC JRC / UN OCHA)", created, v2.BASE_MARKING),
        source("src-eng", "ORGANISATION", "ENGINEERING", "Corridor Engineering Cell", created, v2.ENGINEERING),
        source("src-fieldnet", "SENSOR", "FIELDNET", "Field Sensor Network", created, v2.BASE_MARKING),
        source("src-argus", "EVIDENCE_ADAPTER", "ARGUS-SI", "Curunír Source Intelligence", created, v2.PUBLIC_EVIDENCE),
    ]


def run_scenario_b(store_root: Path) -> dict[str, Any]:
    """Scenario B: an infrastructure cascade across the shared fabric."""
    at = a_config.at
    store = MissionDataStore.create(store_root, "infrastructure-cascade-v2", at(-1))
    registry = SchemaRegistry(store)
    register_sources(store, _sources(at(-1)), recorded_time=at(-1))
    executor = register_bundle(
        store, registry,
        schemas=[*a_config.SCHEMAS, v2.HAZARD_SCHEMA, v2.ENGINEERING_SCHEMA_V1, v2.ENGINEERING_SCHEMA_V1_1],
        mappings=[*a_config.MAPPINGS, v2.HAZARD_MAPPING, v2.ENGINEERING_MAPPING_V1],
        pipelines=[*a_config.PIPELINES, v2.HAZARD_PIPELINE, v2.ENGINEERING_PIPELINE_V1],
        workshops=[a_config.WORKSHOP, v2.INFRASTRUCTURE_WORKBENCH],
        recorded_time=at(-0.8))

    log: list[dict[str, Any]] = []

    def ingest(pipeline, body, source_id, source_time, received, **kw):
        result = executor.run(pipeline, body, source_id=source_id, source_time=source_time,
                              received_time=received, recorded_time=received, actor="connector", **kw)
        log.append(result)
        return result

    # Phase 1: a shared baseline both workbenches read.
    ingest("infrastructure-status", feeds.corridor_infrastructure(at(-24)), "src-regsys", at(-24), at(0.1))
    ingest("route-registry", feeds.corridor_routes(at(-24)), "src-regsys", at(-24), at(0.2))
    ingest("logistics-stock", feeds.stock_csv(at(0)), "src-logsys", at(0), at(0.5))
    ingest("movement-plan", a_config_movement(), "src-logsys", at(0.75), at(0.8))
    seq_phase1 = store.head()["event_count"]

    # Phase 2: real public-document evidence, labelled as such, via the adapter.
    live_bundle = feeds.load_evidence_bundle()
    ingest("argus-evidence-observations", json.dumps(live_bundle).encode(), "src-argus",
           live_bundle["assertion"]["report_time"], at(2.0), marking_override=v2.PUBLIC_EVIDENCE)

    # Phase 3: a synthetic corridor hazard in a live feed's shape.
    ingest("hazard-ingest", feeds.hazard_over_corridor("2026-03-02T04:00:00"), "src-gdacs",
           "2026-03-02T04:00:00+00:00", at(22.0))
    # A restricted engineering assessment.
    ingest("engineering-assessments",
           feeds.engineering_assessment_v1("ENG-1", "SUB-4", "DEGRADED", at(23.0), inspector="eng-ruiz"),
           "src-eng", at(23.0), at(23.2), marking_override=v2.ENGINEERING)
    # Conflicting field reports on the bridge.
    ingest("field-observations",
           feeds.field_observation("FN-3001", "BR-7", "infrastructure", "DAMAGED", at(23.4),
                                   detail="span deflection", lon=feeds.CORRIDOR["BR-7"][0], lat=feeds.CORRIDOR["BR-7"][1]),
           "src-fieldnet", at(23.4), at(23.5))
    ingest("civdef-bulletins",
           feeds.field_observation("CD-3002", "BR-7", "infrastructure", "OPERATIONAL", at(23.6),
                                   detail="district inspection nominal"),
           "src-fieldnet", at(23.6), at(23.7))
    # Wire the communications and route dependencies explicitly.
    _relate(store, "AFFECTS", "hazard-9900001", "infra-SUB-4", at(24.0))
    _relate(store, "DEPENDS_ON", "infra-BR-7", "infra-COMMS-2", at(24.0))
    _relate(store, "SUPPLIES", "infra-ALDEN", "infra-BR-7", at(24.0), rationale="repair materiel staging")

    # Phase 4: analytics over the shared fabric.
    projection4 = Projection(store, snapshot_time=at(25.0), staleness_hours=v2.STALENESS_HOURS)
    rules = DeterministicRuleProvider(store)
    proposals = rules.run(projection4, v2.CONTEXTS["rules"], recorded_time=at(25.0))
    MockAssessmentProvider(store).run(projection4, v2.CONTEXTS["rules"], "infra-BR-7", recorded_time=at(25.05))
    workflow = WorkflowEngine(store)
    for proposal in proposals:
        if proposal["proposal_type"] in ("ALERT_CANDIDATE", "RELATIONSHIP_CANDIDATE", "STATE_CANDIDATE",
                                         "RECOMMENDATION_CANDIDATE"):
            workflow.materialize(proposal, actor_id="workflow-policy", recorded_time=at(25.1))

    # Phase 5: the cross-workbench mission workflow.
    missions = MissionWorkflow(store)
    hazard_alert = store.find_alert_by_dedup("rule-hazard-impact:hazard-9900001")
    requirement = missions.open_requirement(
        mission_context="infrastructure cascade", question="Is Vessia Crossing Seven load-bearing after the hazard?",
        affected_ids=("infra-BR-7",), priority="HIGH",
        rationale="conflicting reports plus hazard proximity", required_evidence_type="ENGINEERING_ASSESSMENT",
        owning_role="ANALYST", closure_criteria="a reviewed engineering assessment of BR-7 exists",
        due_time=at(48.0), recorded_time=at(25.2), marking=INFRA_MARK, actor="cp-analyst",
        source_alert_id=hazard_alert)
    evidence_request = missions.request_evidence(
        requirement["requirement_id"], request_kind="UPDATED_INFRASTRUCTURE_REPORT",
        detail="commission an on-site engineering assessment of BR-7",
        affected_ids=("infra-BR-7",), due_time=at(40.0), recorded_time=at(25.3), marking=INFRA_MARK, actor="cp-analyst")
    task = missions.assign_task(
        assigned_role="ANALYST", assigned_actor="eng-ruiz", task_type="ASSESSMENT",
        affected_ids=("infra-BR-7",), required_action="inspect BR-7 load capacity",
        due_time=at(40.0), depends_on=(evidence_request["request_id"],),
        recorded_time=at(25.4), marking=v2.ENGINEERING, actor="cp-analyst")
    missions.transition("analyst_task", task["task_id"], "IN_PROGRESS", actor_id="eng-ruiz",
                        actor_kind="HUMAN", evidence_refs=(), note="en route", recorded_time=at(26.0),
                        marking=v2.ENGINEERING)
    # The assessment arrives as new evidence, completing the task and
    # answering the requirement.
    ingest("engineering-assessments",
           feeds.engineering_assessment_v1("ENG-2", "BR-7", "DEGRADED", at(30.0), inspector="eng-ruiz"),
           "src-eng", at(30.0), at(30.2), marking_override=v2.ENGINEERING)
    missions.transition("analyst_task", task["task_id"], "DONE", actor_id="eng-ruiz", actor_kind="HUMAN",
                        evidence_refs=("eng-ENG-2",), note="assessment filed: load-restricted",
                        recorded_time=at(30.3), marking=v2.ENGINEERING)
    missions.transition("evidence_request", evidence_request["request_id"], "FULFILLED", actor_id="eng-ruiz",
                        actor_kind="HUMAN", evidence_refs=("eng-ENG-2",), note="assessment delivered",
                        recorded_time=at(30.4), marking=INFRA_MARK)
    missions.transition("requirement", requirement["requirement_id"], "ANSWERED", actor_id="cp-analyst",
                        actor_kind="HUMAN", evidence_refs=("eng-ENG-2",),
                        note="BR-7 load-restricted per engineering assessment", recorded_time=at(30.5),
                        marking=INFRA_MARK)

    # A civil-protection decision restricts the logistics route.
    route_change = next((r for r in store.records_of("recommendation") if r["action_kind"] == "ROUTE_CHANGE"), None)
    decision_effect = None
    if route_change:
        decision = workflow.decide(route_change["recommendation_id"], context=v2.CONTEXTS["joint"],
                                   state="ACCEPTED", rationale="BR-7 load-restricted; route R1 restricted for heavy movement",
                                   recorded_time=at(31.0), marking=v2.BASE_MARKING)
        decision_effect = workflow.enact_decision_effect(
            decision["decision_id"], object_id="route-R1",
            attributes_patch={"status": "RESTRICTED", "restriction_reason": "engineering load restriction on BR-7"},
            rationale="civil-protection decision propagated to logistics route state",
            recorded_time=at(31.1), actor="workflow-policy")

    final = Projection(store, snapshot_time=at(33.0), staleness_hours=v2.STALENESS_HOURS)
    return {"store": store, "registry": registry, "executor": executor, "projection": final,
            "seq_phase1": seq_phase1, "proposals": proposals, "log": log,
            "requirement_id": requirement["requirement_id"], "task_id": task["task_id"],
            "evidence_request_id": evidence_request["request_id"],
            "decision_effect": decision_effect, "hazard_alert": hazard_alert}


def a_config_movement():
    return json.dumps({"movement_id": "RELIEF-201", "from_ref": "ALDEN", "to_ref": "BRUSKA",
                       "route_ref": "R1", "cargo": "repair", "status": "PLANNED",
                       "depart_time": a_config.at(30), "effective_time": a_config.at(0.75)}).encode()


def _relate(store, relation_type, source_id, target_id, recorded, rationale=""):
    from curunir_operational.contracts import RelationshipVersion, ProvenanceSummary
    rid = sha256((relation_type, source_id, target_id))[:20]
    store.append("RELATIONSHIP_VERSION_APPENDED",
                 RelationshipVersion(f"rel-{rid}", store.next_relationship_version(f"rel-{rid}"), relation_type,
                                     source_id, target_id, recorded, None, recorded, ("fixture",), "MAPPING",
                                     "UNKNOWN", "ACTIVE", v2.BASE_MARKING, ProvenanceSummary(mode="OPERATIONAL"),
                                     rationale),
                 recorded_time=recorded, actor="fixture")


def run_scenario_c(store_root: Path) -> dict[str, Any]:
    """Scenario C: false corroboration and schema drift."""
    at = a_config.at
    store = MissionDataStore.create(store_root, "false-corroboration-v2", at(-1))
    registry = SchemaRegistry(store)
    register_sources(store, _sources(at(-1)), recorded_time=at(-1))
    executor = register_bundle(
        store, registry,
        schemas=[*a_config.SCHEMAS, v2.ENGINEERING_SCHEMA_V1, v2.ENGINEERING_SCHEMA_V2],
        mappings=[*a_config.MAPPINGS, v2.ENGINEERING_MAPPING_V1, v2.ENGINEERING_MAPPING_V2],
        pipelines=[*a_config.PIPELINES, v2.ENGINEERING_PIPELINE_V1],
        recorded_time=at(-0.8))
    log = []

    def ingest(pipeline, body, source_id, source_time, received, **kw):
        result = executor.run(pipeline, body, source_id=source_id, source_time=source_time,
                              received_time=received, recorded_time=received, actor="connector", **kw)
        log.append(result)
        return result

    # Received times never decrease; source times vary independently so late
    # arrivals and corrections get exercised.
    ingest("infrastructure-status", feeds.corridor_infrastructure(at(-24)), "src-regsys", at(-24), at(0.1))
    # Four dependent reports off one basis, so the count looks higher than the
    # evidence is. Each names the full peer set, so the group id is stable.
    wire_keys = ["wire-a", "wire-b", "wire-c", "wire-d"]
    for index, (key, letter) in enumerate(zip(wire_keys, "ABCD")):
        peers = [k for k in wire_keys if k != key]
        ingest("argus-evidence-observations",
               feeds.argus_bundle(key, f"asrt-{letter}", key if key == "wire-a" else letter,
                                  "basis-strike", "BR-7", "DAMAGED", at(24.0 + index * 0.01),
                                  dependents=peers), "src-argus", at(24.0 + index * 0.01), at(24.10 + index * 0.01))
    # The first civil-defence report; corrected later.
    ingest("civdef-bulletins",
           feeds.field_observation("CD-4001", "BR-7", "infrastructure", "DAMAGED", at(24.5)),
           "src-fieldnet", at(24.5), at(24.60))
    # One genuinely independent source.
    ingest("argus-evidence-observations",
           feeds.argus_bundle("independent-eng", "asrt-IND", "Independent Engineer", "basis-independent",
                              "BR-7", "DAMAGED", at(25.0)), "src-argus", at(25.0), at(25.10))
    # A v1 engineering assessment.
    ingest("engineering-assessments",
           feeds.engineering_assessment_v1("ENG-C1", "BR-7", "DEGRADED", at(24.0)), "src-eng", at(24.0), at(25.30),
           marking_override=v2.ENGINEERING)
    # A restricted re-report of SUB-4, changing its marking.
    ingest("field-observations",
           feeds.field_observation("FN-4010", "SUB-4", "infrastructure", "DEGRADED", at(26.0),
                                   detail="restricted feeder note", lon=feeds.CORRIDOR["SUB-4"][0],
                                   lat=feeds.CORRIDOR["SUB-4"][1]),
           "src-fieldnet", at(26.0), at(26.10), marking_override=v2.RESTRICTED_INFRA)
    # A delayed correction of the earlier bulletin.
    ingest("civdef-bulletins",
           feeds.field_observation("CD-4001", "BR-7", "infrastructure", "PARTIALLY_DAMAGED", at(26.5),
                                   detail="delayed correction", corrects="CD-4001"),
           "src-fieldnet", at(26.5), at(26.60))
    # A duplicate publication of wire-a.
    ingest("argus-evidence-observations",
           feeds.argus_bundle("wire-a", "asrt-A", "wire-a", "basis-strike", "BR-7", "DAMAGED", at(24.0),
                              dependents=["wire-b", "wire-c", "wire-d"]), "src-argus", at(24.0), at(27.00))
    # Schema drift: a v2 pipeline and a v2 payload for the same asset.
    executor.register_pipeline(v2.ENGINEERING_PIPELINE_V2, recorded_time=at(27.0), actor="fixture")
    ingest("engineering-assessments",
           feeds.engineering_assessment_v2("ENG-C2", "BR-7", "RESTRICTED", at(27.5)), "src-eng", at(27.5), at(27.60),
           marking_override=v2.ENGINEERING)

    final = Projection(store, snapshot_time=at(30.0), staleness_hours=v2.STALENESS_HOURS)
    # A v2-shaped payload offered to the v1 schema without declaring its
    # version must come out invalid rather than be quietly coerced.
    v2_shaped_no_version = {"assessment_id": "X", "asset_ref": "BR-7", "condition": "CLOSED",
                            "assessed_time": at(28.0)}
    incompatible = registry.validate_payload("eng-assessment", "1.0", v2_shaped_no_version)
    # A payload declaring a version no schema provides.
    unsupported = registry.validate_payload("eng-assessment", "1.0",
                                            {"schema_version": "9.9", "assessment_id": "Y", "asset": "BR-7",
                                             "condition": "SOUND", "assessed_time": at(28.0)})
    # An old producer's v1 payload into the registered v1 schema.
    compatible = registry.validate_payload("eng-assessment", "1.0",
                                           {"assessment_id": "W", "asset": "BR-7", "condition": "SOUND",
                                            "assessed_time": at(28.0)})
    return {"store": store, "registry": registry, "projection": final, "log": log,
            "incompatible_validation": incompatible, "unsupported_validation": unsupported,
            "compatible_validation": compatible}
