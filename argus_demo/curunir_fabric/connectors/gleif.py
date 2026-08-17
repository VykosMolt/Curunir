"""GLEIF LEI registry — structured legal-entity lookup with pagination.

SEARCH filters LEI records by legal name (page-number pagination); LOOKUP
retrieves one LEI record. Records carry registration timestamps, successor
links and jurisdiction facts that feed pivots and coverage accounting.
"""
from __future__ import annotations

import json
from urllib.parse import quote, urlencode

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport

API_ENDPOINT = "https://api.gleif.org/api/v1/lei-records"


def _record_result(item: dict) -> NativeResult:
    attributes_block = item.get("attributes") or {}
    entity = attributes_block.get("entity") or {}
    registration = attributes_block.get("registration") or {}
    lei = attributes_block.get("lei") or item.get("id", "")
    legal_name = ((entity.get("legalName") or {}).get("name")) or ""
    other_names = tuple((name.get("name") or "") for name in entity.get("otherNames") or ())
    successor = (entity.get("successorEntity") or {}).get("lei") or ""
    attributes = [
        ("legal_name", legal_name),
        ("jurisdiction", entity.get("jurisdiction") or ""),
        ("entity_status", entity.get("status") or ""),
        ("registration_status", registration.get("status") or ""),
        ("legal_address_city", ((entity.get("legalAddress") or {}).get("city")) or ""),
        ("legal_address_country", ((entity.get("legalAddress") or {}).get("country")) or ""),
        ("initial_registration", registration.get("initialRegistrationDate") or ""),
    ]
    attributes.extend(("other_name", name) for name in other_names if name)
    if successor:
        attributes.append(("successor_lei", successor))
    identifiers = [("LEI", lei)]
    return NativeResult(
        native_id=lei, title=legal_name,
        url=f"{API_ENDPOINT}/{quote(lei)}",
        snippet=f"{entity.get('jurisdiction') or ''} {registration.get('status') or ''}".strip(),
        source_time=registration.get("lastUpdateDate"),
        identifiers=tuple(identifiers), attributes=tuple(attributes),
    )


class GleifConnector(SourceConnector):
    connector_id = "gleif-lei-v1"
    connector_version = "1.0"
    operations = ("SEARCH", "LOOKUP")

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        if request.operation == "SEARCH":
            page = request.cursor or "1"
            params = {
                "filter[fulltext]": request.value,
                "page[size]": str(request.limit), "page[number]": page,
            }
            url = f"{API_ENDPOINT}?{urlencode(params)}"
        else:
            url = f"{API_ENDPOINT}/{quote(request.value)}"
        raw = self._get(url, transport, headers={"Accept": "application/vnd.api+json"})
        if raw.get("error"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw))
        try:
            payload = json.loads(raw["body"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return self._failed(request, url, raw, now=now, error_class="PARSE", error_detail=str(exc))
        # A source-side error/throttle answers HTTP 200 with a JSON:API error
        # document ({"errors": [...]}) or a body with no `data` member at all.
        # That is a FAILED retrieval, NOT an empty result set — classifying it
        # EMPTY would let a throttled source masquerade as evidence of absence.
        if not isinstance(payload, dict) or ("data" not in payload):
            detail = (f"source-declared errors: {str(payload.get('errors'))[:200]}"
                      if isinstance(payload, dict) and payload.get("errors")
                      else "response is not a valid JSON:API document (no data member)")
            return self._failed(request, url, raw, now=now, error_class="HTTP", error_detail=detail)
        data = payload.get("data")
        items = data if isinstance(data, list) else ([data] if data else [])
        results = tuple(_record_result(item) for item in items)
        next_cursor = None
        if request.operation == "SEARCH":
            pagination = (payload.get("meta") or {}).get("pagination") or {}
            current, last = pagination.get("currentPage"), pagination.get("lastPage")
            if current is not None and last is not None and current < last:
                next_cursor = str(current + 1)
        return self._response(request, url, raw, results=results, now=now,
                              next_cursor=next_cursor, media_type="application/vnd.api+json")
