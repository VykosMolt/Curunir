"""Wayback Machine: HISTORICAL_ENUMERATE lists captures; HISTORICAL_FETCH gets one capture's bytes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
CAPTURE_ENDPOINT = "https://web.archive.org/web"


def _wayback_timestamp(iso_time: str | None) -> str:
    if not iso_time:
        return ""
    parsed = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%d%H%M%S")


def capture_time_iso(timestamp: str) -> str:
    parsed = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return parsed.isoformat()


class WaybackConnector(SourceConnector):
    connector_id = "wayback-machine-v1"
    connector_version = "1.0"
    operations = ("HISTORICAL_ENUMERATE", "HISTORICAL_FETCH")
    timeout_seconds = 60.0

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        if request.operation == "HISTORICAL_ENUMERATE":
            return self._enumerate(request, transport, now)
        return self._fetch_capture(request, transport, now)

    def _enumerate(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        params = {
            "url": request.value, "output": "json", "limit": str(request.limit),
            "fl": "timestamp,original,digest,mimetype,statuscode",
            "showResumeKey": "true",
        }
        start, end = request.time_bounds
        if start:
            params["from"] = _wayback_timestamp(start)
        if end:
            params["to"] = _wayback_timestamp(end)
        if request.cursor:
            params["resumeKey"] = request.cursor
        url = f"{CDX_ENDPOINT}?{urlencode(params)}"
        raw = self._get(url, transport)
        if raw.get("error"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw))
        if not raw.get("body"):
            return self._failed(request, url, raw, now=now, error_class="HTTP",
                                error_detail="empty CDX response body")
        try:
            rows = json.loads(raw["body"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return self._failed(request, url, raw, now=now, error_class="PARSE", error_detail=str(exc))
        if not isinstance(rows, list):
            return self._failed(request, url, raw, now=now, error_class="PARSE",
                                error_detail="CDX response is not a JSON array")
        next_cursor = None
        if rows and rows[-1] and len(rows[-1]) == 1:  # The resume key comes as a final one-cell row.
            next_cursor = rows[-1][0]
            rows = rows[:-1]
        rows = [row for row in rows if row]
        results = []
        for row in rows[1:]:  # The first row is the header.
            if not isinstance(row, list) or len(row) < 5:
                continue
            timestamp, original, digest, mimetype, statuscode = row[:5]
            try:
                source_time = capture_time_iso(timestamp)
            except (TypeError, ValueError):
                continue
            results.append(NativeResult(
                native_id=f"{timestamp}/{original}",
                url=f"{CAPTURE_ENDPOINT}/{timestamp}id_/{original}",
                source_time=source_time,
                identifiers=(("wayback_digest", digest),),
                attributes=(("mimetype", mimetype), ("statuscode", statuscode),
                            ("original_url", original), ("timestamp", timestamp)),
            ))
        return self._response(request, url, raw, results=tuple(results), now=now,
                              next_cursor=next_cursor, media_type="application/json")

    def _fetch_capture(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        # The value is "<14-digit timestamp>/<original url>", as enumeration returns it.
        timestamp, _, original = request.value.partition("/")
        if len(timestamp) != 14 or not timestamp.isdigit() or not original:
            return self._failed(request, request.value, {}, now=now, error_class="PARSE",
                                error_detail="capture reference must be '<timestamp>/<url>'")
        url = f"{CAPTURE_ENDPOINT}/{timestamp}id_/{original}"
        raw = self._get(url, transport)
        if raw.get("error") or not raw.get("body"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw) or "empty capture body")
        result = NativeResult(
            native_id=request.value, url=url,
            source_time=capture_time_iso(timestamp),
            attributes=(("original_url", original), ("timestamp", timestamp)),
        )
        return self._response(request, url, raw, results=(result,), now=now)
