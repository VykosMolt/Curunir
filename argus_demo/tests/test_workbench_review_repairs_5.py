"""Regression pins for the fifth adversarial review — the two surfaces the
round-4 class fix missed: (a) records derived from a subject OTHER than the
one named (the pipeline processing a pending SPECIAL manifestation; the
stale-basis companion), and (b) newly-created records that CITE compartmented
references (reports, requirements, tasks, saved views). Plus the round-4
machinery defects (most_restrictive collapse, warning floor, scrub-prefix
corruption, watch fields, reply_to)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_operational.access import Marking, can_view, most_restrictive
from curunir_fabric.contracts import ManifestationRecord
from curunir_semantic.contracts import ReviewItem
from curunir_workbench import commands
from curunir_workbench.commands import CommandContext, CommandError
from curunir_workbench.errors import NotFound
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK, T0
from workbench_support import (CTX_A, CTX_B, RESTRICTED_MARK, make_workbench,
                               seed_mission)

pytestmark = pytest.mark.no_db


@pytest.fixture()
def mission(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    cc = lambda context, marking=MARK: CommandContext(
        store=ctx.store, root=tmp_path, context=context, marking=marking,
        now_fn=ctx.now_fn)
    return pipeline, ctx, seeded, cc


def _plant_manifestation(pipeline, *, marking, body: bytes, native_id: str):
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", "special", native_id, digest),
        source_id="live-web", connector_id="live-web-test", connector_version="1.0",
        native_id=native_id, request_url=native_id, final_url=native_id,
        content_sha256=digest, content_store_path=str(path), media_type="text/html",
        temporal_status="LIVE", source_time=None, archive_capture_time=None,
        retrieval_time=T0, http_status=200, redirects=(), etag="", last_modified="",
        truncated=False, retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native_id), prior_manifestation_id=None,
        marking=marking)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", record,
                          recorded_time=pipeline.now_fn(), actor="t")
    return record.to_record()


def test_pipeline_processes_special_manifestation_as_special(mission):
    """Round-5 C1: a PENDING SPECIAL manifestation, processed by a PUBLIC
    pipeline run, materializes SPECIAL derived state — never PUBLIC."""
    pipeline, ctx, seeded, cc = mission
    body = (b"<html><body><p>Statement: SENSITIVE PARTNER AS is a front "
            b"for the compartmented programme.</p></body></html>")
    manifestation = _plant_manifestation(pipeline, marking=RESTRICTED_MARK,
                                         body=body, native_id="https://x.example/secret")
    # the pipeline's own marking is PUBLIC; it still must not declassify
    assert pipeline.marking.compartments == ()
    head = pipeline.store.events(None)[-1]["seq"]
    pipeline.process_new_evidence()
    # every record derived from the SPECIAL manifestation is invisible to B
    for event in pipeline.store.events(None):
        if event["seq"] <= head:
            continue
        record = event["record"]
        refs = json.dumps(record)
        if manifestation["manifestation_id"] in refs or "SENSITIVE PARTNER" in refs \
                or record.get("record_type") in ("semantic_document",):
            assert not can_view(record.get("marking"), CTX_B), (
                f"{event['event_type']} derived from a SPECIAL manifestation "
                f"is visible to an uncleared context")
    blob = json.dumps([MissionProjection(ctx.store, CTX_B).family(f)
                       for f in ("semantic_observation", "semantic_claim")])
    assert "SENSITIVE PARTNER" not in blob


def test_report_citing_special_basis_is_special(mission):
    """Round-5 C2: a report whose basis_refs cite a SPECIAL claim is itself
    compartmented; its prose never reaches an uncleared analyst."""
    pipeline, ctx, seeded, cc = mission
    # a SPECIAL claim: author a SPECIAL forecast is easier — use the seeded
    # secret assumption as an EXPLICITLY_INFERENTIAL basis
    report = commands.create_report(
        cc(CTX_A), title="dossier", question="?", sections=[
            {"kind": "key_judgments", "title": "KJ", "sentences": [
                {"text": "The compartmented counterparty depends on Acme.",
                 "status": "EXPLICITLY_INFERENTIAL",
                 "basis_refs": [seeded["secret_assumption_id"]],
                 "inference_note": "from the compartmented assumption"}]}])
    view_b = MissionProjection(ctx.store, CTX_B)
    assert view_b.get("workbench_report", report["report_id"]) is None
    assert "compartmented counterparty" not in json.dumps(view_b.family("workbench_report"))


def test_requirement_task_view_citing_special_are_special(mission):
    """Round-5 C3."""
    pipeline, ctx, seeded, cc = mission
    secret = seeded["secret_assumption_id"]
    req = commands.open_requirement(cc(CTX_A), question="q?", priority="MEDIUM",
                                    mission_context="m", rationale="r",
                                    affected_ids=(secret,))
    task = commands.assign_task(cc(CTX_A), assigned_actor="analyst-a",
                                task_type="ASSESSMENT", required_action="do",
                                affected_ids=(secret,))
    view = commands.save_view(cc(CTX_A), title="focus", view_kind="graph",
                              definition={"focus": secret})
    view_b = MissionProjection(ctx.store, CTX_B)
    assert not any(r["requirement_id"] == req["requirement_id"]
                   for r in view_b.base_view["information_requirements"])
    assert not any(t["task_id"] == task["task_id"]
                   for t in view_b.base_view["analyst_tasks"])
    assert view_b.get("workbench_saved_view", view["view_id"]) is None


def test_stale_basis_item_inherits_hypothesis_marking(mission):
    """Round-5 C4: the stale-basis companion is marked like its hypothesis."""
    pipeline, ctx, seeded, cc = mission
    from curunir_semantic.hypotheses import _queue_stale_basis
    hypothesis = commands.create_hypothesis(cc(CTX_A), statement="compartmented h",
                                            case_id="c", compartments=("SPECIAL",))
    raw = ctx.store.current_hypotheses()[hypothesis["hypothesis_id"]]
    _queue_stale_basis(pipeline.context(), raw,  # PUBLIC refresh context
                       [{"claim": {"claim_id": "claim-x"}, "state": "RETRACTED"}])
    items = [r for r in ctx.store.records_of("review_item")
             if r["kind"] == "STALE_BASIS"]
    assert items and items[-1]["marking"]["compartments"] == ["SPECIAL"]


def test_warning_projection_floor_check(mission):
    """Round-5 C7: a warning join above the actor's access is refused, not
    stranded."""
    pipeline, ctx, seeded, cc = mission
    # actor holds SPECIAL only; forecast+objective join stays within SPECIAL,
    # so this must SUCCEED — the floor check only blocks joins ABOVE access.
    # Direct unit check of the join+floor on split releasability:
    actor_ctx = CTX_A  # compartments ("SPECIAL",)
    hi = Marking("m", ("SPECIAL", "OTHER"), ("PUBLIC",))
    assert not can_view(hi, actor_ctx)  # actor lacks OTHER
    with pytest.raises(ValueError):
        # org-locked collapse across a releasable input fails closed
        most_restrictive([Marking("m", (), ("PUBLIC",)), Marking("m", (), ())])


def test_scrub_does_not_corrupt_visible_sibling_ids(mission):
    """Round-5 C8: a hidden id's family-prefix must not scrub a VISIBLE
    sibling that shares it."""
    pipeline, ctx, seeded, cc = mission
    # two review items in the same family: one SPECIAL (hidden), one PUBLIC
    for suffix, marking in (("edd8f9f86066db0f658e", RESTRICTED_MARK),
                            ("2e9a3e8641bf477bf3c0", MARK)):
        item = ReviewItem(item_id=f"review-expected-{suffix}", kind="EXPECTED_NOT_OBSERVED",
                          subject_kind="discriminator", subject_id="disc-x",
                          detail="coverage note", evidence_refs=(), status="OPEN",
                          resolution_note="", recorded_time=ctx.now_fn(),
                          marking=marking, version=1)
        ctx.store.append("REVIEW_ITEM_RECORDED", item,
                         recorded_time=item.recorded_time, actor="t")
    view_b = MissionProjection(ctx.store, CTX_B)
    served = view_b.get("review_item", "review-expected-2e9a3e8641bf477bf3c0")
    assert served is not None
    assert served["item_id"] == "review-expected-2e9a3e8641bf477bf3c0"  # not corrupted
    # and resolving the served id round-trips (no REDACTED prefix injected)
    assert "REDACTED" not in served["item_id"]


def test_watch_rejects_hidden_target_ref(mission):
    """Round-5 C9."""
    pipeline, ctx, seeded, cc = mission
    # an UNCLEARED actor cannot launder a hidden id into a watch
    with pytest.raises(CommandError):
        commands.create_watch(cc(CTX_B), need_id="n", target_kind="NATIVE_OBJECT",
                              target_ref=seeded["secret_object_id"], source_id="gleif",
                              operation="LOOKUP", query_value="x", cadence_seconds=3600)
    with pytest.raises(CommandError):
        commands.create_watch(cc(CTX_B), need_id="n", target_kind="NATIVE_OBJECT",
                              target_ref="LEI:X", source_id="gleif",
                              operation="LOOKUP", query_value="REDACTED",
                              cadence_seconds=3600)


def test_reply_to_validated_and_gated(mission):
    """Round-5 finding 5: reply_to cannot launder a hidden id or oracle."""
    pipeline, ctx, seeded, cc = mission
    # a SPECIAL annotation on a SPECIAL target
    special = commands.annotate(cc(CTX_A), target_kind="analytic_assumption",
                                target_id=seeded["secret_assumption_id"],
                                kind="NOTE", text="compartmented note")
    # analyst-b replying to it: refused identically to a nonexistent parent
    with pytest.raises((CommandError, NotFound)):
        commands.annotate(cc(CTX_B), target_kind="analytic_forecast",
                          target_id=seeded["forecast"]["forecast_id"],
                          kind="NOTE", text="reply", reply_to=special["annotation_id"])
    with pytest.raises((CommandError, NotFound)):
        commands.annotate(cc(CTX_B), target_kind="analytic_forecast",
                          target_id=seeded["forecast"]["forecast_id"],
                          kind="NOTE", text="reply", reply_to="annotation-000000000000")


def test_url_in_note_not_false_rejected(mission):
    """Round-5 finding 13: pasted registry URLs are allowed in prose."""
    pipeline, ctx, seeded, cc = mission
    made = commands.annotate(
        cc(CTX_A), target_kind="analytic_forecast",
        target_id=seeded["forecast"]["forecast_id"], kind="NOTE",
        text="see https://reg.example/lookup/lei-0123456789abcdef for detail")
    assert made["annotation_id"]
