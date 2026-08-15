"""Common source/connector acquisition boundary.

A connector translates one typed ``ConnectorRequest`` into one bounded
retrieval against its source family and returns a ``ConnectorResponse`` that
always carries: the native raw bytes (for custody), the request/final URLs,
transport facts, a classified status, and typed per-record results with
source-native identifiers and timestamps. Connectors never write stores,
never consult policy (the executor gates access before invoking them), and
receive their transport injected so unit tests run offline against recorded
bytes while the product path uses the real public-web transport.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from argus.public_web_transport_v4 import retrieve_public_bytes_v4

from ..contracts import OPERATIONS

# transport: (url, headers, timeout, max bytes) -> transport dict (body/status/…)
Transport = Callable[..., Mapping[str, Any]]

RESPONSE_STATUS = ("OK", "EMPTY", "FAILED", "ACCESS_RESTRICTED", "NOT_SUPPORTED")
ERROR_CLASSES = ("TRANSPORT", "HTTP", "PARSE", "TRUNCATED")

DEFAULT_USER_AGENT = "CurunirFabric/0.1 (public-interest research collection; contact operator)"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAXIMUM_BYTES = 40_000_000


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

    def body_sha256(self) -> str:
        return hashlib.sha256(self.raw_body).hexdigest()


class ConnectorError(ValueError):
    pass


class SourceConnector:
    """Base class: one connector per acquisition shape, stateless, injectable transport."""

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
        transport = transport or retrieve_public_bytes_v4
        return self._execute(request, transport, now or utc_now())

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        raise NotImplementedError

    # ---- shared plumbing -------------------------------------------------

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
