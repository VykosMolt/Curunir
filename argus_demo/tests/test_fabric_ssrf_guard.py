"""V6.7 exploit lock — the acquisition transport must not be usable for SSRF.

The public-web transport follows redirects; without an egress guard a watched
or operator-supplied URL that resolves to (or redirects to) the cloud metadata
address, loopback, or an RFC1918 host would be fetched and its bytes ingested as
evidence. These locks pin the tracked guard: forbidden targets are refused
before the request, and bytes reached through a forbidden redirect hop are never
ingested.

Hermetic: only literal IPs are used, so no test performs DNS/network I/O.
"""
from __future__ import annotations

import pytest

from curunir_fabric.connectors.base import ConnectorRequest
from curunir_fabric.connectors.web_page import WebPageConnector
from curunir_fabric.net_guard import (UnsafeUrlError, assert_url_safe,
                                       guarded_transport)

pytestmark = pytest.mark.no_db

FORBIDDEN = [
    "http://127.0.0.1/",              # loopback
    "https://127.0.0.1:8443/admin",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
    "http://[fd00:ec2::254]/latest/meta-data/",   # IPv6 metadata (ULA/private)
    "http://10.0.0.5/",              # RFC1918
    "http://192.168.1.1/",
    "http://172.16.0.1/",
    "http://[::1]/",                 # IPv6 loopback
    "http://0.0.0.0/",               # unspecified
    "http://[::ffff:127.0.0.1]/",    # IPv4-mapped loopback
    "file:///etc/passwd",            # non-http scheme
    "ftp://10.0.0.1/x",
    "gopher://127.0.0.1/",
    "http:///nohost",                # no host
]

PUBLIC_OK = [
    "http://8.8.8.8/",
    "https://1.1.1.1/resource",
    "http://93.184.216.34/",         # public literal (example.com range)
]


@pytest.mark.parametrize("url", FORBIDDEN)
def test_assert_url_safe_refuses_forbidden(url):
    with pytest.raises(UnsafeUrlError):
        assert_url_safe(url)


@pytest.mark.parametrize("url", PUBLIC_OK)
def test_assert_url_safe_allows_public_literals(url):
    assert_url_safe(url)  # must not raise


def _inner(*, final_url, redirects=(), body=b"INTERNAL-METADATA-SECRET"):
    calls = {"n": 0}

    def inner(*, url, request_headers, timeout_seconds, maximum_bytes):
        calls["n"] += 1
        return {"status": 200, "final_url": final_url, "redirects": tuple(redirects),
                "body": body, "truncated": False, "headers": {}}

    inner.calls = calls
    return inner


def test_guard_blocks_initial_target_before_request():
    inner = _inner(final_url="http://127.0.0.1/")
    out = guarded_transport(inner)(
        url="http://127.0.0.1/", request_headers={}, timeout_seconds=1, maximum_bytes=10)
    assert out["status"] is None and "SSRF_BLOCKED" in out["error"]
    assert out["body"] == b""
    assert inner.calls["n"] == 0, "the forbidden target must not be requested at all"


def test_guard_blocks_redirect_to_metadata_and_drops_body():
    # a public initial URL that (per the transport) redirected to metadata
    inner = _inner(final_url="http://169.254.169.254/latest/meta-data/",
                   redirects=["http://169.254.169.254/latest/meta-data/"])
    out = guarded_transport(inner)(
        url="http://93.184.216.34/", request_headers={}, timeout_seconds=1, maximum_bytes=10)
    assert "SSRF_BLOCKED_REDIRECT" in out["error"]
    assert out["body"] == b"", "metadata bytes must never be ingested"


def test_guard_passes_clean_public_fetch_through():
    inner = _inner(final_url="http://93.184.216.34/final",
                   redirects=["http://8.8.8.8/hop"], body=b"public-bytes")
    out = guarded_transport(inner)(
        url="http://93.184.216.34/", request_headers={}, timeout_seconds=1, maximum_bytes=10)
    assert out.get("error") is None
    assert out["body"] == b"public-bytes"
    assert inner.calls["n"] == 1


def test_web_page_connector_does_not_ingest_ssrf_redirect():
    """End to end through a real connector: a page that redirects to the
    metadata service yields a FAILED response with no ingested body."""
    connector = WebPageConnector()
    transport = guarded_transport(_inner(
        final_url="http://169.254.169.254/latest/meta-data/iam/",
        redirects=["http://169.254.169.254/latest/meta-data/iam/"],
        body=b"AWS_SECRET_ACCESS_KEY=leaked"))
    response = connector.execute(
        ConnectorRequest(operation="FETCH", value="http://93.184.216.34/page"),
        transport=transport)
    assert response.status == "FAILED"
    assert response.raw_body == b""
    assert b"leaked" not in response.raw_body
