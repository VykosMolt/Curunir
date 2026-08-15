from __future__ import annotations

import json

import pytest

from curunir_operational.v5.models import SURFACES
from curunir_operational.v5_1 import cli

pytestmark = pytest.mark.no_db

EXTRACTION, ORIGIN, DEPENDENCE, CLAIM, CONTRADICTION, FAITHFULNESS = SURFACES

ALL_SUBCOMMANDS = (
    "corpora-partition", "scan-memorization", "freeze-production", "verify-freeze",
    "candidate-scan-record", "freeze-campaigns", "acquire", "analyze", "report",
    "build-heldout", "assign-reviewers", "freeze-reviews", "score-heldout",
    "kernel-regression", "replay", "mutations", "stress", "human-package",
)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
                            for record in records), encoding="utf-8")


def _corpora_spec(tmp_path):
    corpus = tmp_path / "corpus"
    _write_jsonl(corpus / "frozen_packets.jsonl", [
        {"packet_id": "pkt-0001", "surface": EXTRACTION, "material": {"excerpt": "alpha"}},
        {"packet_id": "pkt-0002", "surface": DEPENDENCE, "material": {"excerpt": "beta"}},
        {"packet_id": "pkt-0003", "surface": CLAIM, "material": {"excerpt": "gamma"}},
    ])
    consensus = tmp_path / "consensus.json"
    _write_json(consensus, [
        {"blind_packet_id": "pkt-0001", "surface": EXTRACTION,
         "decision_counts": {"CORRECT": 3}, "consensus_state": "UNANIMOUS_CORRECT"},
        {"blind_packet_id": "pkt-0002", "surface": DEPENDENCE,
         "decision_counts": {"CORRECT": 2, "INCORRECT": 1}, "consensus_state": "MAJORITY_CORRECT"},
        {"blind_packet_id": "pkt-0003", "surface": CLAIM,
         "decision_counts": {"CORRECT": 2, "PARTIALLY_CORRECT": 1},
         "consensus_state": "MAJORITY_CORRECT"},
    ])
    errors = tmp_path / "error_records.json"
    _write_json(errors, [{"packet_id": "pkt-0002", "error_id": "err-0001"}])
    spec = tmp_path / "corpora_spec.json"
    _write_json(spec, {"corpus_root": str(corpus), "consensus_path": str(consensus),
                       "error_records_paths": [str(errors)],
                       "output_root": str(tmp_path / "corpora_out")})
    return spec


def _scan_spec(tmp_path, *, planted_comment=None, include_heldout=False):
    campaign_root = tmp_path / "campaign_a_artifacts"
    _write_json(campaign_root / "source_object_manifest.json", [{
        "title": "Harbor Renewal Oversight Notice",
        "requested_urls": ["https://registry.example/notices/harbor-renewal"],
        "final_urls": ["https://registry.example/notices/harbor-renewal"],
    }])
    heldout_root = tmp_path / "heldout_c_artifacts"
    _write_json(heldout_root / "source_object_manifest.json", [{
        "title": "Coastal Ledger Compliance Notice",
        "requested_urls": ["https://ledger.example/records/coastal-compliance"],
        "final_urls": [],
    }])
    target = tmp_path / "production_module.py"
    body = "def classify(states):\n    return sorted(states)\n"
    if planted_comment:
        body += f"# {planted_comment}\n"
    target.write_text(body, encoding="utf-8")
    spec = tmp_path / "scan_spec.json"
    payload = {"campaign_roots": {"campaign_a": str(campaign_root)},
               "target_paths": [str(target)]}
    if include_heldout:
        payload["heldout_roots"] = {"campaign_c": str(heldout_root)}
    _write_json(spec, payload)
    return spec


def _freeze_spec(tmp_path):
    code = tmp_path / "frozen_code.py"
    code.write_text("VALUE = 1\n", encoding="utf-8")
    spec = tmp_path / "freeze_spec.json"
    _write_json(spec, {
        "scope_paths": {"PRODUCTION_CODE": [str(code)]},
        "thresholds": {"minimum_signals": 2},
        "prompts": {"reviewer": "Adjudicate strictly from the packet contents."},
        "packet_builder_version": "v5-1-packet-builder-1",
        "provider_versions": {"parser": "stdlib-1"},
    })
    return spec, code


def test_help_renders_every_subcommand(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0
    rendered = capsys.readouterr().out
    for name in ALL_SUBCOMMANDS:
        assert name in rendered


def test_subcommand_help_renders(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["scan-memorization", "--help"])
    assert excinfo.value.code == 0
    rendered = capsys.readouterr().out
    assert "--spec" in rendered and "--output" in rendered


def test_missing_required_argument_is_nonzero():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["corpora-partition"])
    assert excinfo.value.code != 0


def test_unknown_subcommand_is_nonzero():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["not-a-command"])
    assert excinfo.value.code != 0


def test_corpora_partition_partitions_fixture(tmp_path, capsys):
    output = tmp_path / "partition_result.json"
    exit_code = cli.main(["corpora-partition", "--spec", str(_corpora_spec(tmp_path)),
                          "--output", str(output)])
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "PASS"
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["counts"] == {"V5_1_REPAIR_DEVELOPMENT_CORPUS": 2,
                                  "V5_1_REGRESSION_CORPUS": 1,
                                  "V5_1_REPAIR_VERIFICATION_CORPUS": 2}
    assert (tmp_path / "corpora_out/12_regression/regression_corpus.jsonl").is_file()


def test_corpora_partition_output_is_append_only(tmp_path, capsys):
    spec = _corpora_spec(tmp_path)
    output = tmp_path / "partition_result.json"
    assert cli.main(["corpora-partition", "--spec", str(spec), "--output", str(output)]) == 0
    assert cli.main(["corpora-partition", "--spec", str(spec), "--output", str(output)]) == 1
    captured = capsys.readouterr()
    error = json.loads(captured.err.splitlines()[-1])
    assert error["status"] == "ERROR"
    assert error["category"] == "FileExistsError"


def test_scan_memorization_clean_target_passes(tmp_path, capsys):
    audit = tmp_path / "memorization_audit.json"
    exit_code = cli.main(["scan-memorization", "--spec", str(_scan_spec(tmp_path)),
                          "--output", str(audit)])
    assert exit_code == 0
    report = json.loads(audit.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS_HARDENED"
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_scan_memorization_blocks_case_encoding(tmp_path, capsys):
    spec = _scan_spec(tmp_path, planted_comment="registry.example")
    audit = tmp_path / "memorization_audit.json"
    assert cli.main(["scan-memorization", "--spec", str(spec), "--output", str(audit)]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "FAIL"
    assert printed["verdict"] == "BLOCKED_SUSPICIOUS_CASE_ENCODING"
    assert json.loads(audit.read_text(encoding="utf-8"))["occurrences_by_class"][
        "SUSPICIOUS_CASE_ENCODING"] >= 1


def test_scan_memorization_flags_heldout_leakage(tmp_path, capsys):
    spec = _scan_spec(tmp_path, planted_comment="ledger.example", include_heldout=True)
    audit = tmp_path / "memorization_audit.json"
    assert cli.main(["scan-memorization", "--spec", str(spec), "--output", str(audit)]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "INVALID_FORBIDDEN_HELDOUT_LEAKAGE"


def test_scan_memorization_spec_requires_roots_and_targets(tmp_path, capsys):
    spec = tmp_path / "scan_spec.json"
    _write_json(spec, {"target_paths": []})
    exit_code = cli.main(["scan-memorization", "--spec", str(spec),
                          "--output", str(tmp_path / "audit.json")])
    assert exit_code == 1
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert error["category"] == "ValueError"


def test_freeze_production_then_verify_passes(tmp_path, capsys):
    spec, _ = _freeze_spec(tmp_path)
    manifest_path = tmp_path / "freeze_manifest.json"
    assert cli.main(["freeze-production", "--spec", str(spec),
                     "--output", str(manifest_path)]) == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["scopes"]["PRODUCTION_CODE"]
    assert cli.main(["verify-freeze", "--manifest", str(manifest_path)]) == 0
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert printed["status"] == "PASS"
    assert printed["result"]["verdict"] == "PASS"


def test_verify_freeze_detects_post_freeze_drift(tmp_path, capsys):
    spec, code = _freeze_spec(tmp_path)
    manifest_path = tmp_path / "freeze_manifest.json"
    assert cli.main(["freeze-production", "--spec", str(spec),
                     "--output", str(manifest_path)]) == 0
    code.write_text("VALUE = 2\n", encoding="utf-8")
    result_path = tmp_path / "verification_result.json"
    exit_code = cli.main(["verify-freeze", "--manifest", str(manifest_path),
                          "--output", str(result_path)])
    assert exit_code == 1
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert printed["status"] == "FAIL"
    assert printed["result"]["reason"] == "POST_FREEZE_MODIFICATION_DETECTED"
    assert json.loads(result_path.read_text(encoding="utf-8"))["verdict"] == "INVALID"


def test_spec_must_be_a_json_object(tmp_path, capsys):
    spec = tmp_path / "bad_spec.json"
    spec.write_text("[1, 2, 3]\n", encoding="utf-8")
    exit_code = cli.main(["corpora-partition", "--spec", str(spec),
                          "--output", str(tmp_path / "out.json")])
    assert exit_code == 1
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert error["status"] == "ERROR"
    assert error["category"] == "ValueError"


def test_missing_spec_file_is_nonzero(tmp_path, capsys):
    exit_code = cli.main(["corpora-partition", "--spec", str(tmp_path / "absent.json"),
                          "--output", str(tmp_path / "out.json")])
    assert exit_code == 1
    assert json.loads(capsys.readouterr().err.splitlines()[-1])["status"] == "ERROR"


def test_unimplemented_backing_module_fails_loudly(tmp_path, capsys):
    spec = tmp_path / "empty_spec.json"
    _write_json(spec, {})
    exit_code = cli.main(["build-heldout", "--spec", str(spec)])
    assert exit_code == 1
    error = json.loads(capsys.readouterr().err.splitlines()[-1])
    assert error["status"] == "ERROR"
    assert error["command"] == "build-heldout"
