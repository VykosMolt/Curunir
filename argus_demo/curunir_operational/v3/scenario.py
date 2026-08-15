"""Process-isolated synthetic V3 distributed and strategic scenarios."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from ..canonical import canonical_line, sha256
from ..write_observation import declared_label
from .models import (OperatorSession, OperatorStudyDefinition)
from .node import DistributedNode, joint_knowledge
from .operator import (FORBIDDEN_COLLECTION, OperatorStudyHarness, render_harness,
                       render_static_baseline, scripted_validation)
from .strategic import adapt_argus_evidence
from .ui import render_collaboration_workbench


MISSION = "PARTITIONED_REGIONAL_RESPONSE_V3"
NODE_IDS = ("LOGISTICS_NODE", "CIVIL_PROTECTION_NODE", "STRATEGIC_EVIDENCE_NODE")
TIMES = {index: f"2026-05-12T{index:02d}:00:00+00:00" for index in range(0, 20)}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_line(value) + "\n", encoding="utf-8")


def _run(argv: list[str], commands: list[dict[str, Any]], *, allow_failure: bool = False) -> dict[str, Any]:
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    output: dict[str, Any] = {}
    if completed.stdout.strip():
        try:
            output = json.loads(completed.stdout)
        except json.JSONDecodeError:
            output = {"stdout_category": "NON_JSON_OUTPUT"}
    record = {
        "argv": argv, "returncode": completed.returncode, "child_pid": output.get("pid"),
        "status": output.get("status"),
        "stderr": completed.stderr.strip()[:500],
    }
    commands.append(record)
    if completed.returncode and not allow_failure:
        raise RuntimeError(f"process-isolated command failed: {record}")
    return output


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "curunir_operational.v3.cli", *args]


def _node_identities() -> dict[str, dict[str, Any]]:
    return {
        "LOGISTICS_NODE": {"node_id": "LOGISTICS_NODE", "owning_authority": "NERETH_LOGISTICS_AUTHORITY",
                           "role": "LOGISTICS", "trust_state": "TRUSTED_TEST",
                           "sharing_scopes": [MISSION], "supported_protocol_versions": ["curunir-distributed-sync-v3"],
                           "status": "ACTIVE"},
        "CIVIL_PROTECTION_NODE": {"node_id": "CIVIL_PROTECTION_NODE", "owning_authority": "VELORIA_CIVIL_AUTHORITY",
                                  "role": "CIVIL_PROTECTION", "trust_state": "TRUSTED_TEST",
                                  "sharing_scopes": [MISSION], "supported_protocol_versions": ["curunir-distributed-sync-v3"],
                                  "status": "ACTIVE"},
        "STRATEGIC_EVIDENCE_NODE": {"node_id": "STRATEGIC_EVIDENCE_NODE", "owning_authority": "ORISON_STRATEGIC_AUTHORITY",
                                    "role": "STRATEGIC_EVIDENCE", "trust_state": "TRUSTED_TEST",
                                    "sharing_scopes": [MISSION], "supported_protocol_versions": ["curunir-distributed-sync-v3"],
                                    "status": "ACTIVE"},
    }


def _actors() -> list[dict[str, Any]]:
    return [
        {"actor_id": "logistics-analyst-v3", "display_name": "Synthetic Logistics Analyst",
         "organization": "NERETH_LOGISTICS_AUTHORITY", "roles": ["LOGISTICS_ANALYST"],
         "attributes": {}, "compartments": [], "mission_scopes": [MISSION], "authority_scopes": [],
         "valid_from": TIMES[0], "valid_to": None, "status": "ACTIVE",
         "identity_provider_ref": "curunir-v3-test-identity-provider"},
        {"actor_id": "civil-engineer-v3", "display_name": "Synthetic Civil Engineer",
         "organization": "VELORIA_CIVIL_AUTHORITY", "roles": ["CIVIL_PROTECTION_ENGINEER"],
         "attributes": {}, "compartments": ["ENGINEERING"], "mission_scopes": [MISSION],
         "authority_scopes": ["ENGINEERING_STATUS"], "valid_from": TIMES[0], "valid_to": None,
         "status": "ACTIVE", "identity_provider_ref": "curunir-v3-test-identity-provider"},
        {"actor_id": "strategic-analyst-v3", "display_name": "Synthetic Strategic Analyst",
         "organization": "ORISON_STRATEGIC_AUTHORITY", "roles": ["STRATEGIC_ANALYST"],
         "attributes": {}, "compartments": ["INSTITUTIONAL"], "mission_scopes": [MISSION],
         "authority_scopes": [], "valid_from": TIMES[0], "valid_to": None, "status": "ACTIVE",
         "identity_provider_ref": "curunir-v3-test-identity-provider"},
        {"actor_id": "joint-coordinator-v3", "display_name": "Synthetic Joint Coordinator",
         "organization": "JOINT_COORDINATION_AUTHORITY", "roles": ["JOINT_COORDINATOR"],
         "attributes": {}, "compartments": ["ENGINEERING", "INSTITUTIONAL"],
         "mission_scopes": [MISSION], "authority_scopes": ["CROSS_AUTHORITY", "ENGINEERING_STATUS"],
         "valid_from": TIMES[0], "valid_to": None, "status": "ACTIVE",
         "identity_provider_ref": "curunir-v3-test-identity-provider"},
        {"actor_id": "provider-v3", "display_name": "Deterministic Advisory Provider",
         "organization": "ORISON_STRATEGIC_AUTHORITY", "roles": ["PROVIDER"], "attributes": {},
         "compartments": [], "mission_scopes": [MISSION], "authority_scopes": [],
         "valid_from": TIMES[0], "valid_to": None, "status": "ACTIVE",
         "identity_provider_ref": "curunir-v3-test-identity-provider"},
    ]


def _mark(authority: str, *, restricted: str | None = None, sanitized: bool = False) -> dict[str, Any]:
    if restricted == "ENGINEERING":
        return {"owning_authority": authority, "compartments": ["ENGINEERING"],
                "releasability": ["VELORIA_CIVIL_ONLY"], "mission_scopes": [MISSION],
                "min_role": "CIVIL_PROTECTION_ENGINEER",
                "originator_controls": ["AUTHORITY:VELORIA_CIVIL_AUTHORITY"], "sanitized": False}
    if restricted == "INSTITUTIONAL":
        return {"owning_authority": authority, "compartments": ["INSTITUTIONAL"],
                "releasability": ["ORISON_INTERNAL_ONLY"], "mission_scopes": [MISSION],
                "min_role": "STRATEGIC_ANALYST",
                "originator_controls": ["AUTHORITY:ORISON_STRATEGIC_AUTHORITY"], "sanitized": False}
    return {"owning_authority": authority, "compartments": [], "releasability": ["MISSION_PARTNERS"],
            "mission_scopes": [MISSION], "min_role": "OBSERVER", "originator_controls": [],
            "sanitized": sanitized}


PARTNER_ACCESS = {"roles": ["JOINT_COORDINATOR"], "compartments": [],
                  "releasability": ["MISSION_PARTNERS"], "mission_scopes": [MISSION]}
FULL_ACCESS = {"roles": ["AUDITOR"], "compartments": ["ENGINEERING", "INSTITUTIONAL"],
               "releasability": ["MISSION_PARTNERS", "VELORIA_CIVIL_ONLY", "ORISON_INTERNAL_ONLY"],
               "mission_scopes": [MISSION]}


def _argus_bundle(source: str, basis: str, *, dependent_on: str | None = None,
                  review_state: str = "HUMAN_REVIEWED") -> dict[str, Any]:
    seed = sha256({"source": source, "basis": basis})
    relationships = [] if not dependent_on else [{"relationship_type": "DERIVED_FROM",
                                                    "source_object_a": source,
                                                    "source_object_b": dependent_on}]
    return {
        "source_object": {"source_object_id": source, "content_sha256": seed},
        "document": {"document_id": f"document-{source}", "authority_state": "AUTHORITATIVE"},
        "assertion": {"assertion_id": f"assertion-{source}", "evidence_basis_id": basis,
                      "review_state": review_state, "mapping_status": "PRECISE"},
        "identity": {"status": "IDENTITY_CONFIRMED"},
        "admission": {"independence_status": "DEPENDENT" if dependent_on else "INDEPENDENT",
                      "claim_basis_status": "EXPLICIT_BASIS", "dependencies": []},
        "source_relationships": relationships,
    }


class ScenarioRunner:
    def __init__(self, artifact_root: str | Path):
        self.artifact_root = Path(artifact_root)
        self.nodes_root = self.artifact_root / "03_nodes" / "scenario_stores"
        self.commands: list[dict[str, Any]] = []
        self.phase_events: dict[str, list[str]] = {}
        self.event_counter = 0
        self.identities = _node_identities()
        self.actors = _actors()
        self.keys = {subject: f"curunir-v3-deterministic-test-key::{subject}"
                     for subject in [*NODE_IDS, *(actor["actor_id"] for actor in self.actors)]}
        self.node_paths = {node_id: self.nodes_root / node_id.lower() for node_id in NODE_IDS}

    def initialize(self) -> None:
        configs = self.artifact_root / "03_nodes" / "configs"
        _write(configs / "partner_access.json", PARTNER_ACCESS)
        _write(configs / "full_access.json", FULL_ACCESS)
        for node_id in NODE_IDS:
            config = {"identity": self.identities[node_id], "actors": self.actors, "keys": self.keys,
                      "peers": {key: value for key, value in self.identities.items() if key != node_id}}
            path = configs / f"{node_id.lower()}.json"; _write(path, config)
            _run(_cli("init-node", "--store", str(self.node_paths[node_id]), "--config", str(path)), self.commands)

    def append(self, phase: str, node_id: str, *, actor: str, action: str, event_type: str,
               payload: Mapping[str, Any], marking: Mapping[str, Any], time: str,
               parents: tuple[str, ...] = (), allow_failure: bool = False) -> str | None:
        self.event_counter += 1
        nonce = f"scenario-{self.event_counter:04d}-{node_id.lower()}"
        spec = {"actor_id": actor, "action_type": action, "event_type": event_type,
                "payload": dict(payload), "marking": dict(marking), "recorded_time": time,
                "nonce": nonce, "valid_from": time, "valid_to": None,
                "parent_event_ids": list(parents), "object_ref": str(payload.get("subject_id", "")),
                "object_owner": "", "mission_scope": MISSION, "conflict_type": ""}
        path = self.artifact_root / "03_nodes" / "event_specs" / f"{self.event_counter:04d}_{node_id.lower()}.json"
        _write(path, spec)
        output = _run(_cli("append-event", "--store", str(self.node_paths[node_id]), "--spec", str(path)),
                      self.commands, allow_failure=allow_failure)
        event_id = output.get("event_id")
        if event_id:
            self.phase_events.setdefault(phase, []).append(event_id)
        return event_id

    def synchronize(self, label: str, source_id: str, destination_id: str, *,
                    reverse: bool = False, allow_failure: bool = False) -> dict[str, Any]:
        root = self.artifact_root / "04_sync" / label / f"{source_id.lower()}_to_{destination_id.lower()}"
        access_path = self.artifact_root / "03_nodes" / "configs" / "partner_access.json"
        offer, request, bundle, receipt = root / "offer.json", root / "request.json", root / "bundle.json", root / "receipt.json"
        _run(_cli("offer", "--store", str(self.node_paths[source_id]), "--destination", destination_id,
                  "--access", str(access_path), "--out", str(offer)), self.commands)
        _run(_cli("request", "--store", str(self.node_paths[destination_id]), "--source", source_id,
                  "--access", str(access_path), "--scope", MISSION, "--out", str(request)), self.commands)
        build_args = _cli("build-bundle", "--store", str(self.node_paths[source_id]),
                          "--request", str(request), "--out", str(bundle), "--time", TIMES[9])
        if reverse:
            build_args.append("--reverse")
        _run(build_args, self.commands, allow_failure=allow_failure)
        if not bundle.exists():
            return {"status": "BUILD_REJECTED"}
        imported = _run(_cli("import-bundle", "--store", str(self.node_paths[destination_id]),
                             "--bundle", str(bundle), "--time", TIMES[9], "--receipt-out", str(receipt)),
                        self.commands, allow_failure=allow_failure)
        if receipt.exists():
            _run(_cli("ack-receipt", "--store", str(self.node_paths[source_id]),
                      "--bundle", str(bundle), "--receipt", str(receipt)), self.commands)
        return {"status": imported.get("status"), "bundle": str(bundle), "receipt": str(receipt)}

    def sync_all(self, label: str, *, reverse_strategic_to_logistics: bool = False) -> None:
        for source in NODE_IDS:
            for destination in NODE_IDS:
                if source == destination:
                    continue
                self.synchronize(label, source, destination,
                                 reverse=reverse_strategic_to_logistics and source == "STRATEGIC_EVIDENCE_NODE"
                                 and destination == "LOGISTICS_NODE")

    def phase1_baseline(self) -> None:
        log = _mark("NERETH_LOGISTICS_AUTHORITY")
        civ = _mark("VELORIA_CIVIL_AUTHORITY")
        strat = _mark("ORISON_STRATEGIC_AUTHORITY")
        self.append("phase1", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                    event_type="ROUTE_STATUS", payload={"record_type": "route_status", "subject_id": "route-R1",
                    "status": "OPEN", "geometry": {"type": "LineString", "coordinates": [[14.1, 45.1], [14.6, 45.4]]},
                    "evidence_snapshot": ["baseline-survey"]}, marking=log, time=TIMES[1])
        for subject, value, geometry in (
            ("depot-Merath", "AVAILABLE", {"type": "Point", "coordinates": [14.05, 45.08]}),
            ("stock-medical-Merath", "320_KITS", None),
            ("movement-relief-17", "PLANNED", {"type": "Point", "coordinates": [14.2, 45.18]}),
        ):
            self.append("phase1", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                        event_type="BASELINE_OBJECT", payload={"record_type": "observation", "subject_id": subject,
                        "value": value, "geometry": geometry, "evidence_snapshot": ["baseline-manifest"]},
                        marking=log, time=TIMES[1])
        self.append("phase1", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
                    action="MARK_INFRASTRUCTURE_STATUS", event_type="INFRASTRUCTURE_STATUS",
                    payload={"record_type": "infrastructure_status", "subject_id": "bridge-Elden",
                    "status": "OPERATIONAL", "geometry": {"type": "Point", "coordinates": [14.42, 45.31]},
                    "evidence_snapshot": ["baseline-engineering-inspection"]}, marking=civ, time=TIMES[1])
        self.append("phase1", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
                    action="MARK_INFRASTRUCTURE_STATUS", event_type="INFRASTRUCTURE_STATUS",
                    payload={"record_type": "infrastructure_status", "subject_id": "substation-Tovan",
                    "status": "OPERATIONAL", "geometry": {"type": "Point", "coordinates": [14.55, 45.37]},
                    "evidence_snapshot": ["baseline-grid-report"]}, marking=civ, time=TIMES[1])
        evidence = adapt_argus_evidence(_argus_bundle("argus-public-baseline", "basis-public-baseline"))
        self.append("phase1", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
                    action="ATTACH_PUBLIC_EVIDENCE", event_type="PUBLIC_EVIDENCE_REFERENCE",
                    payload={"record_type": "public_evidence_reference", "subject_id": "public-baseline-1",
                    "argus_evidence_refs": [evidence], "evidence_snapshot": [evidence["evidence_basis_id"]]},
                    marking=strat, time=TIMES[1])
        self.sync_all("phase1_synchronized_baseline")

    def phase2_and_3_partition(self) -> dict[str, str]:
        log = _mark("NERETH_LOGISTICS_AUTHORITY"); civ = _mark("VELORIA_CIVIL_AUTHORITY")
        strat = _mark("ORISON_STRATEGIC_AUTHORITY"); restricted_civ = _mark("VELORIA_CIVIL_AUTHORITY", restricted="ENGINEERING")
        restricted_strat = _mark("ORISON_STRATEGIC_AUTHORITY", restricted="INSTITUTIONAL")
        stale = self.append("phase2", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                            event_type="STALE_ROUTE_REPORT", payload={"record_type": "route_status",
                            "subject_id": "route-R1", "status": "OPEN", "freshness": "STALE",
                            "geometry": {"type": "LineString", "coordinates": [[14.1, 45.1], [14.6, 45.4]]},
                            "evidence_snapshot": ["radio-report-stale"]}, marking=log, time=TIMES[4])
        self.append("phase2", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                    event_type="MOVEMENT_PLAN", payload={"record_type": "planning_assumption",
                    "subject_id": "movement-relief-17", "value": "USE_ROUTE_R1", "basis_state": "STALE",
                    "evidence_snapshot": [stale]}, marking=log, time=TIMES[4])
        log_task = self.append("phase2", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="TASK_ASSIGNMENT",
                               event_type="TASK_ASSIGNED", payload={"record_type": "task_assignment",
                               "subject_id": "joint-task-bridge-check", "task_id": "joint-task-bridge-check",
                               "assigned_to": "logistics-route-team", "state": "ASSIGNED",
                               "evidence_snapshot": [stale]}, marking=log, time=TIMES[5])
        self.append("phase2", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                    event_type="ANNOTATION", payload={"record_type": "annotation", "subject_id": "route-R1",
                    "annotation_id": "annotation-log-1", "body": "Proceeding on stale open report",
                    "state": "SHARED", "evidence_snapshot": [stale]}, marking=log, time=TIMES[5])
        secret = self.append("phase2", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
                             action="SUBMIT_ENGINEERING_EVIDENCE", event_type="ENGINEERING_ASSESSMENT",
                             payload={"record_type": "observation", "subject_id": "engineering-assessment-Elden",
                             "value": "STRUCTURAL_MOVEMENT_DETECTED", "exact_location": "fictional-pier-2",
                             "evidence_snapshot": ["restricted-sensor-trace"]}, marking=restricted_civ, time=TIMES[4])
        bridge = self.append("phase2", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
                             action="MARK_INFRASTRUCTURE_STATUS", event_type="INFRASTRUCTURE_STATUS",
                             payload={"record_type": "infrastructure_status", "subject_id": "bridge-Elden",
                             "status": "UNSAFE", "geometry": {"type": "Point", "coordinates": [14.42, 45.31]},
                             "evidence_snapshot": [secret]}, marking=restricted_civ, time=TIMES[5], parents=(secret,))
        self.append("phase2", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3", action="ANNOTATION",
                    event_type="ALERT", payload={"record_type": "alert", "subject_id": "alert-bridge-unsafe",
                    "severity": "HIGH", "state": "OPEN", "evidence_snapshot": [bridge]},
                    marking=restricted_civ, time=TIMES[5], parents=(bridge,))
        self.append("phase2", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3", action="INFORMATION_REQUIREMENT",
                    event_type="INFORMATION_REQUIREMENT", payload={"record_type": "information_requirement",
                    "subject_id": "ir-bridge-capacity", "question": "Can emergency medical vehicles cross?",
                    "state": "OPEN", "evidence_snapshot": [bridge]}, marking=restricted_civ, time=TIMES[5])
        # Strategic scenario: derivatives share one basis, an official source is independent,
        # and a misleading report is later retracted.
        derivative_a = adapt_argus_evidence(_argus_bundle("argus-derivative-a", "basis-bulletin",
                                                          dependent_on="argus-derivative-b"))
        derivative_b = adapt_argus_evidence(_argus_bundle("argus-derivative-b", "basis-bulletin",
                                                          dependent_on="argus-derivative-a"))
        official = adapt_argus_evidence(_argus_bundle("argus-official-nereth", "basis-official"))
        misleading = adapt_argus_evidence(_argus_bundle("argus-unofficial-rumor", "basis-rumor",
                                                        review_state="UNREVIEWED"))
        self.append("phase2", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
                    action="ATTACH_PUBLIC_EVIDENCE", event_type="SOURCE_DEPENDENCE_GROUP",
                    payload={"record_type": "source_dependence_group", "subject_id": "dependence-bulletin",
                    "group_id": "dependence-bulletin", "members": ["argus-derivative-a", "argus-derivative-b"],
                    "evidence_basis": "basis-bulletin", "independent_basis_count": 1,
                    "interpretation": "source count is not corroboration"}, marking=strat, time=TIMES[4])
        indicator_events = []
        for index, (statement, ref, group, correction) in enumerate((
            ("Public bulletin suggests possible fuel supply disruption", derivative_a, "dependence-bulletin", "ACTIVE"),
            ("Derivative publication repeats the same bulletin", derivative_b, "dependence-bulletin", "ACTIVE"),
            ("Independent official source confirms a localized fuel delay", official, None, "ACTIVE"),
            ("Unofficial report claims total medical-supply collapse", misleading, None, "ACTIVE"),
        ), start=1):
            indicator_events.append(self.append("phase2", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
                action="ATTACH_PUBLIC_EVIDENCE", event_type="INDICATOR", payload={"record_type": "indicator",
                "subject_id": f"indicator-{index}", "indicator_id": f"indicator-{index}", "statement": statement,
                "argus_evidence_refs": [ref], "source_dependence_group": group,
                "correction_state": correction, "evidence_snapshot": [ref["evidence_basis_id"]]},
                marking=strat, time=TIMES[5]))
        restricted_assessment = self.append("phase2", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
            action="ATTACH_PUBLIC_EVIDENCE", event_type="INSTITUTIONAL_ASSESSMENT",
            payload={"record_type": "observation", "subject_id": "institutional-assessment-1",
                     "value": "DEPENDENCY_REVIEW_REQUIRED", "evidence_snapshot": ["institutional-note"]},
            marking=restricted_strat, time=TIMES[5])
        hypothesis_events = {}
        hypotheses = (
            ("hypothesis-localized", "Localized fuel delay may affect route R1", "VIABLE", ["indicator-1", "indicator-3"], ["indicator-4"]),
            ("hypothesis-collapse", "All medical supply has collapsed", "REJECTED", ["indicator-4"], ["indicator-3"]),
            ("hypothesis-demand", "Medical demand may exceed Tovan capacity", "UNRESOLVED", ["indicator-1"], []),
        )
        for hypothesis_id, statement, state, support, against in hypotheses:
            hypothesis_events[hypothesis_id] = self.append(
                "phase2", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3", action="ANNOTATION",
                event_type="STRATEGIC_HYPOTHESIS", payload={"record_type": "strategic_hypothesis",
                "subject_id": hypothesis_id, "hypothesis_id": hypothesis_id, "statement": statement,
                "scope": MISSION, "valid_interval": {"from": TIMES[4], "to": None},
                "supporting_indicators": support, "contradicting_indicators": against,
                "evidence_bases": ["basis-bulletin", "basis-official"],
                "source_dependence": ["dependence-bulletin"], "assumptions": ["demand remains unknown"],
                "confidence_dimensions": {"evidence_strength": "MIXED", "calibration": "NOT_CALIBRATED"},
                "analyst_state": state, "review_state": "UNDER_REVIEW" if state == "UNRESOLVED" else "PEER_REVIEWED",
                "operational_implications": [], "history": [], "evidence_snapshot": indicator_events},
                marking=strat, time=TIMES[6])
        self.append("phase2", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3", action="INFORMATION_REQUIREMENT",
                    event_type="INFORMATION_REQUIREMENT", payload={"record_type": "information_requirement",
                    "subject_id": "ir-medical-demand", "question": "Will medical demand exceed current stock?",
                    "state": "OPEN", "evidence_snapshot": indicator_events}, marking=strat, time=TIMES[6])
        # Phase 3 concurrent actions.
        recommendation = self.append("phase3", "LOGISTICS_NODE", actor="logistics-analyst-v3",
            action="RECOMMENDATION_DISPOSITION", event_type="RECOMMENDATION", payload={"record_type": "recommendation",
            "subject_id": "recommendation-use-R1", "recommendation_id": "recommendation-use-R1",
            "value": "USE_ROUTE_R1", "state": "PROPOSED", "basis_state": "STALE",
            "evidence_snapshot": [stale]}, marking=log, time=TIMES[7])
        civil_task = self.append("phase3", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
            action="TASK_ASSIGNMENT", event_type="TASK_ASSIGNED", payload={"record_type": "task_assignment",
            "subject_id": "joint-task-bridge-check", "task_id": "joint-task-bridge-check",
            "assigned_to": "civil-bridge-team", "state": "ASSIGNED", "evidence_snapshot": [bridge]},
            marking=civ, time=TIMES[7])
        sanitized_route = self.append("phase3", "CIVIL_PROTECTION_NODE", actor="civil-engineer-v3",
            action="MARK_INFRASTRUCTURE_STATUS", event_type="SANITIZED_ROUTE_RESTRICTION",
            payload={"record_type": "route_status", "subject_id": "route-R1", "status": "RESTRICTED",
            "reason_category": "ENGINEERING_RESTRICTION", "restricted_basis_ref": f"restricted-basis-{sha256(secret)[:16]}",
            "geometry": {"type": "LineString", "coordinates": [[14.1, 45.1], [14.6, 45.4]]},
            "evidence_snapshot": [], "basis_disclosed": False}, marking=_mark("VELORIA_CIVIL_AUTHORITY", sanitized=True),
            time=TIMES[7], parents=(secret,))
        self.append("phase3", "LOGISTICS_NODE", actor="joint-coordinator-v3", action="ACCESS_POLICY_CHANGE",
                    event_type="ACCESS_POLICY", payload={"record_type": "access_policy", "subject_id": "route-sharing-policy",
                    "policy_id": "route-sharing-policy", "releasability": ["MISSION_PARTNERS"], "effect": "CONTINUE"},
                    marking=log, time=TIMES[7])
        self.append("phase3", "CIVIL_PROTECTION_NODE", actor="joint-coordinator-v3", action="ACCESS_POLICY_CHANGE",
                    event_type="ACCESS_POLICY", payload={"record_type": "access_policy", "subject_id": "route-sharing-policy",
                    "policy_id": "route-sharing-policy", "releasability": ["VELORIA_CIVIL_ONLY"],
                    "effect": "FAIL_CLOSED_PENDING_REVIEW"}, marking=civ, time=TIMES[7])
        correction = self.append("phase3", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
            action="ATTACH_PUBLIC_EVIDENCE", event_type="CORRECTION", payload={"record_type": "correction",
            "subject_id": "indicator-1", "correction_of": indicator_events[0],
            "correcting_authority": "ORISON_PUBLIC_BULLETIN_SERVICE", "value": "LOCALIZED_DELAY_ONLY",
            "synchronization_history": [], "evidence_snapshot": ["basis-bulletin"]}, marking=strat, time=TIMES[7])
        retraction = self.append("phase3", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
            action="ATTACH_PUBLIC_EVIDENCE", event_type="RETRACTION", payload={"record_type": "retraction",
            "subject_id": "indicator-4", "correction_of": indicator_events[3],
            "correcting_authority": "UNOFFICIAL_PUBLISHER", "value": "RETRACTED",
            "synchronization_history": [], "evidence_snapshot": ["basis-rumor"]}, marking=strat, time=TIMES[7])
        self.append("phase3", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3",
                    action="ATTACH_PUBLIC_EVIDENCE", event_type="SOURCE_DEPENDENCE_UPDATED",
                    payload={"record_type": "source_dependence_group", "subject_id": "dependence-bulletin-update",
                    "group_id": "dependence-bulletin", "members": ["argus-derivative-a", "argus-derivative-b", "argus-derivative-c"],
                    "evidence_basis": "basis-bulletin", "independent_basis_count": 1,
                    "interpretation": "three publications still represent one basis"}, marking=strat, time=TIMES[7])
        implication = self.append("phase3", "STRATEGIC_EVIDENCE_NODE", actor="provider-v3",
            action="PROPOSE_IMPLICATION", event_type="OPERATIONAL_IMPLICATION", payload={"record_type": "operational_implication",
            "subject_id": "implication-monitor-R1", "implication_id": "implication-monitor-R1",
            "source_hypothesis_id": "hypothesis-localized", "implication_type": "ROUTE_RISK",
            "proposed_consequence": "Monitor route R1", "status": "DISPUTED", "direct_operational_mutation": False,
            "operational_state_mutated": False, "evidence_snapshot": [correction]}, marking=strat, time=TIMES[7])
        for target, ir, parent in (("SHARED_LOGISTICS_VISIBILITY_WORKBENCH_V1", "ir-log-fuel", implication),
                                   ("INFRASTRUCTURE_RESILIENCE_AND_CIVIL_PROTECTION_WORKBENCH_V2", "ir-civ-dependency", restricted_assessment)):
            self.append("phase3", "STRATEGIC_EVIDENCE_NODE", actor="strategic-analyst-v3", action="HANDOFF",
                        event_type="EVIDENCE_HANDOFF", payload={"record_type": "evidence_handoff",
                        "subject_id": f"handoff-{ir}", "handoff_id": f"handoff-{ir}",
                        "argus_evidence_refs": [official], "strategic_subject_id": "hypothesis-localized",
                        "receiving_workbench": target, "information_requirement_id": ir,
                        "review_state": "PEER_REVIEWED", "unresolved_state": "OPEN_INFORMATION_REQUIREMENT",
                        "provenance_preserved": True, "evidence_snapshot": ["basis-official"]},
                        marking=strat, time=TIMES[8], parents=(parent,))
        # A valid historical action precedes local actor revocation.
        historical = self.append("phase3", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                                 event_type="ANNOTATION", payload={"record_type": "annotation", "subject_id": "route-R1",
                                 "annotation_id": "valid-before-revocation", "body": "Valid historical action",
                                 "state": "SHARED", "evidence_snapshot": [recommendation]}, marking=log, time=TIMES[8])
        revocation = {"revocation_id": "revocation-logistics-analyst", "subject_type": "ACTOR",
                      "subject_id": "logistics-analyst-v3", "effective_time": "2026-05-12T08:30:00+00:00",
                      "recorded_time": "2026-05-12T08:30:00+00:00", "authority": "JOINT_COORDINATION_AUTHORITY",
                      "rationale": "synthetic role rotation"}
        rev_path = self.artifact_root / "03_nodes" / "actor_revocation.json"; _write(rev_path, revocation)
        _run(_cli("revoke", "--store", str(self.node_paths["LOGISTICS_NODE"]), "--record", str(rev_path)), self.commands)
        return {"log_task": log_task, "civil_task": civil_task, "recommendation": recommendation,
                "sanitized_route": sanitized_route, "historical_action": historical,
                "correction": correction, "retraction": retraction}

    def phase4_reconnect(self) -> None:
        self.sync_all("phase4_reconnection", reverse_strategic_to_logistics=True)
        # A stale-base request is rejected before transmission.  The error is
        # category-only and discloses neither a count nor a hidden identifier.
        request_path = self.artifact_root / "04_sync" / "phase4_stale_base_probe" / "request.json"
        _write(request_path, {"destination_node": "CIVIL_PROTECTION_NODE",
                              "known_causal_context": {"LOGISTICS_NODE": "GENESIS"},
                              "requested_scope": [MISSION], "access_and_releasability_context": PARTNER_ACCESS,
                              "supported_schemas": ["curunir-distributed-event-v3"],
                              "maximum_bundle_constraints": {"max_events": 1000, "max_bytes": 10000000}})
        output = _run(_cli("build-bundle", "--store", str(self.node_paths["LOGISTICS_NODE"]),
                          "--request", str(request_path), "--out",
                          str(request_path.parent / "must_not_exist_bundle.json"), "--time", TIMES[9]),
                      self.commands, allow_failure=True)
        _write(request_path.parent / "stale_base_result.json",
               {"expected": "STALE_BASE_OR_MISSING_BASE_REJECTED", "return_status": output.get("status", "REJECTED"),
                "bundle_created": (request_path.parent / "must_not_exist_bundle.json").exists()})

    def phase5_resolution(self) -> list[str]:
        resolutions = []
        targets = (("CIVIL_PROTECTION_NODE", "CONTRADICTORY_OPERATIONAL_STATUS", {"status": "RESTRICTED"}),
                   ("LOGISTICS_NODE", "TASK_ASSIGNMENT_CONFLICT", {"assigned_to": "civil-bridge-team"}),
                   ("CIVIL_PROTECTION_NODE", "ACCESS_POLICY_CONFLICT", {"effect": "FAIL_CLOSED_SANITIZED_ONLY"}))
        marking_path = self.artifact_root / "05_conflicts" / "resolution_marking.json"
        resolution_marking = _mark("JOINT_COORDINATION_AUTHORITY"); _write(marking_path, resolution_marking)
        for index, (node_id, conflict_type, value) in enumerate(targets, start=1):
            node = DistributedNode(self.node_paths[node_id])
            conflict = next(item for item in node.conflicts() if item.conflict_type == conflict_type)
            resolution_path = self.artifact_root / "05_conflicts" / f"resolution_{index}.json"; _write(resolution_path, value)
            output = _run(_cli("resolve-conflict", "--store", str(self.node_paths[node_id]),
                              "--conflict", conflict.conflict_id, "--actor", "joint-coordinator-v3",
                              "--resolution", str(resolution_path), "--marking", str(marking_path),
                              "--time", TIMES[10 + index], "--nonce", f"resolution-{index}"), self.commands)
            resolutions.append(output["event_id"])
        self.append("phase5", "LOGISTICS_NODE", actor="joint-coordinator-v3", action="RECOMMENDATION_DISPOSITION",
                    event_type="RECOMMENDATION_DISPOSITION", payload={"record_type": "recommendation_disposition",
                    "subject_id": "recommendation-use-R1", "recommendation_id": "recommendation-use-R1",
                    "disposition": "WITHDRAWN_STALE", "evidence_snapshot": resolutions},
                    marking=_mark("NERETH_LOGISTICS_AUTHORITY"), time=TIMES[14])
        self.append("phase5", "CIVIL_PROTECTION_NODE", actor="joint-coordinator-v3", action="DECISION",
                    event_type="JOINT_DECISION", payload={"record_type": "decision", "subject_id": "decision-route-R1",
                    "decision_id": "decision-route-R1", "disposition": "ACCEPTED",
                    "rationale": "Use sanitized restriction; preserve engineering basis locally",
                    "evidence_snapshot": resolutions, "policy_version": "curunir-bounded-authorization-v3"},
                    marking=_mark("JOINT_COORDINATION_AUTHORITY"), time=TIMES[15])
        # Revoked actor is refused; its prior event remains in the log.
        self.append("phase5", "LOGISTICS_NODE", actor="logistics-analyst-v3", action="ANNOTATION",
                    event_type="ANNOTATION", payload={"record_type": "annotation", "subject_id": "route-R1",
                    "annotation_id": "must-not-append", "body": "future action", "state": "SHARED",
                    "evidence_snapshot": []}, marking=_mark("NERETH_LOGISTICS_AUTHORITY"),
                    time=TIMES[16], allow_failure=True)
        self.sync_all("phase5_resolutions")
        return resolutions

    def outputs(self, references: Mapping[str, str], resolutions: list[str]) -> dict[str, Any]:
        full_path = self.artifact_root / "03_nodes" / "configs" / "full_access.json"
        partner_path = self.artifact_root / "03_nodes" / "configs" / "partner_access.json"
        projections = {}
        semantics = {}
        for node_id in NODE_IDS:
            output = self.artifact_root / "03_nodes" / f"projection_{node_id.lower()}.json"
            _run(_cli("project", "--store", str(self.node_paths[node_id]), "--access", str(full_path),
                      "--out", str(output)), self.commands)
            node = DistributedNode(self.node_paths[node_id])
            projections[node_id] = json.loads(output.read_text(encoding="utf-8"))
            semantics[node_id] = node.semantic_projection_hash(PARTNER_ACCESS)
            _write(self.artifact_root / "05_conflicts" / f"conflicts_{node_id.lower()}.json",
                   [item.to_record() for item in node.conflicts()])
        nodes = [DistributedNode(self.node_paths[node_id]) for node_id in NODE_IDS]
        joint = joint_knowledge(nodes, FULL_ACCESS)
        _write(self.artifact_root / "03_nodes" / "joint_projection.json", joint)
        # Temporal distributed-knowledge examples.
        temporal = {
            "world_state_at_partition": nodes[0].projection(FULL_ACCESS, valid_at=TIMES[7], known_at=TIMES[8]),
            "logistics_knew_before_reconnect": nodes[0].projection(FULL_ACCESS, known_at=TIMES[8]),
            "logistics_knew_after_reconnect": nodes[0].projection(FULL_ACCESS, known_at=TIMES[9]),
            "joint_known_after_resolution": joint_knowledge(nodes, FULL_ACCESS, known_at=TIMES[16]),
            "future_correction_excluded_before_arrival": references["correction"] not in {
                event["event_id"] for event in nodes[0].projection(FULL_ACCESS, known_at=TIMES[8])["union_records"]},
            "valid_historical_revoked_actor_action_present": references["historical_action"] in {
                event.event_id for event in nodes[0].events()},
            "revoked_actor_future_action_present": any(event.payload.get("annotation_id") == "must-not-append"
                                                        for event in nodes[0].events()),
            "classification": "FULL_BITEMPORAL_QUERY_WITH_DISTRIBUTED_KNOWLEDGE",
        }
        _write(self.artifact_root / "03_nodes" / "temporal_query_examples.json", temporal)
        # Three browser workbenches, including lower-access sanitized logistics.
        ui_root = self.artifact_root / "06_strategic_workbench"
        ui_root.mkdir(parents=True, exist_ok=True)
        logistics_lower = nodes[0].projection(PARTNER_ACCESS)
        logistics_html = render_collaboration_workbench(
            logistics_lower, title="Shared logistics visibility — distributed V3",
            workbench_id="SHARED_LOGISTICS_VISIBILITY_WORKBENCH_V3",
            record_types=("route_status", "stock_availability", "planning_assumption", "observation",
                          "task_assignment", "task_acceptance", "task_completion", "annotation",
                          "recommendation", "recommendation_disposition", "decision", "evidence_handoff"))
        civil_html = render_collaboration_workbench(
            nodes[1].projection(FULL_ACCESS), title="Infrastructure resilience and civil protection — distributed V3",
            workbench_id="INFRASTRUCTURE_RESILIENCE_AND_CIVIL_PROTECTION_WORKBENCH_V3",
            record_types=("route_status", "infrastructure_status", "observation", "alert",
                          "information_requirement", "task_assignment", "decision", "evidence_handoff"))
        (ui_root / "logistics_workbench.html").write_text(logistics_html, encoding="utf-8")
        (ui_root / "civil_protection_workbench.html").write_text(civil_html, encoding="utf-8")
        strategic_json, strategic_html = ui_root / "strategic_workbench.json", ui_root / "strategic_workbench.html"
        _run(_cli("render-strategic", "--store", str(self.node_paths["STRATEGIC_EVIDENCE_NODE"]),
                  "--access", str(full_path), "--json-out", str(strategic_json), "--html-out", str(strategic_html)),
             self.commands)
        # Open export/import replay for every independently persisted node.
        replay_report = {}
        for node_id, node in zip(NODE_IDS, nodes):
            export = self.artifact_root / "12_final" / "open_exports" / node_id.lower()
            replay = self.artifact_root / "12_final" / "replay_stores" / node_id.lower()
            manifest = node.export_open(export); imported = DistributedNode.import_open(export, replay)
            replay_report[node_id] = {
                "source_semantic_hash": node.semantic_projection_hash(PARTNER_ACCESS),
                "replay_semantic_hash": imported.semantic_projection_hash(PARTNER_ACCESS),
                "equal": node.semantic_projection_hash(PARTNER_ACCESS) == imported.semantic_projection_hash(PARTNER_ACCESS),
                "provider_reinvocations": manifest["provider_reinvocations"],
            }
        _write(self.artifact_root / "12_final" / "distributed_replay_report.json", replay_report)
        convergence = {
            "semantic_hashes": semantics, "authorized_convergence": len(set(semantics.values())) == 1,
            "definition": "same mission-partner-authorized event set + compatible policy + equivalent access => same conflict-aware semantics",
            "unresolved_hypothesis_retained": any(
                event.payload.get("hypothesis_id") == "hypothesis-demand" and event.payload.get("analyst_state") == "UNRESOLVED"
                for event in nodes[2].events()),
            "resolution_events": resolutions,
            "replay": replay_report,
        }
        _write(self.artifact_root / "12_final" / "convergence_report.json", convergence)
        return {"projections": projections, "convergence": convergence, "temporal": temporal,
                "joint": joint, "replay": replay_report}

    def operator_harness(self) -> dict[str, Any]:
        tasks = (
            {"task_id": "conflict-recognition", "prompt": "Identify the route status after reconnection.",
             "expected_answer": {"fields": {"route_state": "CONFLICT"}},
             "evidence_path": ["route-R1", "CONTRADICTORY_OPERATIONAL_STATUS"],
             "policy_constraints": ["REVEAL_RESTRICTED_BASIS"]},
            {"task_id": "dependence-recognition", "prompt": "How many independent bases support the bulletin derivatives?",
             "expected_answer": {"fields": {"independent_basis_count": 1}},
             "evidence_path": ["dependence-bulletin", "basis-bulletin"], "policy_constraints": []},
            {"task_id": "stale-recognition", "prompt": "Was the route recommendation based on current evidence?",
             "expected_answer": {"fields": {"basis_state": "STALE"}},
             "evidence_path": ["recommendation-use-R1", "radio-report-stale"], "policy_constraints": []},
            {"task_id": "hypothesis-boundary", "prompt": "Is medical demand resolved?",
             "expected_answer": {"fields": {"state": "UNRESOLVED"}},
             "evidence_path": ["hypothesis-demand"], "policy_constraints": []},
        )
        definition = OperatorStudyDefinition(
            "CURUNIR_OPERATOR_STUDY_V3", "3.0",
            ("TRAINING", "EVALUATION", "BASELINE_COMPARISON", "AFTER_ACTION_REVIEW"),
            ("STATIC_ARTIFACT_BASELINE", "CURUNIR_WORKBENCH"), tasks,
            ("correctness", "completion", "time", "evidence-trace correctness",
             "source-dependence recognition", "conflict recognition", "stale-data recognition",
             "observed versus inferred distinction", "inappropriate certainty", "confidence calibration",
             "report quality", "policy compliance", "workload", "trust", "usability issues"),
            FORBIDDEN_COLLECTION)
        root = self.artifact_root / "07_operator_harness"
        root.mkdir(parents=True, exist_ok=True)
        harness = OperatorStudyHarness.initialize(root / "study", definition)
        baseline = render_static_baseline(
            definition, "Route R1 is disputed after partition; bridge basis is restricted.",
            [{"object": "route-R1", "state": "CONFLICT"}, {"object": "bridge-Elden", "state": "RESTRICTED"}],
            '<svg viewBox="0 0 500 180" role="img"><title>Static route map</title><path d="M40 130 L450 40" stroke="#333" stroke-width="5" stroke-dasharray="8 5"/><text x="180" y="100">route-R1 [CONFLICT]</text></svg>',
            "Derivative publications share basis-bulletin; the independent official basis is distinct."
        )
        (root / "baseline.html").write_text(baseline, encoding="utf-8")
        harness_html = render_harness(definition, {
            "Logistics workbench": "../06_strategic_workbench/logistics_workbench.html",
            "Civil-protection workbench": "../06_strategic_workbench/civil_protection_workbench.html",
            "Strategic workbench": "../06_strategic_workbench/strategic_workbench.html",
        })
        (root / "harness.html").write_text(harness_html, encoding="utf-8")
        result = scripted_validation(
            harness, OperatorSession("SCRIPTED-V3-SESSION", definition.study_id, "P-SCRIPTED-V3",
                                     "JOINT_COORDINATOR", "EVALUATION", "CURUNIR_WORKBENCH", TIMES[16]),
            start_time=TIMES[16], end_time=TIMES[17])
        _write(root / "ai_secondary_usability_review.json", {
            "review_type": "AI_SECONDARY_USABILITY_REVIEW", "human_review": False,
            "findings": ["conflicts are explicit", "source dependence is tabular",
                         "restricted-basis disclosure is refused", "human usability remains untested"],
            "claim_boundary": "does not replace or simulate a human operator study",
        })
        return result

    def run(self) -> dict[str, Any]:
        self.initialize(); self.phase1_baseline()
        references = self.phase2_and_3_partition(); self.phase4_reconnect()
        resolutions = self.phase5_resolution(); outputs = self.outputs(references, resolutions)
        operator = self.operator_harness()
        pid_set = sorted({record["child_pid"] for record in self.commands if record.get("child_pid")})
        scenario = {
            "scenario": "SYNTHETIC_PARTITIONED_REGIONAL_RESPONSE_V3",
            "strategic_scenario": "SYNTHETIC_STRATEGIC_WARNING_AND_SUPPLY_DISRUPTION_V3",
            "fictional_entities": True, "nodes": list(NODE_IDS),
            "node_store_paths": {key: str(value) for key, value in self.node_paths.items()},
            "process_isolation": {"parent_pid": os.getpid(), "child_pids": pid_set,
                                  "distinct_child_processes": len(pid_set), "shared_python_memory": False},
            "phase_events": self.phase_events, "commands": self.commands,
            "convergence": outputs["convergence"], "operator_harness": operator,
            "authentication": "TEST_SIGNER_ONLY", "canonical_writes": 0,
            **declared_label("v3/scenario.py::ScenarioRunner.run"),
        }
        _write(self.artifact_root / "12_final" / "scenario_execution.json", scenario)
        _write(self.artifact_root / "03_nodes" / "process_isolation_log.json", {
            "parent_pid": os.getpid(), "child_pids": pid_set, "commands": self.commands,
            "proof": "each init, append, bundle, import, receipt and projection call executed through a separate CLI process",
        })
        return scenario


def run_distributed_scenario(artifact_root: str | Path) -> dict[str, Any]:
    return ScenarioRunner(artifact_root).run()
