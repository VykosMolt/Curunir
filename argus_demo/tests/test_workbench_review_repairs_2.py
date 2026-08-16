"""Regression pins for the second adversarial review: requirement-fold
declassification, base-view scrubbing, workflow visibility, command-response
redaction, operational hidden ids, dict-key scrubbing, watch oracle,
separation of duties."""
from __future__ import annotations

import json

import pytest

from curunir_operational.access import AccessContext, Marking
from curunir_operational.contracts import ActivityRecord, ProvenanceSummary
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, Conflict
from curunir_workbench.errors import NotFound
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    cc = lambda context: CommandContext(store=ctx.store, root=tmp_path,
                                        context=context, marking=MARK,
                                        now_fn=ctx.now_fn)
    return ctx, seeded, cc


def _restricted_requirement(ctx, seeded):
    from curunir_operational.missions import MissionWorkflow
    return MissionWorkflow(ctx.store).open_requirement(
        mission_context="compartment", question="Who supplies the partner?",
        affected_ids=(seeded["secret_object_id"],), priority="HIGH",
        rationale=f"depends on {seeded['secret_object_id']}",
        required_evidence_type="OPEN_SOURCE", owning_role="ANALYST",
        closure_criteria="answered", due_time=None,
        recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK, actor="analyst-a")


def test_requirement_fold_neither_discloses_nor_declassifies(mission):
    """Round-2 C1: a colliding open_requirement from an uncleared actor is
    refused without content; a cleared actor's fold keeps the marking."""
    ctx, seeded, cc = mission
    secret_requirement = _restricted_requirement(ctx, seeded)
    with pytest.raises(PermissionError):
        commands.open_requirement(cc(CTX_B), question="Who supplies the partner?",
                                  priority="CRITICAL", mission_context="compartment",
                                  rationale="my own question")
    # nothing about the restricted requirement reached B
    view_b = MissionProjection(ctx.store, CTX_B)
    blob = json.dumps(view_b.base_view["information_requirements"])
    assert secret_requirement["requirement_id"] not in blob
    # a CLEARED actor's fold escalates priority but keeps the SPECIAL marking
    folded = commands.open_requirement(cc(CTX_A), question="Who supplies the partner?",
                                       priority="CRITICAL", mission_context="compartment",
                                       rationale="escalation")
    assert folded["priority"] == "CRITICAL"
    raw = [r for r in ctx.store.records_of("information_requirement")
           if r["requirement_id"] == secret_requirement["requirement_id"]][-1]
    assert raw["marking"]["compartments"] == ["SPECIAL"]
    assert MissionProjection(ctx.store, CTX_B).base_view["information_requirements"] == [] \
        or secret_requirement["requirement_id"] not in json.dumps(
            MissionProjection(ctx.store, CTX_B).base_view["information_requirements"])


def test_base_view_is_scrubbed_of_hidden_ids(mission):
    """Round-2 C2 + round-5 C3: base_view is scrubbed of hidden ids, AND a
    requirement that CITES a compartmented assumption is itself compartmented
    (round 5) — a strictly stronger guarantee than scrubbing a PUBLIC record."""
    ctx, seeded, cc = mission
    secret_assumption = seeded["secret_assumption_id"]
    # round 5: the requirement inherits the assumption's SPECIAL marking, so
    # analyst-b never sees the requirement at all
    special = commands.open_requirement(
        cc(CTX_A), question="Does the dependency assumption hold?",
        priority="MEDIUM", mission_context="acme-mission",
        rationale=f"tests {secret_assumption}",
        affected_ids=(secret_assumption,))
    view_b = MissionProjection(ctx.store, CTX_B)
    blob = json.dumps(view_b.base_view) + json.dumps(view_b.overview())
    assert secret_assumption not in blob
    assert not any(r["requirement_id"] == special["requirement_id"]
                   for r in view_b.base_view["information_requirements"])
    # the base-view SCRUB itself still holds for a genuinely PUBLIC record
    # that references hidden state through a non-marking-bearing field
    from curunir_semantic.contracts import ReviewItem
    item = ReviewItem(item_id="ri-bv", kind="STALE_BASIS", subject_kind="object",
                      subject_id=seeded["status_claim"]["claim_id"],
                      detail=f"rests on {secret_assumption}", evidence_refs=(),
                      status="OPEN", resolution_note="", recorded_time=ctx.now_fn(),
                      marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="analyst-a")
    served = MissionProjection(ctx.store, CTX_B).get("review_item", "ri-bv")
    assert secret_assumption not in json.dumps(served) and "REDACTED" in served["detail"]


def test_workflow_transition_requires_visibility(mission):
    """Round-2 C3: hidden subjects can be neither mutated nor enumerated."""
    ctx, seeded, cc = mission
    secret_requirement = _restricted_requirement(ctx, seeded)
    with pytest.raises(NotFound):
        commands.transition_workflow(cc(CTX_B), subject_kind="requirement",
                                     subject_id=secret_requirement["requirement_id"],
                                     to_status="CLOSED_UNANSWERED", note="probe")
    with pytest.raises(NotFound):
        commands.transition_workflow(cc(CTX_B), subject_kind="requirement",
                                     subject_id="req-does-not-exist",
                                     to_status="CLOSED_UNANSWERED", note="probe")
    from curunir_workbench.commands import CommandError
    with pytest.raises(CommandError):
        commands.transition_workflow(cc(CTX_B), subject_kind="bogus_kind",
                                     subject_id="x", to_status="DONE")


def test_operational_hidden_ids_are_scrubbed(mission):
    """Round-2 C5: a hidden ACTIVITY id scrubs out of visible records."""
    ctx, seeded, cc = mission
    now = ctx.now_fn()
    secret_activity = ActivityRecord(
        activity_id="act-secret-meeting", activity_type="MEETING",
        epistemic_state="REPORTED", subject_ids=(seeded["secret_object_id"],),
        description="compartmented meeting", valid_from=None, valid_to=None,
        source_time=None, recorded_time=now, evidence_refs=(),
        marking=RESTRICTED_MARK, provenance=ProvenanceSummary(mode="OPERATIONAL"),
        participants=(), time_precision="UNKNOWN")
    ctx.store.append("ACTIVITY_RECORDED", secret_activity, recorded_time=now,
                     actor="analyst-a")
    from curunir_semantic.contracts import ReviewItem
    item = ReviewItem(item_id="ri-act", kind="STALE_BASIS",
                      subject_kind="activity", subject_id="act-secret-meeting",
                      detail="basis rests on act-secret-meeting",
                      evidence_refs=("act-secret-meeting",), status="OPEN",
                      resolution_note="", recorded_time=ctx.now_fn(),
                      marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="analyst-a")
    record_b = MissionProjection(ctx.store, CTX_B).get("review_item", "ri-act")
    assert "act-secret-meeting" not in json.dumps(record_b)


def test_command_responses_are_scrubbed_over_http(mission, tmp_path):
    """Round-2 C4: a write path never returns state its author could not
    read — the HTTP response is redacted like any projection."""
    from fastapi.testclient import TestClient
    from curunir_workbench.auth import write_registry
    from curunir_workbench.server import create_app
    from semantic_support import clock
    ctx, seeded, cc = mission
    from curunir_semantic.contracts import ReviewItem
    item = ReviewItem(item_id="ri-http", kind="CONTRADICTED",
                      subject_kind="object", subject_id=seeded["secret_object_id"],
                      detail=f"ambiguity around {seeded['secret_object_id']}",
                      evidence_refs=(seeded["secret_object_id"],), status="OPEN",
                      resolution_note="", recorded_time=ctx.now_fn(),
                      marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="analyst-a")
    actors = tmp_path / "actors2.json"
    write_registry(actors, [
        {"token": "token-b", "actor_id": "analyst-b", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "releasability": ["PUBLIC"],
         "organisation": "workbench-test"}])
    client = TestClient(create_app(tmp_path, actors, now_fn=clock(start_minute=700)))
    response = client.post("/api/commands/review/ri-http/resolve",
                           headers={"Authorization": "Bearer token-b"},
                           json={"expected_version": 1, "status": "DISMISSED",
                                 "note": "noted"})
    assert response.status_code == 200
    assert seeded["secret_object_id"] not in response.text


def test_dict_keys_are_scrubbed(mission):
    """Round-2 M6: a hidden id used as a mapping KEY is redacted too."""
    ctx, seeded, cc = mission
    saved = commands.save_view(cc(CTX_A), title="pinned", view_kind="graph",
                               definition={"pins": {seeded["secret_object_id"]: True},
                                           "note": f"focus {seeded['secret_object_id']}"})
    record_b = MissionProjection(ctx.store, CTX_B).get("workbench_saved_view",
                                                       saved["view_id"])
    assert seeded["secret_object_id"] not in json.dumps(record_b)


def test_invisible_watch_collision_is_generic_refusal(mission):
    """Round-2 M8: colliding with a hidden watch is not a 409 oracle."""
    ctx, seeded, cc = mission
    restricted = CommandContext(store=ctx.store, root=None, context=CTX_A,
                                marking=RESTRICTED_MARK, now_fn=ctx.now_fn)
    commands.create_watch(restricted, need_id="n", target_kind="NATIVE_OBJECT",
                          target_ref="LEI:Y", source_id="gleif",
                          operation="LOOKUP", query_value="Y",
                          cadence_seconds=3600)
    with pytest.raises(PermissionError):
        commands.create_watch(cc(CTX_B), need_id="n", target_kind="NATIVE_OBJECT",
                              target_ref="LEI:Y", source_id="gleif",
                              operation="LOOKUP", query_value="Y",
                              cadence_seconds=3600)
