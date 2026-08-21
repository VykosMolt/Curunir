"""RSS / Atom feed retrieval — the polling/change-feed acquisition shape."""
from __future__ import annotations

import email.utils
import xml.etree.ElementTree as ElementTree
from datetime import timezone

from curunir_operational.xml_safety import reject_dtd

from .base import ConnectorRequest, ConnectorResponse, NativeResult, SourceConnector, Transport

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _rfc822_to_iso(value: str) -> str | None:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _iso_or_none(value: str) -> str | None:
    from datetime import datetime
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _text(node: ElementTree.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def parse_feed_items(body: bytes) -> list[NativeResult]:
    reject_dtd(body)
    root = ElementTree.fromstring(body)
    items: list[NativeResult] = []
    if root.tag == "rss":
        for item in root.iter("item"):
            link = _text(item.find("link"))
            guid = _text(item.find("guid")) or link
            published = _rfc822_to_iso(_text(item.find("pubDate")))
            if not guid:
                continue
            items.append(NativeResult(
                native_id=guid, title=_text(item.find("title")), url=link,
                snippet=_text(item.find("description"))[:500], source_time=published,
            ))
    elif root.tag == f"{ATOM_NS}feed":
        for entry in root.findall(f"{ATOM_NS}entry"):
            link_node = entry.find(f"{ATOM_NS}link")
            link = link_node.get("href", "") if link_node is not None else ""
            identity = _text(entry.find(f"{ATOM_NS}id")) or link
            published = _iso_or_none(_text(entry.find(f"{ATOM_NS}updated"))
                                     or _text(entry.find(f"{ATOM_NS}published")))
            if not identity:
                continue
            items.append(NativeResult(
                native_id=identity, title=_text(entry.find(f"{ATOM_NS}title")), url=link,
                snippet=_text(entry.find(f"{ATOM_NS}summary"))[:500], source_time=published,
            ))
    else:
        raise ValueError(f"unsupported feed root: {root.tag}")
    return items


class RssFeedConnector(SourceConnector):
    connector_id = "rss-feed-v1"
    connector_version = "1.0"
    operations = ("FETCH", "POLL")

    def _execute(self, request: ConnectorRequest, transport: Transport, now: str) -> ConnectorResponse:
        url = request.value
        raw = self._get(url, transport)
        if raw.get("error") or not raw.get("body"):
            return self._failed(request, url, raw, now=now,
                                error_class="TRANSPORT" if raw.get("status") is None else "HTTP",
                                error_detail=self.transport_error_detail(raw) or "empty feed body")
        try:
            items = parse_feed_items(raw["body"])
        except (ElementTree.ParseError, ValueError) as exc:
            return self._failed(request, url, raw, now=now, error_class="PARSE", error_detail=str(exc))
        return self._response(request, url, raw, results=tuple(items[: request.limit]), now=now)
