"""Normalize a manifestation into addressable text and, if structured, fields.

JSON becomes a table of field paths, HTML becomes block-level regions. The
original bytes stay authoritative in custody, so every anchor is recoverable.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import subprocess
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from argus.prospective.research.offline_official_ingestion import (
    classify_html, detect_format, text_derivative, xml_derivative,
)
from argus.source_intelligence.models import digest_id
from curunir_operational.canonical import parse_external_json
from curunir_operational.xml_safety import reject_dtd

from . import PARSER_VERSION
from .contracts import NormalizedDocumentRecord
from .store import SemanticStore

# Bound a runaway payload without truncating real records: the largest Wikidata
# organisation flattens to about 24k rows, so 50k leaves room.
MAX_FIELDS = 50_000
MAX_FIELD_VALUE = 2000
MAX_REGIONS = 400
MAX_PDF_TEXT_BYTES = 25_000_000

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "caption",
               "blockquote", "pre", "figcaption", "dt", "dd", "title", "summary"}
_HIDDEN_TAGS = {"script", "style", "noscript", "template"}


class NormalizationError(ValueError):
    pass


def load_manifestation_bytes(custody_root: str | Path, manifestation: dict) -> bytes:
    """Read the original bytes back from custody and check their hash."""
    digest = manifestation["content_sha256"]
    path = Path(custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    if not path.exists():
        raise NormalizationError(f"manifestation bytes not in custody: {digest}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise NormalizationError(f"custody bytes fail hash verification: {digest}")
    return data


# JSON structured records


def _json_fields(payload: Any, *, prefix: str = "$"
                 ) -> tuple[list[tuple[str, str]], bool, bool]:
    """Flatten a JSON payload into (field path, value) rows. The paths are stable
    JSONPath-style addresses, so the structure stays addressable.
    """
    rows: list[tuple[str, str]] = []
    field_cap_reached = False
    value_cap_reached = False

    def walk(node: Any, path: str) -> None:
        nonlocal field_cap_reached, value_cap_reached
        if len(rows) >= MAX_FIELDS:
            field_cap_reached = True
            return
        if isinstance(node, dict):
            for key in sorted(node):
                walk(node[key], f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        else:
            value = "" if node is None else (node if isinstance(node, str) else json.dumps(node))
            if len(value) > MAX_FIELD_VALUE:
                value_cap_reached = True
            rows.append((path, value[:MAX_FIELD_VALUE]))

    walk(payload, prefix)
    return rows, field_cap_reached, value_cap_reached


def json_fields(payload: Any, *, prefix: str = "$") -> list[tuple[str, str]]:
    return _json_fields(payload, prefix=prefix)[0]


# HTML block normalization


class _BlockCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (label, text)
        self._hidden_depth = 0
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

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if tag == "title":
            self._in_title = False
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


def decode_html(data: bytes) -> tuple[str, list[str]]:
    """Decode HTML bytes using any declared charset, and say what was used."""
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
    """Normalize HTML into newline-joined blocks with a region per block. Offsets are
    exact within the emitted text; the mapping back to the original bytes is
    approximate and declared as such.
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
        offset = end + 1  # for the newline between blocks
    if len(collector.blocks) > MAX_REGIONS:
        warnings.append(f"REGIONS_TRUNCATED_AT_{MAX_REGIONS}")
    title = " ".join(collector.title.split())
    return "\n".join(parts), regions, title, warnings, content_class


# Main entry


def _detect(manifestation: dict, data: bytes) -> str:
    media = (manifestation.get("media_type") or "").casefold()
    stripped = data.lstrip()[:64]
    if "json" in media or stripped.startswith((b"{", b"[")):
        return "JSON"
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
    """Normalize one manifestation into the semantic plane. Re-normalizing under the
    same parser version returns the existing record rather than duplicating.
    """
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
        try:
            payload, repaired = parse_external_json(data)
        except ValueError as error:
            raise NormalizationError(str(error)) from error
        rows, fields_truncated, values_truncated = _json_fields(payload)
        field_count = len(rows)
        if fields_truncated:
            warnings.append(f"FIELDS_TRUNCATED_AT_{MAX_FIELDS}")
        if values_truncated:
            warnings.append(f"FIELD_VALUES_TRUNCATED_AT_{MAX_FIELD_VALUE}")
        if repaired:
            warnings.append("SCRUBBED_UNENCODABLE_CHARACTERS")
        fields_body = json.dumps(rows, ensure_ascii=False, sort_keys=False).encode("utf-8")
        fields_sha = store.put_payload(fields_body)
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
        content_class = "API_RESPONSE"
        structural.append(("field_paths", "JSONPATH_DOLLAR"))
    elif fmt in ("FEED", "XML"):
        try:
            reject_dtd(data)
            normalized, maps, legacy_warnings = xml_derivative(data)
        except Exception as exc:
            raise NormalizationError("NORMALIZATION_FAILED_MALFORMED_XML") from exc
        warnings.extend(legacy_warnings)
        text = normalized.decode("utf-8", "replace")
        for path, start, end in maps[:MAX_REGIONS]:
            regions.append((path, start, end))
        if len(maps) > MAX_REGIONS:
            warnings.append(f"REGIONS_TRUNCATED_AT_{MAX_REGIONS}")
        # the element paths double as a field table for extraction
        rows = [(path, text[start:end][:MAX_FIELD_VALUE])
                for path, start, end in maps[:MAX_FIELDS]]
        field_count = len(rows)
        if len(maps) > MAX_FIELDS:
            warnings.append(f"FIELDS_TRUNCATED_AT_{MAX_FIELDS}")
        fields_body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
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
        regions = page_regions[:MAX_REGIONS]
        if len(page_regions) > MAX_REGIONS:
            pdf_warnings.append(f"REGIONS_TRUNCATED_AT_{MAX_REGIONS}")
        warnings.extend(pdf_warnings)
        structural.append(("pdf_tool", "pdftotext-layout"))
    else:
        raise NormalizationError(f"NORMALIZATION_FAILED_UNSUPPORTED_FORMAT:{fmt}")

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
    event = store.append(
        "SEMANTIC_DOCUMENT_RECORDED", record,
        recorded_time=now, actor=actor)
    return event["record"]


def _pdf_text(data: bytes) -> tuple[str, list[tuple[str, int, int]], list[str]]:
    """Run pdftotext under a time limit and an output size limit."""
    path = ""
    process: subprocess.Popen | None = None
    output = bytearray()
    truncated = False
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as source:
            source.write(data)
            path = source.name
        process = subprocess.Popen(
            ["pdftotext", "-layout", path, "-"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        assert process.stdout is not None
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, 30)
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                raise subprocess.TimeoutExpired(process.args, 30)
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                break
            remaining_bytes = MAX_PDF_TEXT_BYTES + 1 - len(output)
            output.extend(chunk[:remaining_bytes])
            if len(output) > MAX_PDF_TEXT_BYTES:
                truncated = True
                process.terminate()
                break
        try:
            returncode = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait(timeout=2)
    except FileNotFoundError as error:
        raise NormalizationError("PDF_TEXT_DERIVATIVE_TOOL_UNAVAILABLE") from error
    except (OSError, subprocess.TimeoutExpired) as error:
        if process is not None:
            process.kill()
            process.wait()
        raise NormalizationError("PDF_TEXT_DERIVATIVE_RESOURCE_LIMIT") from error
    finally:
        if path:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    if (returncode != 0 and not truncated) or not output.strip():
        raise NormalizationError("PDF_TEXT_DERIVATIVE_PARSE_FAILURE")
    warnings = ["ORIGINAL_PDF_BYTES_PRESERVED",
                "DERIVATIVE_OFFSETS_NOT_ORIGINAL_PDF_BYTE_OFFSETS"]
    if truncated:
        del output[MAX_PDF_TEXT_BYTES:]
        warnings.append("PDF_TEXT_DERIVATIVE_TRUNCATED_TO_BOUND")
    text = bytes(output).decode("utf-8", "replace")
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
    """Read back the field table of a structured document."""
    if not document_record["fields_sha256"]:
        return []
    body = store.get_payload(document_record["fields_sha256"])
    return [(path, value) for path, value in json.loads(body.decode("utf-8"))]


def load_text(store: SemanticStore, document_record: dict) -> str:
    return store.get_payload(document_record["normalized_sha256"]).decode("utf-8")
