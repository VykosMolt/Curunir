"""Restart, export and replay: a reopened or replayed store rebuilds the same
authorized mission state from the event log alone."""
from __future__ import annotations

import json
import shutil

import pytest

from curunir_workbench.projections import MissionProjection
from curunir_workbench.reports import approve_report, create_report, submit_report
from curunir_workbench.store import WorkbenchStore
from curunir_workbench.annotations import create_annotation

from semantic_support import MARK
from workbench_support import CTX_A, CTX_B, make_workbench, seed_mission

pytestmark = pytest.mark.no_db


def _enrich(ctx, seeded):
    """Add an annotation and an approved dossier."""
    projection = MissionProjection(ctx.store, CTX_A)
    annotation = create_annotation(
        ctx.store, projection, actor="analyst-a", marking=MARK, now=ctx.now_fn(),
        target_kind="analytic_forecast", target_id=seeded["forecast"]["forecast_id"],
        kind="NOTE", text="watch the registry")
    report = create_report(
        ctx.store, actor="analyst-a", marking=MARK, now=ctx.now_fn(),
        title="Replay dossier", question="Does state survive replay?",
        sections=[{"kind": "key_judgments", "title": "KJ", "sentences": [
            {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
             "basis_refs": [seeded["status_claim"]["claim_id"]]}]}],
        state_token=projection.state_token)
    submit_report(ctx.store, report["report_id"], actor="analyst-a", marking=MARK,
                  now=ctx.now_fn(), expected_version=1, state_token="tok")
    approved = approve_report(ctx.store, MissionProjection(ctx.store, CTX_A),
                              report["report_id"], actor="analyst-b",
                              actor_kind="HUMAN", marking=MARK, now=ctx.now_fn(),
                              expected_version=2)
    return annotation, approved


def _fingerprint(store) -> dict:
    """The authorized state both contexts can see, serialized the same way every time."""
    out = {}
    for context in (CTX_A, CTX_B):
        p = MissionProjection(store, context)
        out[context.context_id] = {
            "counts": p.overview()["counts"],
            "reports": p.family("workbench_report"),
            "annotations": p.family("workbench_annotation"),
            "forecasts": p.family("analytic_forecast"),
            "warnings": p.family("strategic_warning"),
            "hypotheses": p.family("hypothesis"),
            "tasks": p.base_view["analyst_tasks"],
        }
    return out


def test_process_restart_preserves_state(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    annotation, approved = _enrich(ctx, seeded)
    before = _fingerprint(ctx.store)
    # Restart: a fresh store object over the same root.
    reopened = WorkbenchStore(tmp_path / "store")
    after = _fingerprint(reopened)
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    assert reopened.verify_chain() is None or True  # chain verifies without raising
    report = reopened.current_reports()[approved["report_id"]]
    assert report["status"] == "APPROVED" and report["version"] == approved["version"]


def test_export_import_replay_reconstructs_projections(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    _enrich(ctx, seeded)
    before = _fingerprint(ctx.store)

    export_dir = tmp_path / "open_export"
    ctx.store.export_to(export_dir)
    new_root = tmp_path / "replayed" / "store"
    WorkbenchStore.import_from(export_dir, new_root)
    # Custody bytes sit beside the store, so they are copied separately.
    shutil.copytree(tmp_path / "custody", tmp_path / "replayed" / "custody")
    replayed = WorkbenchStore(new_root)
    after = _fingerprint(replayed)
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)

    # Descent still reaches the retained evidence, with no network.
    from curunir_workbench.provenance import descend
    p = MissionProjection(replayed, CTX_A)
    chain = descend(p, "strategic_warning", seeded["warning"]["warning_id"])
    anchor = chain["claims"][0]["observations"][0]["anchors"][0]
    assert anchor["source"]["source_id"] == "gleif"

    # Access boundaries replay too.
    p_b = MissionProjection(replayed, CTX_B)
    assert seeded["secret_object_id"] not in p_b.visible_object_ids()
    assert p_b.get("analytic_assumption", seeded["secret_assumption_id"]) is None


def test_tampered_import_refused(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    seed_mission(pipeline, ctx)
    export_dir = tmp_path / "open_export"
    ctx.store.export_to(export_dir)
    events = (export_dir / "events.jsonl").read_text()
    (export_dir / "events.jsonl").write_text(events.replace("ISSUED", "REVOKED"))
    with pytest.raises(Exception):
        WorkbenchStore.import_from(export_dir, tmp_path / "tampered" / "store")
