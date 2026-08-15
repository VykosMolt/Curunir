from __future__ import annotations

from pathlib import Path

import pytest

from curunir_operational.v4.mutation import run_mutation_suite, threat_model

pytestmark = pytest.mark.no_db


def test_all_ten_load_bearing_mutations_are_caught_and_restored(tmp_path):
    root = Path(__file__).parents[1]
    report = run_mutation_suite(tmp_path / "mutation_report.json", root)
    assert report["verdict"] == "PASS"
    assert report["required_mutations"] == report["caught"] == 10
    assert report["survived"] == report["canonical_writes"] == 0
    assert all(item["mutated_test_failed_for_intended_reason"] for item in report["mutations"])
    assert all(item["restored_test_passed"] for item in report["mutations"])


def test_threat_model_covers_required_v4_public_source_attacks():
    records = threat_model(); attacks = {item["attack"] for item in records}
    required = {"malicious search result", "poisoned official-looking domain", "typosquatted domain",
                "redirect laundering", "HTML injection", "PDF embedded prompt injection",
                "document prompt injection", "malicious metadata", "source impersonation",
                "fabricated publication date", "duplicate-domain laundering", "translation laundering",
                "citation laundering", "false corroboration", "model hallucination",
                "source-span fabrication", "malicious report text", "unauthorized evidence handoff",
                "restricted-source leakage", "kernel-proposal privilege escalation",
                "canonical-write escape", "stale public source", "changed live webpage",
                "replay network dependency", "campaign-scope creep"}
    assert required <= attacks
    # W11-T8 class finding, executed for V4: these threat records were never
    # executed, so no per-threat "PASS" may appear — only the declaration.
    assert all(item["result"] == "DECLARED_NOT_EXECUTED" and item["residual_risk"]
               for item in records)
