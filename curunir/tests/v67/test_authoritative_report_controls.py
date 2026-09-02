"""The report approval gate reads the stored state, not a filtered view of it,
and fails closed when it cannot see everything the report rests on."""
from __future__ import annotations

import pytest

from curunir_analytic.contracts import (
    OFFICIAL_STATUS_VOCABULARIES,
    SETTLED_SUPPORT_STATUSES,
    MissionObjective,
)
from curunir_analytic.impact import propose_response_option, review_response_option
from curunir_analytic.substrate import append_version
from curunir_operational.access import marking_from_record
from curunir_semantic.contracts import (
    HYPOTHESIS_SETTLED_SUPPORT_STATUSES,
    HYPOTHESIS_STATUSES,
    ClaimStateRecord,
)
from curunir_workbench.annotations import create_annotation
from curunir_workbench.projections import MissionProjection
from curunir_workbench.reports import (
    ReportValidationError,
    _next_version,
    approve_report,
    create_report,
    submit_report,
)

from semantic_support import MARK
from workbench_support import (
    CTX_A,
    CTX_B,
    RESTRICTED_MARK,
    make_workbench,
    seed_mission,
)

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, context = make_workbench(tmp_path)
    return context, seed_mission(pipeline, context)


def _projection(context, access=CTX_A):
    return MissionProjection(context.store, access)


def _draft(context, seeded, *, sentences=None, actor="analyst-a"):
    if sentences is None:
        sentences = [{
            "text": "Acme Industri AS holds an ISSUED GLEIF registration.",
            "status": "SUPPORTED",
            "basis_refs": [seeded["status_claim"]["claim_id"]],
        }]
    return create_report(
        context.store,
        actor=actor,
        marking=MARK,
        now=context.now_fn(),
        title="V6.7 authoritative control regression",
        question="May this dossier be approved?",
        sections=[{
            "kind": "key_judgments",
            "title": "Key judgments",
            "sentences": sentences,
        }],
        state_token=_projection(context).state_token,
    )


def _submit(context, report, *, actor="analyst-a"):
    return submit_report(
        context.store,
        report["report_id"],
        actor=actor,
        marking=MARK,
        now=context.now_fn(),
        expected_version=report["version"],
        state_token="test-state",
    )


def _approve(context, report, submitted, *, access=CTX_A,
             actor="supervisor", acknowledge_dissent=()):
    return approve_report(
        context.store,
        _projection(context, access),
        report["report_id"],
        actor=actor,
        actor_kind="HUMAN",
        marking=MARK,
        now=context.now_fn(),
        expected_version=submitted["version"],
        acknowledge_dissent=acknowledge_dissent,
    )


def _record_claim_state(context, claim_id, *, state, marking, state_id):
    now = context.now_fn()
    context.store.append(
        "SEMANTIC_CLAIM_STATE_RECORDED",
        ClaimStateRecord(
            state_id=state_id,
            claim_id=claim_id,
            state=state,
            reason=f"regression state {state}",
            caused_by="v67-regression",
            superseded_by="",
            actor_id="analyst-a",
            actor_kind="HUMAN",
            recorded_time=now,
            marking=marking,
        ),
        recorded_time=now,
        actor="analyst-a",
    )


def test_official_status_complements_are_fail_closed():
    assert set(SETTLED_SUPPORT_STATUSES) == set(OFFICIAL_STATUS_VOCABULARIES)
    for family, vocabulary in OFFICIAL_STATUS_VOCABULARIES.items():
        assert SETTLED_SUPPORT_STATUSES[family]
        assert SETTLED_SUPPORT_STATUSES[family] <= set(vocabulary)
    assert HYPOTHESIS_SETTLED_SUPPORT_STATUSES <= set(HYPOTHESIS_STATUSES)
    assert "ACHIEVED" not in SETTLED_SUPPORT_STATUSES["mission_objective"]


def test_hidden_dissent_blocks_filtered_approver_and_cleared_control_passes(mission):
    context, seeded = mission
    report = _draft(context, seeded)
    submitted = _submit(context, report)
    dissent = create_annotation(
        context.store,
        _projection(context, CTX_A),
        actor="analyst-a",
        marking=RESTRICTED_MARK,
        now=context.now_fn(),
        target_kind="workbench_report",
        target_id=report["report_id"],
        kind="DISSENT",
        text="compartmented objection",
    )
    with pytest.raises(ValueError, match="not cleared to view"):
        _approve(context, report, submitted, access=CTX_B)
    approved = _approve(
        context,
        report,
        submitted,
        acknowledge_dissent=(dissent["annotation_id"],),
    )
    assert approved["status"] == "APPROVED_WITH_DISSENT"


@pytest.mark.parametrize("basis_kind", ("claim", "observation", "forecast"))
def test_hidden_retraction_blocks_every_legal_basis_shape(mission, basis_kind):
    context, seeded = mission
    claim = seeded["status_claim"]
    refs = {
        "claim": claim["claim_id"],
        "observation": claim["observation_ids"][0],
        "forecast": seeded["forecast"]["forecast_id"],
    }
    report = _draft(context, seeded, sentences=[{
        "text": "The cited material supports this assessed judgment.",
        "status": "SUPPORTED" if basis_kind != "forecast"
        else "EXPLICITLY_INFERENTIAL",
        "basis_refs": [refs[basis_kind]],
        "inference_note": "authored forecast" if basis_kind == "forecast" else "",
    }])
    submitted = _submit(context, report)
    _record_claim_state(
        context,
        claim["claim_id"],
        state="RETRACTED",
        marking=RESTRICTED_MARK,
        state_id=f"hidden-retraction-{basis_kind}",
    )
    with pytest.raises(ValueError, match="not cleared to view"):
        _approve(context, report, submitted, access=CTX_B)


def test_visible_retraction_beneath_forecast_is_a_validation_failure(mission):
    context, seeded = mission
    report = _draft(context, seeded, sentences=[{
        "text": "Acme most likely remains operational.",
        "status": "EXPLICITLY_INFERENTIAL",
        "basis_refs": [seeded["forecast"]["forecast_id"]],
        "inference_note": "authored forecast",
    }])
    submitted = _submit(context, report)
    _record_claim_state(
        context,
        seeded["status_claim"]["claim_id"],
        state="RETRACTED",
        marking=MARK,
        state_id="visible-retraction-forecast",
    )
    with pytest.raises(ReportValidationError) as raised:
        _approve(context, report, submitted)
    assert "STALE_BASIS" in {finding["code"] for finding in raised.value.findings}


def test_terminal_objective_and_rejected_option_are_not_live_support(mission):
    context, seeded = mission
    objective = seeded["objective"]
    append_version(context, MissionObjective(
        objective_id=objective["objective_id"],
        version=context.store.next_analytic_version(
            "mission_objective", objective["objective_id"]),
        mission_context=objective["mission_context"],
        statement=objective["statement"],
        status="ACHIEVED",
        priority=objective["priority"],
        time_horizon=objective.get("time_horizon") or "",
        depends_on=tuple(tuple(pair) for pair in objective.get("depends_on", ())),
        assumption_ids=tuple(objective.get("assumption_ids", ())),
        change_reason="objective met",
        history=tuple(objective["history"]) + ("ACHIEVED",),
        recorded_time=context.now_fn(),
        marking=marking_from_record(objective["marking"]),
    ))
    option = propose_response_option(
        context,
        objective_id=objective["objective_id"],
        path_id=seeded["path"]["path_id"],
        description="Do not use this rejected option",
        tradeoffs=("cost",),
        uncertainty_note="unverified",
    )
    review_response_option(
        context,
        option["option_id"],
        accept=False,
        actor_id="supervisor",
        actor_kind="HUMAN",
        note="rejected",
    )
    report = create_report(
        context.store,
        actor="analyst-a",
        marking=MARK,
        now=context.now_fn(),
        title="Terminal object gate",
        question="Is this option live?",
        sections=[{
            "kind": "decision_options",
            "title": "Options",
            "option_ids": [option["option_id"]],
            "sentences": [{
                "text": "Options remain unresolved.",
                "status": "UNRESOLVED",
                "unresolved_reason": "not selected",
            }],
        }],
        state_token=_projection(context).state_token,
    )
    submitted = _submit(context, report)
    with pytest.raises(ReportValidationError) as raised:
        _approve(context, report, submitted)
    assert "CONTESTED_AS_SETTLED" in {
        finding["code"] for finding in raised.value.findings
    }


def test_submitter_identity_survives_missing_disposition(mission):
    context, seeded = mission
    report = _draft(context, seeded, actor="drafter")
    # Submitting writes the version first and the disposition second; this is
    # the state a crash between them leaves behind.
    submitted = _next_version(
        context.store,
        report,
        expected_version=1,
        actor="submitter",
        now=context.now_fn(),
        status="IN_REVIEW",
        change_note="submitted for review",
    )
    with pytest.raises(PermissionError, match="separation of duties"):
        _approve(context, report, submitted, actor="submitter")


def test_content_mutation_requires_an_attributed_author(mission):
    context, seeded = mission
    report = _draft(context, seeded)
    with pytest.raises(ValueError, match="content_author"):
        _next_version(
            context.store,
            report,
            expected_version=1,
            actor="ghost-editor",
            now=context.now_fn(),
            status="DRAFT",
            change_note="unattributed rewrite",
            title="silently rewritten title",
        )
