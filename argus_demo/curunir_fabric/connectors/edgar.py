"""SEC EDGAR full-text search — regulatory filings with deep history.

SEARCH queries the EDGAR full-text search API (quoted phrases, date bounds);
FETCH retrieves one filing document from the EDGAR archive. The SEC asks
automated clients to declare an identifying User-Agent; the fabric complies.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport

SEARCH_ENDPOINT = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_PREFIX = "https://www.sec.gov/Archives/edgar/data"


class EdgarFullTextConnector(SourceConnector):
    connector_id = "sec-edgar-fts-v1"
    connector_version = "1.0"
    operations = ("SEARCH", "FETCH")
    user_agent = "CurunirFabric/0.1 research collection (operator contact on file)"

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        if request.operation == "FETCH":
            return self._fetch(request, transport, now)
        return self._search(request, transport, now)

    def _search(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        params: dict[str, str] = {"q": f'"{request.value}"'}
        start, end = request.time_bounds
        if start or end:
            params["dateRange"] = "custom"
            if start:
                params["startdt"] = start[:10]
            if end:
                params["enddt"] = end[:10]
        if request.cursor:
            params["from"] = request.cursor
        url = f"{SEARCH_ENDPOINT}?{urlencode(params)}"
        raw = self._get(url, transport)
        if raw.get("error"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw))
        try:
            payload = json.loads(raw["body"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return self._failed(request, url, raw, now=now, error_class="PARSE", error_detail=str(exc))
        hits = ((payload.get("hits") or {}).get("hits")) or []
        results = []
        for hit in hits[: request.limit]:
            source = hit.get("_source") or {}
            adsh = source.get("adsh", "")
            ciks = source.get("ciks") or []
            cik = str(ciks[0]).lstrip("0") if ciks else ""
            document_id = hit.get("_id", "")  # "<adsh>:<filename>"
            filename = document_id.partition(":")[2]
            document_url = ""
            if cik and adsh and filename:
                document_url = f"{ARCHIVE_PREFIX}/{cik}/{adsh.replace('-', '')}/{filename}"
            names = source.get("display_names") or []
            results.append(NativeResult(
                native_id=document_id or adsh, title="; ".join(names),
                url=document_url,
                snippet=f"{source.get('file_type') or source.get('root_forms') or ''}".strip(),
                source_time=(source.get("file_date") + "T00:00:00+00:00") if source.get("file_date") else None,
                identifiers=tuple(("SEC_CIK", str(item)) for item in ciks),
                attributes=(("accession", adsh), ("form", str(source.get("file_type") or "")),
                            ("file_date", str(source.get("file_date") or ""))),
            ))
        total = ((payload.get("hits") or {}).get("total") or {}).get("value") or 0
        offset = int(request.cursor or 0)
        next_cursor = str(offset + len(hits)) if offset + len(hits) < total and hits else None
        return self._response(request, url, raw, results=tuple(results), now=now,
                              next_cursor=next_cursor, media_type="application/json")

    def _fetch(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        url = request.value
        if not url.startswith("https://www.sec.gov/"):
            return self._failed(request, url, {}, now=now, error_class="PARSE",
                                error_detail="EDGAR fetch requires a www.sec.gov archive URL")
        raw = self._get(url, transport)
        if raw.get("error") or not raw.get("body"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw) or "empty body")
        result = NativeResult(native_id=url, url=raw.get("final_url") or url)
        return self._response(request, url, raw, results=(result,), now=now)
