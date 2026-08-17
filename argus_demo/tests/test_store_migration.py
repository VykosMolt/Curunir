"""V6.7 §6 — migration / rollback safety and fail-closed defaults.

Curunír has one contract version and no schema evolution yet, so "migration" is
(a) additive-field backward compatibility with safe defaults, (b) a fail-closed
version guard so a backup written under an unknown contract is refused rather
than silently misread, (c) backup-based rollback, and (d) partial/old records
never defaulting to a MORE-privileged or MORE-visible state.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from curunir_operational.access import AccessContext, can_view
from curunir_operational.canonical import canonical_line
from curunir_operational.store import CONTRACT_VERSION, StoreError
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.store import WorkbenchStore

from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


# ---- fail-closed defaults for partial / pre-upgrade records -----------------

def test_missing_actor_kind_fails_closed_to_service_not_human(tmp_path):
    # a registry entry written before actor_kind existed must NOT gain the
    # privileged HUMAN value that gates human-only adjudication.
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-partial", "actor_id": "legacy", "roles": ["ANALYST"],
         "releasability": ["PUBLIC"], "organisation": "m"},  # no actor_kind
    ])
    registry = ActorRegistry(actors)
    assert registry.context_for("tok-partial").actor_kind == "SERVICE"
    assert registry.context_for_actor("legacy").actor_kind == "SERVICE"


def test_missing_marking_is_not_viewable(tmp_path):
    # a record carrying no marking is viewable by no one (never defaults to PUBLIC)
    ctx = AccessContext("c", "a", "HUMAN", ("ANALYST",), releasability=("PUBLIC",))
    assert can_view(None, ctx) is False


# ---- additive-field backward compatibility ---------------------------------

def test_old_execution_record_without_truncated_replays_safely(tmp_path):
    from curunir_fabric.contracts import ExecutionRecord
    from curunir_operational.access import Marking
    make_workbench(tmp_path)  # creates the store
    store = WorkbenchStore(tmp_path / "store")
    mark = Marking(owning_authority="workbench-test", releasability=("PUBLIC",))
    rec = ExecutionRecord(
        execution_id="exec-old", plan_id="", query_id="q", source_id="gleif",
        connector_id="c", connector_version="1", operation="LOOKUP",
        outcome="EXECUTED_EMPTY", result_count=0, request_url="u", http_status=200,
        policy_decision="ALLOW", error_class=None, error_detail="",
        manifestation_ids=(), started_time="2026-08-17T12:00:00+00:00",
        completed_time="2026-08-17T12:00:00+00:00",
        absence_semantics="ABSENCE_IS_UNKNOWN_NOT_NONEXISTENCE", marking=mark).to_record()
    del rec["truncated"]                       # simulate a pre-upgrade record
    store.append("FABRIC_EXECUTION_RECORDED", rec,
                 recorded_time="2026-08-17T12:00:00+00:00", actor="t")
    # reopen under current code: it replays and the absence logic reads the
    # missing field as non-truncated (the pre-upgrade behavior — transparent).
    reopened = WorkbenchStore(tmp_path / "store")
    got = [e for e in reopened.records_of("fabric_execution") if e["execution_id"] == "exec-old"]
    assert got and "truncated" not in got[0]
    assert not got[0].get("truncated")         # safe default on read
    assert reopened.verify_chain()["valid"]


# ---- version guard + rollback ----------------------------------------------

def test_incompatible_contract_version_is_refused_on_import(tmp_path):
    make_workbench(tmp_path)
    store = WorkbenchStore(tmp_path / "store")
    backup = tmp_path / "backup"
    store.export_to(backup)

    # forge a FUTURE contract version in the (authenticated) store_meta and
    # re-hash it in the manifest, so only the version guard can catch it.
    meta_path = backup / "store_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["contract_version"] = "curunir-operational-contracts-vFUTURE"
    new_bytes = (canonical_line(meta) + "\n").encode("utf-8")
    meta_path.write_bytes(new_bytes)
    manifest_path = backup / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["store_meta_sha256"] = hashlib.sha256(new_bytes).hexdigest()
    manifest_path.write_text(canonical_line(manifest) + "\n", encoding="utf-8")

    with pytest.raises(StoreError, match="contract version"):
        WorkbenchStore.import_from(backup, tmp_path / "restored")
    # and importing the UNMODIFIED (compatible) backup still works — rollback path
    store.export_to(tmp_path / "clean_backup")
    restored = WorkbenchStore.import_from(tmp_path / "clean_backup", tmp_path / "restored2")
    assert restored.meta["contract_version"] == CONTRACT_VERSION


def test_backup_rollback_restores_prior_state(tmp_path):
    # rollback = restore the pre-change backup; the original mission survives an
    # (undesired) later change intact.
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    store = WorkbenchStore(tmp_path / "store")
    pre_upgrade = store.head()
    backup = tmp_path / "pre_upgrade_backup"
    store.export_to(backup)

    # an undesired later change lands on the live store
    from curunir_analytic.impact import create_objective
    from curunir_analytic.substrate import AnalyticContext
    from semantic_support import MARK, clock
    live_ctx = AnalyticContext(store=store, actor="t", marking=MARK, now_fn=clock(600))
    create_objective(live_ctx, mission_context="regret", statement="undesired change")
    assert store.head() != pre_upgrade

    # roll back: restore the backup into a fresh root, previous state intact
    rolled_back = WorkbenchStore.import_from(backup, tmp_path / "rolled_back")
    assert rolled_back.head() == pre_upgrade
    assert rolled_back.verify_chain()["valid"]
    assert seeded["forecast"]["forecast_id"] in rolled_back.current_forecasts()
    # the undesired objective is not in the rolled-back mission
    assert not any(o["statement"] == "undesired change"
                   for o in rolled_back.current_objectives().values())
