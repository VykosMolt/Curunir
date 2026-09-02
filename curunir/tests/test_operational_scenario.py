"""One synthetic scenario run end to end, checked against the counts, projections,
reports and replay hashes it is expected to produce."""
from __future__ import annotations

import json

import pytest

from curunir_operational.scenario.runner import run_scenario

pytestmark = pytest.mark.no_db


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    base = tmp_path_factory.mktemp("vessia")
    return run_scenario(base / "store", base / "out"), base / "out"


def test_ingestion_counts(scenario):
    result, _ = scenario
    ingestion = result["metrics"]["ingestion"]
    assert ingestion["payloads_received"] == 18
    assert ingestion["quarantined"] == 1
    assert ingestion["duplicated"] == 1
    assert ingestion["late"] == 1
    assert ingestion["corrected_reports"] == 1
    special = result["metrics"]["special_cases"]
    assert special["late_ingestion"]["late"] and special["duplicate_ingestion"]["duplicate"] \
        and special["malformed_ingestion"]["quarantined"]


def test_provenance_completeness(scenario):
    result, _ = scenario
    provenance = result["metrics"]["provenance"]
    assert provenance["admitted_records_with_source_provenance_pct"] == 100.0
    assert provenance["derived_records_with_complete_transformation_lineage_pct"] == 100.0
    assert provenance["unresolved_provenance_links"] == 0
    assert provenance["argus_evidence_links_preserved"] == 2
    assert provenance["dependent_evidence_groups_identified"] == 1


def test_identity_and_fusion(scenario):
    result, _ = scenario
    fusion = result["metrics"]["identity_and_fusion"]
    assert fusion["proposed_associations"] == 2
    assert fusion["split_or_reversed_resolutions"] == 1
    assert fusion["accepted_resolutions"] == 1
    assert fusion["destructive_merges"] == 0


def test_workflow_and_alerts(scenario):
    result, _ = scenario
    workflow = result["metrics"]["workflow"]
    assert workflow["accepted_recommendations"] == 1
    assert workflow["modified_recommendations"] == 1
    assert workflow["deferred_recommendations"] == 1
    assert workflow["decisions_with_frozen_evidence_snapshots"] == 3
    assert workflow["acknowledged_alerts"] == 2
    assert result["summary"]["alerts"] >= 5


def test_access_differentiation_and_no_leakage(scenario):
    result, out = scenario
    access = result["metrics"]["access_security"]
    assert access["leakage_failures"] == 0
    assert access["restricted_identifiers_in_partner_projection"] == 0
    assert access["full_view_object_count"] > access["partner_view_object_count"]
    restricted = (out / "projection_restricted.json").read_text()
    # "DEGRADED" is a legitimate quality state; what must not appear is the
    # observation id, its detail text, or its compartment.
    for token in ("obs-FN-2205", "SENSITIVE-INFRA", "protected feeder", "feeder telemetry"):
        assert token not in restricted
    low_cop = (out / "cop_restricted.html").read_text()
    assert "obs-FN-2205" not in low_cop and "feeder" not in low_cop
    assert "infra-SUB-4" in low_cop  # the substation is public registry data
    answers = json.loads((out / "mission_question_answers.json").read_text())
    q7 = next(a for a in answers["answers"] if a["number"] == 7)
    assert q7["answer"]["alert_ids"], "restricted alert must exist for the full-access view"


def test_determinism_and_replay(scenario):
    result, _ = scenario
    determinism = result["metrics"]["determinism"]
    assert determinism["replay_head_hash_equal"] is True
    assert determinism["projection_hash_equal"] is True
    assert determinism["pace_verification_valid"] is True
    assert determinism["provider_reinvocations_during_replay"] == 0
    assert determinism["inference_records_equal_after_replay"] is True


def test_mission_questions_all_answerable(scenario):
    result, out = scenario
    answers = json.loads((out / "mission_question_answers.json").read_text())["answers"]
    assert len(answers) == 15
    assert all(a["answerable"] for a in answers)
    q1 = next(a for a in answers if a["number"] == 1)
    assert q1["answer"]["least_exposed"] == "route-R2"
    q3 = next(a for a in answers if a["number"] == 3)
    assert q3["answer"]["groups"][0]["member_count"] == 2
    q6 = next(a for a in answers if a["number"] == 6)
    assert q6["answer"]["current_quantity"]["before"] == q6["answer"]["current_quantity"]["after"] == 11000.0
    assert q6["answer"]["history_count"]["after"] == q6["answer"]["history_count"]["before"] + 1
    q12 = next(a for a in answers if a["number"] == 12)
    assert q12["answer"]["exit_test_passed"] is True


def test_disputed_state_and_conflict_visible(scenario):
    result, out = scenario
    projection = json.loads((out / "projection_full.json").read_text())["view"]
    bridge = next(o for o in projection["objects"] if o["object_id"] == "infra-BR-7")
    assert bridge["epistemic_state"] == "DISPUTED"
    assert bridge["history_count"] >= 2
    assert any(r["relation_type"] == "CONFLICTS_WITH" for r in projection["relationships"])
    sitrep = (out / "sitrep_full.txt").read_text()
    assert "DISPUTED" in sitrep and "share one underlying basis" in sitrep
    assert max(len(line) for line in sitrep.splitlines()) <= 72


def test_artifacts_written(scenario):
    _, out = scenario
    expected = ["cop_initial.html", "cop_full.html", "cop_restricted.html", "projection_full.json",
                "projection_restricted.json", "sitrep_full.md", "sitrep_full.txt", "sitrep_restricted.txt",
                "sovereignty_manifest.json", "mission_question_answers.json", "scenario_metrics.json",
                "ingestion_log.json", "schema_registry_export.json", "pipeline_definitions.json",
                "workshop_definition.json", "explanation_samples.md", "scenario_file_manifest.json"]
    for name in expected:
        assert (out / name).exists(), f"missing artifact {name}"
    assert (out / "pace_bundle" / "bundle_manifest.json").exists()
    assert (out / "open_export" / "export_manifest.json").exists()
