"""Analytics plane: a deterministic rule provider and a replaceable mock model.

Providers read an access-filtered slice of the projection, record every
invocation as an InferenceRecord, and emit proposals. Nothing a provider emits
becomes operational state until the workflow materializes it, and no provider
can record a human decision.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .access import AccessContext, Marking, can_view, inherited_marking, marking_from_record
from .canonical import digest_id, sha256
from .contracts import AnalyticalProposal, InferenceRecord, ModelPackage
from .geometry import haversine_m, point_to_linestring_m
from .projection import Projection
from .store import MissionDataStore

DISRUPTION_STATES = frozenset({"DAMAGED", "BLOCKED", "OBSTRUCTED", "DESTROYED", "DEGRADED", "OUT_OF_SERVICE"})


def rules_model_package() -> ModelPackage:
    return ModelPackage(
        model_id="curunir-deterministic-rules", version="1.0", provider="curunir-internal",
        task="operational-condition-detection", input_schema_id="curunir-operational-projection",
        output_schema_id="curunir-operational-analytical-proposal",
        training_data="none (deterministic rules)", evaluation_summary="synthetic fixture exercises only",
        limitations=("fixture-scale only", "no calibrated confidence", "no learned component"),
        approved_uses=("research-shadow condition detection",),
        prohibited_uses=("targeting", "any real operational decision authority"),
        latency_profile="milliseconds at fixture scale", hardware="cpu", licence="internal",
        accreditation_state="SYNTHETIC_EVALUATION_ONLY",
    )


def mock_model_package() -> ModelPackage:
    return ModelPackage(
        model_id="mock-damage-assessment", version="1.0", provider="replaceable-mock-vendor",
        task="infrastructure-damage-assessment", input_schema_id="curunir-operational-object",
        output_schema_id="curunir-operational-assessment",
        training_data="none (deterministic mock)", evaluation_summary="not evaluated",
        limitations=("mock output keyed to input hash", "no analytical validity"),
        approved_uses=("interface demonstration",), prohibited_uses=("any operational reliance",),
        latency_profile="instant", hardware="cpu", licence="replaceable",
        accreditation_state="UNACCREDITED",
    )


def _joined(markings: Iterable[Mapping[str, Any] | Marking]) -> Marking:
    """High-water join of every marking that contributed content."""
    markings = list(markings)
    if not markings:
        raise ValueError("no markings supplied")
    return inherited_marking(markings[0], list(markings[1:]))


class _ProviderBase:
    def __init__(self, store: MissionDataStore, package: ModelPackage, provider_actor: str):
        self.store = store
        self.package = package
        self.provider_actor = provider_actor

    def ensure_registered(self, *, recorded_time: str) -> None:
        registered = [m for m in self.store.records_of("model_package") if m["model_id"] == self.package.model_id]
        if not registered:
            self.store.append("MODEL_REGISTERED", self.package, recorded_time=recorded_time, actor=self.provider_actor)

    def _record(self, *, input_refs: tuple[str, ...], inputs: Any, output: Mapping[str, Any],
                recorded_time: str, marking: Marking, downstream: tuple[str, ...],
                errors: tuple[str, ...] = ()) -> str:
        input_hash = sha256(inputs)
        inference = InferenceRecord(
            inference_id=digest_id("inf", self.package.model_id, input_hash, recorded_time),
            model_id=self.package.model_id, model_version=self.package.version,
            input_refs=input_refs, input_hash=input_hash, output=dict(output), output_hash=sha256(output),
            started=recorded_time, completed=recorded_time, parameters={},
            errors=errors, validation="INVALID" if errors else "VALID", marking=marking,
            downstream_use=downstream,
        )
        self.store.append("INFERENCE_RECORDED", inference, recorded_time=recorded_time, actor=self.provider_actor)
        return inference.inference_id

    def _propose(self, inference_id: str, proposal_type: str, content: Mapping[str, Any],
                 recorded_time: str, marking: Marking) -> dict[str, Any]:
        proposal = AnalyticalProposal(
            proposal_id=digest_id("prop", inference_id, proposal_type, sha256(content)),
            inference_id=inference_id, proposal_type=proposal_type, content=dict(content),
            status="PROPOSED", recorded_time=recorded_time, marking=marking,
        )
        event = self.store.append(
            "ANALYTICAL_PROPOSAL_RECORDED", proposal,
            recorded_time=recorded_time, actor=self.provider_actor)
        return event["record"]


class DeterministicRuleProvider(_ProviderBase):
    """Access-filtered, deterministic condition detection over a projection."""

    RULES_VERSION = "1.0"

    def __init__(self, store: MissionDataStore, provider_actor: str = "provider-rules"):
        super().__init__(store, rules_model_package(), provider_actor)

    def _visible_objects(self, projection: Projection, context: AccessContext) -> dict[str, dict[str, Any]]:
        result = {}
        for object_id, entry in projection.objects.items():
            if can_view(entry["current"].get("marking"), context):
                result[object_id] = entry
        return result

    def _visible_relationships(self, projection: Projection, context: AccessContext,
                               visible_ids: set[str]) -> list[dict[str, Any]]:
        result = []
        for entry in projection.relationships.values():
            current = entry["current"]
            if current["status"] == "ACTIVE" and can_view(current.get("marking"), context) \
                    and current["source_object_id"] in visible_ids and current["target_object_id"] in visible_ids:
                result.append(current)
        return result

    def run(self, projection: Projection, context: AccessContext, *, recorded_time: str,
            route_proximity_m: float = 500.0, hazard_impact_m: float = 30_000.0) -> list[dict[str, Any]]:
        self.ensure_registered(recorded_time=recorded_time)
        objects = self._visible_objects(projection, context)
        relationships = self._visible_relationships(projection, context, set(objects))
        proposals: list[dict[str, Any]] = []
        proposals += self._rule_infrastructure_conflict(objects, relationships, recorded_time)
        proposals += self._rule_reported_disruption(objects, relationships, recorded_time)
        proposals += self._rule_hazard_impact(objects, recorded_time, hazard_impact_m)
        proposals += self._rule_route_exposure(objects, relationships, recorded_time, route_proximity_m)
        proposals += self._rule_stale_records(objects, recorded_time)
        proposals += self._rule_source_dependence(projection, objects, recorded_time)
        return proposals

    def _rule_hazard_impact(self, objects, recorded_time, hazard_impact_m) -> list[dict[str, Any]]:
        """Proposals for hazards close to a route or infrastructure object.

        The calculation, threshold, geometry basis and uncertainty travel with
        the proposal; the relationship is real only once materialized.
        """
        rule_id = "rule-hazard-impact"
        hazards = {oid: e["current"] for oid, e in objects.items()
                   if e["current"]["object_type"] == "OPERATIONAL_CONCERN"
                   and e["current"]["lifecycle"] == "ACTIVE" and e["current"].get("geometry")}
        targets = {oid: e["current"] for oid, e in objects.items()
                   if e["current"]["object_type"] in ("ROUTE", "INFRASTRUCTURE") and e["current"].get("geometry")}
        proposals = []
        for hazard_id, hazard in sorted(hazards.items()):
            hazard_geometry = hazard["geometry"]
            if hazard_geometry["kind"] == "POINT":
                hazard_points = [hazard_geometry["coordinates"]]
            else:
                hazard_points = list(hazard_geometry["coordinates"])
            findings = []
            for target_id, target in sorted(targets.items()):
                geometry = target["geometry"]
                if geometry["kind"] == "LINESTRING":
                    distance = min(point_to_linestring_m(p, geometry["coordinates"]) for p in hazard_points)
                elif geometry["kind"] == "POINT":
                    distance = min(haversine_m(p, geometry["coordinates"]) for p in hazard_points)
                else:
                    continue
                if distance <= hazard_impact_m:
                    findings.append({"target_id": target_id, "distance_m": round(distance, 1)})
            if not findings:
                continue
            marking = _joined([hazard["marking"], *[targets[f["target_id"]]["marking"] for f in findings]])
            inference = self._record(input_refs=(hazard_id, *[f["target_id"] for f in findings]),
                                     inputs={"hazard": hazard, "findings": findings},
                                     output={"rule_id": rule_id, "hazard": hazard_id, "findings": findings},
                                     recorded_time=recorded_time, marking=marking,
                                     downstream=(hazard_id, *[f["target_id"] for f in findings]))
            uncertainty = ("hazard geometry uncertainty "
                           f"{hazard_geometry.get('uncertainty_m') or 'UNKNOWN'} m; source timestamps preserved "
                           "verbatim from the feed (timezone-naive); distances are equirectangular approximations")
            for finding in findings:
                proposals.append(self._propose(inference, "RELATIONSHIP_CANDIDATE",
                                               {"relation_type": "AFFECTS", "left": hazard_id,
                                                "right": finding["target_id"],
                                                "rationale": f"{hazard_id} within {finding['distance_m']} m "
                                                             f"(threshold {hazard_impact_m} m)",
                                                "calculation": {"method": "min point/vertex to geometry, "
                                                                          "equirectangular local approximation",
                                                                "distance_m": finding["distance_m"],
                                                                "threshold_m": hazard_impact_m},
                                                "uncertainty": uncertainty},
                                               recorded_time, marking))
            proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                           {"rule_id": rule_id, "rule_version": self.RULES_VERSION,
                                            "trigger": f"hazard {hazard_id} "
                                                       f"({hazard.get('attributes', {}).get('hazard_kind', 'UNKNOWN')}) "
                                                       f"impacts {len(findings)} corridor object(s)",
                                            "affected_ids": [hazard_id, *[f["target_id"] for f in findings]],
                                            "evidence_refs": [hazard_id, *[f["target_id"] for f in findings]],
                                            "severity": {"Red": "CRITICAL", "Orange": "HIGH"}.get(
                                                hazard.get("attributes", {}).get("alert_level"), "WARNING"),
                                            "severity_rationale": "hazard proximity to corridor objects; severity "
                                                                  "follows the feed alert level",
                                            "dedup_key": f"{rule_id}:{hazard_id}",
                                            "expiry_condition": "until the hazard event closes"},
                                           recorded_time, marking))
        return proposals

    def _rule_reported_disruption(self, objects, relationships, recorded_time) -> list[dict[str, Any]]:
        """Disruption notice from a single report, marked to cover both the
        observation and the object it reports on."""
        rule_id = "rule-reported-disruption"
        proposals = []
        for relation in relationships:
            if relation["relation_type"] != "REPORTS_ON" or relation["source_object_id"] not in objects:
                continue
            observation = objects[relation["source_object_id"]]["current"]
            target_id = relation["target_object_id"]
            target = objects.get(target_id)
            status = observation.get("attributes", {}).get("reported_status")
            if status not in DISRUPTION_STATES or target is None \
                    or target["current"]["object_type"] not in ("INFRASTRUCTURE", "ROUTE"):
                continue
            marking = _joined([observation["marking"], target["current"]["marking"]])
            inference = self._record(input_refs=(observation["object_id"],), inputs=observation,
                                     output={"rule_id": rule_id, "target": target_id, "status": status},
                                     recorded_time=recorded_time, marking=marking,
                                     downstream=(target_id, observation["object_id"]))
            proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                           {"rule_id": rule_id, "rule_version": self.RULES_VERSION,
                                            "trigger": f"{target_id} reported {status} by {observation['object_id']}",
                                            "affected_ids": [target_id, observation["object_id"]],
                                            "evidence_refs": [observation["object_id"]],
                                            "severity": "WARNING",
                                            "severity_rationale": "reported disruption of corridor infrastructure",
                                            "dedup_key": f"{rule_id}:{target_id}:{status}",
                                            "expiry_condition": "until a superseding status report arrives"},
                                           recorded_time, marking))
        return proposals

    def _rule_infrastructure_conflict(self, objects, relationships, recorded_time) -> list[dict[str, Any]]:
        rule_id = "rule-infrastructure-conflict"
        reports: dict[str, list[str]] = {}
        for relation in relationships:
            if relation["relation_type"] == "REPORTS_ON" and relation["source_object_id"] in objects:
                reports.setdefault(relation["target_object_id"], []).append(relation["source_object_id"])
        proposals = []
        for target, observers in sorted(reports.items()):
            statuses = {}
            for observation_id in observers:
                status = objects[observation_id]["current"].get("attributes", {}).get("reported_status")
                if status not in (None, "UNKNOWN"):
                    statuses[observation_id] = status
            distinct = sorted(set(statuses.values()))
            if len(distinct) < 2:
                continue
            involved = sorted(statuses)
            marking = _joined([objects[o]["current"]["marking"] for o in involved])
            inputs = {o: objects[o]["current"] for o in involved}
            inference = self._record(input_refs=tuple(involved), inputs=inputs,
                                     output={"rule_id": rule_id, "target": target, "statuses": statuses},
                                     recorded_time=recorded_time, marking=marking, downstream=(target, *involved))
            pairs = [(involved[i], involved[j]) for i in range(len(involved)) for j in range(i + 1, len(involved))
                     if statuses[involved[i]] != statuses[involved[j]]]
            for left, right in pairs:
                proposals.append(self._propose(inference, "RELATIONSHIP_CANDIDATE",
                                               {"relation_type": "CONFLICTS_WITH", "left": left, "right": right,
                                                "rationale": f"{left} reports {statuses[left]}; {right} reports {statuses[right]}"},
                                               recorded_time, marking))
            proposals.append(self._propose(inference, "STATE_CANDIDATE",
                                           {"object_id": target, "epistemic_state": "DISPUTED",
                                            "reason": f"conflicting reported status: {distinct}"},
                                           recorded_time, marking))
            proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                           {"rule_id": rule_id, "rule_version": self.RULES_VERSION,
                                            "trigger": f"conflicting infrastructure reports on {target}",
                                            "affected_ids": [target, *involved], "evidence_refs": involved,
                                            "severity": "HIGH",
                                            "severity_rationale": "conflicting reports on movement-critical infrastructure",
                                            "dedup_key": f"{rule_id}:{target}",
                                            "expiry_condition": "until conflict resolved"},
                                           recorded_time, marking))
            proposals.append(self._propose(inference, "RECOMMENDATION_CANDIDATE",
                                           {"action_kind": "INFORMATION_REQUEST",
                                            "proposed_action": f"request an additional independent observation of {target}",
                                            "rationale": f"reported status of {target} is disputed ({', '.join(distinct)})",
                                            "assumptions": ["target remains observable"],
                                            "alternatives": ["accept the more conservative report"],
                                            "evidence_refs": involved, "uncertainty": "conflict unresolved",
                                            "expected_benefit": "resolves disputed infrastructure state",
                                            "potential_risk": "delay while awaiting observation",
                                            "required_role": "ANALYST",
                                            "dedup_key": f"rec:{rule_id}:{target}:observe"},
                                           recorded_time, marking))
            proposals.append(self._propose(inference, "RECOMMENDATION_CANDIDATE",
                                           {"action_kind": "SOURCE_INSPECTION",
                                            "proposed_action": f"inspect the conflicting reporting chain for {target}",
                                            "rationale": "one of the conflicting reports may rest on a weaker basis",
                                            "assumptions": ["source metadata is reviewable"],
                                            "alternatives": ["wait for further reporting"],
                                            "evidence_refs": involved, "uncertainty": "conflict unresolved",
                                            "expected_benefit": "identifies unreliable source path",
                                            "potential_risk": "analyst time",
                                            "required_role": "ANALYST",
                                            "dedup_key": f"rec:{rule_id}:{target}:inspect"},
                                           recorded_time, marking))
        return proposals

    def _rule_route_exposure(self, objects, relationships, recorded_time, route_proximity_m) -> list[dict[str, Any]]:
        rule_id = "rule-route-exposure"
        routes = {oid: e for oid, e in objects.items() if e["current"]["object_type"] == "ROUTE"}
        disruptions = {}
        for object_id, entry in objects.items():
            current = entry["current"]
            if current["object_type"] == "OPERATIONAL_CONCERN" and current["lifecycle"] == "ACTIVE":
                disruptions[object_id] = current
            elif current.get("attributes", {}).get("reported_status") in DISRUPTION_STATES:
                disruptions[object_id] = current
        depends = {}
        affects = {}
        reports_on = {}
        planned = {}
        alternates = {}
        for relation in relationships:
            kind = relation["relation_type"]
            if kind == "DEPENDS_ON":
                depends.setdefault(relation["source_object_id"], set()).add(relation["target_object_id"])
            elif kind == "AFFECTS":
                affects.setdefault(relation["target_object_id"], set()).add(relation["source_object_id"])
            elif kind == "REPORTS_ON":
                reports_on.setdefault(relation["source_object_id"], set()).add(relation["target_object_id"])
            elif kind == "PLANNED_FOR":
                planned.setdefault(relation["target_object_id"], set()).add(relation["source_object_id"])
            elif kind == "ALTERNATE_OF":
                alternates.setdefault(relation["source_object_id"], set()).add(relation["target_object_id"])
                alternates.setdefault(relation["target_object_id"], set()).add(relation["source_object_id"])
        proposals = []
        exposure: dict[str, list[dict[str, Any]]] = {}
        for route_id, entry in sorted(routes.items()):
            found: list[dict[str, Any]] = []
            for disruption_id in affects.get(route_id, ()):
                found.append({"disruption_id": disruption_id, "condition": "AFFECTS_RELATIONSHIP"})
            for infra_id in depends.get(route_id, ()):
                infra = objects.get(infra_id)
                if infra and infra["current"]["epistemic_state"] == "DISPUTED":
                    found.append({"disruption_id": infra_id, "condition": "DEPENDENCY_DISPUTED"})
                if infra and infra["current"].get("attributes", {}).get("status") in DISRUPTION_STATES:
                    found.append({"disruption_id": infra_id, "condition": "DEPENDENCY_DISRUPTED"})
                for observation_id, targets in reports_on.items():
                    if infra_id in targets and objects[observation_id]["current"].get("attributes", {})\
                            .get("reported_status") in DISRUPTION_STATES:
                        found.append({"disruption_id": observation_id, "condition": "DEPENDENCY_REPORTED_DISRUPTED",
                                      "dependency": infra_id})
            geometry = entry["current"].get("geometry")
            if geometry and geometry["kind"] == "LINESTRING":
                for disruption_id, disruption in sorted(disruptions.items()):
                    dgeo = disruption.get("geometry")
                    if dgeo and dgeo["kind"] == "POINT":
                        distance = point_to_linestring_m(dgeo["coordinates"], geometry["coordinates"])
                        if distance <= route_proximity_m:
                            found.append({"disruption_id": disruption_id, "condition": "PROXIMATE_DISRUPTION",
                                          "distance_m": round(distance, 1)})
            unique = {f["disruption_id"]: f for f in found}
            exposure[route_id] = sorted(unique.values(), key=lambda f: f["disruption_id"])
        involved = sorted(routes)
        if not involved:
            return proposals
        # Every disruption named in the exposure map contributes content, so the
        # assessment inherits its marking too.
        exposed = sorted({f["disruption_id"] for findings in exposure.values() for f in findings})
        marking = _joined([routes[r]["current"]["marking"] for r in involved]
                          + [objects[d]["current"]["marking"] for d in exposed if d in objects])
        inference = self._record(input_refs=tuple(involved),
                                 inputs={r: routes[r]["current"] for r in involved},
                                 output={"rule_id": rule_id, "exposure": exposure},
                                 recorded_time=recorded_time, marking=marking, downstream=tuple(involved))
        proposals.append(self._propose(inference, "ASSESSMENT",
                                       {"rule_id": rule_id, "kind": "route-exposure",
                                        "exposure": exposure,
                                        "least_exposed": min(sorted(exposure), key=lambda r: len(exposure[r]))},
                                       recorded_time, marking))
        for route_id, findings in sorted(exposure.items()):
            if not findings:
                continue
            for movement_id in sorted(planned.get(route_id, ())):
                clear_alternates = sorted(a for a in alternates.get(route_id, ()) if not exposure.get(a))
                evidence = [route_id, movement_id] + [f["disruption_id"] for f in findings]
                proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                               {"rule_id": "rule-movement-route-risk", "rule_version": self.RULES_VERSION,
                                                "trigger": f"planned movement {movement_id} is exposed to disruption on {route_id}",
                                                "affected_ids": [movement_id, route_id],
                                                "evidence_refs": evidence, "severity": "HIGH",
                                                "severity_rationale": "planned movement routed over a disrupted or disputed segment",
                                                "dedup_key": f"rule-movement-route-risk:{route_id}:{movement_id}",
                                                "expiry_condition": "until movement completes or replans"},
                                               recorded_time, marking))
                if clear_alternates:
                    proposals.append(self._propose(inference, "RECOMMENDATION_CANDIDATE",
                                                   {"action_kind": "ROUTE_CHANGE",
                                                    "proposed_action": f"replan movement {movement_id} onto {clear_alternates[0]}",
                                                    "rationale": f"{route_id} has {len(findings)} known disruption(s); "
                                                                 f"{clear_alternates[0]} currently shows none",
                                                    "assumptions": ["alternate route capacity is sufficient",
                                                                    "no unreported disruption on the alternate"],
                                                    "alternatives": ["hold movement until disruption clears"],
                                                    "evidence_refs": evidence,
                                                    "uncertainty": "alternate route state is only as fresh as its last report",
                                                    "expected_benefit": "avoids the disrupted segment",
                                                    "potential_risk": "longer distance; unverified alternate state",
                                                    "required_role": "SUPERVISOR",
                                                    "dedup_key": f"rec:route-change:{movement_id}"},
                                                   recorded_time, marking))
        return proposals

    def _rule_stale_records(self, objects, recorded_time) -> list[dict[str, Any]]:
        rule_id = "rule-stale-stock"
        stale = {oid: e for oid, e in sorted(objects.items())
                 if e["current"]["object_type"] == "RESOURCE_STOCK" and e["freshness"]["state"] == "STALE"}
        proposals = []
        if not stale:
            return proposals
        marking = _joined([e["current"]["marking"] for e in stale.values()])
        inference = self._record(input_refs=tuple(stale), inputs={o: e["freshness"] for o, e in stale.items()},
                                 output={"rule_id": rule_id, "stale": sorted(stale)},
                                 recorded_time=recorded_time, marking=marking, downstream=tuple(stale))
        for object_id, entry in stale.items():
            proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                           {"rule_id": rule_id, "rule_version": self.RULES_VERSION,
                                            "trigger": f"resource stock {object_id} is stale "
                                                       f"({entry['freshness']['age_hours']}h old, "
                                                       f"threshold {entry['freshness']['threshold_hours']}h)",
                                            "affected_ids": [object_id],
                                            "evidence_refs": [f"{object_id}@v{entry['current']['version']}"],
                                            "severity": "WARNING",
                                            "severity_rationale": "planning over stale stock misstates readiness",
                                            "dedup_key": f"{rule_id}:{object_id}",
                                            "expiry_condition": "until a fresh stock report arrives"},
                                           recorded_time, marking))
            proposals.append(self._propose(inference, "RECOMMENDATION_CANDIDATE",
                                           {"action_kind": "SCHEDULE_CHANGE",
                                            "proposed_action": f"delay dependent movement until {object_id} is re-reported",
                                            "rationale": "a required stock threshold cannot be confirmed from stale data",
                                            "assumptions": ["stock level may have changed since last report"],
                                            "alternatives": ["proceed and accept unknown stock risk"],
                                            "evidence_refs": [f"{object_id}@v{entry['current']['version']}"],
                                            "uncertainty": "current stock level is unknown",
                                            "expected_benefit": "prevents commitment on unknown readiness",
                                            "potential_risk": "schedule slip",
                                            "required_role": "SUPERVISOR",
                                            "dedup_key": f"rec:delay:{object_id}"},
                                           recorded_time, marking))
        return proposals

    def _rule_source_dependence(self, projection: Projection, objects, recorded_time) -> list[dict[str, Any]]:
        rule_id = "rule-source-dependence"
        proposals = []
        for group_id, members in projection.dependence_groups.items():
            visible = [m for m in members if m in objects]
            if len(visible) < 2:
                continue
            marking = _joined([objects[m]["current"]["marking"] for m in visible])
            inference = self._record(input_refs=tuple(visible), inputs={"group": group_id, "members": visible},
                                     output={"rule_id": rule_id, "group_id": group_id, "members": visible},
                                     recorded_time=recorded_time, marking=marking, downstream=tuple(visible))
            proposals.append(self._propose(inference, "ALERT_CANDIDATE",
                                           {"rule_id": rule_id, "rule_version": self.RULES_VERSION,
                                            "trigger": f"{len(visible)} reports share one underlying evidence basis "
                                                       f"(group {group_id}); they are not independent corroboration",
                                            "affected_ids": visible, "evidence_refs": visible,
                                            "severity": "WARNING",
                                            "severity_rationale": "dependent reporting can masquerade as corroboration",
                                            "dedup_key": f"{rule_id}:{group_id}",
                                            "expiry_condition": "standing notice while group persists"},
                                           recorded_time, marking))
        return proposals


class MockAssessmentProvider(_ProviderBase):
    """Stand-in for an external model: same contract surface, no analytical
    validity — the output is keyed to the input hash."""

    def __init__(self, store: MissionDataStore, provider_actor: str = "provider-mock-model"):
        super().__init__(store, mock_model_package(), provider_actor)

    def run(self, projection: Projection, context: AccessContext, object_id: str, *,
            recorded_time: str) -> dict[str, Any] | None:
        self.ensure_registered(recorded_time=recorded_time)
        entry = projection.objects.get(object_id)
        if entry is None or not can_view(entry["current"].get("marking"), context):
            return None
        current = entry["current"]
        input_hash = sha256(current)
        grade = ["MINOR", "MODERATE", "SEVERE"][int(input_hash[:2], 16) % 3]
        marking = marking_from_record(current["marking"])
        inference = self._record(input_refs=(f"{object_id}@v{current['version']}",), inputs=current,
                                 output={"assessment": f"STRUCTURAL_IMPACT_{grade}",
                                         "caveat": "MOCK_OUTPUT_NO_ANALYTICAL_VALIDITY"},
                                 recorded_time=recorded_time, marking=marking, downstream=(object_id,))
        return self._propose(inference, "ASSESSMENT",
                             {"kind": "mock-damage-assessment", "object_id": object_id,
                              "assessment": f"STRUCTURAL_IMPACT_{grade}",
                              "caveat": "MOCK_OUTPUT_NO_ANALYTICAL_VALIDITY"},
                             recorded_time, marking)
