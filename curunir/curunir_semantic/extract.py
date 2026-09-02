"""Read observations out of a normalized document, without any model.

Each source shape gets the right instrument: field paths for structured
registry and API records, element paths for feeds, and the text capture in
`provenance_capture` for prose and HTML. Every observation lands on a field or
text-span anchor in its manifestation, and re-running appends nothing new.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping

from argus.source_intelligence.models import digest_id

from . import PARSER_VERSION
from .contracts import EvidenceAnchor, SemanticObservation
from .normalize import load_fields, load_text
from curunir_fabric.connectors.rss import _iso_or_none, _rfc822_to_iso

from .provenance_capture import DerivativeMapping, NormalizedDocument, capture
from .store import SemanticStore

FIELD_MAPPING = "EXACT_FIELD_PATH"
TEXT_MAPPING = "NORMALIZED_EXACT_ORIGINAL_APPROXIMATE"

# the Wikidata properties that carry an external identifier
_WIKIDATA_IDENTIFIERS = {
    "P1278": "LEI", "P5531": "SEC_CIK", "P1320": "OPENCORPORATES",
    "P946": "ISIN", "P249": "TICKER", "P6782": "ROR", "P2427": "GRID",
}


def _observation_identity(document_id: str, observation_type: str, subject: str,
                          attribute: str, value: str, object_ref: str) -> str:
    # Hash exactly the value the record stores, so two observations that differ
    # only past the stored prefix cannot collide.
    return digest_id("semobs", document_id, observation_type, subject, attribute,
                     value[:2000], object_ref)


class _Emitter:
    """Appends one document's observations, skipping ones already recorded."""

    def __init__(self, store: SemanticStore, document: dict, *, producer_id: str,
                 now: str, actor: str, marking):
        self.store = store
        self.document = document
        self.producer_id = producer_id
        self.now = now
        self.actor = actor
        self.marking = marking
        self.existing = {r["observation_id"]
                         for r in store.observations_for_document(document["document_id"])}
        self.emitted: list[dict] = []

    def field_anchor(self, field_path: str, value: str) -> EvidenceAnchor:
        # Name the field table the path addresses, so the anchor alone says
        # which payload a replay must read.
        return EvidenceAnchor(
            manifestation_id=self.document["manifestation_id"],
            source_id=self.document["source_id"],
            content_sha256=self.document["content_sha256"],
            kind="FIELD",
            normalized_sha256=self.document["fields_sha256"] or self.document["normalized_sha256"],
            field_path=field_path, exact_value=value[:500], mapping_status=FIELD_MAPPING,
        )

    def span_anchor(self, start: int, end: int, exact_value: str) -> EvidenceAnchor:
        return EvidenceAnchor(
            manifestation_id=self.document["manifestation_id"],
            source_id=self.document["source_id"],
            content_sha256=self.document["content_sha256"],
            kind="TEXT_SPAN", normalized_sha256=self.document["normalized_sha256"],
            start=start, end=end, exact_value=exact_value[:500], mapping_status=TEXT_MAPPING,
        )

    def emit(self, observation_type: str, subject_ref: str, attribute: str, value: str,
             anchors: tuple[EvidenceAnchor, ...], *, object_ref: str = "",
             valid_from: str | None = None, valid_to: str | None = None,
             source_time: str | None = None, time_precision: str = "UNKNOWN",
             language: str = "") -> None:
        if not value and not object_ref:
            return
        observation_id = _observation_identity(
            self.document["document_id"], observation_type, subject_ref, attribute,
            value, object_ref)
        if observation_id in self.existing:
            return
        record = SemanticObservation(
            observation_id=observation_id,
            document_id=self.document["document_id"],
            manifestation_id=self.document["manifestation_id"],
            source_id=self.document["source_id"],
            observation_type=observation_type, subject_ref=subject_ref,
            attribute=attribute, value=value[:2000], object_ref=object_ref,
            valid_from=valid_from, valid_to=valid_to,
            source_time=source_time or self.document.get("source_time"),
            time_precision=time_precision,
            language=language, representation="ORIGINAL",
            anchors=anchors,
            producer_kind="DETERMINISTIC_PARSER", producer_id=self.producer_id,
            producer_version=PARSER_VERSION, inference_id="",
            recorded_time=self.now, marking=self.marking,
        )
        self.store.append("SEMANTIC_OBSERVATION_RECORDED", record,
                          recorded_time=self.now, actor=self.actor)
        self.existing.add(observation_id)
        self.emitted.append(record.to_record())


def _iso_day(value: str | None) -> str | None:
    """Parse a source-stated date or timestamp to an ISO string with a zone.

    A bare date is taken as UTC at day precision. A bare timestamp returns None
    rather than being stamped with a zone the source never gave.
    """
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.isoformat()


# ---- GLEIF ---------------------------------------------------------------


def _record_prefixes(fields: Mapping[str, str], container: str) -> list[str]:
    """The path of each record: '$.data' for a lookup, '$.data[N]' for a search."""
    prefixes = set()
    for path in fields:
        m = re.match(rf"^\$\.{container}(\[\d+\])?\.", path)
        if m:
            prefixes.add(f"$.{container}{m.group(1) or ''}")
    return sorted(prefixes)


def extract_gleif(emitter: _Emitter, fields: dict[str, str]) -> None:
    for prefix in _record_prefixes(fields, "data"):
        lei = fields.get(f"{prefix}.attributes.lei") or fields.get(f"{prefix}.id", "")
        if not lei:
            continue
        subject = f"LEI:{lei}"
        entity = f"{prefix}.attributes.entity"
        registration = f"{prefix}.attributes.registration"
        last_update = _iso_day(fields.get(f"{registration}.lastUpdateDate"))

        def field(path: str, attribute: str, observation_type: str = "ENTITY_ATTRIBUTE",
                  valid_from: str | None = None) -> None:
            value = fields.get(path, "")
            if value:
                emitter.emit(observation_type, subject, attribute, value,
                             (emitter.field_anchor(path, value),),
                             source_time=last_update, valid_from=valid_from,
                             time_precision="DAY" if valid_from else "UNKNOWN")

        emitter.emit("ENTITY_IDENTIFIER", subject, "LEI", lei,
                     (emitter.field_anchor(f"{prefix}.attributes.lei", lei),),
                     source_time=last_update)
        field(f"{entity}.legalName.name", "legal_name", "ENTITY_NAME")
        field(f"{entity}.jurisdiction", "jurisdiction")
        field(f"{entity}.status", "entity_status")
        field(f"{registration}.status", "registration_status")
        field(f"{entity}.legalAddress.city", "legal_address_city")
        field(f"{entity}.legalAddress.country", "legal_address_country")
        initial = fields.get(f"{registration}.initialRegistrationDate", "")
        if initial:
            emitter.emit("EVENT", subject, "lei_registered", initial,
                         (emitter.field_anchor(f"{registration}.initialRegistrationDate", initial),),
                         valid_from=_iso_day(initial), source_time=last_update,
                         time_precision="DAY")
        for path, value in fields.items():
            if value and re.fullmatch(rf"{re.escape(entity)}\.otherNames\[\d+\]\.name", path):
                emitter.emit("ENTITY_NAME", subject, "other_name", value,
                             (emitter.field_anchor(path, value),), source_time=last_update)
        successor = fields.get(f"{entity}.successorEntity.lei", "")
        if successor:
            emitter.emit("RELATION", subject, "SUCCESSOR_OF", successor,
                         (emitter.field_anchor(f"{entity}.successorEntity.lei", successor),),
                         object_ref=f"LEI:{successor}", source_time=last_update)


# ---- Wikidata ------------------------------------------------------------


def extract_wikidata(emitter: _Emitter, fields: dict[str, str]) -> None:
    qids = sorted({m.group(1) for path in fields
                   if (m := re.match(r"^\$\.entities\.(Q\d+)\.", path))})
    for qid in qids:
        subject = f"WIKIDATA_QID:{qid}"
        base = f"$.entities.{qid}"
        emitter.emit("ENTITY_IDENTIFIER", subject, "WIKIDATA_QID", qid,
                     (emitter.field_anchor(f"{base}.id", qid),))
        for path, value in fields.items():
            if not value or not path.startswith(base):
                continue
            label = re.fullmatch(rf"{re.escape(base)}\.labels\.([\w-]+)\.value", path)
            if label:
                emitter.emit("ENTITY_NAME", subject, "label", value,
                             (emitter.field_anchor(path, value),), language=label.group(1))
                continue
            alias = re.fullmatch(rf"{re.escape(base)}\.aliases\.([\w-]+)\[\d+\]\.value", path)
            if alias:
                emitter.emit("ENTITY_NAME", subject, "alias", value,
                             (emitter.field_anchor(path, value),), language=alias.group(1))
                continue
            claim = re.fullmatch(
                rf"{re.escape(base)}\.claims\.(P\d+)\[\d+\]\.mainsnak\.datavalue\.value", path)
            if claim:
                prop = claim.group(1)
                if prop in _WIKIDATA_IDENTIFIERS:
                    emitter.emit("ENTITY_IDENTIFIER", subject, _WIKIDATA_IDENTIFIERS[prop],
                                 value, (emitter.field_anchor(path, value),))
                elif prop == "P856":
                    emitter.emit("RELATION", subject, "OPERATES", value,
                                 (emitter.field_anchor(path, value),),
                                 object_ref=f"URL:{value}")


# ---- SEC EDGAR full-text search -----------------------------------------


def extract_edgar_search(emitter: _Emitter, fields: dict[str, str]) -> None:
    hits = sorted({m.group(1) for path in fields
                   if (m := re.match(r"^(\$\.hits\.hits\[\d+\])\.", path))})
    for hit in hits:
        source = f"{hit}._source"
        adsh = fields.get(f"{source}.adsh", "")
        cik = fields.get(f"{source}.ciks[0]", "")
        if not adsh or not cik:
            continue
        subject = f"SEC_CIK:{cik.lstrip('0') or cik}"
        file_date = _iso_day(fields.get(f"{source}.file_date"))
        form = fields.get(f"{source}.file_type", "") or fields.get(f"{source}.root_forms[0]", "")
        document_id = fields.get(f"{hit}._id", adsh)
        emitter.emit("ENTITY_IDENTIFIER", subject, "SEC_CIK", cik,
                     (emitter.field_anchor(f"{source}.ciks[0]", cik),))
        display = fields.get(f"{source}.display_names[0]", "")
        if display:
            name = re.sub(r"\s*\((CIK|AAPL|[A-Z0-9.]+)\s*[0-9]*\)\s*$", "",
                          display.split("(CIK")[0]).strip()
            if name:
                emitter.emit("ENTITY_NAME", subject, "display_name", name,
                             (emitter.field_anchor(f"{source}.display_names[0]", display),))
        emitter.emit("EVENT", subject, "filing_published", form or "FILING",
                     (emitter.field_anchor(f"{source}.adsh", adsh),
                      emitter.field_anchor(f"{source}.file_date",
                                           fields.get(f"{source}.file_date", ""))),
                     object_ref=f"SEC_ACCESSION:{adsh}#{document_id}",
                     valid_from=file_date, source_time=file_date, time_precision="DAY")


# ---- Feeds ---------------------------------------------------------------


def _strip_ns(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "", path)


def extract_feed(emitter: _Emitter, fields: dict[str, str]) -> None:
    # In an element path the counter that tells sibling items apart sits on the
    # segment before the tag, so an item is identified by its whole stem up to
    # and including "/item" or "/entry".
    items: dict[str, dict[str, tuple[str, str]]] = {}
    channel_title = ""
    title_path = ""
    for path, value in fields.items():
        clean = _strip_ns(path)
        if not channel_title and re.fullmatch(
                r"/rss\[\d+\]/channel\[\d+\]/title|/feed\[\d+\]/title", clean):
            channel_title, title_path = value, path
        m = re.fullmatch(r"(?P<stem>.*/(?:item|entry))\[\d+\]/(?P<tag>\w+)", clean)
        if m:
            items.setdefault(m.group("stem"), {})[m.group("tag")] = (path, value)
    feed_subject = f"FEED:{emitter.document['native_id']}"
    if channel_title:
        emitter.emit("ENTITY_NAME", feed_subject, "feed_title", channel_title,
                     (emitter.field_anchor(title_path, channel_title),))
    for stem in sorted(items):
        entry = items[stem]
        guid_path, guid = entry.get("guid") or entry.get("id") or entry.get("link") or ("", "")
        if not guid:
            continue
        title = entry.get("title", ("", ""))[1]
        published = entry.get("pubDate") or entry.get("updated") or entry.get("published")
        published_iso = None
        if published:
            published_iso = _rfc822_to_iso(published[1]) or _iso_or_none(published[1])
        anchors = [emitter.field_anchor(guid_path, guid)]
        if entry.get("title"):
            anchors.append(emitter.field_anchor(entry["title"][0], title))
        emitter.emit("PUBLICATION", feed_subject, "item_published", title or guid,
                     tuple(anchors), object_ref=guid,
                     valid_from=published_iso, source_time=published_iso,
                     time_precision="MINUTE" if published_iso else "UNKNOWN")


# ---- Prose / HTML --------------------------------------------------------


def extract_text_signals(emitter: _Emitter, text: str) -> None:
    """Read statements and provenance lines out of normalized text."""
    document = emitter.document
    shim = NormalizedDocument(
        document_id=document["document_id"],
        source_object_id=document["manifestation_id"],
        source_hash=document["content_sha256"],
        derivative_hash=document["normalized_sha256"] or document["content_sha256"],
        parser=document["parser"], parser_version=document["parser_version"],
        language=document["language"] or "und", text=text,
        sections=(), pages=(),
        mappings=(DerivativeMapping(
            mapping_id=digest_id("map", document["document_id"]),
            source_object_id=document["manifestation_id"],
            source_hash=document["content_sha256"],
            derivative_hash=document["normalized_sha256"] or document["content_sha256"],
            source_locator="normalized-document", derivative_start=0,
            derivative_end=len(text), precision="APPROXIMATE_SECTION"),),
        warnings=tuple(document["warnings"]), omitted_content=(),
        generated_time=document["recorded_time"],
    )
    raws, observations = capture(
        shim, source_object_id=document["manifestation_id"],
        content_hash=document["content_sha256"], title=document.get("title") or None)
    # The anchoring below pairs each raw with its observation by position. If
    # that ever stops holding, fail loudly rather than mis-pair.
    if len(raws) != len(observations):
        raise ValueError("capture returned unpaired raws/observations; "
                         "anchor pairing would be wrong")
    # A page's statements are about where the page came from, so an archived
    # copy speaks for the site it archived, not for the archive.
    native = document.get("native_id") or document["manifestation_id"]
    if document["source_id"] == "wayback" and "/" in native:
        subject = f"URL:{native.partition('/')[2]}"
    elif native.startswith(("http://", "https://")):
        subject = f"URL:{native}"
    else:
        subject = native
    # Labeled lines like "Managing Director: Kari Nordmann." — the label
    # becomes the attribute.
    offset = 0
    for line in text.split("\n")[:200]:
        match = re.match(r"^\s*([A-Za-zÀ-ÿ][\w /()-]{2,48}):\s+(.{2,160}?)\s*\.?\s*$", line)
        if match and not match.group(2).startswith(("//", "http")):
            attribute = re.sub(r"[^a-z0-9]+", "_", match.group(1).strip().casefold()).strip("_")
            value = match.group(2).strip()
            start = offset + match.start(2)
            emitter.emit("STATEMENT", subject, attribute, value,
                         (emitter.span_anchor(start, start + len(match.group(2).strip()),
                                              value),))
        offset += len(line) + 1

    for raw, item in zip(raws, observations):
        # Anchor on the matched text, then narrow to the value inside it. If
        # the value cannot be located, say so rather than invent a span.
        needle = item.observed_value
        raw_text = raw.raw_text or ""
        raw_position = text.find(raw_text) if raw_text else -1
        if raw_position >= 0 and needle:
            inner = text.find(needle, raw_position, raw_position + len(raw_text) + len(needle))
            position = inner if inner >= 0 else -1
        else:
            position = text.find(needle) if needle else -1
        if position >= 0:
            anchor = emitter.span_anchor(position, position + len(needle), needle)
        elif raw_position >= 0:
            anchor = emitter.span_anchor(raw_position, raw_position + len(raw_text), raw_text)
        else:
            anchor = EvidenceAnchor(
                manifestation_id=emitter.document["manifestation_id"],
                source_id=emitter.document["source_id"],
                content_sha256=emitter.document["content_sha256"],
                kind="DOCUMENT",
                normalized_sha256=emitter.document["normalized_sha256"],
                exact_value=needle[:500],
                mapping_status="VALUE_NOT_LOCATED_DOCUMENT_SCOPE")
        emitter.emit(
            "ROLE_SIGNAL" if item.observation_type in
            ("EXPLICIT_PUBLISHER_LINE", "EXPLICIT_ISSUER_LINE", "INSTITUTIONAL_ATTRIBUTION",
             "DOCUMENT_SERIES_OWNER") else "STATEMENT",
            subject, item.observation_type, item.observed_value, (anchor,))


# ---- dispatch ------------------------------------------------------------

_EXTRACTORS = {
    "gleif": ("gleif-record-parser", extract_gleif),
    "wikidata": ("wikidata-record-parser", extract_wikidata),
    "sec-edgar": ("edgar-fts-parser", extract_edgar_search),
}


def extract_observations(store: SemanticStore, document_record: dict, *,
                         now: str, actor: str, marking) -> list[dict]:
    """Run the extractor that suits one normalized document."""
    source_id = document_record["source_id"]
    fmt = document_record["format"]
    if source_id in _EXTRACTORS and fmt == "JSON":
        producer_id, extractor = _EXTRACTORS[source_id]
        emitter = _Emitter(store, document_record, producer_id=producer_id,
                           now=now, actor=actor, marking=marking)
        extractor(emitter, dict(load_fields(store, document_record)))
        return emitter.emitted
    if fmt == "FEED":
        emitter = _Emitter(store, document_record, producer_id="feed-parser",
                           now=now, actor=actor, marking=marking)
        extract_feed(emitter, dict(load_fields(store, document_record)))
        return emitter.emitted
    if fmt in ("HTML", "PLAIN_TEXT", "PDF"):
        emitter = _Emitter(store, document_record, producer_id="text-signal-parser",
                           now=now, actor=actor, marking=marking)
        extract_text_signals(emitter, load_text(store, document_record))
        return emitter.emitted
    # A source no extractor understands yields nothing; nothing is invented
    # from a schema the parser cannot read.
    return []
