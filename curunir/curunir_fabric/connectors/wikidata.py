"""Wikidata: SEARCH resolves names to entities; LOOKUP expands one entity."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport

API_ENDPOINT = "https://www.wikidata.org/w/api.php"

# External-id properties with a known scheme. Others keep the wikidata:PNNN name.
IDENTIFIER_PROPERTIES = {
    "P1278": "LEI",
    "P5531": "SEC_CIK",
    "P1320": "OPENCORPORATES",
    "P946": "ISIN",
    "P249": "TICKER",
    "P6782": "ROR",
    "P2427": "GRID",
    "P856": "OFFICIAL_WEBSITE",
}


class WikidataConnector(SourceConnector):
    connector_id = "wikidata-v1"
    connector_version = "1.0"
    operations = ("SEARCH", "LOOKUP")

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        if request.operation == "SEARCH":
            return self._search(request, transport, now)
        return self._lookup(request, transport, now)

    def _api(self, request: ConnectorRequest, transport: Transport, now: str,
             params: dict[str, str]) -> tuple[dict | None, str, dict, ConnectorResponse | None]:
        url = f"{API_ENDPOINT}?{urlencode({**params, 'format': 'json'})}"
        raw = self._get(url, transport)
        if raw.get("error"):
            failure = self._failed(request, url, raw, now=now,
                                   error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                   error_detail=self.transport_error_detail(raw))
            return None, url, raw, failure
        try:
            payload = json.loads(raw["body"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return None, url, raw, self._failed(request, url, raw, now=now,
                                                error_class="PARSE", error_detail=str(exc))
        if "error" in payload:
            return None, url, raw, self._failed(request, url, raw, now=now, error_class="HTTP",
                                                error_detail=str(payload["error"])[:240])
        return payload, url, raw, None

    def _search(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        params = {
            "action": "wbsearchentities", "search": request.value,
            "language": request.language or "en", "uselang": request.language or "en",
            "limit": str(request.limit),
        }
        if request.cursor:
            params["continue"] = request.cursor
        payload, url, raw, failure = self._api(request, transport, now, params)
        if failure is not None:
            return failure
        results = tuple(
            NativeResult(
                native_id=item["id"], title=item.get("label", ""),
                url=item.get("concepturi", f"https://www.wikidata.org/wiki/{item['id']}"),
                snippet=item.get("description", ""),
                identifiers=(("WIKIDATA_QID", item["id"]),),
                attributes=(("match_language", str(item.get("match", {}).get("language", ""))),
                            ("match_text", str(item.get("match", {}).get("text", "")))),
            )
            for item in payload.get("search", ())
        )
        next_cursor = str(payload["search-continue"]) if "search-continue" in payload else None
        return self._response(request, url, raw, results=results, now=now,
                              next_cursor=next_cursor, media_type="application/json")

    def _lookup(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        params = {
            "action": "wbgetentities", "ids": request.value,
            "props": "labels|aliases|descriptions|claims|sitelinks",
        }
        payload, url, raw, failure = self._api(request, transport, now, params)
        if failure is not None:
            return failure
        entities = payload.get("entities", {})
        entity = entities.get(request.value)
        if not entity or "missing" in entity:
            return self._response(request, url, raw, results=(), now=now,
                                  media_type="application/json")
        identifiers = [("WIKIDATA_QID", request.value)]
        for prop, claims in (entity.get("claims") or {}).items():
            for claim in claims:
                snak = claim.get("mainsnak", {})
                if snak.get("snaktype") != "value":
                    continue
                value = snak.get("datavalue", {}).get("value")
                if not isinstance(value, str):
                    continue
                if prop in IDENTIFIER_PROPERTIES:
                    identifiers.append((IDENTIFIER_PROPERTIES[prop], value))
                elif snak.get("datatype") == "external-id":
                    identifiers.append((f"wikidata:{prop}", value))
        labels = entity.get("labels") or {}
        aliases = entity.get("aliases") or {}
        attributes = [("label:" + lang, data["value"]) for lang, data in sorted(labels.items())]
        for lang, entries in sorted(aliases.items()):
            for entry in entries:
                attributes.append(("alias:" + lang, entry["value"]))
        english = labels.get("en", {}).get("value", "")
        result = NativeResult(
            native_id=request.value, title=english,
            url=f"https://www.wikidata.org/wiki/{request.value}",
            snippet=(entity.get("descriptions") or {}).get("en", {}).get("value", ""),
            identifiers=tuple(identifiers), attributes=tuple(attributes),
        )
        return self._response(request, url, raw, results=(result,), now=now,
                              media_type="application/json")
