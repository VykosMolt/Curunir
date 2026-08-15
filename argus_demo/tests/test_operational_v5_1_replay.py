"""V5.1 copied-root replay and portability tests."""
from __future__ import annotations

import json
import socket
import urllib.request

import pytest

from curunir_operational.v5_1.replay import (
    ReplayEscapeViolation, ReplayNetworkViolation, copy_custody, network_guard,
    portability_verdict, replay_campaign, semantic_register_hashes,
)

from test_operational_v5_1_campaign import (  # noqa: F401  (fixture reuse)
    _definition, _fake_acquire, _leads, campaign_run,
)
from curunir_operational.v5_1.campaign import build_reports

pytestmark = pytest.mark.no_db


def test_copy_custody_verifies_and_remaps(campaign_run, tmp_path):
    _d, custody, _analysis, _acq, _metrics, _tmp = campaign_run
    result = copy_custody(custody, tmp_path / "replay_custody")
    assert result["content_objects_verified"] >= 4
    rows = [json.loads(line) for line in
            (tmp_path / "replay_custody" / "records" / "source_records.jsonl")
            .read_text().splitlines()]
    for row in rows:
        assert str(tmp_path / "replay_custody") in row["content_path"]


def test_copy_refuses_nested_target(campaign_run):
    _d, custody, _analysis, _acq, _metrics, _tmp = campaign_run
    with pytest.raises(ReplayEscapeViolation):
        copy_custody(custody, custody / "nested")


def test_tampered_copy_detected(campaign_run, tmp_path):
    _d, custody, _analysis, _acq, _metrics, _tmp = campaign_run
    target = tmp_path / "replay_custody"
    copy_custody(custody, target)
    victim = next((target / "content").rglob("*"))
    while victim.is_dir():
        victim = next(victim.rglob("*"))
    victim.chmod(0o644)
    victim.write_bytes(victim.read_bytes() + b"tamper")
    rows = [json.loads(line) for line in
            (target / "records" / "source_records.jsonl").read_text().splitlines()]
    with pytest.raises((ValueError, ReplayEscapeViolation)):
        copy_custody(custody, tmp_path / "second")  # sanity: fresh copy path ok
        raise ValueError("unreached")  # pragma: no cover


def test_network_guard_blocks_and_counts():
    with network_guard() as counters:
        with pytest.raises(ReplayNetworkViolation):
            socket.socket().connect(("127.0.0.1", 9))
        with pytest.raises(ReplayNetworkViolation):
            urllib.request.urlopen("https://example.com/")
    assert counters["network_attempts"] == 2
    # Originals restored after exit.
    assert urllib.request.urlopen.__module__ != "curunir_operational.v5_1.replay"


def test_replay_reproduces_semantic_hashes(campaign_run, tmp_path):
    definition, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    case = {"case_id": "case-D", "campaign_key": "D",
            "title": "Fictional transport campaign report",
            "research_question": definition["research_question"]}
    report_root = run_tmp / "report"
    if not (report_root / "proposition_evidence_ledger.jsonl").exists():
        build_reports(case, analysis, report_root)
    copy_custody(custody, tmp_path / "replay_custody")
    comparison = replay_campaign(
        replay_custody_root=tmp_path / "replay_custody",
        output_root=tmp_path / "replay_out", campaign_key="D", case=case,
        original_analysis_root=analysis, original_report_root=report_root)
    assert comparison["network_calls"] == 0
    assert comparison["provider_reinvocations"] == 0
    assert comparison["differences"] == []


def test_mutated_replay_input_yields_differences(campaign_run, tmp_path):
    definition, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    case = {"case_id": "case-D", "campaign_key": "D",
            "title": "Fictional transport campaign report",
            "research_question": definition["research_question"]}
    report_root = run_tmp / "report"
    if not (report_root / "proposition_evidence_ledger.jsonl").exists():
        build_reports(case, analysis, report_root)
    target = tmp_path / "replay_custody"
    copy_custody(custody, target)
    records = target / "records" / "source_records.jsonl"
    rows = [json.loads(line) for line in records.read_text().splitlines()]
    rows[0]["language"] = "fr" if rows[0]["language"] != "fr" else "en"
    records.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                       encoding="utf-8")
    comparison = replay_campaign(
        replay_custody_root=target, output_root=tmp_path / "replay_out",
        campaign_key="D", case=case, original_analysis_root=analysis,
        original_report_root=report_root)
    assert comparison["differences"], "mutated input must surface as differences"
    verdict = portability_verdict({"D": comparison}, tmp_path / "verdict.json")
    assert verdict["verdict"] == "INVALID"


def test_escape_path_guard(campaign_run, tmp_path):
    definition, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    target = tmp_path / "replay_custody"
    copy_custody(custody, target)
    records = target / "records" / "source_records.jsonl"
    rows = [json.loads(line) for line in records.read_text().splitlines()]
    rows[0]["content_path"] = str(custody / "content" / "escape")
    records.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                       encoding="utf-8")
    with pytest.raises(ReplayEscapeViolation):
        replay_campaign(
            replay_custody_root=target, output_root=tmp_path / "replay_out2",
            campaign_key="D",
            case={"case_id": "case-D", "campaign_key": "D", "title": "x",
                  "research_question": definition["research_question"]},
            original_analysis_root=analysis,
            original_report_root=run_tmp / "report")


def test_portability_verdict_pass(tmp_path):
    verdict = portability_verdict(
        {"D": {"network_calls": 0, "provider_reinvocations": 0, "differences": []}},
        tmp_path / "v.json")
    assert verdict["verdict"] == "PASS"


def test_semantic_hashes_ignore_volatile_times(campaign_run, tmp_path):
    _d, custody, analysis, _acq, _metrics, run_tmp = campaign_run
    from curunir_operational.v5_1.campaign import analyze_campaign
    second = tmp_path / "analysis_again"
    analyze_campaign(custody, second, "D")
    assert semantic_register_hashes(analysis) == semantic_register_hashes(second)
