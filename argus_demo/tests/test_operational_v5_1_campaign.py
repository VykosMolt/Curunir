"""V5.1 campaign lifecycle tests — synthetic fictional custody, no network."""
from __future__ import annotations

import json

import pytest

from curunir_operational.v4.models import RetrievalRecord, SourceRecord, sha256, stable_id
from curunir_operational.v4.io import append_jsonl
from curunir_operational.v5_1.campaign import (
    CANDIDATE_SCAN_FIELDS, analyze_campaign, build_reports, execute_acquisition,
    freeze_campaign_definitions, plan_acquisition, record_candidate_scan,
)

pytestmark = pytest.mark.no_db

_TIME = "2027-05-06T10:00:00+00:00"


def _matter(index: int, state: str = "REJECTED") -> dict:
    return {field: f"fictional value {index}" for field in CANDIDATE_SCAN_FIELDS} | {
        "matter": f"Fictional matter {index}", "expected_source_count": "20",
        "languages": "en,de", "selection_state": state,
        "selection_reason": "synthetic fixture"}


def test_candidate_scan_requires_six_matters(tmp_path):
    with pytest.raises(ValueError, match="at least 6"):
        record_candidate_scan([_matter(1)], tmp_path / "scan.json")


def test_candidate_scan_requires_all_fields(tmp_path):
    matters = [_matter(index) for index in range(6)]
    del matters[0]["privacy_risk"]
    with pytest.raises(ValueError, match="missing fields"):
        record_candidate_scan(matters, tmp_path / "scan.json")


def test_candidate_scan_records_and_hashes(tmp_path):
    matters = [_matter(index, "SELECTED" if index < 3 else "REJECTED")
               for index in range(7)]
    record = record_candidate_scan(matters, tmp_path / "scan.json")
    assert record["candidate_count"] == 7 and record["selected_count"] == 3
    assert record["integrity_hash"]


def _definition(key: str, klass: str, domain: str) -> dict:
    return {"campaign_key": key, "campaign_class": klass,
            "research_question": f"Fictional frozen question {key}",
            "scope_statement": "synthetic scope", "stop_rules": ["budget"],
            "expected_languages": ["en", "de"],
            "seed_urls": [f"https://{domain}/index"]}


def test_freeze_definitions_validates_classes(tmp_path):
    bad = [_definition("C", "TRANSPORT_DISRUPTION", "c.example"),
           _definition("D", "ENERGY_INCIDENT", "d.example"),
           _definition("E", "IMPLEMENTING_ACT", "e.example")]
    with pytest.raises(ValueError, match="not in its eligible class set"):
        freeze_campaign_definitions(bad, tmp_path / "defs.json")


def test_freeze_definitions_rejects_prior_campaign_domains(tmp_path):
    prior = tmp_path / "prior_custody"
    (prior / "capture").mkdir(parents=True)
    (prior / "capture" / "source_object_manifest.json").write_text(json.dumps(
        [{"requested_urls": ["https://reused.example/report"], "final_urls": []}]),
        encoding="utf-8")
    definitions = [_definition("C", "PUBLIC_AI_PROGRAMME", "reused.example"),
                   _definition("D", "ENERGY_INCIDENT", "d.example"),
                   _definition("E", "IMPLEMENTING_ACT", "e.example")]
    with pytest.raises(ValueError, match="overlap prior"):
        freeze_campaign_definitions(definitions, tmp_path / "defs.json", [prior])


def test_freeze_definitions_requires_three_campaigns(tmp_path):
    with pytest.raises(ValueError, match="exactly three"):
        freeze_campaign_definitions(
            [_definition("C", "PUBLIC_AI_PROGRAMME", "c.example")],
            tmp_path / "defs.json")


# --- synthetic custody -------------------------------------------------------

_EN_OFFICIAL = (
    "The Fictional Transit Authority approved the tunnel refurbishment plan on "
    "5 May 2027. The Fictional Transit Authority plans to reopen the freight "
    "corridor in September 2027.\n\n"
    "The corridor closure affected 41 freight services during April 2027 and "
    "required replacement bus operations across the region throughout the "
    "closure period.\n"
)
_EN_MIRROR = _EN_OFFICIAL + "Mirrored for archive purposes.\n"
_DE_TRANSLATION = (
    "Die Fictional Transit Authority hat den Tunnelsanierungsplan am 5. Mai 2027 "
    "genehmigt. Die Fictional Transit Authority plant die Wiedereröffnung des "
    "Frachtkorridors im September 2027. Quelle: "
    "https://transit-authority.example/plan-2027.\n"
)
_EN_DISPUTE = (
    "The Fictional Transit Authority did not approve the tunnel refurbishment "
    "plan on 5 May 2027, the works council stated on 6 May 2027.\n"
)


def _fake_acquire(*, case_id, lead, decision, custody_root, publisher, source_class,
                  title=None, publication_time=None, **_ignored):
    body = _fake_acquire.bodies[lead.url].encode("utf-8")
    content_hash = sha256(body)
    content_dir = custody_root / "content" / content_hash[:2]
    content_dir.mkdir(parents=True, exist_ok=True)
    content_path = content_dir / content_hash
    content_path.write_bytes(body)
    retrieval = RetrievalRecord(
        stable_id("retrieval", case_id, lead.lead_id), case_id, lead.lead_id,
        lead.url, lead.url, (), publisher, _TIME, _TIME, 200,
        {"content-type": "text/plain; charset=utf-8"}, "text/plain", len(body),
        content_hash, decision.decision_id, "fake-acquirer-1", None, None,
        lead.language, decision.access_marking, "CAPTURED")
    source = SourceRecord(
        stable_id("source-object", content_hash), case_id,
        (retrieval.retrieval_id,), content_hash, str(content_path),
        (lead.url,), (lead.url,), publisher, source_class, title or lead.title,
        lead.language, publication_time, _TIME, "CAPTURED_UNREVIEWED",
        decision.access_marking)
    records = custody_root / "records"
    append_jsonl(records / "retrieval_records.jsonl", (retrieval.to_record(),))
    append_jsonl(records / "source_records.jsonl", (source.to_record(),))
    return retrieval, source


def _leads():
    return [
        {"url": "https://transit-authority.example/plan-2027",
         "title": "Official refurbishment decision", "language": "en",
         "publisher": "Fictional Transit Authority", "source_class": "OFFICIAL_PRIMARY",
         "access_rationale": "public official record",
         "publication_time": _TIME},
        {"url": "https://mirror-archive.example/plan-2027-copy",
         "title": "Archived copy", "language": "en",
         "publisher": "Fictional Mirror Archive", "source_class": "ARCHIVE",
         "access_rationale": "public archive", "publication_time": _TIME},
        {"url": "https://nachrichten.example/tunnelplan",
         "title": "German coverage", "language": "de",
         "publisher": "Fictional Nachrichten", "source_class": "SECONDARY_REPORTING",
         "access_rationale": "public reporting", "publication_time": _TIME},
        {"url": "https://werkrat.example/dispute",
         "title": "Dispute statement", "language": "en",
         "publisher": "Fictional Works Council", "source_class": "SECONDARY_REPORTING",
         "access_rationale": "public statement", "publication_time": _TIME},
        {"url": "https://blocked.example/paywalled",
         "title": "Paywalled item", "language": "en",
         "publisher": "Fictional Paywall", "source_class": "SECONDARY_REPORTING",
         "access_rationale": "blocked by policy", "block_reason": "PAYWALLED"},
    ]


@pytest.fixture()
def campaign_run(tmp_path):
    _fake_acquire.bodies = {
        "https://transit-authority.example/plan-2027": _EN_OFFICIAL,
        "https://mirror-archive.example/plan-2027-copy": _EN_MIRROR,
        "https://nachrichten.example/tunnelplan": _DE_TRANSLATION,
        "https://werkrat.example/dispute": _EN_DISPUTE,
        "https://blocked.example/paywalled": "never fetched",
    }
    definition = _definition("D", "TRANSPORT_DISRUPTION", "transit-authority.example")
    custody = tmp_path / "custody"

    def blocked_aware(**kwargs):
        decision = kwargs["decision"]
        if decision.state != "ALLOW_PUBLIC_RETRIEVAL":
            lead = kwargs["lead"]
            retrieval = RetrievalRecord(
                stable_id("retrieval", kwargs["case_id"], lead.lead_id),
                kwargs["case_id"], lead.lead_id, lead.url, None, (),
                kwargs["publisher"], _TIME, _TIME, None, {}, None, None, None,
                decision.decision_id, "fake-acquirer-1",
                f"ACCESS_POLICY:{decision.state}", None, lead.language,
                decision.access_marking, "BLOCKED")
            append_jsonl(kwargs["custody_root"] / "records" / "retrieval_records.jsonl",
                         (retrieval.to_record(),))
            return retrieval, None
        return _fake_acquire(**kwargs)

    planned = plan_acquisition(definition, _leads())
    acquisition = execute_acquisition(definition, planned, custody,
                                      acquire_function=blocked_aware)
    analysis = tmp_path / "analysis"
    metrics = analyze_campaign(custody, analysis, "D")
    return definition, custody, analysis, acquisition, metrics, tmp_path


def test_acquisition_counts_and_ledger(campaign_run):
    _definition_, custody, _analysis, acquisition, _metrics, _tmp = campaign_run
    assert acquisition["attempted"] == 5
    assert acquisition["captured"] == 4
    assert acquisition["blocked"] == 1
    assert acquisition["network_requests"] == 4
    ledger = (custody / "records" / "v5_1_acquisition_ledger.jsonl").read_text()
    assert len(ledger.splitlines()) == 5


def test_analysis_pipeline_end_to_end(campaign_run):
    _definition_, _custody, analysis, _acq, metrics, _tmp = campaign_run
    assert metrics["sources_analyzed"] == 4
    assert metrics["claims"] >= 3
    assert metrics["dependence_pairs"] == 6
    states = set(metrics["dependence_by_state"])
    assert states & {"DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                     "PARTIAL_DEPENDENCE", "INDEPENDENCE_UNKNOWN",
                     "NO_DEPENDENCE_FOUND"}
    claims = json.loads((analysis / "claim_register.json").read_text())
    modalities = {claim["modality"] for claim in claims}
    polarities = {claim["polarity"] for claim in claims}
    assert "NEGATIVE" in polarities, "dispute negation must be preserved"
    assert modalities & {"PLANNED", "REPORTED", "ATTRIBUTED"}, modalities


def test_mirror_pair_is_dependent_not_independent(campaign_run):
    _definition_, _custody, analysis, _acq, _metrics, _tmp = campaign_run
    rows = [json.loads(line) for line in
            (analysis / "dependence_register.jsonl").read_text().splitlines()]
    assert rows, "dependence register must not be empty"
    for row in rows:
        assert row["state"] != "INDEPENDENCE_SUPPORTED"


def test_report_build_and_registers(campaign_run):
    definition, _custody, analysis, _acq, _metrics, tmp = campaign_run
    out = tmp / "report"
    summary = build_reports(
        {"case_id": "case-D", "campaign_key": "D",
         "title": "Fictional transport campaign report",
         "research_question": definition["research_question"]},
        analysis, out)
    assert summary["published_propositions"] >= 1
    assert summary["report_validation_verdict"] == "PASS"
    for name in ("investigation_report.json", "investigation_report.md",
                 "investigation_report.txt", "proposition_evidence_ledger.jsonl",
                 "source_register.json", "claim_register.json",
                 "dependence_register.json", "contradiction_register.json",
                 "hypothesis_register.json", "refusal_register.json"):
        assert (out / name).exists(), name
    refusals = json.loads((out / "refusal_register.json").read_text())
    assert any(item["refusal_reason"] == "SUPPORT_STATE_BELOW_PUBLICATION_THRESHOLD"
               or item["refusal_reason"] == "FAITHFULNESS_VIOLATION"
               for item in refusals) or refusals == []


def test_analysis_is_deterministic(campaign_run):
    _definition_, custody, _analysis, _acq, metrics, tmp = campaign_run
    second = analyze_campaign(custody, tmp / "analysis2", "D")
    assert second["integrity_hash"] == metrics["integrity_hash"]
