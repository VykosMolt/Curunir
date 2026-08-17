"""Report/dossier engine: sentence-level epistemic status, validation
rejections, human-only approval, dissent, immutable approved versions,
role projections, export."""
from __future__ import annotations

import pytest

from curunir_workbench.annotations import create_annotation
from curunir_workbench.projections import MissionProjection
from curunir_workbench.reports import (ReportConflict, ReportValidationError,
                                       approve_report, create_report, edit_report,
                                       export_html, export_package, reject_report,
                                       role_view, submit_report, validate_report)

from semantic_support import MARK
from workbench_support import CTX_A, CTX_B, make_workbench, seed_mission

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    return pipeline, ctx, seeded


def _projection(ctx, context=CTX_A):
    return MissionProjection(ctx.store, context)


def _draft(ctx, seeded, *, sentences=None):
    projection = _projection(ctx)
    claim_id = seeded["status_claim"]["claim_id"]
    sentences = sentences if sentences is not None else [
        {"text": "Acme Industri AS holds an ISSUED GLEIF registration.",
         "status": "SUPPORTED", "basis_refs": [claim_id]},
        {"text": "Acme most likely remains operational.",
         "status": "EXPLICITLY_INFERENTIAL", "basis_refs": [claim_id],
         "inference_note": "registry standing implies operation, per assumption"},
        {"text": "Ownership structure is unknown.",
         "status": "UNRESOLVED", "unresolved_reason": "no ownership evidence collected"},
    ]
    return create_report(
        ctx.store, actor="analyst-a", marking=MARK, now=ctx.now_fn(),
        title="Acme standing dossier", question="Is Acme a viable counterparty?",
        sections=[{"kind": "key_judgments", "title": "Key judgments",
                   "sentences": sentences}],
        state_token=projection.state_token)


def test_draft_validate_approve_lifecycle(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    assert report["status"] == "DRAFT" and report["version"] == 1
    validation = validate_report(_projection(ctx), report)
    assert validation["ok"], validation["blocking"]
    submitted = submit_report(ctx.store, report["report_id"], actor="analyst-a",
                              marking=MARK, now=ctx.now_fn(), expected_version=1,
                              state_token="tok")
    assert submitted["status"] == "IN_REVIEW"
    approved = approve_report(ctx.store, _projection(ctx), report["report_id"],
                              actor="supervisor", actor_kind="HUMAN", marking=MARK,
                              now=ctx.now_fn(), expected_version=submitted["version"])
    assert approved["status"] == "APPROVED"
    dispositions = ctx.store.report_dispositions(report["report_id"])
    assert [d["disposition"] for d in dispositions] == ["SUBMITTED", "APPROVED"]
    assert dispositions[-1]["actor_id"] == "supervisor"
    assert dispositions[-1]["validation_sha256"]


def test_unsupported_factual_sentence_blocks_approval(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded, sentences=[
        {"text": "Acme secretly controls three shell companies.",
         "status": "SUPPORTED", "basis_refs": []}])
    validation = validate_report(_projection(ctx), report)
    assert not validation["ok"]
    assert validation["blocking"][0]["code"] == "NO_EVIDENCE_BASIS"
    submit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                  now=ctx.now_fn(), expected_version=1, state_token="tok")
    with pytest.raises(ReportValidationError):
        approve_report(ctx.store, _projection(ctx), report["report_id"],
                       actor="supervisor", actor_kind="HUMAN", marking=MARK,
                       now=ctx.now_fn(), expected_version=2)


def test_unresolvable_and_inferential_basis_rejected(mission):
    _, ctx, seeded = mission
    forecast_id = seeded["forecast"]["forecast_id"]
    report = _draft(ctx, seeded, sentences=[
        {"text": "Acme is definitely fine.", "status": "SUPPORTED",
         "basis_refs": ["claim-does-not-exist"]},
        {"text": "The registry will lapse.", "status": "SUPPORTED",
         "basis_refs": [forecast_id]}])
    validation = validate_report(_projection(ctx), report)
    codes = {f["code"] for f in validation["blocking"]}
    assert "UNRESOLVABLE_BASIS" in codes
    assert "INFERENCE_AS_OBSERVATION" in codes


def test_forecast_probability_mismatch_rejected(mission):
    _, ctx, seeded = mission
    forecast_id = seeded["forecast"]["forecast_id"]
    report = _draft(ctx, seeded, sentences=[
        {"text": "The lapse probability stands at 0.80.",
         "status": "EXPLICITLY_INFERENTIAL", "basis_refs": [forecast_id],
         "inference_note": "authored forecast"}])
    validation = validate_report(_projection(ctx), report)
    assert any(f["code"] == "FORECAST_PROBABILITY_MISMATCH"
               for f in validation["blocking"])
    ok_report = _draft(ctx, seeded, sentences=[
        {"text": "The lapse probability stands at 0.35.",
         "status": "EXPLICITLY_INFERENTIAL", "basis_refs": [forecast_id],
         "inference_note": "authored forecast"}])
    assert validate_report(_projection(ctx), ok_report)["ok"]


def test_independence_misrepresentation_rejected(mission):
    _, ctx, seeded = mission
    claim_id = seeded["status_claim"]["claim_id"]
    report = _draft(ctx, seeded, sentences=[
        {"text": "Independent sources confirm the ISSUED status.",
         "status": "SUPPORTED", "basis_refs": [claim_id],
         "asserts_independent": True}])
    validation = validate_report(_projection(ctx), report)
    assert any(f["code"] == "INDEPENDENCE_MISREPRESENTED"
               for f in validation["blocking"])


def test_support_invisible_to_approver_blocks(mission):
    """Analyst B cannot approve a report resting on compartmented support."""
    _, ctx, seeded = mission
    report = _draft(ctx, seeded, sentences=[
        {"text": "A sensitive partner depends on Acme.",
         "status": "EXPLICITLY_INFERENTIAL",
         "basis_refs": [seeded["secret_assumption_id"]],
         "inference_note": "compartmented dependency assumption"}])
    assert validate_report(_projection(ctx, CTX_A), report)["ok"]
    validation_b = validate_report(_projection(ctx, CTX_B), report)
    assert any(f["code"] == "UNRESOLVABLE_BASIS" for f in validation_b["blocking"])


def test_service_actor_cannot_approve(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    submit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                  now=ctx.now_fn(), expected_version=1, state_token="tok")
    with pytest.raises(PermissionError):
        approve_report(ctx.store, _projection(ctx), report["report_id"],
                       actor="model-runner", actor_kind="SERVICE", marking=MARK,
                       now=ctx.now_fn(), expected_version=2)


def test_open_dissent_blocks_plain_approval(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    submitted = submit_report(ctx.store, report["report_id"], actor="analyst-a",
                              marking=MARK, now=ctx.now_fn(), expected_version=1,
                              state_token="tok")
    dissent = create_annotation(
        ctx.store, _projection(ctx, CTX_B), actor="analyst-b", marking=MARK,
        now=ctx.now_fn(), target_kind="workbench_report",
        target_id=report["report_id"], kind="DISSENT",
        text="The viability judgment overweights registry standing")
    with pytest.raises(ValueError, match="dissent"):
        approve_report(ctx.store, _projection(ctx), report["report_id"],
                       actor="supervisor", actor_kind="HUMAN", marking=MARK,
                       now=ctx.now_fn(), expected_version=submitted["version"])
    approved = approve_report(
        ctx.store, _projection(ctx), report["report_id"], actor="supervisor",
        actor_kind="HUMAN", marking=MARK, now=ctx.now_fn(),
        expected_version=submitted["version"],
        acknowledge_dissent=(dissent["annotation_id"],))
    assert approved["status"] == "APPROVED_WITH_DISSENT"
    disposition = ctx.store.report_dispositions(report["report_id"])[-1]
    assert disposition["dissent_annotation_ids"] == [dissent["annotation_id"]]


def test_approved_version_immutable_and_revision_is_new_version(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    submit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                  now=ctx.now_fn(), expected_version=1, state_token="tok")
    approved = approve_report(ctx.store, _projection(ctx), report["report_id"],
                              actor="supervisor", actor_kind="HUMAN", marking=MARK,
                              now=ctx.now_fn(), expected_version=2)
    revised = edit_report(
        ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
        now=ctx.now_fn(), expected_version=approved["version"],
        sections=[{"kind": "key_judgments", "title": "Key judgments",
                   "sentences": [{"text": "Revised judgment pending.",
                                  "status": "UNRESOLVED",
                                  "unresolved_reason": "under revision"}]}])
    assert revised["status"] == "DRAFT" and revised["version"] == approved["version"] + 1
    versions = ctx.store.report_versions(report["report_id"])
    frozen = next(v for v in versions if v["version"] == approved["version"])
    assert frozen["status"] == "APPROVED"
    assert frozen["sections"][0]["sentences"][0]["text"].startswith("Acme Industri")


def test_stale_edit_raises_conflict(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    edit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                now=ctx.now_fn(), expected_version=1,
                sections=[{"kind": "key_judgments", "title": "KJ",
                           "sentences": []}])
    with pytest.raises(ReportConflict):
        edit_report(ctx.store, report["report_id"], actor="analyst-b", marking=MARK,
                    now=ctx.now_fn(), expected_version=1,
                    sections=[{"kind": "key_judgments", "title": "KJ2",
                               "sentences": []}])


def test_reject_and_return_for_revision(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    submit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                  now=ctx.now_fn(), expected_version=1, state_token="tok")
    returned = reject_report(ctx.store, report["report_id"], actor="supervisor",
                             actor_kind="HUMAN", marking=MARK, now=ctx.now_fn(),
                             expected_version=2, note="tighten the inference",
                             return_for_revision=True)
    assert returned["status"] == "RETURNED_FOR_REVISION"
    resubmitted = submit_report(ctx.store, report["report_id"], actor="analyst-a",
                                marking=MARK, now=ctx.now_fn(),
                                expected_version=returned["version"],
                                state_token="tok")
    assert resubmitted["status"] == "IN_REVIEW"


def test_role_views_derive_from_one_state(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    projection = _projection(ctx)
    analyst = role_view(projection, report, "ANALYST")
    executive = role_view(projection, report, "EXECUTIVE")
    legal = role_view(projection, report, "SOURCE_LEGAL")
    operator = role_view(projection, report, "OPERATOR")
    assert analyst["version"] == executive["version"] == legal["version"] \
        == operator["version"] == report["version"]
    # executive keeps uncertainty visible
    assert executive["uncertainty_note"]["UNRESOLVED"] == 1
    # legal view expands lineage down to sources
    legal_sentence = legal["sections"][0]["sentences"][0]
    assert legal_sentence["lineage"][0]["observations"][0]["anchors"][0]["source"]["source_id"] == "gleif"
    # every projection preserves sentence status labels
    for view in (analyst, executive, legal):
        for section in view["sections"]:
            for sentence in section["sentences"]:
                assert sentence["status"] in ("SUPPORTED", "EXPLICITLY_INFERENTIAL",
                                              "UNRESOLVED")


def test_export_package_and_html(mission):
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    projection = _projection(ctx)
    package = export_package(projection, ctx.store, report["report_id"])
    assert package["format"] == "curunir-workbench-report-export-v1"
    assert package["report"]["report_id"] == report["report_id"]
    claim_id = seeded["status_claim"]["claim_id"]
    assert claim_id in package["basis"]
    html = export_html(projection, report)
    assert "[SUPPORTED]" in html and "[UNRESOLVED]" in html
    assert "doctype html" in html


def test_compartmented_dissent_invisible_to_approver_blocks(mission):
    # review B-3: an OPEN dissent the approver is not cleared to SEE must still
    # gate the approval (fail closed) — an approval must never assert "no dissent"
    # over a dissent merely invisible to the approver (the dissent analogue of
    # test_support_invisible_to_approver_blocks / UNRESOLVABLE_BASIS).
    from workbench_support import RESTRICTED_MARK
    _, ctx, seeded = mission
    report = _draft(ctx, seeded)
    submitted = submit_report(ctx.store, report["report_id"], actor="analyst-a",
                              marking=MARK, now=ctx.now_fn(), expected_version=1, state_token="tok")
    # a cleared analyst raises a SPECIAL (compartmented) dissent on the report
    create_annotation(
        ctx.store, _projection(ctx, CTX_A), actor="analyst-a", marking=RESTRICTED_MARK,
        now=ctx.now_fn(), target_kind="workbench_report", target_id=report["report_id"],
        kind="DISSENT", text="compartmented objection to the judgment")
    # the uncleared approver cannot SEE the dissent -> BLOCKED, not silently APPROVED
    with pytest.raises(ValueError, match="not cleared to view"):
        approve_report(ctx.store, _projection(ctx, CTX_B), report["report_id"],
                       actor="supervisor", actor_kind="HUMAN", marking=MARK,
                       now=ctx.now_fn(), expected_version=submitted["version"])
    # and a CLEARED approver still sees it and can carry it visibly (control)
    cleared = approve_report(ctx.store, _projection(ctx, CTX_A), report["report_id"],
                             actor="supervisor", actor_kind="HUMAN", marking=MARK,
                             now=ctx.now_fn(), expected_version=submitted["version"],
                             acknowledge_dissent=tuple(
                                 a["annotation_id"] for a in
                                 __import__("curunir_workbench.reports", fromlist=["_raw_open_dissent"])
                                 ._raw_open_dissent(ctx.store, report["report_id"])))
    assert cleared["status"] == "APPROVED_WITH_DISSENT"


def test_hidden_claim_state_on_cited_basis_blocks_approval(mission):
    # review R23B-3: a cited basis whose current claim-STATE the approver cannot
    # SEE must BLOCK approval (fail closed) — validate_report reads the filtered
    # projection and would silently count the retraction absent, letting an
    # approval render retracted evidence as settled fact (the dissent-gate fix,
    # swept to the basis gate).
    from workbench_support import RESTRICTED_MARK
    from curunir_semantic.contracts import ClaimStateRecord
    _, ctx, seeded = mission
    claim_id = seeded["status_claim"]["claim_id"]
    report = _draft(ctx, seeded)                                  # cites status_claim
    submitted = submit_report(ctx.store, report["report_id"], actor="analyst-a",
                              marking=MARK, now=ctx.now_fn(), expected_version=1, state_token="tok")
    # a benign VISIBLE current state lands first — it must NOT mask the later hidden
    # retraction (the append-family masking B1 exploited)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", ClaimStateRecord(
        state_id="cs-visible-0", claim_id=claim_id, state="CURRENT",
        reason="", caused_by="ingest", superseded_by="",
        actor_id="analyst-a", actor_kind="HUMAN",
        recorded_time=ctx.now_fn(), marking=MARK), recorded_time=ctx.now_fn(), actor="analyst-a")
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", ClaimStateRecord(
        state_id="cs-secret-1", claim_id=claim_id, state="RETRACTED",
        reason="retracted on compartmented evidence", caused_by="review-x",
        superseded_by="", actor_id="analyst-a", actor_kind="HUMAN",
        recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK),
        recorded_time=ctx.now_fn(), actor="analyst-a")
    with pytest.raises(ValueError, match="not cleared to view"):   # uncleared approver: BLOCKED
        approve_report(ctx.store, _projection(ctx, CTX_B), report["report_id"],
                       actor="supervisor", actor_kind="HUMAN", marking=MARK,
                       now=ctx.now_fn(), expected_version=submitted["version"])
