"""V5.1 bounded stress test — reduced scale, real modules."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1.stress import DEFAULT_SCALE, run_stress

pytestmark = pytest.mark.no_db

_SMALL = {"extraction_candidates": 60, "substantive_decisions": 12,
          "source_role_decisions": 10, "source_origin_edges": 10,
          "dependence_groups": 9, "claims": 20, "relations": 10,
          "report_propositions": 10, "campaign_graphs": 1,
          "review_packets": 8, "proposal_dependency_checks": 9}


@pytest.fixture(scope="module")
def stress_report(tmp_path_factory):
    out = tmp_path_factory.mktemp("stress")
    return out, run_stress(out, scale=_SMALL)


def test_all_stages_execute_with_consistent_counters(stress_report):
    _out, report = stress_report
    counters = report["counters"]
    for stage in ("extraction_candidates", "source_role_decisions",
                  "dependence_groups", "claims", "relations",
                  "report_propositions", "review_packets",
                  "proposal_dependency_checks"):
        assert counters[stage] >= _SMALL[stage] * 0.9, stage


def test_injected_furniture_is_quarantined(stress_report):
    _out, report = stress_report
    assert report["furniture_quarantined"] == max(
        1, _SMALL["extraction_candidates"] // 10)


def test_injected_unresolvable_cases_are_not_failures(stress_report):
    _out, report = stress_report
    assert report["injected_unresolvable_classified_correctly"] == 3
    assert report["capability_failures"] == 0


def test_report_carries_no_human_validation_markers(stress_report):
    out, report = stress_report
    assert report["human_validation_claimed"] is False
    assert report["no_human_validation"] is True
    assert report["review_boundary"] == "MODEL_PANEL_ONLY"
    written = json.loads((out / "stress_report.json").read_text())
    assert written["integrity_hash"] == report["integrity_hash"]


def test_memory_and_artifact_measurements_recorded(stress_report):
    _out, report = stress_report
    assert report["peak_rss_kb"] > 0


def test_default_scale_meets_section_31_minimums():
    assert DEFAULT_SCALE["extraction_candidates"] >= 5000
    assert DEFAULT_SCALE["substantive_decisions"] >= 1000
    assert DEFAULT_SCALE["source_role_decisions"] >= 500
    assert DEFAULT_SCALE["source_origin_edges"] >= 500
    assert DEFAULT_SCALE["dependence_groups"] >= 300
    assert DEFAULT_SCALE["claims"] >= 1000
    assert DEFAULT_SCALE["relations"] >= 300
    assert DEFAULT_SCALE["report_propositions"] >= 300
    assert DEFAULT_SCALE["review_packets"] >= 1000
    assert DEFAULT_SCALE["proposal_dependency_checks"] >= 300


def test_stress_is_deterministic_modulo_time(tmp_path):
    first = run_stress(tmp_path / "one", scale=_SMALL)
    second = run_stress(tmp_path / "two", scale=_SMALL)
    assert first["integrity_hash"] == second["integrity_hash"]


def test_multilingual_material_is_exercised(stress_report):
    _out, report = stress_report
    # 60 candidates over 10-per-document = 6 documents cycling 4 languages.
    assert report["counters"]["extraction_candidates"] >= 4 * 10
