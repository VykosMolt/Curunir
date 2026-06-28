"""Tests for the REPORT_SURFACE_V0 integrity/reproducibility audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_capsules.report_surface_integrity_audit import (
    REQUIRED_REPORT_SURFACE_V0_FILES,
    audit_artifact_completeness,
    audit_carrier_style_routing,
    audit_open_boundary_integrity,
    audit_reproduction_commands,
    audit_sample_report_records,
    audit_verdict_consistency,
    compute_report_surface_integrity_verdict,
    detect_sample_report_headings,
    sample_report_safety_flags,
    static_scan_source_texts,
)


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


EXPECTED_VERDICTS = {
    "ARGUS_CAPSULE_REPORT_SURFACE_TINY64": "PARTIAL_FIELD_SIGNAL",
    "ARGUS_CAPSULE_REPORT_SURFACE_TINY256_STAGE2": (
        "PASS_REPORT_SURFACE_STAGE2"
    ),
    "ARGUS_CAPSULE_REPORT_SURFACE_TINY256_STAGE3": (
        "PASS_REPORT_SURFACE_WEAK_SMOKE"
    ),
    "REPORT_SURFACE_NEGATIVE_CONTROLS": "PASS_CORE_ZERO",
    "REPORT_SURFACE_CARRIER_CAUSALITY": "PASS_STRONG",
    "REPORT_SURFACE_V0": "WEAK_VALID_OPEN_DEMO",
}


GOOD_OPEN_BOUNDARY = {
    "allowed_open_inputs": [
        "carrier_text",
        "secret",
        "salt",
        "version",
        "tokenizer",
        "private_decoder_weights",
        "schema_hash_validation",
    ],
    "forbidden_open_inputs": [
        "original_report",
        "target_json",
        "heldout_labels",
        "renderer",
        "report_plan",
        "encoder",
        "gold_slot_ids",
        "gold_slot_spans",
        "debug_ids",
        "teacher_logits",
        "date_teacher",
        "lookup_tables",
        "nearest-neighbor retrieval",
        "inverse_grammar",
        "symbolic_parser",
        "hidden JSON/base64/hex",
        "remote_llm",
    ],
    "renderer_used_at_open": False,
    "report_plan_used_at_open": False,
}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_claim_files(root: Path) -> None:
    (root / "no_claims.md").write_text(
        "\n".join(
            [
                "# no claims",
                "- No cryptographic security claim.",
                "- No full ARGUS codec claim.",
                "- No operational deception claim.",
                "- No real agency / intelligence report claim.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "known_limitations.md").write_text(
        "\n".join(
            [
                "# limitations",
                "- Value remains the exact tuple limiter.",
                "- Near-secret and paraphrase cases were not separately "
                "stress-tested in this weak demo.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_minimal_artifact_dir(root: Path) -> None:
    root.mkdir(parents=True)
    for name in REQUIRED_REPORT_SURFACE_V0_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "final_verdicts.json":
            _write_json(path, EXPECTED_VERDICTS)
        elif name == "open_boundary_audit.json":
            _write_json(path, GOOD_OPEN_BOUNDARY)
        elif name.endswith(".json"):
            _write_json(path, {"ok": True})
        elif name.endswith(".jsonl"):
            path.write_text('{"carrier_text": "synthetic"}\n', encoding="utf-8")
        elif name.endswith(".csv"):
            path.write_text("column\nvalue\n", encoding="utf-8")
        elif name == "reproduction_commands.sh":
            path.write_text(
                "python -m argus_capsules.run_codec_stage2_field_head_study "
                "--schema tiny_64 --codec-carrier-style synthetic_report "
                "--out-dir artifacts/private_capsules/report_surface_v0/tiny64\n",
                encoding="utf-8",
            )
        else:
            path.write_text("synthetic artifact\n", encoding="utf-8")
    _write_claim_files(root)


def test_integrity_required_file_list(tmp_path):
    root = tmp_path / "report_surface_v0"
    _write_minimal_artifact_dir(root)
    audit = audit_artifact_completeness(root)
    assert audit["REPORT_SURFACE_V0_ARTIFACT_COMPLETENESS"] == "PASS"
    assert set(REQUIRED_REPORT_SURFACE_V0_FILES) == set(
        audit["required_files"]
    )

    (root / "sample_reports.jsonl").unlink()
    missing = audit_artifact_completeness(root)
    assert (
        missing["REPORT_SURFACE_V0_ARTIFACT_COMPLETENESS"]
        == "FAIL_MISSING_FILES"
    )
    assert missing["missing_files"] == ["sample_reports.jsonl"]


def test_verdict_consistency_accepts_weak_demo(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    _write_json(root / "final_verdicts.json", EXPECTED_VERDICTS)
    _write_claim_files(root)
    (root / "report_surface_summary.md").write_text(
        "Weak valid-open synthetic demo only.\n",
        encoding="utf-8",
    )
    audit = audit_verdict_consistency(root)
    assert audit["REPORT_SURFACE_V0_VERDICT_CONSISTENCY"] == "PASS"


def test_verdict_consistency_rejects_crypto_or_full_codec_claims(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    bad = dict(EXPECTED_VERDICTS)
    bad["REPORT_SURFACE_V0"] = "PASS_CRYPTOGRAPHIC_FULL_CODEC"
    _write_json(root / "final_verdicts.json", bad)
    _write_claim_files(root)
    audit = audit_verdict_consistency(root)
    assert audit["REPORT_SURFACE_V0_VERDICT_CONSISTENCY"] == "FAIL_OVERCLAIM"


def test_open_boundary_required_forbidden_inputs(tmp_path):
    root = tmp_path / "artifact"
    root.mkdir()
    _write_json(root / "open_boundary_audit.json", GOOD_OPEN_BOUNDARY)
    audit = audit_open_boundary_integrity(root, source_files=())
    assert audit["REPORT_SURFACE_OPEN_BOUNDARY_INTEGRITY"] == "PASS"

    bad = dict(GOOD_OPEN_BOUNDARY)
    bad["forbidden_open_inputs"] = [
        item for item in GOOD_OPEN_BOUNDARY["forbidden_open_inputs"]
        if item != "renderer"
    ]
    _write_json(root / "open_boundary_audit.json", bad)
    failed = audit_open_boundary_integrity(root, source_files=())
    assert (
        failed["REPORT_SURFACE_OPEN_BOUNDARY_INTEGRITY"]
        == "FAIL_BOUNDARY_LEAK"
    )
    assert "renderer" in failed["missing_forbidden_open_concepts"]


def test_routing_audit_detects_hardcoded_frozen_path():
    source = '''
parser.add_argument("--codec-carrier-style", choices=("narrative", "synthetic_report"), default="narrative")
parser.add_argument("--codec-word-count-min")
parser.add_argument("--codec-word-count-max")
parser.add_argument("--allow-weak-stage3", action="store_true")
narrative_style = args.codec_carrier_style == "narrative"
stage3_dir = Path(TINY256_STAGE3_FIELD_PRESERVATION_OUT_DIR) if narrative_style else out_dir / "stage3_field_preservation"
if args.codec_carrier_style == "synthetic_report":
    args.out_dir = "artifacts/private_capsules/tiny256_story_capsule_v0"
'''
    audit = audit_carrier_style_routing(source_text=source)
    assert (
        audit["REPORT_SURFACE_CARRIER_STYLE_ROUTING"]
        == "FAIL_HARDCODED_FROZEN_PATH"
    )


def test_reproduction_commands_detect_unsafe_frozen_output():
    command_text = """
python -m argus_capsules.run_codec_stage2_field_head_study \
  --schema tiny_256 --codec-carrier-style synthetic_report \
  --out-dir artifacts/private_capsules/tiny256_story_capsule_v0
"""
    audit = audit_reproduction_commands(command_text=command_text)
    assert (
        audit["REPORT_SURFACE_REPRO_COMMANDS"]
        == "FAIL_UNSAFE_OR_OVERWRITES"
    )
    assert audit["frozen_path_hits"]


def test_sample_report_heading_detection_paragraph_style():
    text = """
Internal Policy Analysis Memo: synthetic heading fixture

Executive Summary. Body.
Background. Body.
Synthetic Entities. Body.
Event Timeline. Body.
Evidence Summary. Body.
Assessment. Body.
Confidence / Limitations. Body.
Follow-up Questions. Body.
"""
    headings = detect_sample_report_headings(text)
    assert all(headings.values())


def test_sample_report_rejects_classification_markers():
    flags = sample_report_safety_flags(
        "Executive Summary. This synthetic fixture says TOP SECRET // NOFORN."
    )
    assert "top secret" in flags
    assert "noforn" in flags


def test_static_scan_ignores_declarative_forbidden_lists():
    findings = static_scan_source_texts(
        {
            "fixture.py": '''
FORBIDDEN_OPEN_INPUTS = [
    "renderer",
    "report_plan",
    "inverse_grammar",
    "symbolic_parser",
    "base64_hex_json_in_report",
]
'''
        }
    )
    assert findings == []


def test_final_verdict_logic_pass():
    verdicts = {
        "FROZEN_STORY_ARTIFACTS_UNTOUCHED": "PASS",
        "REPORT_SURFACE_V0_ARTIFACT_COMPLETENESS": "PASS",
        "REPORT_SURFACE_V0_VERDICT_CONSISTENCY": "PASS",
        "REPORT_SURFACE_OPEN_BOUNDARY_INTEGRITY": "PASS",
        "REPORT_SURFACE_CARRIER_STYLE_ROUTING": "PASS",
        "REPORT_SURFACE_REPRO_COMMANDS": "PASS_PLAUSIBLE",
        "REPORT_SURFACE_SAMPLE_REPORTS": "PASS",
    }
    assert (
        compute_report_surface_integrity_verdict(verdicts)
        == "PASS_READY_TO_FREEZE_WEAK_DEMO"
    )


def test_final_verdict_logic_fail_on_frozen_modification():
    verdicts = {
        "FROZEN_STORY_ARTIFACTS_UNTOUCHED": "FAIL_MODIFIED",
        "REPORT_SURFACE_V0_ARTIFACT_COMPLETENESS": "PASS",
        "REPORT_SURFACE_V0_VERDICT_CONSISTENCY": "PASS",
        "REPORT_SURFACE_OPEN_BOUNDARY_INTEGRITY": "PASS",
        "REPORT_SURFACE_CARRIER_STYLE_ROUTING": "PASS",
        "REPORT_SURFACE_REPRO_COMMANDS": "PASS_PLAUSIBLE",
        "REPORT_SURFACE_SAMPLE_REPORTS": "PASS",
    }
    assert (
        compute_report_surface_integrity_verdict(verdicts)
        == "FAIL_DO_NOT_FREEZE"
    )
