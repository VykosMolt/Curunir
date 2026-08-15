"""V3 collaboration, strategic-evidence workbench and operator harness."""
from __future__ import annotations

import re

import pytest

from curunir_operational.v3.models import (AccessMarkingV3, AnalystHandoff, CollaborativeAnnotation,
                                           DecisionRecordV3, EvidenceHandoff, Indicator,
                                           OperationalImplicationProposal, OperatorSession,
                                           OperatorStudyDefinition, ReviewRecord, StrategicHypothesis,
                                           SubjectiveFeedbackRecord, TaskEvent, TaskRecord, WarningAssessment)
from curunir_operational.v3.operator import (FORBIDDEN_COLLECTION, OperatorStudyHarness, StudyError,
                                             render_harness, render_static_baseline, scripted_validation)
from curunir_operational.v3.strategic import (StrategicWarningWorkbench, adapt_argus_evidence,
                                              render_strategic_html, strategic_view)
from curunir_operational.v3.workflow import CollaborativeWorkflow

from test_operational_v3_distributed import (FULL_ACCESS, MISSION, PARTNER_ACCESS, PUBLIC, T0, T1, T2,
                                             make_nodes, sync)

pytestmark = pytest.mark.no_db


def evidence_bundle(source_id: str, basis: str, *, dependence: tuple[str, ...] = (), review="HUMAN_REVIEWED"):
    return {
        "source_object": {"source_object_id": source_id, "content_sha256": (source_id[-1] * 64)},
        "document": {"document_id": f"doc-{source_id}", "authority_state": "AUTHORITATIVE"},
        "assertion": {"assertion_id": f"assert-{source_id}", "evidence_basis_id": basis,
                      "review_state": review, "mapping_status": "PRECISE"},
        "identity": {"status": "IDENTITY_CONFIRMED"},
        "admission": {"independence_status": "DEPENDENT" if dependence else "INDEPENDENT",
                      "claim_basis_status": "EXPLICIT_BASIS", "dependencies": []},
        "source_relationships": [
            {"relationship_type": "DERIVED_FROM", "source_object_a": source_id,
             "source_object_b": other} for other in dependence],
    }


def test_collaboration_annotation_handoff_review_task_decision(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    flow = CollaborativeWorkflow(node)
    annotation = CollaborativeAnnotation("ann-1", "route-R1", "Report needs verification", "SHARED",
                                         ("ev-1",), PUBLIC.to_record())
    ann_event = flow.annotation(annotation, actor_id="log-analyst", recorded_time=T0,
                                nonce="ann", mission_scope=MISSION)
    handoff = AnalystHandoff("handoff-1", "log-analyst", "CIVIL_PROTECTION_ENGINEER", MISSION,
                             ("route-R1",), ("ev-1",), ("Is bridge safe?",), T2,
                             PUBLIC.to_record(), "PENDING", ("engineering review",))
    handoff_event = flow.handoff(handoff, actor_id="log-analyst", recorded_time=T1,
                                 nonce="handoff", mission_scope=MISSION)
    review = ReviewRecord("review-1", "EVIDENCE_REVIEW", "route-R1", "log-analyst", None,
                          "REQUESTED", (), ("ev-1",))
    review_event = flow.review_request(review, actor_id="log-analyst", recorded_time=T1,
                                       nonce="review", marking=PUBLIC, mission_scope=MISSION)
    task = TaskRecord("task-1", MISSION, "civil-engineer", "ASSIGNED", (), ("ev-1",), PUBLIC.to_record())
    task_event = flow.assign_task(task, actor_id="log-analyst", recorded_time=T1,
                                  nonce="task", mission_scope=MISSION)
    accepted = flow.transition_task("task-1", "ACCEPTED", actor_id="civil-engineer",
                                    recorded_time=T2, nonce="accept", marking=PUBLIC,
                                    mission_scope=MISSION, parent_event_ids=(task_event,))
    completed = flow.transition_task("task-1", "COMPLETED", actor_id="civil-engineer",
                                     recorded_time="2026-04-01T03:00:00+00:00", nonce="complete",
                                     marking=PUBLIC, mission_scope=MISSION,
                                     parent_event_ids=(accepted,), evidence_snapshot=("ev-2",))
    decision = DecisionRecordV3("decision-1", "route-R1", "DEFERRED", "await engineering",
                                ("ev-1",), {}, "curunir-bounded-authorization-v3", PUBLIC.to_record())
    decision_event = flow.decision(decision, actor_id="joint-coordinator",
                                   recorded_time="2026-04-01T04:00:00+00:00", nonce="decision",
                                   mission_scope=MISSION, parent_event_ids=(review_event,))
    record_types = {event.payload["record_type"] for event in node.events()}
    assert {"annotation", "handoff", "review_request", "task_assignment", "task_acceptance",
            "task_completion", "decision"} <= record_types
    assert {ann_event, handoff_event, completed, decision_event} <= {event.event_id for event in node.events()}
    assert next(event for event in node.events() if event.event_id == ann_event).payload["state"] == "SHARED"


def test_annotation_is_not_accepted_operational_state(tmp_path):
    node = make_nodes(tmp_path)["LOGISTICS_NODE"]
    event_id = CollaborativeWorkflow(node).annotation(
        CollaborativeAnnotation("ann", "route", "I think closed", "ACCEPTED_ANALYTICAL_STATE", (),
                                PUBLIC.to_record()), actor_id="log-analyst", recorded_time=T0,
        nonce="ann", mission_scope=MISSION)
    projection = node.projection(PARTNER_ACCESS)
    assert event_id in {event["event_id"] for event in projection["union_records"]}
    assert not any(state["record_type"] == "route_status" for state in projection["state"])


def test_concurrent_task_assignment_and_access_policy_fail_closed(tmp_path):
    nodes = make_nodes(tmp_path)
    logistics, civil = nodes["LOGISTICS_NODE"], nodes["CIVIL_PROTECTION_NODE"]
    CollaborativeWorkflow(logistics).assign_task(
        TaskRecord("joint-task", MISSION, "log-team", "ASSIGNED", (), (), PUBLIC.to_record()),
        actor_id="log-analyst", recorded_time=T0, nonce="log-task", mission_scope=MISSION)
    CollaborativeWorkflow(civil).assign_task(
        TaskRecord("joint-task", MISSION, "civil-team", "ASSIGNED", (), (), PUBLIC.to_record()),
        actor_id="civil-engineer", recorded_time=T0, nonce="civil-task", mission_scope=MISSION)
    logistics.append_action(actor_id="joint-coordinator", action_type="ACCESS_POLICY_CHANGE",
                            event_type="POLICY", payload={"record_type": "access_policy",
                            "subject_id": "policy-1", "releasability": ["MISSION_PARTNERS"]},
                            marking=PUBLIC, recorded_time=T0, nonce="log-policy", mission_scope=MISSION)
    civil.append_action(actor_id="joint-coordinator", action_type="ACCESS_POLICY_CHANGE",
                        event_type="POLICY", payload={"record_type": "access_policy",
                        "subject_id": "policy-1", "releasability": ["CIVIL_ONLY"]},
                        marking=PUBLIC, recorded_time=T0, nonce="civil-policy", mission_scope=MISSION)
    sync(logistics, civil); sync(civil, logistics)
    projection = logistics.projection(PARTNER_ACCESS)
    task_conflict = next(conflict for conflict in projection["conflicts"]
                         if conflict["conflict_type"] == "TASK_ASSIGNMENT_CONFLICT")
    access = next(state for state in projection["state"] if state["record_type"] == "access_policy")
    assert task_conflict["status"] == "OPEN"
    assert access["status"] == "CONFLICT" and access["fail_closed"] is True


def test_strategic_evidence_adapter_hypotheses_corrections_retractions_and_handoffs(tmp_path):
    node = make_nodes(tmp_path)["STRATEGIC_EVIDENCE_NODE"]
    workbench = StrategicWarningWorkbench(node)
    derivative_a = adapt_argus_evidence(evidence_bundle("source-a", "basis-shared", dependence=("source-b",)))
    derivative_b = adapt_argus_evidence(evidence_bundle("source-b", "basis-shared", dependence=("source-a",)))
    independent = adapt_argus_evidence(evidence_bundle("source-c", "basis-official"))
    misleading = adapt_argus_evidence(evidence_bundle("source-d", "basis-rumor", review="UNREVIEWED"))
    assert derivative_a["dependence_group_id"] and derivative_b["dependence_group_id"]
    marking = AccessMarkingV3("STRAT-AUTH", releasability=("MISSION_PARTNERS",), mission_scopes=(MISSION,))
    workbench.source_dependence_group("dep-shared", ("source-a", "source-b"), "basis-shared",
                                      actor_id="strategic-analyst", recorded_time=T0,
                                      nonce="dep", marking=marking, mission_scope=MISSION)
    indicator_ids = []
    for index, (statement, evidence, group, state) in enumerate((
        ("Bulletin suggests fuel disruption", derivative_a, "dep-shared", "ACTIVE"),
        ("Derivative repeats bulletin", derivative_b, "dep-shared", "RETRACTED"),
        ("Official source confirms localized delay", independent, None, "CORRECTED"),
        ("Unofficial report claims complete closure", misleading, None, "RETRACTED"),
    ), start=1):
        indicator_ids.append(workbench.indicator(
            Indicator(f"indicator-{index}", statement, (evidence,), group, state, marking.to_record()),
            actor_id="strategic-analyst", recorded_time=f"2026-04-01T0{index}:00:00+00:00",
            nonce=f"indicator-{index}", mission_scope=MISSION))
    hypotheses = (
        StrategicHypothesis("hyp-viable", "Localized fuel delays will affect one corridor", MISSION,
                            {"from": T0, "to": None}, ("indicator-1", "indicator-3"), ("indicator-4",),
                            ("basis-shared", "basis-official"), ("dep-shared",), ("normal demand",),
                            {"evidence_strength": "MIXED", "calibration": "NOT_CALIBRATED"},
                            "VIABLE", "PEER_REVIEWED", ("impl-route",), ()),
        StrategicHypothesis("hyp-rejected", "All regional supply is cut", MISSION,
                            {"from": T0, "to": None}, ("indicator-4",), ("indicator-3",),
                            ("basis-rumor",), (), (), {"evidence_strength": "CONTRADICTED"},
                            "REJECTED", "EVIDENCE_REVIEWED", (), ()),
        StrategicHypothesis("hyp-unresolved", "Medical demand may rise", MISSION,
                            {"from": T0, "to": None}, ("indicator-1",), (), ("basis-shared",),
                            ("dep-shared",), ("demand unknown",), {"evidence_strength": "INSUFFICIENT"},
                            "UNRESOLVED", "UNDER_REVIEW", ("impl-medical",), ()),
    )
    hypothesis_events = [workbench.hypothesis(hypothesis, actor_id="strategic-analyst",
                                               recorded_time="2026-04-01T05:00:00+00:00",
                                               nonce=f"h-{hypothesis.hypothesis_id}", marking=marking,
                                               mission_scope=MISSION) for hypothesis in hypotheses]
    assessment = WarningAssessment("assessment-1", tuple(h.hypothesis_id for h in hypotheses),
                                   ("hyp-viable",), ("hyp-rejected",), ("hyp-unresolved",),
                                   ("Source count is not corroboration.", "Implication is not fact.",
                                    "Unresolved hypothesis is not a planning assumption.",
                                    "Available evidence does not support regional certainty."),
                                   tuple(indicator_ids))
    workbench.assessment(assessment, actor_id="strategic-analyst",
                         recorded_time="2026-04-01T06:00:00+00:00", nonce="assessment",
                         marking=marking, mission_scope=MISSION)
    implication = OperationalImplicationProposal("impl-route", "hyp-viable", "ROUTE_RISK",
                                                 "Request route monitoring", "DISPUTED",
                                                 tuple(indicator_ids), False)
    implication_event = workbench.implication(implication, actor_id="strategic-analyst",
                                              recorded_time="2026-04-01T07:00:00+00:00",
                                              nonce="imp", marking=marking, mission_scope=MISSION)
    handoff = EvidenceHandoff("evidence-handoff-log", (independent,), "hyp-viable",
                              "SHARED_LOGISTICS_VISIBILITY_WORKBENCH_V1", "ir-log-1",
                              "PEER_REVIEWED", "OPEN_INFORMATION_REQUIREMENT")
    workbench.evidence_handoff(handoff, actor_id="strategic-analyst",
                               recorded_time="2026-04-01T08:00:00+00:00", nonce="handoff",
                               marking=marking, mission_scope=MISSION,
                               parent_event_ids=(hypothesis_events[0], implication_event))
    view = strategic_view(node.projection(FULL_ACCESS))
    assert {item["analyst_state"] for item in view["hypotheses"]} == {"VIABLE", "REJECTED", "UNRESOLVED"}
    assert len(view["corrections"]) == 1 and len(view["retractions"]) == 2
    assert view["source_dependence_groups"][0]["independent_basis_count"] == 1
    assert view["implications"][0]["operational_state_mutated"] is False
    assert view["evidence_handoffs"][0]["provenance_preserved"] is True
    rendered = render_strategic_html(view)
    for semantic in ("Competing hypotheses", "Source dependence", "Implication is not fact",
                     "visible records only", "What the system refuses to conclude"):
        assert semantic in rendered
    assert not re.search(r'(src|href)=["\']https?://', rendered)


def study_definition():
    tasks = (
        {"task_id": "recognize-conflict", "prompt": "Identify the route conflict.",
         "expected_answer": {"fields": {"route_state": "CONFLICT"}},
         "evidence_path": ["route-R1", "conflict-route"], "policy_constraints": ["REVEAL_RESTRICTED_BASIS"]},
        {"task_id": "source-dependence", "prompt": "Count independent evidence bases.",
         "expected_answer": {"fields": {"independent_bases": 2}},
         "evidence_path": ["dep-shared", "basis-official"], "policy_constraints": []},
        {"task_id": "unresolved-hypothesis", "prompt": "State whether medical demand is resolved.",
         "expected_answer": {"fields": {"state": "UNRESOLVED"}},
         "evidence_path": ["hyp-unresolved"], "policy_constraints": []},
    )
    return OperatorStudyDefinition(
        "CURUNIR_OPERATOR_STUDY_V3", "3.0",
        ("TRAINING", "EVALUATION", "BASELINE_COMPARISON", "AFTER_ACTION_REVIEW"),
        ("STATIC_ARTIFACT_BASELINE", "CURUNIR_WORKBENCH"), tasks,
        ("correctness", "completion", "time", "evidence-trace correctness",
         "source-dependence recognition", "conflict recognition", "stale-data recognition",
         "observed versus inferred distinction", "inappropriate certainty", "confidence calibration",
         "report quality", "policy compliance", "workload", "trust", "usability issues"),
        FORBIDDEN_COLLECTION)


def test_operator_harness_baseline_instrumentation_scoring_privacy_and_export(tmp_path):
    definition = study_definition()
    harness = OperatorStudyHarness.initialize(tmp_path / "study", definition)
    baseline = render_static_baseline(definition, "Fair situation report", [{"route": "R1", "state": "CONFLICT"}],
                                      '<svg role="img"><title>Static exported map</title></svg>',
                                      "Two independent bases; derivative reports grouped.")
    ui = render_harness(definition, {"Strategic workbench": "strategic.html"})
    assert "Static-artifact baseline" in baseline and "Situation report" in baseline and "Data table" in baseline
    assert "does not collect keystrokes" in ui and "TASK_STARTED" in ui and "baseline.html" in ui
    session = OperatorSession("scripted-session", definition.study_id, "P-SCRIPTED-001", "JOINT_COORDINATOR",
                              "EVALUATION", "CURUNIR_WORKBENCH", T0)
    result = scripted_validation(harness, session, start_time=T0, end_time=T2)
    assert result["status"] == "SCRIPTED_HARNESS_VALID" and result["instrumentation_works"]
    export = harness.scoring_export()
    assert export["human_results"] == "NOT_RUN" and export["integrity_hash"]
    harness.feedback(SubjectiveFeedbackRecord("scripted-session", 3, 4, ("table density",), "scripted only"))
    with pytest.raises(StudyError, match="outside privacy boundary"):
        harness.record_event(TaskEvent("bad", "scripted-session", "recognize-conflict", "KEYSTROKE", T1, {}))
    with pytest.raises(StudyError, match="overcollects"):
        harness.record_event(TaskEvent("bad2", "scripted-session", "recognize-conflict", "VIEW_CHANGED", T1,
                                       {"raw_keystroke": "x"}))
