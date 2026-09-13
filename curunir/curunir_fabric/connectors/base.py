"""Base class and record types shared by all connectors.

A connector turns one request into one bounded retrieval. It never writes the
store and never checks policy; the executor does both. The transport is
injected so tests can run offline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ..contracts import OPERATIONS
from ..transport import retrieve_public_bytes

# transport(url, headers, timeout, max bytes) -> dict with body, status, headers, error
Transport = Callable[..., Mapping[str, Any]]

RESPONSE_STATUS = ("OK", "EMPTY", "FAILED", "ACCESS_RESTRICTED", "NOT_SUPPORTED")
ERROR_CLASSES = ("TRANSPORT", "HTTP", "PARSE", "TRUNCATED")

DEFAULT_USER_AGENT = "CurunirFabric/0.1 (public-interest research collection; contact operator)"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAXIMUM_BYTES = 40_000_000


def scrub_surrogates(value: object) -> str | None:
    """Make a value from a source safe Unicode text."""
    if value is None:
        return None
    return str(value).encode("utf-8", "replace").decode("utf-8")


def _scrub_pairs(pairs: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple((scrub_surrogates(key), scrub_surrogates(value))
                 for key, value in pairs)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ConnectorRequest:
    operation: str            # contracts.OPERATIONS
    value: str                # query text, native id, or URL depending on operation
    language: str = ""
    time_bounds: tuple[str | None, str | None] = (None, None)
    cursor: str | None = None
    limit: int = 10
    extra: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if self.operation not in OPERATIONS:
            raise ValueError(f"invalid operation: {self.operation!r}")
        if not 1 <= self.limit <= 100:
            raise ValueError("request limit out of bounds")
        for name in ("value", "language", "cursor"):
            object.__setattr__(self, name, scrub_surrogates(getattr(self, name)))
        object.__setattr__(self, "extra", _scrub_pairs(self.extra))

    def extra_map(self) -> dict[str, str]:
        return dict(self.extra)


@dataclass(frozen=True)
class NativeResult:
    """One source-native record inside a response."""
    native_id: str
    title: str = ""
    url: str = ""
    snippet: str = ""
    source_time: str | None = None
    identifiers: tuple[tuple[str, str], ...] = ()  # (scheme, value)
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        for name in ("native_id", "title", "url", "snippet", "source_time"):
            object.__setattr__(self, name, scrub_surrogates(getattr(self, name)))
        object.__setattr__(self, "identifiers", _scrub_pairs(self.identifiers))
        object.__setattr__(self, "attributes", _scrub_pairs(self.attributes))


@dataclass(frozen=True)
class ConnectorResponse:
    status: str
    operation: str
    request_url: str
    final_url: str
    http_status: int | None
    raw_body: bytes
    media_type: str
    results: tuple[NativeResult, ...]
    next_cursor: str | None
    error_class: str | None
    error_detail: str
    retrieved_time: str
    redirects: tuple[str, ...] = ()
    etag: str = ""
    last_modified: str = ""
    truncated: bool = False

    def __post_init__(self):
        if self.status not in RESPONSE_STATUS:
            raise ValueError(f"invalid response status: {self.status!r}")
        if self.status == "OK" and not self.results:
            raise ValueError("OK responses carry at least one result")
        if self.error_class is not None and self.error_class not in ERROR_CLASSES:
            raise ValueError(f"invalid error class: {self.error_class!r}")
        for name in ("request_url", "final_url", "media_type", "next_cursor",
                     "error_detail", "retrieved_time", "etag", "last_modified"):
            object.__setattr__(self, name, scrub_surrogates(getattr(self, name)))
        object.__setattr__(self, "redirects",
                           tuple(scrub_surrogates(item) for item in self.redirects))

    def body_sha256(self) -> str:
        return hashlib.sha256(self.raw_body).hexdigest()


class ConnectorError(ValueError):
    pass


class SourceConnector:
    """One connector per acquisition shape. Stateless."""

    connector_id = ""
    connector_version = "0.1"
    operations: tuple[str, ...] = ()
    user_agent = DEFAULT_USER_AGENT
    timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    maximum_bytes = DEFAULT_MAXIMUM_BYTES

    def execute(self, request: ConnectorRequest, *, transport: Transport | None = None,
                now: str | None = None) -> ConnectorResponse:
        if request.operation not in self.operations:
            return self._unsupported(request)
        transport = transport or retrieve_public_bytes
        return self._execute(request, transport, now or utc_now())

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        raise NotImplementedError

    # Shared plumbing

    def _get(self, url: str, transport: Transport, *, headers: Mapping[str, str] | None = None) -> dict:
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        return dict(transport(url=url, request_headers=request_headers,
                              timeout_seconds=self.timeout_seconds, maximum_bytes=self.maximum_bytes))

    def _response(self, request: ConnectorRequest, url: str, raw: Mapping[str, Any], *,
                  results: tuple[NativeResult, ...], now: str,
                  next_cursor: str | None = None, media_type: str = "",
                  error_class: str | None = None, error_detail: str = "") -> ConnectorResponse:
        headers = raw.get("headers") or {}
        status = self._classify(raw, results, error_class)
        return ConnectorResponse(
            status=status, operation=request.operation, request_url=url,
            final_url=raw.get("final_url") or url, http_status=raw.get("status"),
            raw_body=raw.get("body") or b"", media_type=media_type or headers.get("content-type", ""),
            results=results if status == "OK" else (),
            next_cursor=next_cursor, error_class=error_class if status != "OK" else None,
            error_detail=error_detail if status != "OK" else "",
            retrieved_time=now, redirects=tuple(raw.get("redirects") or ()),
            etag=headers.get("etag", ""), last_modified=headers.get("last-modified", ""),
            truncated=bool(raw.get("truncated")),
        )

    def _classify(self, raw: Mapping[str, Any], results: tuple[NativeResult, ...],
                  error_class: str | None) -> str:
        status = raw.get("status")
        if raw.get("error") and status is None:
            return "FAILED"
        if status in (401, 402, 403, 407, 451):
            return "ACCESS_RESTRICTED"
        if status is not None and status >= 400:
            return "FAILED"
        if error_class is not None:
            return "FAILED"
        return "OK" if results else "EMPTY"

    def _failed(self, request: ConnectorRequest, url: str, raw: Mapping[str, Any], *,
                now: str, error_class: str, error_detail: str) -> ConnectorResponse:
        return self._response(request, url, raw, results=(), now=now,
                              error_class=error_class, error_detail=error_detail)

    def _unsupported(self, request: ConnectorRequest) -> ConnectorResponse:
        return ConnectorResponse(
            status="NOT_SUPPORTED", operation=request.operation, request_url="", final_url="",
            http_status=None, raw_body=b"", media_type="", results=(), next_cursor=None,
            error_class=None, error_detail=f"{self.connector_id} does not support {request.operation}",
            retrieved_time=utc_now(),
        )

    def transport_error_detail(self, raw: Mapping[str, Any]) -> str:
        return str(raw.get("error") or "")
