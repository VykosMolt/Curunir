"""Live ordinary-public-web page retrieval (FETCH / POLL of one URL)."""
from __future__ import annotations

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport


class WebPageConnector(SourceConnector):
    connector_id = "web-page-v1"
    connector_version = "1.0"
    operations = ("FETCH", "POLL")

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        url = request.value
        raw = self._get(url, transport)
        if raw.get("error") or not raw.get("body"):
            return self._failed(request, url, raw, now=now, error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw) or "empty body")
        headers = raw.get("headers") or {}
        result = NativeResult(
            native_id=raw.get("final_url") or url, url=raw.get("final_url") or url,
            source_time=None,
            attributes=(("content_type", headers.get("content-type", "")),),
        )
        return self._response(request, url, raw, results=(result,), now=now)
