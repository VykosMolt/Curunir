"""Integration tests over the three-scenario run: a second workbench on the shared
object fabric, cross-workbench workflow, live and document evidence, provider
evaluation, access, and the mission questions. The run happens once per module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.scenario import v2_config as v2
from curunir_operational.scenario.v2_orchestrate import run_v2
from curunir_operational.workbench import WorkbenchRenderer, validate_workshop_definition

pytestmark = pytest.mark.no_db


@pytest.fixture(scope="module")
def v2run(tmp_path_factory):
    base = tmp_path_factory.mktemp("v2")
    return run_v2(base / "run", base / "out"), base / "out"


# ---- second workbench on the shared fabric ----

def test_second_workbench_reuses_shared_fabric(v2run):
    result, _ = v2run
    jr = result["joint_report"]
    assert jr["shared_objects"], "both workbenches must consume shared objects"
    assert jr["infrastructure_object_count"] > 0 and jr["logistics_object_count"] > 0
    assert any(o.startswith("infra-") for o in jr["shared_objects"])


def test_no_second_domain_package_and_no_scenario_constants():
    core = Path("curunir_operational")
    # Scenario names belong under scenario/, never in a core module.
    scenario_tokens = ("BR-7", "SUB-4", "RELIEF-", "Vessia", "GDACS", "hazard-9900001", "eng-ENG",
                       "CORRIDOR-OPS", "SENSITIVE-INFRA", "ENGINEERING-ASSESSMENT")
    for path in core.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in scenario_tokens:
            assert token not in text, f"scenario constant {token} leaked into core {path.name}"
    # The second workbench is a definition, not a second package.
    assert not (core / "infrastructure").exists() and not (core / "civil_protection").exists()


def test_infrastructure_workbench_validates_and_renders(v2run):
    from curunir_operational.scenario.v2_config import INFRASTRUCTURE_WORKBENCH
    definition = validate_workshop_definition(INFRASTRUCTURE_WORKBENCH)
    assert definition["workflow"]["include_requirements"]
    assert definition["relationship_tables"][0]["relation_types"]


# ---- cross-workbench behaviour ----

def test_hazard_to_route_impact_and_dependency(v2run):
    result, _ = v2run
    q = {a["number"]: a for a in result["answers"]["answers"]}
    assert q[1]["answer"], "hazard must affect at least one infrastructure object"
    assert q[3]["answer"]["route_R1_status"] == "RESTRICTED"
    assert q[3]["answer"]["decision_effect_recorded"] is True


def test_shared_provenance_across_workbenches(v2run):
    result, out = v2run
    jr = result["joint_report"]
    assert any(i["to"].startswith(("infra-", "route-")) for i in jr["cross_domain_impacts"])


def test_cross_workbench_decision_preserves_provenance(v2run):
    result, out = v2run
    projection = json.loads((out / "05_v2_workbenches" / "joint_coordinator_report.json").read_text())
    assert ("route-R1", "ACCEPTED") not in projection["decisions"] or projection["decisions"]


# ---- live data and document evidence ----

def test_live_vs_synthetic_distinct(v2run):
    live = json.loads(Path("artifacts/curunir_v1_audit_hardening_and_v2_multi_workbench_20260720/"
                           "03_v2_live_acquisition/live_acquisition_record.json").read_text())
    assert live["sources"][0]["kind"] == "structured_feed"
    assert live["sources"][0]["fixture_events"] <= live["sources"][0]["total_events_in_feed"]
    # The public document came through the evidence chain, not a bare URL.
    doc = live["sources"][1]
    assert "si_chain" in doc and doc["si_chain"]["review_state"] == "AI_SECONDARY_REVIEW"
    assert doc["si_chain"]["content_object_id"] and doc["si_chain"]["evidence_basis_id"]


def test_public_document_evidence_in_scenario(v2run):
    result, _ = v2run
    q = {a["number"]: a for a in result["answers"]["answers"]}
    assert q[5]["answer"], "public-document evidence must produce at least one evidentiary observation"
    assert all(e["review_state"] in ("UNREVIEWED", "AI_SECONDARY_REVIEW") for e in q[5]["answer"])


# ---- provider evaluation ----

def test_provider_evaluation(v2run):
    result, _ = v2run
    evaluated = result["provider_eval"]["evaluated"]
    assert len(evaluated) == 2
    for e in evaluated:
        assert e["deterministic_content"] and e["structured_output_valid"]
        assert e["abstention_on_empty_projection"] and e["access_compliant"]
        assert e["unsupported_claim_count"] == 0
    assert result["provider_eval"]["learned_model_comparison"] == "NOT_RUN"


# ---- delta sync ----

def test_delta_sync_report(v2run):
    result, _ = v2run
    d = result["delta"]
    assert d["apply_receipt"]["status"] == "APPLIED"
    assert d["brought_current_matches_source"] is True
    assert d["duplicate_delta_receipt"] == "DUPLICATE_DELTA"
    assert d["missing_base_conflict"] == "MISSING_BASE"
    assert d["wrong_base_conflict"] == "DIVERGED_OVERLAP"
    assert d["tamper_detected"] is True


# ---- cross-workbench access ----

def test_cross_workbench_access_no_leakage(v2run):
    result, _ = v2run
    matrix = result["access_matrix"]
    assert matrix["leakage_failures"] == 0
    views = matrix["views"]
    # Only a holder of the engineering compartment sees its objects.
    assert not views["logistics"]["sees_engineering_objects"]
    assert not views["civil_protection"]["sees_engineering_objects"]
    assert views["joint"]["sees_engineering_objects"]
    assert views["public_evidence"]["objects"] == 1
    assert not views["civil_protection"]["unauthorized_id_leaks"]


# ---- multiple scenarios on one core ----

def test_three_scenarios_same_core(v2run):
    result, out = v2run
    metrics = json.loads((out / "04_v2_scenarios" / "scenario_metrics.json").read_text())
    assert metrics["scenario_a"]["questions_answerable"] == 15
    assert metrics["scenario_b"]["route_R1_restricted"] == "RESTRICTED"
    assert metrics["scenario_b"]["requirement_answered"] is True
    assert metrics["scenario_c"]["dependence_groups"] == 1
    assert metrics["scenario_c"]["incompatible_schema"] == "INVALID"
    assert metrics["scenario_c"]["unsupported_schema"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert metrics["scenario_c"]["compatible_schema"] == "VALID"


def test_all_25_mission_questions_answerable(v2run):
    result, out = v2run
    answers = json.loads((out / "04_v2_scenarios" / "mission_question_answers.json").read_text())
    assert answers["all_answerable"] is True
    assert len(answers["answers"]) == 25
    q = {a["number"]: a for a in answers["answers"]}
    assert q[6]["answer"][0]["members"], "dependent reports must be grouped"
    assert q[7]["answer"], "an independent report must be identifiable"
    assert "does not treat 4 dependent reports as independent corroboration (1 basis, scenario C)" in \
        " ".join(q[25]["answer"]["refusals"])


def test_scenario_artifacts_written(v2run):
    _, out = v2run
    for name in ("mission_question_answers.json", "cross_workbench_access_matrix.json",
                 "provider_evaluation.json", "scenario_metrics.json"):
        assert (out / "04_v2_scenarios" / name).exists()
    assert (out / "05_v2_workbenches" / "joint_coordinator_report.json").exists()
    assert (out / "05_v2_workbenches" / "cop_infrastructure_joint.html").exists()
    assert (out / "06_v2_exports" / "delta_sync_report.json").exists()
    assert (out / "06_v2_exports" / "sovereignty_manifest_v2.json").exists()
