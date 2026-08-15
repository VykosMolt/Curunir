"""Principal V3 scenario proof across independently invoked OS processes."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v3.scenario import run_distributed_scenario

pytestmark = pytest.mark.no_db


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    root = tmp_path_factory.mktemp("curunir-v3-process-scenario")
    return root, run_distributed_scenario(root)


def test_three_independent_stores_and_process_boundary(scenario):
    root, result = scenario
    stores = result["node_store_paths"]
    assert set(stores) == {"LOGISTICS_NODE", "CIVIL_PROTECTION_NODE", "STRATEGIC_EVIDENCE_NODE"}
    assert len(set(stores.values())) == 3
    assert all((root / "03_nodes" / "scenario_stores" / node.lower() / "events.jsonl").exists()
               for node in stores)
    proof = result["process_isolation"]
    assert proof["shared_python_memory"] is False and proof["distinct_child_processes"] >= 30
    assert proof["parent_pid"] not in proof["child_pids"]


def test_serialized_sync_conflicts_resolution_and_convergence(scenario):
    root, result = scenario
    assert list((root / "04_sync").rglob("bundle.json"))
    assert list((root / "04_sync").rglob("receipt.json"))
    convergence = result["convergence"]
    assert convergence["authorized_convergence"] is True
    assert len(set(convergence["semantic_hashes"].values())) == 1
    assert convergence["unresolved_hypothesis_retained"] is True
    assert all(item["equal"] and item["provider_reinvocations"] == 0 for item in convergence["replay"].values())
    conflict_types = set()
    statuses = set()
    for path in (root / "05_conflicts").glob("conflicts_*.json"):
        records = json.loads(path.read_text(encoding="utf-8"))
        conflict_types.update(item["conflict_type"] for item in records)
        statuses.update(item["status"] for item in records)
    assert {"CONTRADICTORY_OPERATIONAL_STATUS", "TASK_ASSIGNMENT_CONFLICT", "ACCESS_POLICY_CONFLICT"} <= conflict_types
    assert "OPEN" in statuses  # originals stay immutable; resolution is a new event


def test_temporal_strategic_and_operator_outputs(scenario):
    root, result = scenario
    temporal = json.loads((root / "03_nodes" / "temporal_query_examples.json").read_text(encoding="utf-8"))
    assert temporal["classification"] == "FULL_BITEMPORAL_QUERY_WITH_DISTRIBUTED_KNOWLEDGE"
    assert temporal["future_correction_excluded_before_arrival"] is True
    assert temporal["valid_historical_revoked_actor_action_present"] is True
    assert temporal["revoked_actor_future_action_present"] is False
    strategic = json.loads((root / "06_strategic_workbench" / "strategic_workbench.json").read_text(encoding="utf-8"))
    assert {item["analyst_state"] for item in strategic["hypotheses"]} == {"VIABLE", "REJECTED", "UNRESOLVED"}
    assert strategic["source_dependence_groups"][0]["independent_basis_count"] == 1
    assert all(item["operational_state_mutated"] is False for item in strategic["implications"])
    assert result["operator_harness"]["status"] == "SCRIPTED_HARNESS_VALID"
    assert result["operator_harness"]["human_study_status"] == "HUMAN_STUDY_PENDING"
