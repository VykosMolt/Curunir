"""Egress safety guard for the public-web transport (SSRF boundary).

The acquisition transport fetches attacker-influenced URLs (a watched page, an
operator-supplied web/RSS target) and follows redirects. Without a guard, a
public URL that 302-redirects to ``http://169.254.169.254/`` (cloud metadata),
``http://127.0.0.1/`` or an RFC1918 host would be fetched and its bytes ingested
as evidence — a classic server-side request forgery reading internal state into
the mission.

This module is the tracked, dependency-free guard the shipped fabric uses:

  * ``assert_url_safe`` enforces an http/https scheme allowlist and resolves the
    host, refusing when ANY resolved address is loopback / private / link-local
    (which includes the 169.254.169.254 metadata address) / multicast /
    reserved / unspecified;
  * ``guarded_transport`` wraps the real transport: it checks the initial URL
    *before* the request, and re-checks every redirect hop and the final URL
    *after*, refusing to return (hence to ingest) any bytes fetched through a
    forbidden hop.

Bounded limitation, stated honestly: the underlying transport follows redirects
internally, so a redirect to a forbidden address is refused at ingestion but the
GET to it has already physically fired. Fully preventing the redirect request
would require intercepting each hop inside the transport itself (the protected
argus kernel), which this tranche does not modify. Blocking ingestion is the
load-bearing protection — internal state never enters the mission — and the
initial target is blocked before any request. There is also a check-then-connect
DNS window (a name could re-resolve to a different address between the guard's
lookup and the transport's connect); pinning would need transport-level control.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class UnsafeUrlError(Exception):
    """A URL whose scheme or resolved address is not allowed to be fetched."""


def _address_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """A globally-routable public address — everything an internet-facing
    fetch should reach and nothing behind the acquisition host. link_local
    covers 169.254.0.0/16 (the cloud metadata address 169.254.169.254) and
    IPv6 fe80::/10; private covers RFC1918 and IPv6 ULA (fc00::/7, so an IPv6
    metadata endpoint too)."""
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) must be judged on the mapped v4
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def assert_url_safe(url: str) -> None:
    """Raise UnsafeUrlError unless `url` is an http/https URL whose host
    resolves exclusively to public addresses. A host that does not resolve is
    refused (it cannot be safely fetched and an unresolvable target is never a
    legitimate public source)."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme not allowed: {scheme or '(none)'!r}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("url has no host")
    port = parts.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"host does not resolve: {host}") from error
    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeUrlError(f"host resolves to no address: {host}")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])  # strip zone id
        except ValueError as error:
            raise UnsafeUrlError(f"unparseable address {address!r} for {host}") from error
        if not _address_is_public(ip):
            raise UnsafeUrlError(
                f"host {host} resolves to non-public address {ip} "
                "(loopback/private/link-local/metadata/reserved)")


def _blocked(reason: str, url: str, redirects: tuple[str, ...] = ()) -> dict[str, Any]:
    """A transport-shaped refusal: status None + error means the connector
    classifies it FAILED and never ingests a body."""
    return {"error": reason, "status": None, "final_url": url,
            "redirects": redirects, "body": b"", "truncated": False, "headers": {}}


def guarded_transport(inner: Callable[..., Mapping[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Wrap a transport with the egress guard: the initial URL is checked
    before the request; every redirect hop and the final URL are checked after,
    and any response reached through a forbidden hop is refused (no body)."""

    def transport(*, url: str, request_headers: Mapping[str, str],
                  timeout_seconds: float, maximum_bytes: int) -> dict[str, Any]:
        try:
            assert_url_safe(url)
        except UnsafeUrlError as error:
            return _blocked(f"SSRF_BLOCKED: {error}", url)
        result = dict(inner(url=url, request_headers=request_headers,
                            timeout_seconds=timeout_seconds, maximum_bytes=maximum_bytes))
        redirects = tuple(result.get("redirects") or ())
        for hop in redirects + (result.get("final_url") or "",):
            if not hop:
                continue
            try:
                assert_url_safe(hop)
            except UnsafeUrlError as error:
                return _blocked(f"SSRF_BLOCKED_REDIRECT: {error}", hop, redirects)
        return result

    return transport
