"""Historical V6.7 egress/resource exploits, grouped by invariant class."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_fabric.connectors import (ConnectorRequest, EdgarFullTextConnector,
                                       GleifConnector, RssFeedConnector,
                                       WaybackConnector, WikidataConnector)
from curunir_fabric.contracts import ManifestationRecord
from curunir_fabric.transport import (SafePublicTransport, UnsafeUrlError,
                                      _request_once, assert_url_safe)

from analytic_support import make_analytic
from semantic_support import MARK, T0, plant_manifestation
from workbench_support import RESTRICTED_MARK

pytestmark = pytest.mark.no_db


def _gleif(lei: str) -> bytes:
    return (
        '{"data":{"id":"%s","attributes":{"lei":"%s","entity":'
        '{"legalName":{"name":"Entity"},"jurisdiction":"NO","status":"ACTIVE",'
        '"otherNames":[],"legalAddress":{"city":"Oslo","country":"NO"}},'
        '"registration":{"status":"ISSUED","initialRegistrationDate":"2014-03-02",'
        '"lastUpdateDate":"2026-08-01"}}}}' % (lei, lei)).encode()


def _plant_restricted(pipeline, lei: str) -> None:
    body = _gleif(lei)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = f"lei/{lei}"
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest), source_id="gleif",
        connector_id="gleif-test", connector_version="1", native_id=native,
        request_url=native, final_url=native, content_sha256=digest,
        content_store_path=str(path), media_type="application/json",
        temporal_status="LIVE", source_time=None, archive_capture_time=None,
        retrieval_time=T0, http_status=200, redirects=(), etag="", last_modified="",
        truncated=False, retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native), prior_manifestation_id=None,
        marking=RESTRICTED_MARK), recorded_time=pipeline.now_fn(), actor="test")


def _claim(store, lei: str) -> dict:
    return next(record for record in store.current_claims().values()
                if record["subject_ref"] == f"LEI:{lei}"
                and record["predicate"] == "entity_status")


def test_provider_gate_is_before_egress_and_uses_authoritative_marking(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/PUBLIC00000000000001",
                        body=_gleif("PUBLIC00000000000001"),
                        media_type="application/json", retrieval_time=T0)
    _plant_restricted(pipeline, "SECRET00000000000001")
    pipeline.process_new_evidence()
    public = _claim(ctx.store, "PUBLIC00000000000001")
    secret = _claim(ctx.store, "SECRET00000000000001")
    calls: list[dict] = []
    assist = AnalyticalAssist(
        package=analytical_assist_package("external", "model", "1"),
        infer_fn=lambda task, inputs: calls.append(dict(inputs)) or {
            "title": "candidate", "supporting_claim_ids": [public["claim_id"]]},
        allowed_input_marking=MARK)

    refused = assist.propose(
        ctx, task="theme", target_kind="analytic_theme", inputs={"x": "secret"},
        input_refs=(secret["claim_id"],))
    assert refused["status"] == "EGRESS_REFUSED"
    assert calls == []

    allowed = assist.propose(
        ctx, task="theme", target_kind="analytic_theme", inputs={"x": "public"},
        input_refs=(public["claim_id"],))
    assert allowed["status"] == "PROPOSED"
    assert len(calls) == 1


def test_provider_refuses_unresolved_reference_before_call(tmp_path):
    _, ctx = make_analytic(tmp_path)
    calls: list[dict] = []
    assist = AnalyticalAssist(
        package=analytical_assist_package("external", "model", "1"),
        infer_fn=lambda task, inputs: calls.append(dict(inputs)) or {},
        allowed_input_marking=MARK)
    result = assist.propose(ctx, task="t", target_kind="analytic_theme", inputs={},
                            input_refs=("missing-record",))
    assert result["status"] == "EGRESS_REFUSED"
    assert result["missing_input_refs"] == ["missing-record"]
    assert calls == []


def test_provider_cargo_record_id_cannot_bypass_omitted_declaration(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _plant_restricted(pipeline, "SECRET00000000000002")
    pipeline.process_new_evidence()
    secret = _claim(ctx.store, "SECRET00000000000002")
    calls: list[dict] = []
    assist = AnalyticalAssist(
        package=analytical_assist_package("external", "model", "1"),
        infer_fn=lambda task, inputs: calls.append(dict(inputs)) or {},
        allowed_input_marking=MARK)
    result = assist.propose(
        ctx, task="t", target_kind="analytic_theme",
        inputs={"claims": [secret["claim_id"]]}, input_refs=())
    assert result["status"] == "EGRESS_REFUSED"
    assert calls == []


def test_ungoverned_provider_does_not_treat_partner_release_as_public():
    from curunir_operational.access import Marking

    assist = AnalyticalAssist(
        package=analytical_assist_package("external", "model", "1"),
        infer_fn=lambda task, inputs: {})
    assert assist._egress_refusal(
        Marking("ORG", releasability=("MISSION_PARTNERS",))) is not None
    assert assist._egress_refusal(
        Marking("ORG", releasability=("PUBLIC",))) is None


def test_malformed_provider_output_is_audited_but_not_admitted(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/PUBLIC00000000000002",
                        body=_gleif("PUBLIC00000000000002"),
                        media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    claim = _claim(ctx.store, "PUBLIC00000000000002")
    assist = AnalyticalAssist(
        package=analytical_assist_package("external", "bad-model", "1"),
        infer_fn=lambda task, inputs: {"title": "bad\ud800", "scores": {float("nan")}},
        allowed_input_marking=MARK)
    result = assist.propose(ctx, task="t", target_kind="analytic_theme", inputs={},
                            input_refs=(claim["claim_id"],))
    assert result["status"] == "PROVIDER_ERROR"
    inference = ctx.store.records_of("inference")[-1]
    assert inference["validation"] == "INVALID" and inference["output"] == {}


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://2130706433/", "http://0177.0.0.1/", "http://0x7f000001/",
    "http://[64:ff9b::7f00:1]/", "http://[2002:7f00:1::]/",
    "http://user@127.0.0.1/",
    "file:///etc/passwd", "http:///missing-host",
])
def test_public_transport_refuses_non_public_targets(url):
    with pytest.raises(UnsafeUrlError):
        assert_url_safe(url)


def test_redirect_target_is_refused_before_its_request():
    requests: list[tuple[str, tuple[str, ...]]] = []

    def resolver(host: str, port: int) -> tuple[str, ...]:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return ("93.184.216.34",)
        if not address.is_global:
            raise UnsafeUrlError("non-public")
        return (str(address),)

    def request_once(**request):
        requests.append((request["host"], request["addresses"]))
        return 302, {"location": "http://127.0.0.1/internal"}, b"", False

    result = SafePublicTransport(resolver=resolver, request_once=request_once)(
        url="https://public.example/start", request_headers={},
        timeout_seconds=2, maximum_bytes=100)
    assert "SSRF_BLOCKED_REDIRECT" in result["error"]
    assert requests == [("public.example", ("93.184.216.34",))]


def test_connection_receives_only_the_prevalidated_address():
    resolutions: list[str] = []
    requests: list[tuple[str, ...]] = []

    def resolver(host: str, port: int) -> tuple[str, ...]:
        resolutions.append(host)
        return ("93.184.216.34",)

    def request_once(**request):
        requests.append(request["addresses"])
        return 200, {"content-type": "text/plain"}, b"public", False

    result = SafePublicTransport(resolver=resolver, request_once=request_once)(
        url="https://public.example/x", request_headers={},
        timeout_seconds=2, maximum_bytes=100)
    assert result["body"] == b"public"
    assert resolutions == ["public.example"]
    assert requests == [("93.184.216.34",)]


def test_address_failover_consumes_one_shared_deadline(monkeypatch):
    timeouts: list[float] = []

    class Response:
        status = 200

        @staticmethod
        def getheaders():
            return ()

        @staticmethod
        def read(_size):
            return b"ok"

    class Connection:
        sock = None

        def __init__(self, _host, address, _port, timeout):
            self.address = address
            timeouts.append(timeout)

        def request(self, *_args, **_kwargs):
            if self.address == "93.184.216.34":
                time.sleep(0.02)
                raise OSError("first address failed")

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr("curunir_fabric.transport._PinnedHTTPConnection", Connection)
    status, _, body, _ = _request_once(
        scheme="http", host="public.example", port=80, target="/",
        addresses=("93.184.216.34", "93.184.216.35"), request_headers={},
        timeout_seconds=0.2, maximum_bytes=8)
    assert status == 200 and body == b"ok"
    assert len(timeouts) == 2
    assert timeouts[1] < timeouts[0] - 0.01


def test_transport_refuses_a_late_success_past_the_total_deadline():
    def request_once(**_request):
        time.sleep(0.02)
        return 200, {}, b"late", False

    result = SafePublicTransport(
        resolver=lambda _host, _port: ("93.184.216.34",),
        request_once=request_once,
    )(
        url="https://public.example/", request_headers={},
        timeout_seconds=0.005, maximum_bytes=8)
    assert result["body"] == b""
    assert result["error"] == "TimeoutError: total transport deadline exceeded"


def _transport(body: bytes, *, status: int = 200):
    def transport(**request):
        return {"body": body, "status": status, "final_url": request["url"],
                "headers": {"content-type": "application/json"}, "redirects": (),
                "error": None, "truncated": False}
    return transport


def test_source_error_envelopes_are_failures_not_absence():
    gleif = GleifConnector().execute(
        ConnectorRequest(operation="SEARCH", value="x"),
        transport=_transport(b'{"errors":[{"status":"429"}]}'))
    edgar = EdgarFullTextConnector().execute(
        ConnectorRequest(operation="SEARCH", value="x"),
        transport=_transport(b'{"error":"rate limited"}'))
    wayback = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_ENUMERATE", value="http://x.example"),
        transport=_transport(b""))
    assert {gleif.status, edgar.status, wayback.status} == {"FAILED"}


def test_genuine_empty_source_shapes_remain_empty():
    gleif = GleifConnector().execute(
        ConnectorRequest(operation="SEARCH", value="x"),
        transport=_transport(b'{"data":[]}'))
    edgar = EdgarFullTextConnector().execute(
        ConnectorRequest(operation="SEARCH", value="x"),
        transport=_transport(b'{"hits":{"hits":[],"total":{"value":0}}}'))
    assert gleif.status == edgar.status == "EMPTY"


def test_rss_dtd_is_refused_without_false_positive_on_cdata():
    hostile = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "boom">]>'
               b'<rss><channel><item><guid>1</guid><title>&a;</title></item>'
               b'</channel></rss>')
    refused = RssFeedConnector().execute(
        ConnectorRequest(operation="FETCH", value="http://public.example/feed"),
        transport=_transport(hostile))
    benign = (b'<?xml version="1.0"?><rss><channel><item><guid>1</guid>'
              b'<title><![CDATA[example <!DOCTYPE x>]]></title></item></channel></rss>')
    accepted = RssFeedConnector().execute(
        ConnectorRequest(operation="FETCH", value="http://public.example/feed"),
        transport=_transport(benign))
    assert refused.status == "FAILED" and refused.error_class == "PARSE"
    assert accepted.status == "OK"


def test_source_surrogates_are_scrubbed_before_storage():
    from curunir_operational.canonical import canonical_line
    body = json.dumps({"search": [
        {"id": "Q1", "label": "A", "match": {}},
        {"id": "Q\ud800", "label": "B", "match": {}},
    ]}).encode()
    response = WikidataConnector().execute(
        ConnectorRequest(operation="SEARCH", value="x"), transport=_transport(body))
    assert response.status == "OK"
    for result in response.results:
        canonical_line({"id": result.native_id, "title": result.title})
