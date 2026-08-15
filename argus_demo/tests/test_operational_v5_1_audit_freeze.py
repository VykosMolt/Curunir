"""Focused tests: V5.1 anti-memorization scanner and production freeze."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_1 import anti_memorization as memo
from curunir_operational.v5_1 import freeze

pytestmark = pytest.mark.no_db


def _campaign_root(tmp_path, label: str, *, title: str, url: str, sentence: str):
    root = tmp_path / label
    capture = root / "capture"
    capture.mkdir(parents=True)
    (capture / "source_object_manifest.json").write_text(json.dumps([
        {"title": title, "requested_urls": [url], "final_urls": [url]},
    ]), encoding="utf-8")
    (root / "investigation_report.md").write_text(
        "# Heading\n" + sentence + "\n", encoding="utf-8")
    return root


def test_term_manifest_extracts_titles_urls_domains_sentences(tmp_path):
    root = _campaign_root(
        tmp_path, "campaign_x",
        title="Fictional Aurora Deepwater Monitoring Consortium",
        url="https://aurora-example-consortium.example/reports/annual.pdf",
        sentence="The fictional consortium published its findings across three regional programmes in coordination with example authorities.")
    manifest = memo.build_term_manifest({"x": root})
    kinds = {term["term_kind"] for term in manifest["terms"]}
    assert {"ENTITY_NAME", "SOURCE_URL", "SOURCE_DOMAIN", "REPORT_SENTENCE"} <= kinds
    assert manifest["term_count"] == len(manifest["terms"])
    assert manifest["integrity_hash"]


def test_generic_short_terms_are_filtered(tmp_path):
    root = _campaign_root(tmp_path, "campaign_x", title="public",
                          url="https://e.example/a.pdf",
                          sentence="Short line.")
    manifest = memo.build_term_manifest({"x": root})
    assert all(term["term"].casefold() != "public" for term in manifest["terms"])


def test_production_hit_is_suspicious_and_blocks(tmp_path):
    root = _campaign_root(
        tmp_path, "campaign_x",
        title="Fictional Aurora Deepwater Monitoring Consortium",
        url="https://aurora-example-consortium.example/reports/annual.pdf",
        sentence="A sufficiently long fictional report sentence for manifest harvesting purposes only.")
    manifest = memo.build_term_manifest({"x": root})
    production = tmp_path / "pkg" / "rules.py"
    production.parent.mkdir()
    production.write_text(
        "TARGET = 'Fictional Aurora Deepwater Monitoring Consortium'\n",
        encoding="utf-8")
    report = memo.scan_paths([production], manifest)
    assert report["occurrences_by_class"]["SUSPICIOUS_CASE_ENCODING"] == 1
    assert report["verdict"] == "BLOCKED_SUSPICIOUS_CASE_ENCODING"


def test_test_fixture_and_artifact_hits_do_not_block(tmp_path):
    root = _campaign_root(
        tmp_path, "campaign_x",
        title="Fictional Aurora Deepwater Monitoring Consortium",
        url="https://aurora-example-consortium.example/reports/annual.pdf",
        sentence="A sufficiently long fictional report sentence for manifest harvesting purposes only.")
    manifest = memo.build_term_manifest({"x": root})
    test_file = tmp_path / "tests" / "test_something.py"
    test_file.parent.mkdir()
    test_file.write_text("x = 'Fictional Aurora Deepwater Monitoring Consortium'\n",
                         encoding="utf-8")
    artifact = tmp_path / "artifacts" / "notes.md"
    artifact.parent.mkdir()
    artifact.write_text("Fictional Aurora Deepwater Monitoring Consortium\n",
                        encoding="utf-8")
    report = memo.scan_paths([test_file, artifact], manifest)
    assert report["occurrences_by_class"]["TEST_FIXTURE_ONLY"] == 1
    assert report["occurrences_by_class"]["LEGITIMATE_GENERIC_REFERENCE"] == 1
    assert report["verdict"] == "PASS_HARDENED"


def test_heldout_term_is_forbidden_leakage_everywhere(tmp_path):
    heldout = _campaign_root(
        tmp_path, "campaign_c",
        title="Fictional Meridian Spectrum Allocation Tribunal Docket",
        url="https://meridian-docket.example/decisions/42.pdf",
        sentence="A sufficiently long held-out fictional sentence that must never appear in development files.")
    manifest = memo.build_term_manifest({}, {"c": heldout})
    test_file = tmp_path / "tests" / "test_leak.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "x = 'Fictional Meridian Spectrum Allocation Tribunal Docket'\n",
        encoding="utf-8")
    report = memo.scan_paths([test_file], manifest)
    assert report["occurrences_by_class"]["FORBIDDEN_HELDOUT_LEAKAGE"] == 1
    assert report["verdict"] == "INVALID_FORBIDDEN_HELDOUT_LEAKAGE"


def test_documented_rule_classification(tmp_path):
    root = _campaign_root(
        tmp_path, "campaign_x",
        title="Fictional Aurora Deepwater Monitoring Consortium",
        url="https://aurora-example-consortium.example/reports/annual.pdf",
        sentence="A sufficiently long fictional report sentence for manifest harvesting purposes only.")
    manifest = memo.build_term_manifest({"x": root})
    production = tmp_path / "pkg" / "rules.py"
    production.parent.mkdir()
    production.write_text(
        "DOMAIN = 'aurora-example-consortium.example'\n", encoding="utf-8")
    report = memo.scan_paths(
        [production], manifest,
        documented_rules={"aurora-example-consortium.example":
                          "generic archive-domain normalization documented in design"})
    assert report["occurrences_by_class"]["DOCUMENTED_SOURCE_SPECIFIC_RULE"] == 1
    assert report["verdict"] == "PASS_HARDENED"


def _freeze_tree(tmp_path):
    code = tmp_path / "pkg" / "module.py"
    code.parent.mkdir()
    code.write_text("VALUE = 1\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    return code, config


def test_freeze_and_verify_pass(tmp_path):
    code, config = _freeze_tree(tmp_path)
    manifest_path = tmp_path / "production_repair_freeze.json"
    manifest = freeze.freeze_production(
        scope_paths={"PRODUCTION_CODE": [code], "CONFIGURATION": [config]},
        thresholds={"context_radius": 600}, prompts={"reviewer": "neutral instructions"},
        packet_builder_version="v5.1-packets-1", provider_versions={"panel": "isolated-2026"},
        output_path=manifest_path)
    assert manifest["integrity_hash"]
    result = freeze.verify_freeze(manifest_path)
    assert result["verdict"] == "PASS"
    assert result["verified_files"] == 2


def test_post_freeze_modification_detected(tmp_path):
    code, config = _freeze_tree(tmp_path)
    manifest_path = tmp_path / "production_repair_freeze.json"
    freeze.freeze_production(
        scope_paths={"PRODUCTION_CODE": [code], "CONFIGURATION": [config]},
        thresholds={}, prompts={}, packet_builder_version="v5.1-packets-1",
        provider_versions={}, output_path=manifest_path)
    code.write_text("VALUE = 2\n", encoding="utf-8")
    result = freeze.verify_freeze(manifest_path)
    assert result["verdict"] == "INVALID"
    assert result["reason"] == "POST_FREEZE_MODIFICATION_DETECTED"
    assert result["drifted_files"][0]["path"].endswith("module.py")


def test_tampered_manifest_detected(tmp_path):
    code, config = _freeze_tree(tmp_path)
    manifest_path = tmp_path / "production_repair_freeze.json"
    freeze.freeze_production(
        scope_paths={"PRODUCTION_CODE": [code]},
        thresholds={}, prompts={}, packet_builder_version="v5.1-packets-1",
        provider_versions={}, output_path=manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["packet_builder_version"] = "tampered"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    result = freeze.verify_freeze(manifest_path)
    assert result["verdict"] == "INVALID"
    assert result["reason"] == "FREEZE_MANIFEST_TAMPERED"


def test_freeze_guards(tmp_path):
    code, _ = _freeze_tree(tmp_path)
    with pytest.raises(ValueError):
        freeze.freeze_production(
            scope_paths={"CONFIGURATION": [code]}, thresholds={}, prompts={},
            packet_builder_version="x", provider_versions={},
            output_path=tmp_path / "f.json")
    with pytest.raises(ValueError):
        freeze.freeze_production(
            scope_paths={"PRODUCTION_CODE": [code], "UNKNOWN_SCOPE": [code]},
            thresholds={}, prompts={}, packet_builder_version="x",
            provider_versions={}, output_path=tmp_path / "f.json")
    with pytest.raises(ValueError):
        freeze.freeze_production(
            scope_paths={"PRODUCTION_CODE": [tmp_path / "missing.py"]},
            thresholds={}, prompts={}, packet_builder_version="x",
            provider_versions={}, output_path=tmp_path / "f.json")
