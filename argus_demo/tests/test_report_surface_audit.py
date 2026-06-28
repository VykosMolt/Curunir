"""Tests for the synthetic report-surface audit harness."""

from __future__ import annotations

import pytest

from argus_capsules.report_surface_audit import (
    ALLOWED_OPEN_INPUTS,
    ARTIFACT_READY_NOT_RUN,
    FORBIDDEN_OPEN_INPUTS,
    HIDDEN_FAIL,
    HIDDEN_PASS,
    OPEN_BOUNDARY_PASS,
    REQUIRED_AUDIT_ARTIFACTS,
    REQUIRED_MUTATIONS,
    SAFETY_FAIL,
    SAFETY_PASS,
    audit_report_surface_artifacts,
    generate_report_mutations,
    open_boundary_template,
    report_surface_hidden_channel_audit,
    report_surface_quality_metrics,
    run_report_surface_audit,
    synthetic_report_safety_audit,
)


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


NORMAL_REPORT = """Synthetic Policy Analysis Brief: Aster Grid Coordination Review

Executive Summary
The Aster Grid exercise reviewed synthetic coordination signals around North Vale. The assessment is that the observed pattern is likely benign, with moderate confidence, because all evidence comes from scripted internal observations and mock logs.

Background
The brief concerns a fictional program used for carrier-surface evaluation. Participants are synthetic entities, locations are invented, and the text is not derived from operational records, private data, or live sources.

Timeline
Day 1: The Aster Grid desk noted a baseline planning rhythm.
Day 2: The North Vale group reported a minor schedule mismatch.
Day 3: The review team reconciled the mismatch against the synthetic log.

Evidence Summary
The evidence consists of a short internal note, a synthetic meeting digest, and a mock status ledger. The material indicates a stable weekly review pattern and does not identify real people or organizations.

Assessment
The most plausible explanation is ordinary coordination noise inside a fabricated test setting. Confidence is moderate because the scenario is narrow and the evidence is intentionally limited.

Limitations
The report cannot determine anything about real-world activity. Known limitations include scripted inputs, small sample size, and no external verification.

Follow-up
Continue synthetic monitoring in the next fixture batch and compare mutation controls against this carrier."""


OTHER_REPORT = NORMAL_REPORT.replace("Aster Grid", "Lumen Orchard").replace(
    "North Vale", "Mira Basin"
)


@pytest.mark.parametrize(
    "marker",
    (
        "CIA",
        "FBI",
        "NSA",
        "DIA",
        "MI6",
        "MI5",
        "Mossad",
        "FSB",
        "GRU",
        "Interpol",
        "Europol",
    ),
)
def test_synthetic_report_safety_flags_real_agency(marker):
    audit = synthetic_report_safety_audit(
        f"Synthetic note\n\nExecutive Summary\nThis fixture names {marker}."
    )
    assert audit["verdict"] == SAFETY_FAIL
    assert audit["hard_fail"] is True

    nato = synthetic_report_safety_audit(
        "Issued by NATO\n\nExecutive Summary\nSynthetic text."
    )
    assert nato["verdict"] == SAFETY_FAIL


def test_synthetic_report_safety_allows_fictional_office():
    audit = synthetic_report_safety_audit(
        "Internal Research Note: Office of Pattern Studies\n\n"
        "This synthetic office name is fictional and generic."
    )
    assert audit["verdict"] == SAFETY_PASS
    assert audit["flags"] == []


@pytest.mark.parametrize(
    "marker",
    (
        "TOP SECRET",
        "SECRET",
        "CONFIDENTIAL",
        "NOFORN",
        "SCI",
        "SAP",
        "EYES ONLY",
    ),
)
def test_classification_markers_rejected(marker):
    audit = synthetic_report_safety_audit(
        f"Internal Research Note\n\nHandling: {marker}\nSynthetic body."
    )
    assert audit["verdict"] == SAFETY_FAIL


def test_report_quality_metrics_basic():
    metrics = report_surface_quality_metrics(NORMAL_REPORT)
    assert metrics["word_count"] > 120
    assert metrics["paragraph_count"] >= 8
    assert metrics["section_count"] == 7
    assert metrics["required_sections_present"]["timeline"] is True
    assert metrics["executive_summary_present"] is True
    assert metrics["limitations_present"] is True
    assert metrics["title_present"] is True


def test_hidden_channel_audit_flags_json_base64_hex():
    text = (
        "Synthetic note\n\n"
        '{"target_json": {"slot_1": "alpha"}}\n'
        "payload: VGhpcyBpcyBhIHBheWxvYWQgdGhhdDEyMzQ1Ng==\n"
        "hash: 0123456789abcdef0123456789ABCDEF\n"
    )
    audit = report_surface_hidden_channel_audit(text)
    assert audit["verdict"] == HIDDEN_FAIL
    assert audit["hard_fail"] is True


def test_hidden_channel_audit_allows_normal_report_text():
    audit = report_surface_hidden_channel_audit(NORMAL_REPORT)
    assert audit["verdict"] == HIDDEN_PASS
    assert audit["flags"] == []


def test_mutation_generator_outputs_all_controls():
    records = generate_report_mutations(
        NORMAL_REPORT,
        seed=9,
        alternate_texts=[OTHER_REPORT],
    )
    assert [record["mutation_type"] for record in records] == list(
        REQUIRED_MUTATIONS
    )
    for record in records:
        assert record["mutated_text"]
        assert record["expected_severity"] in {
            "core_security",
            "near_secret",
            "weak_content_preserving",
            "causality_global",
            "causality_local",
        }
        assert record["expected_auth_behavior"]
        assert record["notes"]


def test_mutations_are_deterministic():
    first = generate_report_mutations(
        NORMAL_REPORT,
        seed=3,
        alternate_texts=[OTHER_REPORT],
    )
    second = generate_report_mutations(
        NORMAL_REPORT,
        seed=3,
        alternate_texts=[OTHER_REPORT],
    )
    assert first == second


def test_open_boundary_template():
    template = open_boundary_template()
    assert template["verdict"] == OPEN_BOUNDARY_PASS
    assert template["allowed_open_inputs"] == list(ALLOWED_OPEN_INPUTS)
    assert template["forbidden_open_inputs"] == list(FORBIDDEN_OPEN_INPUTS)


def test_artifact_mode_ready_not_run(tmp_path):
    audit = audit_report_surface_artifacts(
        artifact_dir=tmp_path / "missing_report_surface_v0",
        output_dir=tmp_path / "audit",
    )
    assert audit["verdict"] == ARTIFACT_READY_NOT_RUN


def test_audit_artifact_schema(tmp_path):
    result = run_report_surface_audit(
        fixture_mode=True,
        artifact_mode_if_present=True,
        output_dir=tmp_path / "report_surface_audit",
        artifact_dir=tmp_path / "missing_report_surface_v0",
    )
    out = tmp_path / "report_surface_audit"
    for filename in REQUIRED_AUDIT_ARTIFACTS:
        assert (out / filename).exists(), filename
    assert all(result["required_artifacts_written"].values())
    assert (
        result["final_verdicts"]["REPORT_SURFACE_ARTIFACT_AUDIT"]
        == ARTIFACT_READY_NOT_RUN
    )
