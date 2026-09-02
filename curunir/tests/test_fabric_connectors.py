"""Connectors, offline: parsing, pagination and how failures are classified.

The fixture bytes copy the shape of the real endpoints and a fake transport
stands in for the network. The live test against real endpoints is separate.
"""
from __future__ import annotations

import json

import pytest

from curunir_fabric.connectors import (BUILTIN_CONNECTORS, ConnectorRequest, EdgarFullTextConnector,
                                       GleifConnector, RssFeedConnector, WaybackConnector,
                                       WebPageConnector, WikidataConnector)

pytestmark = pytest.mark.no_db


def transport_returning(body: bytes, *, status: int = 200, error: str | None = None,
                        headers: dict | None = None, capture: list | None = None):
    def fake_transport(*, url, request_headers, timeout_seconds, maximum_bytes):
        if capture is not None:
            capture.append({"url": url, "headers": dict(request_headers)})
        return {"body": body, "status": status, "final_url": url,
                "headers": headers or {"content-type": "application/json"},
                "redirects": (), "error": error, "truncated": False}
    return fake_transport


def test_every_builtin_connector_declares_operations_and_identity():
    for connector_id, connector in BUILTIN_CONNECTORS.items():
        assert connector.connector_id == connector_id
        assert connector.operations, connector_id
        assert connector.connector_version, connector_id


def test_unsupported_operation_is_reported_not_guessed():
    response = WikidataConnector().execute(
        ConnectorRequest(operation="HISTORICAL_FETCH", value="Q1"),
        transport=transport_returning(b"{}"))
    assert response.status == "NOT_SUPPORTED"
    assert "does not support" in response.error_detail


def test_wikidata_search_parses_candidates_and_sends_user_agent():
    body = json.dumps({"search": [
        {"id": "Q81230", "label": "Siemens", "description": "conglomerate",
         "match": {"language": "en", "text": "Siemens"},
         "concepturi": "http://www.wikidata.org/entity/Q81230"},
    ], "search-continue": 7}).encode()
    seen: list = []
    response = WikidataConnector().execute(
        ConnectorRequest(operation="SEARCH", value="Siemens", language="en"),
        transport=transport_returning(body, capture=seen))
    assert response.status == "OK"
    assert response.results[0].native_id == "Q81230"
    assert ("WIKIDATA_QID", "Q81230") in response.results[0].identifiers
    assert response.next_cursor == "7"
    assert "User-Agent" in seen[0]["headers"]


def test_wikidata_lookup_extracts_identifiers_labels_and_aliases():
    body = json.dumps({"entities": {"Q81230": {
        "labels": {"en": {"language": "en", "value": "Siemens"},
                   "ru": {"language": "ru", "value": "Сименс"}},
        "aliases": {"de": [{"language": "de", "value": "Siemens AG"}]},
        "descriptions": {"en": {"language": "en", "value": "German conglomerate"}},
        "claims": {
            "P1278": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                    "datavalue": {"value": "W38RGCVGV5CYQMO4W183", "type": "string"}}}],
            "P856": [{"mainsnak": {"snaktype": "value", "datatype": "url",
                                   "datavalue": {"value": "https://www.siemens.com/", "type": "string"}}}],
            "P9999": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                    "datavalue": {"value": "xyz", "type": "string"}}}],
        },
    }}}).encode()
    response = WikidataConnector().execute(
        ConnectorRequest(operation="LOOKUP", value="Q81230"),
        transport=transport_returning(body))
    assert response.status == "OK"
    identifiers = dict(response.results[0].identifiers)
    assert identifiers["LEI"] == "W38RGCVGV5CYQMO4W183"
    assert identifiers["OFFICIAL_WEBSITE"] == "https://www.siemens.com/"
    assert identifiers["wikidata:P9999"] == "xyz"
    attributes = dict(response.results[0].attributes)
    assert attributes["label:ru"] == "Сименс"
    assert attributes["alias:de"] == "Siemens AG"


def test_wikidata_missing_entity_is_empty_not_failure():
    body = json.dumps({"entities": {"Q999999999": {"id": "Q999999999", "missing": ""}}}).encode()
    response = WikidataConnector().execute(
        ConnectorRequest(operation="LOOKUP", value="Q999999999"),
        transport=transport_returning(body))
    assert response.status == "EMPTY"
    assert response.results == ()


def test_gleif_search_pagination_and_record_shape():
    body = json.dumps({
        "meta": {"pagination": {"currentPage": 1, "lastPage": 3}},
        "data": [{"id": "5493001KJTIIGC8Y1R12", "attributes": {
            "lei": "5493001KJTIIGC8Y1R12",
            "entity": {"legalName": {"name": "Siemens AG"}, "jurisdiction": "DE",
                       "status": "ACTIVE",
                       "otherNames": [{"name": "Siemens Aktiengesellschaft"}],
                       "legalAddress": {"city": "München", "country": "DE"},
                       "successorEntity": {"lei": "NEWLEI123"}},
            "registration": {"status": "ISSUED", "initialRegistrationDate": "2012-06-06",
                             "lastUpdateDate": "2026-01-01T00:00:00+00:00"}}}],
    }).encode()
    response = GleifConnector().execute(
        ConnectorRequest(operation="SEARCH", value="Siemens"),
        transport=transport_returning(body))
    assert response.status == "OK"
    result = response.results[0]
    assert result.native_id == "5493001KJTIIGC8Y1R12"
    assert dict(result.identifiers)["LEI"] == "5493001KJTIIGC8Y1R12"
    attributes = dict(result.attributes)
    assert attributes["successor_lei"] == "NEWLEI123"
    assert response.next_cursor == "2"


def test_edgar_search_builds_archive_urls_and_cursor():
    body = json.dumps({"hits": {"total": {"value": 12}, "hits": [
        {"_id": "0000320193-24-000006:aapl-20231230.htm",
         "_source": {"adsh": "0000320193-24-000006", "ciks": ["0000320193"],
                     "display_names": ["Apple Inc.  (AAPL)  (CIK 0000320193)"],
                     "file_type": "10-Q", "file_date": "2024-02-02"}},
    ]}}).encode()
    response = EdgarFullTextConnector().execute(
        ConnectorRequest(operation="SEARCH", value="Apple", limit=10),
        transport=transport_returning(body))
    assert response.status == "OK"
    result = response.results[0]
    assert result.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000006/aapl-20231230.htm"
    assert result.source_time == "2024-02-02T00:00:00+00:00"
    assert response.next_cursor == "1"


def test_edgar_fetch_refuses_non_sec_urls():
    response = EdgarFullTextConnector().execute(
        ConnectorRequest(operation="FETCH", value="https://evil.example/doc.htm"),
        transport=transport_returning(b"x"))
    assert response.status == "FAILED"
    assert response.error_class == "PARSE"


def test_wayback_cdx_parses_captures_and_resume_key():
    rows = [["timestamp", "original", "digest", "mimetype", "statuscode"],
            ["20240301120000", "https://example.com/", "DIGEST1", "text/html", "200"],
            ["20250101120000", "https://example.com/", "DIGEST2", "text/html", "200"],
            [],
            ["resume-key-abc"]]
    response = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_ENUMERATE", value="https://example.com/",
                         time_bounds=("2024-01-01T00:00:00+00:00", None)),
        transport=transport_returning(json.dumps(rows).encode()))
    assert response.status == "OK"
    assert len(response.results) == 2
    first = response.results[0]
    assert first.native_id == "20240301120000/https://example.com/"
    assert first.source_time == "2024-03-01T12:00:00+00:00"
    assert first.url.endswith("20240301120000id_/https://example.com/")
    assert response.next_cursor == "resume-key-abc"


def test_wayback_fetch_validates_capture_reference():
    response = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_FETCH", value="not-a-capture"),
        transport=transport_returning(b"x"))
    assert response.status == "FAILED"
    assert response.error_class == "PARSE"
    good = WaybackConnector().execute(
        ConnectorRequest(operation="HISTORICAL_FETCH", value="20240301120000/https://example.com/"),
        transport=transport_returning(b"<html>archived</html>",
                                      headers={"content-type": "text/html"}))
    assert good.status == "OK"
    assert good.results[0].source_time == "2024-03-01T12:00:00+00:00"


def test_rss_and_atom_parse_with_stable_identity():
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
    <item><title>One</title><link>https://x/1</link><guid>guid-1</guid>
    <pubDate>Fri, 14 Aug 2026 10:00:00 GMT</pubDate><description>d1</description></item>
    </channel></rss>"""
    response = RssFeedConnector().execute(
        ConnectorRequest(operation="POLL", value="https://x/feed"),
        transport=transport_returning(rss, headers={"content-type": "application/rss+xml"}))
    assert response.status == "OK"
    assert response.results[0].native_id == "guid-1"
    assert response.results[0].source_time == "2026-08-14T10:00:00+00:00"

    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><id>tag:x,2026:e1</id><title>E1</title><link href="https://x/e1"/>
    <updated>2026-08-14T10:00:00Z</updated></entry></feed>"""
    response = RssFeedConnector().execute(
        ConnectorRequest(operation="FETCH", value="https://x/feed.atom"),
        transport=transport_returning(atom, headers={"content-type": "application/atom+xml"}))
    assert response.status == "OK"
    assert response.results[0].native_id == "tag:x,2026:e1"


def test_rss_malformed_body_is_a_parse_failure():
    response = RssFeedConnector().execute(
        ConnectorRequest(operation="POLL", value="https://x/feed"),
        transport=transport_returning(b"<html>not a feed</html>"))
    assert response.status == "FAILED"
    assert response.error_class == "PARSE"


def test_failure_classification_transport_http_and_restriction():
    connector = WebPageConnector()
    transport_down = transport_returning(b"", status=None, error="TimeoutError:slow")
    down = connector.execute(ConnectorRequest(operation="FETCH", value="https://x/"),
                             transport=lambda **kw: {"body": b"", "status": None, "final_url": None,
                                                     "headers": {}, "redirects": (),
                                                     "error": "TimeoutError:slow", "truncated": False})
    assert down.status == "FAILED"
    assert down.error_class == "TRANSPORT"
    forbidden = connector.execute(ConnectorRequest(operation="FETCH", value="https://x/"),
                                  transport=transport_returning(b"denied", status=403, error="HTTP_403"))
    assert forbidden.status == "ACCESS_RESTRICTED"
    missing = connector.execute(ConnectorRequest(operation="FETCH", value="https://x/"),
                                transport=transport_returning(b"nope", status=404, error="HTTP_404"))
    assert missing.status == "FAILED"


def test_web_page_fetch_preserves_transport_facts():
    def transport(**kw):
        return {"body": b"<html>page</html>", "status": 200, "final_url": "https://x/final",
                "headers": {"content-type": "text/html", "etag": "W/\"abc\"",
                            "last-modified": "Fri, 14 Aug 2026 10:00:00 GMT"},
                "redirects": ("https://x/final",), "error": None, "truncated": False}
    response = WebPageConnector().execute(
        ConnectorRequest(operation="FETCH", value="https://x/"), transport=transport)
    assert response.status == "OK"
    assert response.final_url == "https://x/final"
    assert response.etag == 'W/"abc"'
    assert response.redirects == ("https://x/final",)
    assert response.body_sha256() == __import__("hashlib").sha256(b"<html>page</html>").hexdigest()
