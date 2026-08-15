from __future__ import annotations

import json

import pytest

from curunir_operational.v5.operations import run_mutations, run_stress, security_review
from curunir_operational.v5.pilot import build_pilot_package

pytestmark = pytest.mark.no_db

def test_pilot_package_has_roles_blinding_and_no_human_results(tmp_path):
    result = build_pilot_package(tmp_path)
    definition = json.loads((tmp_path / "pilot_definition.json").read_text())
    assert result["status"] == "PILOT_READY_HUMAN_PARTNER_PENDING"
    assert result["roles"] == 6 and result["human_participation"] == result["fake_human_results"] == 0
    assert definition["answer_visibility"] == "HIDDEN_DURING_INDEPENDENT_LABELING"


def test_pilot_privacy_is_minimal(tmp_path):
    build_pilot_package(tmp_path)
    privacy = json.loads((tmp_path / "privacy_boundary.json").read_text())
    assert "hidden_keystrokes" in privacy["prohibited"] and "private_files" in privacy["prohibited"]
    assert "anonymized_reviewer_id" in privacy["collected"]


def test_pilot_has_retention_incident_and_exit(tmp_path):
    build_pilot_package(tmp_path)
    assert all((tmp_path / name).is_file() for name in
               ("retention_policy.md", "incident_procedure.md", "exit_and_export.md"))


def test_the_twelve_required_mutations_are_declared_and_not_executed(tmp_path):
    """Was ``test_required_twelve_mutations_are_caught``.

    That test asserted ``caught == 12``, ``survived == 0`` and, per record,
    ``mutated_test_failed_for_intended_reason``.  Every one of those fields was
    a hard-coded constant: ``run_mutations`` executes no mutation and runs no
    mutated test (finding W8-N4), so the old assertions could not fail and
    documented a result that never happened.  The claim is withdrawn at the
    source and these assertions replace it with the stronger requirement that
    the record must DISCLOSE that nothing ran, and must carry no result.  The
    full shape is pinned in
    tests/test_operational_zero_write_measurement.py::test_the_v5_mutation_battery_reports_a_declaration_not_a_result.
    """
    result = run_mutations(tmp_path / "mutation.json")
    assert result["required_mutations"] == result["declared_specifications"] == 12
    assert result["executed"] == 0
    assert result["verdict"] == "DECLARED_NOT_EXECUTED"
    assert "caught" not in result and "survived" not in result
    assert all(x["executed"] is False and x["verdict"] == "DECLARED_NOT_EXECUTED"
               for x in result["mutations"])
    assert all("mutated_test_failed_for_intended_reason" not in x for x in result["mutations"])


def test_the_twenty_two_threats_are_declared_and_not_executed(tmp_path):
    """Was ``test_security_threats_run``.

    That test asserted ``threat_count >= 22``, ``failed == 0`` and
    ``access_leakage_findings == 0``.  ``failed`` and ``access_leakage_findings``
    were hard-coded zeros and ``threat_count`` was ``len()`` of a static tuple:
    ``security_review`` constructs no attack, invokes no mitigation and runs no
    test (finding W11-T8), and the ``v5_<component>_<n>`` identifiers its records
    published name tests that do not exist anywhere in this repository.  The
    claim is withdrawn at the source and these assertions replace it with the
    stronger requirement that the record must DISCLOSE that nothing ran, and
    must carry no result.  The full shape is pinned in
    tests/test_operational_zero_write_measurement.py::test_the_v5_security_review_reports_a_declaration_not_a_result.
    """
    result = security_review(tmp_path / "security.json")
    assert result["threat_count"] == result["declared_specifications"] == 22
    assert result["executed"] == 0
    assert result["verdict"] == "DECLARED_NOT_EXECUTED"
    assert "failed" not in result and "tests_run" not in result
    assert "access_leakage_findings" not in result
    assert all(x["executed"] is False and x["verdict"] == "DECLARED_NOT_EXECUTED"
               for x in result["threats"])
    assert all("result" not in x and "test" not in x for x in result["threats"])


def test_stress_meets_all_minima(tmp_path):
    result = run_stress(tmp_path)
    assert result["evaluation_packets"] >= 2000 and result["surfaces"] == 6
    assert result["blind_reviewer_slots"] >= 3 and result["disagreements"] >= 500
    assert result["correction_propagations"] >= 500 and result["source_versions"] >= 1000
    assert result["claim_deltas"] >= 500 and result["kernel_proposal_revalidations"] >= 200
    assert result["replay_hash_match"] and result["canonical_writes"] == 0
