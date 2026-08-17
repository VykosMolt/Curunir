"""V6.7 §3 resource-governance / limit-truthfulness locks at the connector edge.

The load-bearing invariant: a resource limit, a source-side error/throttle, or a
hostile body must never be recorded as an empty result set (which downstream
becomes evidence of absence), and a hostile body must never expand unbounded or
abort the whole acquisition pass. A GENUINE empty result set must still classify
EMPTY (that truthful "we searched and found nothing" legitimately feeds absence).
"""
from __future__ import annotations

import json

import pytest

from curunir_fabric.connectors import (ConnectorRequest, EdgarFullTextConnector,
                                       GleifConnector, RssFeedConnector, WaybackConnector)

pytestmark = pytest.mark.no_db


def transport(body: bytes, *, status: int = 200, error: str | None = None,
              truncated: bool = False):
    def fake(*, url, request_headers, timeout_seconds, maximum_bytes):
        return {"body": body, "status": status, "final_url": url,
                "headers": {"content-type": "application/json"}, "redirects": (),
                "error": error, "truncated": truncated}
    return fake


# ---- §7.2 a source-side error/throttle 200 is FAILED, not EMPTY -------------

def test_gleif_error_envelope_200_is_failed_not_empty():
    body = json.dumps({"errors": [{"status": "429", "title": "Too Many Requests"}]}).encode()
    r = GleifConnector().execute(ConnectorRequest(operation="SEARCH", value="x"),
                                 transport=transport(body))
    assert r.status == "FAILED"           # NOT "EMPTY" — a throttle is not absence
    assert "error" in r.error_detail.lower()


def test_gleif_genuine_empty_is_still_empty():
    body = json.dumps({"data": []}).encode()   # valid JSON:API, zero matches
    r = GleifConnector().execute(ConnectorRequest(operation="SEARCH", value="x"),
                                 transport=transport(body))
    assert r.status == "EMPTY"            # a real empty result set stays EMPTY


def test_edgar_container_less_200_is_failed_not_empty():
    r = EdgarFullTextConnector().execute(ConnectorRequest(operation="SEARCH", value="x"),
                                         transport=transport(b'{"error":"rate limited"}'))
    assert r.status == "FAILED"


def test_edgar_genuine_empty_is_still_empty():
    body = json.dumps({"hits": {"hits": [], "total": {"value": 0}}}).encode()
    r = EdgarFullTextConnector().execute(ConnectorRequest(operation="SEARCH", value="x"),
                                         transport=transport(body))
    assert r.status == "EMPTY"


def test_wayback_empty_body_200_is_failed_not_empty():
    r = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_ENUMERATE", value="http://x.example"),
        transport=transport(b""))
    assert r.status == "FAILED"           # an empty 200 body is a source failure


def test_wayback_header_only_is_genuine_empty():
    body = json.dumps([["timestamp", "original", "digest", "mimetype", "statuscode"]]).encode()
    r = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_ENUMERATE", value="http://x.example"),
        transport=transport(body))
    assert r.status == "EMPTY"            # a header-only CDX reply is a real empty


def test_wayback_malformed_row_is_skipped_not_crashing():
    # one good row, one short row, one bad-timestamp row: the good one survives,
    # the enumeration does not crash (which would abort the whole pass)
    body = json.dumps([
        ["timestamp", "original", "digest", "mimetype", "statuscode"],
        ["20200101000000", "http://x.example/a", "D1", "text/html", "200"],
        ["tooshort"],
        ["notadate", "http://x.example/b", "D2", "text/html", "200"],
    ]).encode()
    r = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_ENUMERATE", value="http://x.example"),
        transport=transport(body))
    assert r.status == "OK"
    assert len(r.results) == 1            # only the well-formed capture


# ---- §7.4 XML entity-expansion (billion laughs) is refused, not expanded ----

_BILLION_LAUGHS = (
    b'<?xml version="1.0"?>\n'
    b'<!DOCTYPE lolz [\n'
    b' <!ENTITY lol "lol">\n'
    b' <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
    b' <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
    b']>\n<rss><channel><item><title>&lol3;</title></item></channel></rss>'
)


def test_rss_billion_laughs_is_refused_before_parse():
    r = RssFeedConnector().execute(ConnectorRequest(operation="FETCH", value="http://x/feed"),
                                   transport=transport(_BILLION_LAUGHS))
    assert r.status == "FAILED"
    assert r.error_class == "PARSE"
    assert "doctype" in r.error_detail.lower() or "entity" in r.error_detail.lower()


def test_rss_doctype_after_large_comment_is_still_refused():
    # a giant comment preamble must not push the DOCTYPE past the scan window
    body = b'<?xml version="1.0"?>\n<!-- ' + b"A" * 200_000 + b' -->\n' + _BILLION_LAUGHS
    r = RssFeedConnector().execute(ConnectorRequest(operation="FETCH", value="http://x/feed"),
                                   transport=transport(body))
    assert r.status == "FAILED"
    assert r.error_class == "PARSE"


def test_rss_utf16_billion_laughs_is_refused():
    # a byte-level scan misses a DOCTYPE in UTF-16; expat decodes first and still
    # refuses it before any expansion (review finding F3).
    big = "X" * 100_000
    xml = ('<?xml version="1.0" encoding="UTF-16"?>\n'
           '<!DOCTYPE r [<!ENTITY a "%s"><!ENTITY b "%s">]>\n'
           '<rss><channel><item><title>&b;</title></item></channel></rss>\n' % (big, "&a;" * 90))
    body = b'\xff\xfe' + xml.encode("utf-16-le")
    r = RssFeedConnector().execute(ConnectorRequest(operation="FETCH", value="http://x/feed"),
                                   transport=transport(body))
    assert r.status == "FAILED"
    assert r.error_class == "PARSE"


def test_rss_cdata_quoting_a_doctype_is_not_a_false_positive():
    # a legitimate feed whose content quotes <!DOCTYPE in CDATA must still parse
    # (review finding F6 — expat only flags a REAL declaration, not text).
    body = (b'<?xml version="1.0"?><rss><channel>'
            b'<item><title>XXE writeup</title><link>http://x/1</link><guid>g1</guid>'
            b'<description><![CDATA[example: <!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>]]></description>'
            b'</item></channel></rss>')
    r = RssFeedConnector().execute(ConnectorRequest(operation="FETCH", value="http://x/feed"),
                                   transport=transport(body))
    assert r.status == "OK"
    assert r.results[0].title == "XXE writeup"


def test_rss_benign_feed_still_parses():
    body = (b'<?xml version="1.0"?><rss><channel>'
            b'<item><title>Hello</title><link>http://x/1</link>'
            b'<guid>http://x/1</guid></item></channel></rss>')
    r = RssFeedConnector().execute(ConnectorRequest(operation="FETCH", value="http://x/feed"),
                                   transport=transport(body))
    assert r.status == "OK"
    assert r.results[0].title == "Hello"


# ---- §2a a single response cannot drive unbounded pivot appends -------------

def test_pivot_fan_out_is_bounded(tmp_path):
    from curunir_fabric.connectors.base import NativeResult
    from curunir_fabric.executor import ExecutionResult
    from curunir_fabric.pivots import MAX_PIVOTS_PER_RESPONSE, propose_pivots
    from curunir_fabric.store import FabricStore
    from curunir_operational.access import Marking

    store = FabricStore.create(tmp_path / "store", "pivot-bound", "2026-08-17T12:00:00+00:00")
    # one response carrying source-controlled cardinality: 5000 distinct ids
    result = NativeResult(native_id="Q1",
                          identifiers=tuple((f"S{i}", f"v{i}") for i in range(5000)))

    class _Manif:
        manifestation_id = "m-1"

    outcome = ExecutionResult(execution=None, response=None, results=(result,),
                              manifestations=(_Manif(),))
    pivots = propose_pivots(store, outcome, subject="Acme",
                            now="2026-08-17T12:00:00+00:00", actor="t",
                            marking=Marking(owning_authority="t", releasability=("PUBLIC",)))
    assert len(pivots) <= MAX_PIVOTS_PER_RESPONSE
    assert len(store.records_of("fabric_pivot")) <= MAX_PIVOTS_PER_RESPONSE
