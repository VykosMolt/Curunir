"""V6.7 migration compatibility and backup-backed rollback.

There is no in-place schema migrator in V6.7.  Compatibility is deliberately
small: missing additive fields receive least-privilege defaults, unknown store
contract versions are refused, and rollback restores a validated prior export
into a new root.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from curunir_analytic.impact import create_objective
from curunir_analytic.substrate import AnalyticContext
from curunir_operational.access import Marking
from curunir_operational.canonical import canonical_line
from curunir_operational.store import CONTRACT_VERSION, StoreError
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.store import WorkbenchStore

from workbench_support import make_workbench, seed_mission

pytestmark = pytest.mark.no_db


def test_legacy_actor_without_kind_cannot_gain_human_authority(tmp_path):
    registry_path = tmp_path / "actors.json"
    write_registry(
        registry_path,
        [{
            "token": "legacy-token",
            "actor_id": "legacy-actor",
            "roles": ["ANALYST"],
            "releasability": ["PUBLIC"],
        }],
    )
    registry = ActorRegistry(registry_path)
    assert registry.context_for("legacy-token").actor_kind == "SERVICE"
    assert registry.context_for_actor("legacy-actor").actor_kind == "SERVICE"


def test_future_contract_version_is_refused_even_when_manifest_hashes_match(tmp_path):
    pipeline, _ = make_workbench(tmp_path)
    backup = tmp_path / "backup"
    pipeline.store.export_to(backup)

    meta_path = backup / "store_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["contract_version"] = "curunir-operational-contracts-vFUTURE"
    meta_bytes = (canonical_line(meta) + "\n").encode()
    meta_path.write_bytes(meta_bytes)

    manifest_path = backup / "export_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["store_meta_sha256"] = hashlib.sha256(meta_bytes).hexdigest()
    manifest_path.write_text(canonical_line(manifest) + "\n", encoding="utf-8")

    destination = tmp_path / "restored"
    with pytest.raises(StoreError, match="contract version"):
        WorkbenchStore.import_from(backup, destination)
    assert not destination.exists()


def test_backup_rollback_restores_exact_prior_state(tmp_path):
    pipeline, context = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, context)
    store = context.store
    prior_head = store.head()
    prior_event_bytes = store.events_path.read_bytes()
    backup = tmp_path / "prior"
    store.export_to(backup)

    later_context = AnalyticContext(
        store=store,
        actor="migration-test",
        marking=Marking(owning_authority="test", releasability=("PUBLIC",)),
        now_fn=lambda: "2026-08-21T12:00:00+00:00",
    )
    create_objective(
        later_context,
        mission_context="migration-test",
        statement="change that must not survive rollback",
    )
    assert store.head() != prior_head

    restored = WorkbenchStore.import_from(backup, tmp_path / "rolled-back")
    assert restored.head() == prior_head
    assert restored.events_path.read_bytes() == prior_event_bytes
    assert restored.verify_chain()["valid"]
    assert seeded["forecast"]["forecast_id"] in restored.current_forecasts()
    assert not any(
        item["statement"] == "change that must not survive rollback"
        for item in restored.current_objectives().values()
    )
    assert restored.meta["contract_version"] == CONTRACT_VERSION
