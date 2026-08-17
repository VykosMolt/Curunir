"""Manifestation → normalized document/record, with payload custody.

Reuses the established derivative functions (XML element paths, exact
plain-text line mapping, pdftotext page anchoring) and adds the two paths the
repository lacked: JSON structured records normalized to an addressable
field-path table (never flattened to prose), and HTML normalized to
block-level regions rather than one document-sized span.

The original bytes stay authoritative in the immutable custody store; the
normalized text and field table are content-addressed payloads in the event
store, so every span/field anchor is recoverable by replay. Mapping statuses
state honestly how normalized offsets relate to original bytes.
"""
from __future__ import annotations

import hashlib
import html as html_module
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from argus.prospective.research.offline_official_ingestion import (
    classify_html, detect_format, text_derivative, xml_derivative,
)
from argus.source_intelligence.models import digest_id

from . import PARSER_VERSION
from .contracts import NormalizedDocumentRecord
from .store import SemanticStore

# bounds a pathological payload without truncating real registry records: a
# maximal Wikidata organisation entity flattens to ~24k rows, so 50k keeps
# identity- and relation-bearing fields (external ids, official website)
# addressable while FIELDS_TRUNCATED_AT_* still fires on genuine outliers
MAX_FIELDS = 50_000
MAX_FIELD_VALUE = 2000
MAX_REGIONS = 400

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "caption",
               "blockquote", "pre", "figcaption", "dt", "dd", "title", "summary"}
_HIDDEN_TAGS = {"script", "style", "noscript", "template"}


class NormalizationError(ValueError):
    pass


def load_manifestation_bytes(custody_root: str | Path, manifestation: dict) -> bytes:
    """Recover the original bytes from custody by content identity, verified."""
    digest = manifestation["content_sha256"]
    path = Path(custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    if not path.exists():
        raise NormalizationError(f"manifestation bytes not in custody: {digest}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise NormalizationError(f"custody bytes fail hash verification: {digest}")
    return data


# ---- JSON structured records --------------------------------------------


def json_fields(payload: Any, *, prefix: str = "$") -> list[tuple[str, str]]:
    """Flatten a JSON payload into (field_path, scalar value) rows.

    Paths are stable JSONPath-style addresses; structure is preserved as
    addressable fields, never collapsed into prose.
    """
    rows: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if len(rows) >= MAX_FIELDS:
            return
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        else:
            value = "" if node is None else (node if isinstance(node, str) else json.dumps(node))
            rows.append((path, value[:MAX_FIELD_VALUE]))

    walk(payload, prefix)
    return rows


# ---- HTML block normalization -------------------------------------------


class _BlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (label, text)
        self._hidden_depth = 0
        self._stack: list[str] = []
        self._buffer: list[str] = []
        self._buffer_label = "body"
        self.title = ""
        self._in_title = False

    def _flush(self) -> None:
        text = " ".join(" ".join(self._buffer).split())
        if text:
            self.blocks.append((self._buffer_label, text))
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS or tag in ("div", "section", "article", "tr", "table",
                                         "ul", "ol", "header", "footer", "main", "body"):
            self._flush()
            if tag in _BLOCK_TAGS:
                self._buffer_label = tag
            else:
                self._buffer_label = "body"
        if tag not in ("br", "hr", "img", "meta", "link", "input"):
            self._stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in _BLOCK_TAGS:
            self._flush()
            self._buffer_label = "body"

    def handle_data(self, data):
        if self._hidden_depth:
            return
        if self._in_title:
            self.title += data
        self._buffer.append(data)

    def close(self):
        super().close()
        self._flush()


def _sniff_charset(data: bytes) -> str | None:
    head = data[:4096]
    match = re.search(rb'charset\s*=\s*["\']?\s*([\w][\w.:-]{1,30})', head, re.IGNORECASE)
    if match:
        return match.group(1).decode("ascii", "replace").strip().casefold()
    return None


def _scrub_surrogates(value: str) -> str:
    """Replace lone surrogates (and anything that cannot round-trip through
    UTF-8) with U+FFFD. The response BODY is re-derived into strings here — a
    JSON body's \\udXXX escape survives json.loads, and a utf-7/declared-charset
    HTML page can decode to surrogates — and such a string would crash the
    payload/record serialization (json.dumps(...).encode()) it feeds. This is
    the semantic-plane analogue of the connector-edge scrub, which cannot reach
    raw_body-derived strings (review F8-A); it mirrors the errors='replace'
    decoding the xml/text/pdf branches already use."""
    return value.encode("utf-8", "replace").decode("utf-8")


def decode_html(data: bytes) -> tuple[str, list[str]]:
    """Decode HTML bytes honoring a declared charset; report what was used."""
    charset = _sniff_charset(data)
    if charset:
        try:
            return data.decode(charset), [f"DECODED_DECLARED_CHARSET_{charset.upper()}"]
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8"), []
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace"), ["DECODED_UTF8_WITH_REPLACEMENT"]


def html_blocks(data: bytes) -> tuple[str, list[tuple[str, int, int]], str, list[str], str]:
    """Normalize HTML into newline-joined blocks with per-block regions.

    Normalized offsets are exact within the emitted text; the mapping back to
    original bytes remains approximate and is declared as such downstream.
    """
    raw, decode_warnings = decode_html(data)
    content_class = classify_html(raw)
    warnings: list[str] = list(decode_warnings)
    if re.search(r"<script\b", raw, re.IGNORECASE):
        warnings.append("ACTIVE_SCRIPT_PRESENT_NOT_EXECUTED")
    collector = _BlockCollector()
    try:
        collector.feed(raw)
        collector.close()
    except Exception as exc:
        raise NormalizationError(f"NORMALIZATION_FAILED_MALFORMED_HTML: {exc}") from exc
    regions: list[tuple[str, int, int]] = []
    parts: list[str] = []
    offset = 0
    for index, (label, text) in enumerate(collector.blocks):
        start = offset
        end = start + len(text)
        if len(regions) < MAX_REGIONS:
            regions.append((f"{label}:{index}", start, end))
        parts.append(text)
        offset = end + 1  # newline separator
    if len(collector.blocks) > MAX_REGIONS:
        warnings.append(f"REGIONS_TRUNCATED_AT_{MAX_REGIONS}")
    title = " ".join(collector.title.split())
    return "\n".join(parts), regions, title, warnings, content_class


# ---- main entry ----------------------------------------------------------


def _detect(manifestation: dict, data: bytes) -> str:
    media = (manifestation.get("media_type") or "").casefold()
    stripped = data.lstrip()[:64]
    if "json" in media or stripped.startswith((b"{", b"[")):
        try:
            json.loads(data.decode("utf-8"))
            return "JSON"
        except (ValueError, UnicodeDecodeError):
            pass
    if "rss" in media or "atom" in media:
        return "FEED"
    fmt, _, _ = detect_format(data)
    if fmt == "XML" and re.search(rb"<(rss|feed)[\s>]", data[:2000]):
        return "FEED"
    return fmt


def normalized_document_id(manifestation_id: str) -> str:
    return digest_id("semdoc", manifestation_id, PARSER_VERSION)


def normalize_manifestation(store: SemanticStore, manifestation: dict,
                            custody_root: str | Path, *,
                            now: str, actor: str, marking,
                            language_hint: str = "") -> dict:
    """Normalize one manifestation into the semantic plane. Idempotent:
    re-normalizing the same manifestation under the same parser version
    returns the existing record instead of appending a duplicate."""
    document_id = normalized_document_id(manifestation["manifestation_id"])
    for existing in store.records_of("semantic_document"):
        if existing["document_id"] == document_id:
            return existing

    data = load_manifestation_bytes(custody_root, manifestation)
    fmt = _detect(manifestation, data)
    warnings: list[str] = []
    regions: list[tuple[str, int, int]] = []
    structural: list[tuple[str, str]] = [("format", fmt)]
    fields_sha = ""
    field_count = 0
    title = ""
    publisher = ""
    content_class = ""
    language = language_hint

    if fmt == "JSON":
        payload = json.loads(data.decode("utf-8"))
        rows = json_fields(payload)
        field_count = len(rows)
        if field_count >= MAX_FIELDS:
            warnings.append(f"FIELDS_TRUNCATED_AT_{MAX_FIELDS}")
        fields_body = json.dumps(rows, ensure_ascii=False, sort_keys=False).encode("utf-8", "replace")
        fields_sha = store.put_payload(fields_body)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
        content_class = "API_RESPONSE"
        structural.append(("field_paths", "JSONPATH_DOLLAR"))
    elif fmt in ("FEED", "XML"):
        try:
            normalized, maps, legacy_warnings = xml_derivative(data)
        except Exception as exc:
            raise NormalizationError("NORMALIZATION_FAILED_MALFORMED_XML") from exc
        warnings.extend(legacy_warnings)
        text = normalized.decode("utf-8", "replace")
        for path, start, end in maps[:MAX_REGIONS]:
            regions.append((path, start, end))
        if len(maps) > MAX_REGIONS:
            warnings.append(f"REGIONS_TRUNCATED_AT_{MAX_REGIONS}")
        # element paths double as a field table for structured extraction
        rows = [(path, text[start:end][:MAX_FIELD_VALUE]) for path, start, end in maps]
        field_count = len(rows)
        fields_body = json.dumps(rows, ensure_ascii=False).encode("utf-8", "replace")
        fields_sha = store.put_payload(fields_body)
        content_class = "FEED" if fmt == "FEED" else "API_RESPONSE"
        structural.append(("field_paths", "XML_ELEMENT_PATH"))
    elif fmt == "HTML":
        text, regions, title, html_warnings, content_class = html_blocks(data)
        warnings.extend(html_warnings)
        structural.append(("block_mapping", "NORMALIZED_OFFSETS_EXACT_ORIGINAL_APPROXIMATE"))
    elif fmt == "PLAIN_TEXT":
        normalized, encoding, maps, legacy_warnings = text_derivative(data)
        warnings.extend(legacy_warnings)
        text = normalized.decode("utf-8", "replace")
        for line, (source_start, source_end, start, end) in enumerate(maps[:MAX_REGIONS], 1):
            regions.append((f"line:{line}", start, end))
        structural.append(("encoding", encoding))
    elif fmt == "PDF":
        text, page_regions, pdf_warnings = _pdf_text(data)
        regions = page_regions
        warnings.extend(pdf_warnings)
        structural.append(("pdf_tool", "pdftotext-layout"))
    else:
        raise NormalizationError(f"NORMALIZATION_FAILED_UNSUPPORTED_FORMAT:{fmt}")

    # scrub any lone surrogates the body's own content decoded to (a JSON \udXXX
    # escape, a utf-7/declared-charset HTML page) before they enter the stored
    # payload or the document record — otherwise their serialization crashes and
    # the document is silently lost (review F8-A). A visible warning records that
    # unencodable characters were replaced.
    scrubbed = tuple(_scrub_surrogates(v) for v in (text, title, publisher))
    if scrubbed != (text, title, publisher):
        warnings.append("SCRUBBED_UNENCODABLE_CHARACTERS")
    text, title, publisher = scrubbed
    normalized_sha = store.put_payload(text.encode("utf-8"))
    record = NormalizedDocumentRecord(
        document_id=document_id,
        manifestation_id=manifestation["manifestation_id"],
        source_id=manifestation["source_id"],
        retrieval_id=manifestation["retrieval_id"],
        native_id=manifestation["native_id"] or manifestation["request_url"],
        content_sha256=manifestation["content_sha256"],
        normalized_sha256=normalized_sha, fields_sha256=fields_sha,
        format=fmt, content_class=content_class, language=language,
        title=title[:300], publisher=publisher,
        source_time=manifestation.get("source_time"),
        retrieval_time=manifestation["retrieval_time"],
        temporal_status=manifestation["temporal_status"],
        region_count=len(regions), field_count=field_count,
        regions=tuple(regions), structural=tuple(structural),
        parser="curunir-semantic-normalizer", parser_version=PARSER_VERSION,
        warnings=tuple(warnings), recorded_time=now, marking=marking,
    )
    store.append("SEMANTIC_DOCUMENT_RECORDED", record, recorded_time=now, actor=actor)
    return record.to_record()


# a bounded derivative: a pathological PDF can decompress to far more text than
# its (already byte-capped) input, so the extracted text is capped and the cap
# is disclosed as a warning rather than letting an unbounded blob flow downstream
_MAX_PDF_TEXT_BYTES = 25_000_000


def _pdf_text(data: bytes) -> tuple[str, list[tuple[str, int, int]], list[str]]:
    import subprocess
    try:
        result = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NormalizationError("PDF_TEXT_DERIVATIVE_TOOL_UNAVAILABLE") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise NormalizationError("PDF_TEXT_DERIVATIVE_PARSE_FAILURE")
    warnings = ["ORIGINAL_PDF_BYTES_PRESERVED",
                "DERIVATIVE_OFFSETS_NOT_ORIGINAL_PDF_BYTE_OFFSETS"]
    stdout = result.stdout
    if len(stdout) > _MAX_PDF_TEXT_BYTES:
        stdout = stdout[:_MAX_PDF_TEXT_BYTES]
        warnings.append("PDF_TEXT_DERIVATIVE_TRUNCATED_TO_BOUND")
    text = stdout.decode("utf-8", "replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    regions: list[tuple[str, int, int]] = []
    offset = 0
    for number, page in enumerate(pages, 1):
        regions.append((f"page:{number}", offset, offset + len(page)))
        offset += len(page) + 1
    return "\f".join(pages), regions, warnings


def load_fields(store: SemanticStore, document_record: dict) -> list[tuple[str, str]]:
    """Recover the addressable field table for a structured document."""
    if not document_record["fields_sha256"]:
        return []
    body = store.get_payload(document_record["fields_sha256"])
    return [(path, value) for path, value in json.loads(body.decode("utf-8"))]


def load_text(store: SemanticStore, document_record: dict) -> str:
    return store.get_payload(document_record["normalized_sha256"]).decode("utf-8")
