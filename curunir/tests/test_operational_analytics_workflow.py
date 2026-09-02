"""Providers and workflow: rule output becomes proposals, proposals become alerts
and recommendations, and only a human decides."""
from __future__ import annotations

import pytest

from curunir_operational.access import AccessContext
from curunir_operational.analytics import DeterministicRuleProvider, MockAssessmentProvider
from curunir_operational.contracts import (EvidenceRef, ExternalRef, ObjectVersion, ProvenanceSummary,
                                           RelationshipVersion)
from curunir_operational.geometry import Geometry
from curunir_operational.projection import Projection
from curunir_operational.workflow import WorkflowEngine, WorkflowError, evidence_snapshot_hash

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, RESTRICTED_MARKING,
                                 SERVICE_CONTEXT, T0, fake_sha, make_store, t)

pytestmark = pytest.mark.no_db

PROV = ProvenanceSummary(mode="OPERATIONAL", source_ids=("src-a",), ingestion_ids=("ing-1",))


def obj(object_id, object_type, *, version=1, hours=0.0, geometry=None, attributes=None, marking=BASE_MARKING,
        epistemic="REPORTED", evidence=(), labels=None):
    provenance = PROV if not evidence else ProvenanceSummary(mode="EVIDENTIARY", source_ids=("src-argus",),
                                                             ingestion_ids=("ing-e",), evidence=tuple(evidence))
    return ObjectVersion(object_id=object_id, version=version, object_type=object_type, lifecycle="ACTIVE",
                         labels=tuple(labels or (object_id,)), external_refs=(), valid_from=t(hours), valid_to=None,
                         source_time=t(hours), time_precision="HOUR", recorded_time=t(max(hours, 0.0)),
                         geometry=geometry, attributes=attributes or {}, quality={},
                         epistemic_state=epistemic, marking=marking, provenance=provenance)


def rel(relation_type, source, target, *, marking=BASE_MARKING, hours=0.0):
    relationship_id = f"rel-{relation_type.lower()}-{source}-{target}"
    return RelationshipVersion(relationship_id, 1, relation_type, source, target, t(hours), None, t(hours),
                               ("ing-1",), "MAPPING", "UNKNOWN", "ACTIVE", marking, PROV)


def evidence_ref(source_object, group=None):
    return EvidenceRef(source_object, f"doc-{source_object}", fake_sha(source_object), f"asrt-{source_object}",
                       "basis-bridge-strike", "IDENTITY_PROVISIONAL", "REPUTABLE_SECONDARY_REPORT",
                       "UNRESOLVED", "SINGLE_BASIS", "UNREVIEWED", "EXACT", group, ("COMMON_ORIGIN_REVIEW",))


def build_scene(tmp_path, *, restrict_first_observation=False):
    store = make_store(tmp_path)
    line = Geometry("LINESTRING", ((-30.30, 45.10), (-30.10, 45.20), (-29.90, 45.30)))
    alt_line = Geometry("LINESTRING", ((-30.30, 45.10), (-30.05, 45.05), (-29.90, 45.30)))
    group = "evgroup-shared-basis"
    records = [
        obj("infra-BR-7", "INFRASTRUCTURE", geometry=Geometry("POINT", (-30.10, 45.20)),
            attributes={"status": "OPERATIONAL"}),
        obj("route-R1", "ROUTE", geometry=line),
        obj("route-R2", "ROUTE", geometry=alt_line),
        obj("mv-relief-1", "MOVEMENT", epistemic="PLANNED", attributes={"cargo": "medical"}),
        obj("obs-sensor-1", "OBSERVATION", hours=24.0, attributes={"reported_status": "OPERATIONAL"},
            marking=RESTRICTED_MARKING if restrict_first_observation else BASE_MARKING),
        obj("obs-report-1", "OBSERVATION", hours=25.0, attributes={"reported_status": "DAMAGED"},
            evidence=[evidence_ref("argus-src-1", group)]),
        obj("obs-report-2", "OBSERVATION", hours=25.5, attributes={"reported_status": "DAMAGED"},
            evidence=[evidence_ref("argus-src-2", group)]),
        obj("stock-fuel-alden", "RESOURCE_STOCK", hours=-30.0, attributes={"commodity": "fuel"}),
    ]
    for record in records:
        store.append("OBJECT_VERSION_APPENDED", record, recorded_time=t(max(record.version, 26.0)), actor="fixture")
    relationships = [
        rel("REPORTS_ON", "obs-sensor-1", "infra-BR-7",
            marking=RESTRICTED_MARKING if restrict_first_observation else BASE_MARKING, hours=24.0),
        rel("REPORTS_ON", "obs-report-1", "infra-BR-7", hours=25.0),
        rel("REPORTS_ON", "obs-report-2", "infra-BR-7", hours=25.5),
        rel("DEPENDS_ON", "route-R1", "infra-BR-7"),
        rel("PLANNED_FOR", "mv-relief-1", "route-R1"),
        rel("ALTERNATE_OF", "route-R1", "route-R2"),
    ]
    for relationship in relationships:
        store.append("RELATIONSHIP_VERSION_APPENDED", relationship, recorded_time=t(26.0), actor="fixture")
    projection = Projection(store, snapshot_time=t(26.0), staleness_hours={"RESOURCE_STOCK": 24.0})
    return store, projection


def proposals_of(proposals, proposal_type, rule_id=None):
    chosen = [p for p in proposals if p["proposal_type"] == proposal_type]
    if rule_id:
        chosen = [p for p in chosen if p["content"].get("rule_id") == rule_id]
    return chosen


def test_rule_provider_detects_conditions(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    proposals = provider.run(projection, SERVICE_CONTEXT, recorded_time=t(27.0))
    assert proposals_of(proposals, "RELATIONSHIP_CANDIDATE")  # the sensor and the reports disagree
    assert proposals_of(proposals, "STATE_CANDIDATE")
    assert proposals_of(proposals, "ALERT_CANDIDATE", "rule-infrastructure-conflict")
    assert proposals_of(proposals, "ALERT_CANDIDATE", "rule-movement-route-risk")
    assert proposals_of(proposals, "ALERT_CANDIDATE", "rule-stale-stock")
    assert proposals_of(proposals, "ALERT_CANDIDATE", "rule-source-dependence")
    route_change = [p for p in proposals_of(proposals, "RECOMMENDATION_CANDIDATE")
                    if p["content"]["action_kind"] == "ROUTE_CHANGE"]
    assert route_change and "route-R2" in route_change[0]["content"]["proposed_action"]
    assessment = proposals_of(proposals, "ASSESSMENT")[0]
    assert assessment["content"]["least_exposed"] == "route-R2"
    inferences = store.records_of("inference")
    assert inferences and all(i["validation"] == "VALID" for i in inferences)
    packages = store.records_of("model_package")
    assert packages[0]["accreditation_state"] == "SYNTHETIC_EVALUATION_ONLY"


def test_provider_input_is_access_filtered(tmp_path):
    store, projection = build_scene(tmp_path, restrict_first_observation=True)
    low_service = AccessContext("ctx-svc-low", "rule-engine-low", "SERVICE", ("ANALYST",),
                                (), ("CORRIDOR-OPS",), "CIVDEF-AUTH")
    provider = DeterministicRuleProvider(store)
    proposals = provider.run(projection, low_service, recorded_time=t(27.0))
    # Without the restricted observation there is nothing left to conflict with.
    assert not proposals_of(proposals, "ALERT_CANDIDATE", "rule-infrastructure-conflict")
    assert not proposals_of(proposals, "RELATIONSHIP_CANDIDATE")
    high = DeterministicRuleProvider(store).run(projection, SERVICE_CONTEXT, recorded_time=t(27.5))
    assert proposals_of(high, "ALERT_CANDIDATE", "rule-infrastructure-conflict")


def test_materialization_alerts_dedup_dispute(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    proposals = provider.run(projection, SERVICE_CONTEXT, recorded_time=t(27.0))
    workflow = WorkflowEngine(store)
    conflict_alert = proposals_of(proposals, "ALERT_CANDIDATE", "rule-infrastructure-conflict")[0]
    first = workflow.materialize(conflict_alert, actor_id="workflow", recorded_time=t(27.1))
    assert first["created"] is True
    again = workflow.materialize(conflict_alert, actor_id="workflow", recorded_time=t(27.2))
    assert again["created"] is False  # the repeat is recorded as a transition instead
    transitions = store.records_of("alert_transition")
    assert transitions and "retriggered" in transitions[-1]["note"]
    workflow.materialize(proposals_of(proposals, "RELATIONSHIP_CANDIDATE")[0], actor_id="workflow", recorded_time=t(27.3))
    workflow.materialize(proposals_of(proposals, "STATE_CANDIDATE")[0], actor_id="workflow", recorded_time=t(27.4))
    updated = Projection(store, snapshot_time=t(28.0))
    assert updated.objects["infra-BR-7"]["current"]["epistemic_state"] == "DISPUTED"
    assert updated.objects["infra-BR-7"]["history_count"] == 2  # the old version is still there
    conflicts = [r for r in store.records_of("relationship_version") if r["relation_type"] == "CONFLICTS_WITH"]
    assert conflicts and conflicts[0]["status"] == "ACTIVE"


def test_invalid_provider_output_cannot_materialize(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    provider.ensure_registered(recorded_time=t(27.0))
    inference_id = provider._record(input_refs=("route-R1",), inputs={"x": 1},
                                    output={"rule_id": "rule-x"}, recorded_time=t(27.0),
                                    marking=BASE_MARKING, downstream=("route-R1",),
                                    errors=("output schema violation",))
    proposal = provider._propose(inference_id, "ALERT_CANDIDATE",
                                 {"rule_id": "rule-x", "rule_version": "1.0", "trigger": "bad",
                                  "affected_ids": ["route-R1"], "evidence_refs": ["route-R1"],
                                  "severity": "INFO", "severity_rationale": "n/a", "dedup_key": "bad:1"},
                                 t(27.1), BASE_MARKING)
    workflow = WorkflowEngine(store)
    with pytest.raises(WorkflowError):
        workflow.materialize(proposal, actor_id="workflow", recorded_time=t(27.2))


def test_mock_provider_is_deterministic_and_unaccredited(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = MockAssessmentProvider(store)
    first = provider.run(projection, HIGH_CONTEXT, "infra-BR-7", recorded_time=t(27.0))
    second = provider.run(projection, HIGH_CONTEXT, "infra-BR-7", recorded_time=t(27.5))
    assert first["content"]["assessment"] == second["content"]["assessment"]
    assert first["content"]["caveat"] == "MOCK_OUTPUT_NO_ANALYTICAL_VALIDITY"
    package = [m for m in store.records_of("model_package") if m["model_id"] == "mock-damage-assessment"][0]
    assert package["accreditation_state"] == "UNACCREDITED"
    assert provider.run(projection, LOW_CONTEXT, "infra-BR-7", recorded_time=t(28.0)) is not None
    restricted_store, restricted_projection = build_scene(tmp_path.joinpath("r"), restrict_first_observation=True)
    hidden = MockAssessmentProvider(restricted_store).run(restricted_projection, LOW_CONTEXT, "obs-sensor-1",
                                                          recorded_time=t(27.0))
    assert hidden is None  # a provider cannot be aimed at a record the context cannot see


def test_recommendation_decision_guards(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    proposals = provider.run(projection, SERVICE_CONTEXT, recorded_time=t(27.0))
    workflow = WorkflowEngine(store)
    route_change = [p for p in proposals_of(proposals, "RECOMMENDATION_CANDIDATE")
                    if p["content"]["action_kind"] == "ROUTE_CHANGE"][0]
    result = workflow.materialize(route_change, actor_id="workflow", recorded_time=t(27.1))
    recommendation_id = result["recommendation_id"]
    record = [r for r in store.records_of("recommendation") if r["recommendation_id"] == recommendation_id][0]
    assert record["provider_id"] == "curunir-deterministic-rules"
    assert record["evidence_refs"] and all("@v" in ref or ref.startswith("evgroup") for ref in record["evidence_refs"])
    with pytest.raises(WorkflowError):  # a service actor cannot decide
        workflow.decide(recommendation_id, context=SERVICE_CONTEXT, state="ACCEPTED", rationale="x",
                        recorded_time=t(27.2), marking=BASE_MARKING)
    with pytest.raises(WorkflowError):  # deciding needs the SUPERVISOR role
        workflow.decide(recommendation_id, context=LOW_CONTEXT, state="ACCEPTED", rationale="x",
                        recorded_time=t(27.2), marking=BASE_MARKING)
    impostor = AccessContext("ctx-imp", "curunir-deterministic-rules", "HUMAN", ("SUPERVISOR",),
                             ("SENSITIVE-INFRA",), ("CORRIDOR-OPS",), "CIVDEF-AUTH")
    with pytest.raises(WorkflowError):  # the provider that recommended it cannot decide it
        workflow.decide(recommendation_id, context=impostor, state="ACCEPTED", rationale="x",
                        recorded_time=t(27.2), marking=BASE_MARKING)
    # A new version of the referenced object must not move the frozen snapshot.
    store.append("OBJECT_VERSION_APPENDED",
                 obj("route-R1", "ROUTE", version=2, hours=27.0,
                     geometry=Geometry("LINESTRING", ((-30.30, 45.10), (-30.10, 45.20), (-29.90, 45.30)))),
                 recorded_time=t(27.3), actor="fixture")
    decision = workflow.decide(recommendation_id, context=HIGH_CONTEXT, state="MODIFIED",
                               rationale="accepting alternate route with reduced load",
                               modification="split into two serials", recorded_time=t(27.4), marking=BASE_MARKING)
    assert decision["state"] == "MODIFIED"
    assert decision["evidence_snapshot_hash"] == record["evidence_snapshot_hash"]
    with pytest.raises(WorkflowError):  # analyst actions need a human too
        workflow.analyst_action(context=SERVICE_CONTEXT, kind="ACKNOWLEDGE", subject_kind="alert",
                                subject_id="alert-x", note="", recorded_time=t(27.5), marking=BASE_MARKING)


def test_alert_transitions_and_snapshot_recompute(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    proposals = provider.run(projection, SERVICE_CONTEXT, recorded_time=t(27.0))
    workflow = WorkflowEngine(store)
    alert_proposal = proposals_of(proposals, "ALERT_CANDIDATE", "rule-stale-stock")[0]
    alert_id = workflow.materialize(alert_proposal, actor_id="workflow", recorded_time=t(27.1))["alert_id"]
    with pytest.raises(WorkflowError):
        workflow.transition_alert(alert_id, "ACKNOWLEDGED", context=SERVICE_CONTEXT, note="",
                                  recorded_time=t(27.2), marking=BASE_MARKING)
    workflow.transition_alert(alert_id, "ACKNOWLEDGED", context=HIGH_CONTEXT, note="seen",
                              recorded_time=t(27.2), marking=BASE_MARKING)
    workflow.transition_alert(alert_id, "RESOLVED", context=HIGH_CONTEXT, note="fresh report arrived",
                              recorded_time=t(27.3), marking=BASE_MARKING)
    updated = Projection(store, snapshot_time=t(28.0))
    assert updated.alerts[alert_id]["status"] == "RESOLVED"
    assert [tr["to_status"] for tr in updated.alerts[alert_id]["transitions"]] == ["ACKNOWLEDGED", "RESOLVED"]
    alert_record = updated.alerts[alert_id]["record"]
    assert evidence_snapshot_hash(store, tuple(alert_record["evidence_refs"])) \
        == evidence_snapshot_hash(store, tuple(alert_record["evidence_refs"]))
