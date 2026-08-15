from __future__ import annotations

import io
import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from curunir_operational.v4.custody import (
    acquire_public_source, create_translation, normalize_source,
)
from curunir_operational.v4.campaign import retry_captured_lead, run_live_capture
from curunir_operational.v4.investigation import DiscoveryBudget, decide_access, preregister_case
from curunir_operational.v4.io import read_jsonl

pytestmark = pytest.mark.no_db
STAMP = "2026-07-22T09:00:00+00:00"


def case():
    return preregister_case(
        case_id="CASE-CUSTODY", title="Custody case",
        research_question="What do public institutional sources state about this bounded programme and its limitations?",
        purpose="test", scope=("programme",), exclusions=("private data",), geographic_scope=("EU",),
        temporal_scope=("2024-01-01T00:00:00+00:00", STAMP), languages=("en", "fr"),
        entities_of_interest=("PROGRAMME",), source_classes=("OFFICIAL",),
        evidence_requirements=("one acquired official source",),
        prohibited_inference_classes=("deployment without support",), discovery_budget=3, acquisition_budget=2,
        review_policy="HUMAN_REVIEW_REQUIRED", stop_rules=("budget",), owning_node="STRATEGIC_EVIDENCE_NODE",
        access_marking={"releasability": ["PUBLIC"]}, created_time=STAMP,
    )


def lead_and_decision():
    item = case(); budget = DiscoveryBudget(item)
    query = budget.issue(subquestion_id="SQ", formulation="official programme", language="en",
                         provider="FIXTURE", provider_category="PUBLIC_WEB", reason="coverage",
                         expected_source_class="OFFICIAL", execution_time=STAMP)
    lead = budget.admit_results(query, ({"url": "https://authority.example/programme", "title": "Programme"},))[0]
    return item, lead, decide_access(item, lead, decided_time=STAMP)


class Response:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8", "ETag": "v1"}

    def __init__(self, body: bytes): self.body = body
    def read(self, amount: int): return self.body[:amount]
    def geturl(self): return "https://authority.example/programme"
    def getcode(self): return self.status
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Opener:
    def __init__(self, body: bytes): self.body = body
    def open(self, request, timeout): return Response(self.body)


def transport_result(body: bytes, media_type: str = "text/html; charset=utf-8", **extra):
    return {"body": body, "status": 200, "final_url": "https://authority.example/programme",
            "headers": {"content-type": media_type, **extra}, "redirects": (),
            "error": None, "truncated": False}


def test_capture_before_parse_navigation_removed_and_exact_custody(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision()
    body = b"<html><head><title>Title</title></head><body><nav>Menu poison</nav><main><h1>Programme</h1><p>Official statement.</p></main><footer>Footer poison</footer></body></html>"
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4", lambda **kwargs: transport_result(body))
    retrieval, source = acquire_public_source(case_id=item.case_id, lead=lead, decision=decision,
                                               custody_root=tmp_path, publisher="Authority", source_class="OFFICIAL")
    assert source is not None and Path(source.content_path).read_bytes() == body
    events = read_jsonl(tmp_path / "records" / "retrieval_events.jsonl")
    assert events[0]["event"] == "RETRIEVAL_STARTED" and events[-1]["event"] == "RETRIEVAL_COMPLETED"
    document = normalize_source(source, retrieval)
    assert "Official statement" in document.text
    assert "Menu poison" not in document.text and "Footer poison" not in document.text
    assert document.mappings[0].precision == "APPROXIMATE_SECTION"


def test_duplicate_bytes_share_content_object_but_keep_retrievals(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision(); body = b"plain institutional record"
    response = Response(body); response.headers = {"Content-Type": "text/plain"}
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                        lambda **kwargs: transport_result(body, "text/plain"))
    one, source_one = acquire_public_source(case_id=item.case_id, lead=lead, decision=decision,
                                             custody_root=tmp_path, publisher="Authority", source_class="OFFICIAL")
    two, source_two = acquire_public_source(case_id=item.case_id, lead=lead, decision=decision,
                                             custody_root=tmp_path, publisher="Mirror", source_class="SECONDARY")
    assert one.retrieval_id != two.retrieval_id
    assert source_one and source_two and source_one.source_object_id == source_two.source_object_id
    assert source_one.content_path == source_two.content_path
    assert len(read_jsonl(tmp_path / "records" / "retrieval_records.jsonl")) == 2


def test_access_denial_is_preserved_without_network(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision()
    decision = replace(decision, state="DENY_CREDENTIAL_REQUIRED")
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network called")))
    record, source = acquire_public_source(case_id=item.case_id, lead=lead, decision=decision,
                                            custody_root=tmp_path, publisher="Authority", source_class="OFFICIAL")
    assert source is None and record.content_state == "BLOCKED"
    assert "DENY_CREDENTIAL_REQUIRED" in record.failure


def test_html_for_pdf_and_waf_are_quarantined(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision()
    pdf_lead = replace(lead, url="https://authority.example/file.pdf")
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                        lambda **kwargs: transport_result(b"<html>not a PDF</html>"))
    record, source = acquire_public_source(case_id=item.case_id, lead=pdf_lead, decision=decision,
                                            custody_root=tmp_path, publisher="Authority", source_class="OFFICIAL")
    assert source is None and record.content_state == "QUARANTINED" and "PDF" in record.failure


def test_json_xml_plain_normalization_and_immutable_hash(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision()
    bodies = ((b'{"programme":"public"}', "application/json"),
              (b"<root><item>public</item></root>", "application/xml"),
              (b"public text", "text/plain"))
    for index, (body, media) in enumerate(bodies):
        monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                            lambda body=body, media=media, **kwargs: transport_result(body, media))
        record, source = acquire_public_source(case_id=item.case_id, lead=replace(lead, url=f"https://authority.example/{index}"),
                                                decision=decision, custody_root=tmp_path, publisher="Authority",
                                                source_class="OFFICIAL")
        assert source and normalize_source(source, record).text


def test_translation_preserves_original_and_is_never_independent(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision(); body = b"Texte officiel"
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                        lambda **kwargs: transport_result(body, "text/plain; charset=utf-8"))
    retrieval, source = acquire_public_source(case_id=item.case_id, lead=replace(lead, language="fr"),
                                               decision=decision, custody_root=tmp_path,
                                               publisher="Authority", source_class="OFFICIAL")
    document = normalize_source(source, retrieval)
    derivative = create_translation(document, target_language="en", translated_text="Official text",
                                    provider="TEST", provider_version="1", alignment_precision="APPROXIMATE_SECTION")
    assert document.text == "Texte officiel" and derivative.independent_source is False
    with pytest.raises(ValueError, match="independent"):
        replace(derivative, independent_source=True)


def test_gzip_content_encoding_is_normalized_from_immutable_raw(monkeypatch, tmp_path):
    item, lead, decision = lead_and_decision(); compressed = gzip.compress(b"<html><body><main>Bundeswehr cloud statement.</main></body></html>")
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4",
                        lambda **kwargs: transport_result(compressed, "text/html; charset=utf-8", **{"content-encoding": "gzip"}))
    retrieval, source = acquire_public_source(case_id=item.case_id, lead=lead, decision=decision,
                                               custody_root=tmp_path, publisher="Authority", source_class="OFFICIAL")
    assert source and Path(source.content_path).read_bytes() == compressed
    document = normalize_source(source, retrieval)
    assert "Bundeswehr cloud statement" in document.text
    assert "CONTENT_ENCODING_GZIP_DECODED_FROM_IMMUTABLE_RAW_BYTES" in document.warnings


def test_bounded_retry_preserves_prior_failure_and_access_decision(monkeypatch, tmp_path):
    item = case(); body = b"bounded public report bytes"
    calls = {"count": 0}
    def bounded_transport(**kwargs):
        calls["count"] += 1
        limit = kwargs["maximum_bytes"]
        return {**transport_result(body[:limit] if len(body) > limit else body, "text/plain; charset=utf-8"),
                "truncated": len(body) > limit}
    monkeypatch.setattr("curunir_operational.v4.custody.transport_v4", bounded_transport)
    spec = {"maximum_bytes": 5, "queries": [{
        "subquestion_id": "SQ", "formulation": "official report", "language": "en",
        "provider": "OFFICIAL_FIXTURE", "provider_category": "OFFICIAL_SITE", "reason": "bounded",
        "expected_source_class": "OFFICIAL", "execution_time": STAMP,
        "results": [{"url": "https://authority.example/report", "title": "Report",
                     "publisher": "Authority", "source_class": "OFFICIAL"}]}],
        "stop_reason": "bounded fixture complete"}
    metrics = run_live_capture(case=item, discovery_spec=spec, output_root=tmp_path / "capture")
    assert metrics["quarantined"] == 1 and metrics["successful_retrievals"] == 0
    result = retry_captured_lead(case=item, capture_root=tmp_path / "capture",
                                 url="https://authority.example/report", maximum_bytes=100)
    records = read_jsonl(tmp_path / "capture" / "custody" / "records" / "retrieval_records.jsonl")
    assert result["content_state"] == "CAPTURED" and result["access_decision_unchanged"]
    assert len(records) == 2 and records[1]["retry_of"] == records[0]["retrieval_id"]
