"""Bounded public-web transport with complete SSRF mediation.

Every product acquisition uses this path. Each hop is parsed, resolved, and
classified before a socket is opened; the connection is made to one of those
validated addresses rather than resolving the hostname a second time. Redirects
are followed explicitly, so a forbidden target is rejected before its request.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 8


class UnsafeUrlError(ValueError):
    """The requested URL cannot be proven to target the public internet."""


Resolver = Callable[[str, int], tuple[str, ...]]
RequestOnce = Callable[..., tuple[int, dict[str, str], bytes, bool]]

_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is None and address in _NAT64_WELL_KNOWN:
            embedded = ipaddress.IPv4Address(int(address) & 0xffffffff)
        if embedded is not None and not embedded.is_global:
            return False
        if address.teredo is not None:
            server, client = address.teredo
            if not server.is_global or not client.is_global:
                return False
    return True


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"host does not resolve: {host}") from error
    addresses: list[str] = []
    for info in infos:
        raw = info[4][0].split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as error:
            raise UnsafeUrlError(f"host resolved to an invalid address: {raw!r}") from error
        if not _is_public_address(address):
            raise UnsafeUrlError(f"host resolves to non-public address: {address}")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise UnsafeUrlError(f"host resolves to no address: {host}")
    return tuple(addresses)


def resolve_public_url(url: str, resolver: Resolver = _public_addresses
                       ) -> tuple[str, str, int, str, tuple[str, ...]]:
    """Return normalized request components and the validated address set."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.casefold()
        if scheme not in ALLOWED_SCHEMES:
            raise UnsafeUrlError(f"scheme not allowed: {scheme or '(none)'}")
        if parts.username is not None or parts.password is not None:
            raise UnsafeUrlError("userinfo is not allowed in acquisition URLs")
        host = parts.hostname
        if not host:
            raise UnsafeUrlError("URL has no host")
        host = host.encode("idna").decode("ascii")
        port = parts.port or (443 if scheme == "https" else 80)
    except (UnicodeError, ValueError) as error:
        if isinstance(error, UnsafeUrlError):
            raise
        raise UnsafeUrlError(f"invalid URL: {error}") from error
    if not 1 <= port <= 65535:
        raise UnsafeUrlError("URL port is out of range")
    target = parts.path or "/"
    if parts.query:
        target += f"?{parts.query}"
    return scheme, host, port, target, resolver(host, port)


def assert_url_safe(url: str, resolver: Resolver = _public_addresses) -> None:
    resolve_public_url(url, resolver)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        self._curunir_address = address
        super().__init__(host, port=port, timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._curunir_address, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        self._curunir_address = address
        super().__init__(host, port=port, timeout=timeout,
                         context=ssl.create_default_context())

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._curunir_address, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _host_header(host: str, scheme: str, port: int) -> str:
    literal = f"[{host}]" if ":" in host else host
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    return literal if default else f"{literal}:{port}"


def _request_once(*, scheme: str, host: str, port: int, target: str,
                  addresses: tuple[str, ...], request_headers: Mapping[str, str],
                  timeout_seconds: float, maximum_bytes: int
                  ) -> tuple[int, dict[str, str], bytes, bool]:
    """GET one already-validated hop, connecting only to its pinned addresses."""
    last_error: OSError | None = None
    deadline = time.monotonic() + timeout_seconds
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("validated-address connection deadline exceeded")
        connection: http.client.HTTPConnection
        if scheme == "https":
            connection = _PinnedHTTPSConnection(host, address, port, remaining)
        else:
            connection = _PinnedHTTPConnection(host, address, port, remaining)
        try:
            headers = {str(key): str(value) for key, value in request_headers.items()
                       if str(key).casefold() != "host"}
            headers["Host"] = _host_header(host, scheme, port)
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            response_headers = {
                str(key).casefold(): str(value) for key, value in response.getheaders()}
            if response.status in REDIRECT_STATUSES:
                body = b""
                truncated = False
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("response deadline exceeded")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                body = response.read(maximum_bytes + 1)
                truncated = len(body) > maximum_bytes
                body = body[:maximum_bytes]
            return int(response.status), response_headers, body, truncated
        except OSError as error:
            last_error = error
        finally:
            connection.close()
    assert last_error is not None
    raise last_error


@dataclass(frozen=True)
class SafePublicTransport:
    resolver: Resolver = _public_addresses
    request_once: RequestOnce = _request_once
    max_redirects: int = MAX_REDIRECTS

    def __call__(self, *, url: str, request_headers: Mapping[str, str],
                 timeout_seconds: float, maximum_bytes: int) -> dict[str, Any]:
        if timeout_seconds <= 0 or maximum_bytes < 0:
            return _blocked("INVALID_TRANSPORT_LIMIT", url)
        deadline = time.monotonic() + timeout_seconds
        current = url
        redirects: list[str] = []
        for hop in range(self.max_redirects + 1):
            try:
                scheme, host, port, target, addresses = resolve_public_url(
                    current, self.resolver)
            except UnsafeUrlError as error:
                label = "SSRF_BLOCKED_REDIRECT" if redirects else "SSRF_BLOCKED"
                return _blocked(f"{label}: {error}", current, tuple(redirects))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _blocked("TimeoutError: total transport deadline exceeded",
                                current, tuple(redirects))
            try:
                status, headers, body, truncated = self.request_once(
                    scheme=scheme, host=host, port=port, target=target,
                    addresses=addresses, request_headers=request_headers,
                    timeout_seconds=remaining, maximum_bytes=maximum_bytes)
            except Exception as error:  # transport boundary; caller records typed failure
                if isinstance(error, MemoryError):
                    raise
                return _blocked(
                    f"{type(error).__name__}:{str(error)[:240]}",
                    current, tuple(redirects))
            if time.monotonic() > deadline:
                return _blocked("TimeoutError: total transport deadline exceeded",
                                current, tuple(redirects))
            if status in REDIRECT_STATUSES:
                location = headers.get("location")
                if not location:
                    return _blocked(f"HTTP_{status}: redirect has no Location",
                                    current, tuple(redirects))
                if hop >= self.max_redirects:
                    return _blocked("TOO_MANY_REDIRECTS", current, tuple(redirects))
                next_url = urljoin(current, location)
                if next_url in redirects or next_url == current:
                    return _blocked("REDIRECT_LOOP", next_url, tuple(redirects))
                # Resolve before recording/following: an unsafe hop never opens a socket.
                try:
                    resolve_public_url(next_url, self.resolver)
                except UnsafeUrlError as error:
                    return _blocked(f"SSRF_BLOCKED_REDIRECT: {error}", next_url,
                                    tuple((*redirects, next_url)))
                redirects.append(next_url)
                current = next_url
                continue
            error = None if 200 <= status < 300 else f"HTTP_{status}"
            return {"body": body, "status": status, "final_url": current,
                    "headers": headers, "redirects": tuple(redirects),
                    "error": error, "truncated": truncated}
        return _blocked("TOO_MANY_REDIRECTS", current, tuple(redirects))


def _blocked(reason: str, url: str, redirects: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"body": b"", "status": None, "final_url": url,
            "headers": {}, "redirects": redirects,
            "error": reason, "truncated": False}


SAFE_PUBLIC_TRANSPORT = SafePublicTransport()


def retrieve_public_bytes(*, url: str, request_headers: Mapping[str, str],
                          timeout_seconds: float, maximum_bytes: int) -> dict[str, Any]:
    return SAFE_PUBLIC_TRANSPORT(
        url=url, request_headers=request_headers,
        timeout_seconds=timeout_seconds, maximum_bytes=maximum_bytes)
