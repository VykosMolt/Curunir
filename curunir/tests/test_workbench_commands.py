"""Workbench commands: every act is attributed, some are human-only, and stale
writers are refused."""
from __future__ import annotations

import pytest

from curunir_operational.access import AccessContext
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, Conflict
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db

SERVICE_CTX = AccessContext("ctx-svc", "model-runner", "SERVICE", ("ANALYST",),
                            compartments=("SPECIAL",), releasability=("PUBLIC",))


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    root = tmp_path
    cc = lambda context: CommandContext(store=ctx.store, root=root,
                                        context=context, marking=MARK,
                                        now_fn=ctx.now_fn)
    return ctx, seeded, cc


def test_annotation_lifecycle_and_conflict(mission):
    ctx, seeded, cc = mission
    a = commands.annotate(cc(CTX_A), target_kind="analytic_forecast",
                          target_id=seeded["forecast"]["forecast_id"],
                          kind="NOTE", text="watch the horizon")
    assert a["author"] == "analyst-a" and a["version"] == 1
    resolved = commands.resolve_annotation(cc(CTX_A), a["annotation_id"],
                                           expected_version=1, status="RESOLVED",
                                           note="considered")
    assert resolved["status"] == "RESOLVED"
    with pytest.raises(Conflict):
        commands.resolve_annotation(cc(CTX_A), a["annotation_id"],
                                    expected_version=1, status="WITHDRAWN",
                                    note="stale writer")


def test_annotation_cannot_target_hidden_object(mission):
    ctx, seeded, cc = mission
    with pytest.raises(LookupError):
        commands.annotate(cc(CTX_B), target_kind="object",
                          target_id=seeded["secret_object_id"],
                          kind="NOTE", text="I should not see this")
    with pytest.raises(LookupError):
        commands.annotate(cc(CTX_B), target_kind="analytic_assumption",
                          target_id=seeded["secret_assumption_id"],
                          kind="NOTE", text="nope")


def test_task_completion_requires_assigned_human(mission):
    ctx, seeded, cc = mission
    task_id = seeded["task"]["task_id"]
    commands.transition_workflow(cc(CTX_B), subject_kind="analyst_task",
                                 subject_id=task_id, to_status="IN_PROGRESS")
    from curunir_operational.missions import MissionWorkflowError
    with pytest.raises(MissionWorkflowError):
        commands.transition_workflow(cc(CTX_A), subject_kind="analyst_task",
                                     subject_id=task_id, to_status="DONE",
                                     note="not my task")
    done = commands.transition_workflow(cc(CTX_B), subject_kind="analyst_task",
                                        subject_id=task_id, to_status="DONE",
                                        note="rechecked")
    assert done["to_status"] == "DONE" and done["actor_id"] == "analyst-b"


def test_forecast_movement_is_human_authored_with_conflict(mission):
    ctx, seeded, cc = mission
    forecast_id = seeded["forecast"]["forecast_id"]
    with pytest.raises(PermissionError):
        commands.move_forecast(cc(SERVICE_CTX), forecast_id, expected_version=1,
                               probability=0.9, probability_basis="machine says",
                               change_reason="model output")
    moved = commands.move_forecast(cc(CTX_A), forecast_id, expected_version=1,
                                   probability=0.57,
                                   probability_basis="lapse notice observed",
                                   change_reason="new registry evidence")
    assert moved["probability"] == 0.57 and moved["version"] == 2
    with pytest.raises(Conflict):
        commands.move_forecast(cc(CTX_A), forecast_id, expected_version=1,
                               probability=0.6, probability_basis="stale",
                               change_reason="stale view")
    versions = MissionProjection(ctx.store, CTX_A).versions(
        "analytic_forecast", forecast_id)
    assert [v["probability"] for v in versions] == [0.35, 0.57]


def test_hypothesis_assessment_recorded_not_overwritten(mission):
    ctx, seeded, cc = mission
    hypothesis_id = seeded["hypothesis"]["hypothesis_id"]
    current = ctx.store.current_hypotheses()[hypothesis_id]
    assessed = commands.assess_hypothesis(
        cc(CTX_A), hypothesis_id, expected_version=current["version"],
        status="WEAKLY_SUPPORTED", rationale="single-origin registry basis")
    assert assessed["status"] == "WEAKLY_SUPPORTED"
    assert any("ANALYST_ASSESSED:analyst-a" in h for h in assessed["history"])
    with pytest.raises(PermissionError):
        commands.assess_hypothesis(cc(SERVICE_CTX), hypothesis_id,
                                   expected_version=assessed["version"],
                                   status="SUPPORTED", rationale="model opinion")
    dissent = commands.annotate(cc(CTX_B), target_kind="hypothesis",
                                target_id=hypothesis_id, kind="DISSENT",
                                text="registry basis is stronger than stated")
    assert dissent["kind"] == "DISSENT"
    still = ctx.store.current_hypotheses()[hypothesis_id]
    assert still["status"] == "WEAKLY_SUPPORTED"


def test_review_item_dismissal_keeps_history(mission):
    ctx, seeded, cc = mission
    from curunir_semantic.contracts import ReviewItem
    item = ReviewItem(item_id="ri-test", kind="CONTRADICTED",
                      subject_kind="semantic_claim",
                      subject_id=seeded["status_claim"]["claim_id"],
                      detail="synthetic contradiction for disposition test",
                      evidence_refs=(), status="OPEN", resolution_note="",
                      recorded_time=ctx.now_fn(), marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="t")
    with pytest.raises(PermissionError):
        commands.resolve_review_item(cc(SERVICE_CTX), "ri-test",
                                     expected_version=1, status="RESOLVED",
                                     note="service resolution")
    dismissed = commands.resolve_review_item(cc(CTX_A), "ri-test",
                                             expected_version=1,
                                             status="DISMISSED",
                                             note="duplicate of tracked issue")
    assert dismissed["status"] == "DISMISSED"
    versions = MissionProjection(ctx.store, CTX_A).versions("review_item", "ri-test")
    assert [v["status"] for v in versions] == ["OPEN", "DISMISSED"]


def test_watch_create_pause_resume(mission):
    ctx, seeded, cc = mission
    watch = commands.create_watch(
        cc(CTX_A), need_id="need-acme", target_kind="NATIVE_OBJECT",
        target_ref="LEI:ACMELEI000000000001", source_id="gleif",
        operation="LOOKUP", query_value="ACMELEI000000000001",
        cadence_seconds=3600)
    assert watch["active"] is True and watch["created_by"] == "analyst-a"
    paused = commands.set_watch_active(cc(CTX_A), watch["watch_id"], active=False)
    assert paused["active"] is False
    resumed = commands.set_watch_active(cc(CTX_B), watch["watch_id"], active=True,
                                        expected_active=False)
    assert resumed["active"] is True and resumed["created_by"] == "analyst-a"
    with pytest.raises(Conflict):
        commands.set_watch_active(cc(CTX_A), watch["watch_id"], active=False,
                                  expected_active=False)  # stale view
    with pytest.raises(Conflict):
        commands.create_watch(
            cc(CTX_B), need_id="need-acme", target_kind="NATIVE_OBJECT",
            target_ref="LEI:ACMELEI000000000001", source_id="gleif",
            operation="LOOKUP", query_value="ACMELEI000000000001",
            cadence_seconds=3600)  # silent re-creation refused


def test_requirement_open_and_fold(mission):
    ctx, seeded, cc = mission
    first = commands.open_requirement(
        cc(CTX_A), question="Who audits Acme?", priority="MEDIUM",
        mission_context="acme-mission", rationale="counterparty diligence")
    again = commands.open_requirement(
        cc(CTX_B), question="Who audits Acme?", priority="HIGH",
        mission_context="acme-mission", rationale="escalated")
    assert again["requirement_id"] == first["requirement_id"]
    assert again["priority"] == "HIGH"
