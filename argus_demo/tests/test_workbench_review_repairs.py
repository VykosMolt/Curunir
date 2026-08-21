"""Regression pins for the adversarial review findings: marking preservation
on re-appends, uniform hidden-id scrubbing, honest disposition attribution,
approval-state-machine holes, dissent gates, latest-wins for versionless
families, and error semantics."""
from __future__ import annotations

import json

import pytest

from curunir_operational.access import AccessContext
from curunir_semantic.contracts import ReviewItem
from curunir_workbench import commands
from curunir_workbench.annotations import create_annotation, resolve_annotation
from curunir_workbench.commands import CommandContext, Conflict
from curunir_workbench.projections import MissionProjection
from curunir_workbench.reports import (approve_report, create_report, edit_report,
                                       open_dissent, submit_report, validate_report)
from curunir_workbench.views import entity_dossier, review_queue

from semantic_support import MARK
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db

SERVICE_CTX = AccessContext("ctx-svc", "svc-bot", "SERVICE", ("ANALYST",),
                            compartments=("SPECIAL",), releasability=("PUBLIC",))


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    cc = lambda context: CommandContext(store=ctx.store, root=tmp_path,
                                        context=context, marking=MARK,
                                        now_fn=ctx.now_fn)
    return ctx, seeded, cc


def test_review_disposition_never_declassifies(mission):
    """Finding 1: a disposition re-append must keep the item's own marking."""
    ctx, seeded, cc = mission
    item = ReviewItem(item_id="ri-secret", kind="CONTRADICTED",
                      subject_kind="object", subject_id=seeded["secret_object_id"],
                      detail=f"contradiction touching {seeded['secret_object_id']}",
                      evidence_refs=(), status="OPEN", resolution_note="",
                      recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="analyst-a")
    assert MissionProjection(ctx.store, CTX_B).get("review_item", "ri-secret") is None
    commands.resolve_review_item(cc(CTX_A), "ri-secret", expected_version=1,
                                 status="RESOLVED", note="handled in compartment")
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("review_item", "ri-secret") is None
    blob = json.dumps(view_b.family("review_item")) + json.dumps(review_queue(view_b))
    assert "ri-secret" not in blob and seeded["secret_object_id"] not in blob


def test_public_review_of_hidden_subject_is_floored_and_invisible(mission):
    """V6.7 never-write-down supersedes view-time redaction.

    A review item materially derived from a restricted subject inherits that
    subject's marking at admission, so no alternate query path can expose a
    partially redacted PUBLIC shell.
    """
    ctx, seeded, cc = mission
    secret = seeded["secret_object_id"]
    item = ReviewItem(item_id="ri-public", kind="IDENTITY_AMBIGUITY",
                      subject_kind="object", subject_id=secret,
                      detail=f"possible equivalence {secret} ~ somewhere",
                      evidence_refs=(secret,), status="OPEN", resolution_note="",
                      recorded_time=ctx.now_fn(), marking=MARK, version=1)
    ctx.store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                     actor="analyst-a")
    record_b = MissionProjection(ctx.store, CTX_B).get("review_item", "ri-public")
    assert record_b is None
    record_a = MissionProjection(ctx.store, CTX_A).get("review_item", "ri-public")
    assert record_a["subject_id"] == secret
    assert record_a["marking"]["compartments"] == ["SPECIAL"]


def test_submit_disposition_records_real_actor_kind(mission):
    """Finding 3: the disposition log never asserts a human act that did not
    happen."""
    ctx, seeded, cc = mission
    report = commands.create_report(cc(SERVICE_CTX), title="svc draft",
                                    question="?", sections=[
                                        {"kind": "key_judgments", "title": "KJ",
                                         "sentences": []}])
    commands.submit_report(cc(SERVICE_CTX), report["report_id"], expected_version=1)
    disposition = ctx.store.report_dispositions(report["report_id"])[-1]
    assert disposition["actor_kind"] == "SERVICE"
    assert disposition["actor_id"] == "svc-bot"


def test_service_cannot_edit_and_supersession_is_recorded(mission):
    """Finding 4: no service actor voids an approved report; revising an
    approved version records SUPERSEDED against it."""
    ctx, seeded, cc = mission
    claim_id = seeded["status_claim"]["claim_id"]
    sections = [{"kind": "key_judgments", "title": "KJ", "sentences": [
        {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
         "basis_refs": [claim_id]}]}]
    report = commands.create_report(cc(CTX_A), title="approved dossier",
                                    question="?", sections=sections)
    commands.submit_report(cc(CTX_A), report["report_id"], expected_version=1)
    approved = commands.approve_report(cc(CTX_B), report["report_id"],
                                       expected_version=2)
    with pytest.raises(PermissionError):
        commands.edit_report(cc(SERVICE_CTX), report["report_id"],
                             expected_version=approved["version"],
                             sections=sections)
    commands.edit_report(cc(CTX_A), report["report_id"],
                         expected_version=approved["version"], sections=sections)
    dispositions = [d["disposition"] for d in
                    ctx.store.report_dispositions(report["report_id"])]
    assert "SUPERSEDED" in dispositions
    superseded = next(d for d in ctx.store.report_dispositions(report["report_id"])
                      if d["disposition"] == "SUPERSEDED")
    assert superseded["report_version"] == approved["version"]
    # original drafter attribution survives status transitions (finding 23)
    frozen = next(v for v in ctx.store.report_versions(report["report_id"])
                  if v["version"] == approved["version"])
    assert frozen["author"] == "analyst-a"


def test_sentence_anchored_dissent_blocks_approval(mission):
    """Finding 5: dissent below report level cannot dodge the gate."""
    ctx, seeded, cc = mission
    claim_id = seeded["status_claim"]["claim_id"]
    report = commands.create_report(cc(CTX_A), title="dossier",
                                    question="?", sections=[
        {"kind": "key_judgments", "title": "KJ", "sentences": [
            {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
             "basis_refs": [claim_id]}]}])
    sentence_id = report["sections"][0]["sentences"][0]["sentence_id"]
    submitted = commands.submit_report(cc(CTX_A), report["report_id"],
                                       expected_version=1)
    commands.annotate(cc(CTX_B), target_kind="report_sentence",
                      target_id=sentence_id, kind="DISSENT",
                      text="the registry basis is single-origin")
    projection = MissionProjection(ctx.store, CTX_A)
    assert open_dissent(projection, report["report_id"])
    # separation of duties: the drafter cannot approve at all
    with pytest.raises(PermissionError, match="separation of duties"):
        commands.approve_report(cc(CTX_A), report["report_id"],
                                expected_version=submitted["version"])
    # an independent approver is still blocked by the sentence-anchored dissent
    with pytest.raises(ValueError, match="dissent"):
        commands.approve_report(cc(CTX_B), report["report_id"],
                                expected_version=submitted["version"])
    # rewording the sentence does not detach the dissent (finding 7, round 2)
    commands.reject_report(cc(CTX_B), report["report_id"],
                           expected_version=submitted["version"],
                           note="revise wording", return_for_revision=True)
    current = ctx.store.current_reports()[report["report_id"]]
    reworded = commands.edit_report(cc(CTX_A), report["report_id"],
                                    expected_version=current["version"],
                                    sections=[
        {"kind": "key_judgments", "title": "KJ", "sentences": [
            {"text": "Acme holds an ISSUED registration. ", "status": "SUPPORTED",
             "basis_refs": [claim_id]}]}])
    resubmitted = commands.submit_report(cc(CTX_A), report["report_id"],
                                         expected_version=reworded["version"])
    with pytest.raises(ValueError, match="dissent"):
        commands.approve_report(cc(CTX_B), report["report_id"],
                                expected_version=resubmitted["version"])


def test_only_the_dissenting_author_resolves_dissent(mission):
    """Finding 6: the approver a dissent blocks cannot clear their own path."""
    ctx, seeded, cc = mission
    dissent = commands.annotate(cc(CTX_B), target_kind="hypothesis",
                                target_id=seeded["hypothesis"]["hypothesis_id"],
                                kind="DISSENT", text="disagree")
    with pytest.raises(PermissionError):
        commands.resolve_annotation(cc(CTX_A), dissent["annotation_id"],
                                    expected_version=1, status="RESOLVED",
                                    note="clearing my path")
    resolved = commands.resolve_annotation(cc(CTX_B), dissent["annotation_id"],
                                           expected_version=1, status="RESOLVED",
                                           note="satisfied by discussion")
    assert resolved["status"] == "RESOLVED"


def test_versionless_families_resolve_latest_by_log_order(mission):
    """Findings 8/10/11/12: pause state is projection-visible, authorship
    survives, re-creation is refused, overview counts the truth."""
    ctx, seeded, cc = mission
    watch = commands.create_watch(
        cc(CTX_A), need_id="need-x", target_kind="NATIVE_OBJECT",
        target_ref="LEI:X", source_id="gleif", operation="LOOKUP",
        query_value="X", cadence_seconds=3600)
    assert MissionProjection(ctx.store, CTX_A).overview()["counts"]["watches_active"] == 1
    commands.set_watch_active(cc(CTX_B), watch["watch_id"], active=False,
                              expected_active=True)
    projection = MissionProjection(ctx.store, CTX_A)
    current = projection.get("fabric_watch", watch["watch_id"])
    assert current["active"] is False
    assert current["created_by"] == "analyst-a"
    assert projection.overview()["counts"]["watches_active"] == 0


def test_source_registry_visible_and_not_hidden(mission):
    """Finding 9: profiles/status are registry metadata; source ids never
    poison the hidden set."""
    ctx, seeded, cc = mission
    projection = MissionProjection(ctx.store, CTX_B)
    assert projection.family("fabric_source_profile")
    assert "gleif" not in projection.hidden_ids
    note = commands.annotate(cc(CTX_A), target_kind="fabric_source_descriptor",
                             target_id="gleif", kind="NOTE", text="registry source")
    assert note["target_id"] == "gleif"
    assert MissionProjection(ctx.store, CTX_A).annotations_for("gleif")


def test_historical_as_current_derived_from_basis(mission):
    """Finding 18: omitting temporal_scope does not opt out."""
    ctx, seeded, cc = mission
    from curunir_semantic.contracts import SemanticClaim
    raw = ctx.store.current_claims()[seeded["status_claim"]["claim_id"]]
    expired = SemanticClaim(**{
        **{k: v for k, v in raw.items() if k != "record_type"},
        "observation_ids": tuple(raw["observation_ids"]),
        "dependence_group_ids": tuple(raw["dependence_group_ids"]),
        "world_refs": tuple(raw["world_refs"]),
        "valid_to": "2026-08-01T00:00:00+00:00",
        "version": raw["version"] + 1, "recorded_time": ctx.now_fn()})
    ctx.store.append("SEMANTIC_CLAIM_RECORDED", expired,
                     recorded_time=expired.recorded_time, actor="t")
    report = commands.create_report(cc(CTX_A), title="temporal", question="?",
                                    sections=[
        {"kind": "key_judgments", "title": "KJ", "sentences": [
            {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
             "basis_refs": [seeded["status_claim"]["claim_id"]]}]}])
    validation = validate_report(MissionProjection(ctx.store, CTX_A), report)
    assert any(f["code"] == "HISTORICAL_AS_CURRENT" for f in validation["blocking"])


def test_probability_checked_against_authored_versions(mission):
    """Finding 21: every quoted probability must be an authored version."""
    ctx, seeded, cc = mission
    forecast_id = seeded["forecast"]["forecast_id"]
    commands.move_forecast(cc(CTX_A), forecast_id, expected_version=1,
                           probability=0.57, probability_basis="evidence",
                           change_reason="movement")
    ok = commands.create_report(cc(CTX_A), title="probmove", question="?",
                                sections=[
        {"kind": "forecasts", "title": "F", "sentences": [
            {"text": "The forecast moved from 0.35 to 0.57.",
             "status": "EXPLICITLY_INFERENTIAL", "basis_refs": [forecast_id],
             "inference_note": "authored history"}]}])
    assert validate_report(MissionProjection(ctx.store, CTX_A), ok)["ok"]
    bad = commands.create_report(cc(CTX_A), title="probfake", question="?",
                                 sections=[
        {"kind": "forecasts", "title": "F", "sentences": [
            {"text": "The forecast stands at 0.90.",
             "status": "EXPLICITLY_INFERENTIAL", "basis_refs": [forecast_id],
             "inference_note": "authored"}]}])
    validation = validate_report(MissionProjection(ctx.store, CTX_A), bad)
    assert any(f["code"] == "FORECAST_PROBABILITY_MISMATCH"
               for f in validation["blocking"])


def test_malformed_report_body_is_400_not_404(mission):
    """Finding 19: KeyError from malformed input is never an existence claim."""
    ctx, seeded, cc = mission
    with pytest.raises(ValueError):
        commands.create_report(cc(CTX_A), title="broken", question="?",
                               sections=[{"kind": "key_judgments",
                                          "sentences": []}])  # no title


def test_theme_appears_in_entity_dossier(mission):
    """Findings 13/14: dossier joins use the real contract fields."""
    ctx, seeded, cc = mission
    from curunir_analytic.themes import create_theme
    from curunir_analytic.substrate import AnalyticContext
    acme_object = seeded["status_claim"]["subject_object_id"]
    actx = AnalyticContext(store=ctx.store, actor="t", marking=MARK,
                           now_fn=ctx.now_fn)
    theme = create_theme(
        actx, title="Acme registry standing", description="registry theme",
        supporting_claim_ids=(seeded["status_claim"]["claim_id"],),
        entity_ids=(acme_object,))
    projection = MissionProjection(ctx.store, CTX_A)
    dossier = entity_dossier(projection, acme_object)
    assert any(t["theme_id"] == theme["theme_id"] for t in dossier["themes"])
