"""Providers and workflow: rule output becomes proposals, proposals become alerts
and recommendations, and only a human decides."""
from __future__ import annotations

import pytest

from curunir_operational.access import AccessContext, can_view
from curunir_operational.analytics import DeterministicRuleProvider, MockAssessmentProvider
from curunir_operational.contracts import ExternalRef, ObjectVersion
from curunir_operational.geometry import Geometry
from curunir_operational.projection import Projection
from curunir_operational.workflow import WorkflowEngine, WorkflowError, evidence_snapshot_hash

from operational_support import (BASE_MARKING, HIGH_CONTEXT, LOW_CONTEXT, PROV, RESTRICTED_MARKING,
                                 SERVICE_CONTEXT, T0, build_scene, evidence_ref, make_store, obj, rel, t)

pytestmark = pytest.mark.no_db

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
    provider._start(projection, t(27.0))
    inference = provider._record(inputs={"route-R1": projection.objects["route-R1"]["current"]},
                                 output={"rule_id": "rule-x"}, recorded_time=t(27.0), downstream=("route-R1",),
                                 errors=("output schema violation",))
    proposal = provider._propose(inference, "ALERT_CANDIDATE",
                                 {"rule_id": "rule-x", "rule_version": "1.0", "trigger": "bad",
                                  "affected_ids": ["route-R1"], "evidence_refs": ["route-R1"],
                                  "severity": "INFO", "severity_rationale": "n/a", "dedup_key": "bad:1"},
                                 t(27.1))
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


def test_provider_refuses_output_that_names_an_object_it_did_not_read(tmp_path):
    store, projection = build_scene(tmp_path)
    provider = DeterministicRuleProvider(store)
    provider._start(projection, t(27.0))
    read = {"route-R1": projection.objects["route-R1"]["current"]}
    with pytest.raises(ValueError, match="infra-BR-7"):
        provider._record(inputs=read, output={"rule_id": "x", "note": "depends on infra-BR-7"},
                         recorded_time=t(27.0), downstream=("route-R1",))
    inference = provider._record(inputs=read, output={"rule_id": "x"}, recorded_time=t(27.0),
                                 downstream=("route-R1",))
    with pytest.raises(ValueError, match="mv-relief-1"):
        provider._propose(inference, "ALERT_CANDIDATE", {"trigger": "mv-relief-1 at risk"}, t(27.1))


def test_inference_marking_is_the_join_of_everything_it_read(tmp_path):
    store, projection = build_scene(tmp_path, restrict_first_observation=True)
    provider = DeterministicRuleProvider(store)
    provider._start(projection, t(27.0))
    inference = provider._record(inputs={o: projection.objects[o]["current"] for o in ("route-R1", "obs-sensor-1")},
                                 output={"rule_id": "x"}, recorded_time=t(27.0), downstream=("route-R1",))
    assert not can_view(inference.marking, LOW_CONTEXT) and can_view(inference.marking, HIGH_CONTEXT)
    proposal = provider._propose(inference, "ALERT_CANDIDATE", {"trigger": "obs-sensor-1 reports"}, t(27.1))
    assert not can_view(proposal["marking"], LOW_CONTEXT)
