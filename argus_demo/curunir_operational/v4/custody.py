"""Public-web custody and deterministic normalization for V4.

Retrieval is intentionally small and policy-bound.  It does not authenticate,
solve challenges, retry around blocks, or parse a response before immutable
custody and the retrieval record have been written.
"""
from __future__ import annotations

import json
import gzip
import os
import re
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from .io import append_jsonl, write_json
from ..canonical import retrieve_public_bytes_v4 as transport_v4
from .models import (
    AccessDecision, DerivativeMapping, NormalizedDocument, RetrievalRecord,
    SearchLead, SourceRecord, TranslationDerivative, sha256, stable_id,
)

ACQUISITION_VERSION = "curunir-public-web-acquirer-v4"
NORMALIZER_VERSION = "curunir-document-normalizer-v4"
SUPPORTED_MEDIA = {
    "text/html", "application/xhtml+xml", "application/pdf", "application/json",
    "application/xml", "text/xml", "application/rss+xml", "application/atom+xml",
    "text/plain",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _media_type(headers: Mapping[str, str], final_url: str) -> str:
    declared = str(headers.get("content-type", "")).split(";", 1)[0].strip().casefold()
    if declared:
        return declared
    lowered = final_url.casefold()
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith(".json"):
        return "application/json"
    if lowered.endswith((".xml", ".rss", ".atom")):
        return "application/xml"
    if lowered.endswith((".txt", ".md")):
        return "text/plain"
    return "application/octet-stream"


def _failure_from_body(status: int | None, media_type: str | None, body: bytes,
                       requested_url: str) -> tuple[str | None, str]:
    sample = body[:16000].decode("utf-8", "ignore").casefold()
    if status in {401, 403}:
        return "ACCESS_CONTROLLED_OR_FORBIDDEN", "BLOCKED"
    if status == 429:
        return "RATE_LIMITED_NO_CIRCUMVENTION", "BLOCKED"
    if status and status >= 500:
        return f"HTTP_{status}", "FAILED"
    if status and status >= 400:
        return f"HTTP_{status}", "FAILED"
    waf_tokens = ("captcha", "attention required", "access denied", "cf-chl-", "web application firewall")
    if any(token in sample for token in waf_tokens):
        return "WAF_OR_CHALLENGE_PAGE", "QUARANTINED"
    if not body:
        return "EMPTY_DOCUMENT", "QUARANTINED"
    expected_pdf = requested_url.casefold().split("?", 1)[0].endswith(".pdf")
    if expected_pdf and not body.startswith(b"%PDF-"):
        return "HTML_OR_NON_PDF_RETURNED_FOR_EXPECTED_PDF", "QUARANTINED"
    if media_type == "application/pdf" and not body.startswith(b"%PDF-"):
        return "INVALID_PDF_SIGNATURE", "QUARANTINED"
    if media_type not in SUPPORTED_MEDIA:
        return f"UNSUPPORTED_MEDIA_TYPE:{media_type}", "QUARANTINED"
    return None, "CAPTURED"


def acquire_public_source(*, case_id: str, lead: SearchLead, decision: AccessDecision,
                          custody_root: str | Path, publisher: str, source_class: str,
                          title: str | None = None, publication_time: str | None = None,
                          timeout_seconds: float = 20.0, maximum_bytes: int = 20_000_000,
                          user_agent: str = "CurunirResearchShadow/4.0 (+public-source-custody)",
                          retry_of: str | None = None) -> tuple[RetrievalRecord, SourceRecord | None]:
    """Acquire one allowed public lead and preserve all outcomes append-only."""
    root = Path(custody_root)
    records_dir = root / "records"
    event_path = records_dir / "retrieval_events.jsonl"
    record_path = records_dir / "retrieval_records.jsonl"
    source_path = records_dir / "source_records.jsonl"
    request_time = now_utc()
    retrieval_id = stable_id("retrieval", case_id, lead.lead_id, request_time, retry_of or "FIRST")
    append_jsonl(event_path, ({"event": "RETRIEVAL_STARTED", "retrieval_id": retrieval_id,
                               "case_id": case_id, "lead_id": lead.lead_id,
                               "requested_url": lead.url, "access_decision_id": decision.decision_id,
                               "time": request_time, "acquirer": ACQUISITION_VERSION},))
    if decision.state != "ALLOW_PUBLIC_RETRIEVAL":
        record = RetrievalRecord(
            retrieval_id, case_id, lead.lead_id, lead.url, None, (), publisher, request_time,
            now_utc(), None, {}, None, None, None, decision.decision_id, ACQUISITION_VERSION,
            f"ACCESS_POLICY:{decision.state}", retry_of, lead.language, decision.access_marking, "BLOCKED",
        )
        append_jsonl(record_path, (record.to_record(),))
        append_jsonl(event_path, ({"event": "RETRIEVAL_COMPLETED", "retrieval_id": retrieval_id,
                                   "time": record.response_time, "content_state": record.content_state},))
        return record, None

    response = transport_v4(
        url=lead.url, request_headers={"User-Agent": user_agent,
                                      "Accept": "text/html,application/pdf,application/json,application/xml,text/plain;q=0.8,*/*;q=0.2"},
        timeout_seconds=timeout_seconds, maximum_bytes=maximum_bytes)
    body = response["body"]; status = response["status"]; final_url = response["final_url"]
    headers = response["headers"]; redirects = response["redirects"]
    failure: str | None = response["error"]; state = "FAILED"
    if response["truncated"]:
        failure, state = "MAXIMUM_BYTES_EXCEEDED_TRUNCATED", "QUARANTINED"
    elif failure:
        state = "BLOCKED" if status in {401, 403, 429} else "FAILED"

    media = _media_type(headers, final_url or lead.url) if final_url or headers else None
    if failure is None:
        failure, state = _failure_from_body(status, media, body, lead.url)
    content_hash = sha256(body) if body else None
    content_path = ""
    if body:
        target = root / "content" / "sha256" / content_hash[:2] / content_hash[2:4] / content_hash
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256(target.read_bytes()) != content_hash:
                raise RuntimeError("immutable content-store collision")
        else:
            target.write_bytes(body)
            try:
                target.chmod(0o444)
            except OSError:
                pass
        content_path = str(target)
    response_time = now_utc()
    record = RetrievalRecord(
        retrieval_id, case_id, lead.lead_id, lead.url, final_url, tuple(redirects), publisher,
        request_time, response_time, status, headers, media, len(body) if body else 0, content_hash,
        decision.decision_id, ACQUISITION_VERSION, failure, retry_of, lead.language,
        decision.access_marking, state,
    )
    # This final custody record is persisted before any caller can normalize the bytes.
    append_jsonl(record_path, (record.to_record(),))
    append_jsonl(event_path, ({"event": "RETRIEVAL_COMPLETED", "retrieval_id": retrieval_id,
                               "time": response_time, "content_state": state,
                               "content_hash": content_hash, "failure": failure},))
    source: SourceRecord | None = None
    if state == "CAPTURED" and content_hash:
        source = SourceRecord(
            stable_id("source-object", content_hash), case_id, (retrieval_id,), content_hash,
            content_path, (lead.url,), (final_url or lead.url,), publisher, source_class,
            title or lead.title, lead.language, publication_time, response_time,
            "CAPTURED_UNREVIEWED", decision.access_marking,
        )
        append_jsonl(source_path, (source.to_record(),))
    return record, source


class _BodyHTMLParser(HTMLParser):
    BLOCKED = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "canvas"}
    BREAK = {"p", "div", "article", "main", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0; self.parts: list[str] = []; self.title_parts: list[str] = []
        self.in_title = False; self.links: list[str] = []; self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self.BLOCKED:
            self.depth += 1
        if tag == "title": self.in_title = True
        if tag == "a": self._href = dict(attrs).get("href")
        if not self.depth and tag in self.BREAK: self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self.BLOCKED and self.depth: self.depth -= 1
        if tag == "title": self.in_title = False
        if tag == "a": self._href = None
        if not self.depth and tag in self.BREAK: self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title: self.title_parts.append(data)
        if self.depth: return
        compact = re.sub(r"\s+", " ", data).strip()
        if compact:
            self.parts.append(compact)


def _decode(body: bytes, headers: Mapping[str, str]) -> tuple[str, list[str]]:
    content_type = headers.get("content-type", "")
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "utf-8-sig", "windows-1252", "latin-1"])
    for encoding in encodings:
        try:
            return body.decode(encoding), ([] if encoding.casefold().startswith("utf") else [f"FALLBACK_ENCODING:{encoding}"])
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace"), ["DECODE_REPLACEMENT_CHARACTERS"]


def _pdf_text(body: bytes) -> tuple[str, tuple[tuple[int, int, int], ...], list[str]]:
    in_fd, in_name = tempfile.mkstemp(prefix="curunir-v4-", suffix=".pdf")
    out_fd, out_name = tempfile.mkstemp(prefix="curunir-v4-", suffix=".txt")
    os.close(out_fd)
    try:
        with os.fdopen(in_fd, "wb") as handle: handle.write(body)
        result = subprocess.run(["pdftotext", "-layout", in_name, out_name], capture_output=True,
                                text=True, timeout=60, check=False)
        if result.returncode:
            raise ValueError(f"PDF_PARSE_FAILED:{result.stderr.strip()[:240]}")
        text = Path(out_name).read_text(encoding="utf-8", errors="replace")
    finally:
        for name in (in_name, out_name):
            try: os.unlink(name)
            except FileNotFoundError: pass
    offsets: list[tuple[int, int, int]] = []; start = 0
    for number, page in enumerate(text.split("\f"), 1):
        if not page and number > 1: continue
        offsets.append((number, start, start + len(page))); start += len(page) + 1
    warnings = [] if text.strip() else ["EMPTY_PDF_TEXT_OCR_MAY_BE_REQUIRED"]
    return text, tuple(offsets), warnings


def normalize_source(source: SourceRecord, retrieval: RetrievalRecord,
                     *, output_dir: str | Path | None = None) -> NormalizedDocument:
    if retrieval.content_state != "CAPTURED" or retrieval.content_hash != source.content_hash:
        raise ValueError("only captured immutable source bytes may be normalized")
    raw_body = Path(source.content_path).read_bytes()
    if sha256(raw_body) != source.content_hash:
        raise ValueError("source custody hash mismatch")
    body = raw_body
    media = retrieval.media_type or "application/octet-stream"
    warnings: list[str] = []; omitted: list[str] = []
    sections: tuple[tuple[str, int, int], ...] = (); pages: tuple[tuple[int, int, int], ...] = ()
    precision = "EXACT_CHARACTER"; parser = "stdlib-text"
    content_encoding = retrieval.headers.get("content-encoding", "").casefold()
    if content_encoding == "gzip" or body.startswith(b"\x1f\x8b"):
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as exc:
            raise ValueError(f"CONTENT_ENCODING_GZIP_INVALID:{exc}") from None
        warnings.append("CONTENT_ENCODING_GZIP_DECODED_FROM_IMMUTABLE_RAW_BYTES")
        precision = "APPROXIMATE_SECTION"
    if media in {"text/html", "application/xhtml+xml"}:
        raw, decode_warnings = _decode(body, retrieval.headers); warnings.extend(decode_warnings)
        html_parser = _BodyHTMLParser(); html_parser.feed(raw)
        lines = [line.strip() for line in " ".join(html_parser.parts).splitlines() if line.strip()]
        text = "\n".join(lines)
        parser = "stdlib-html-body"; precision = "APPROXIMATE_SECTION"
        omitted.extend(("navigation", "headers", "footers", "scripts", "styles", "forms"))
        sections = (("BODY", 0, len(text)),) if text else ()
    elif media == "application/pdf":
        text, pages, pdf_warnings = _pdf_text(body); warnings.extend(pdf_warnings)
        parser = "pdftotext-layout"; precision = "EXACT_PAGE_CHARACTER"
    elif media == "application/json":
        raw, decode_warnings = _decode(body, retrieval.headers); warnings.extend(decode_warnings)
        text = json.dumps(json.loads(raw), indent=2, sort_keys=True, ensure_ascii=False)
        parser = "stdlib-json"; precision = "APPROXIMATE_SECTION"; sections = (("JSON", 0, len(text)),)
    elif media in {"application/xml", "text/xml", "application/rss+xml", "application/atom+xml"}:
        raw, decode_warnings = _decode(body, retrieval.headers); warnings.extend(decode_warnings)
        root = ElementTree.fromstring(raw)
        chunks = [value.strip() for value in root.itertext() if value.strip()]
        text = "\n".join(chunks); parser = "stdlib-xml"
        precision = "APPROXIMATE_SECTION"; sections = ((root.tag, 0, len(text)),)
    elif media == "text/plain":
        text, decode_warnings = _decode(body, retrieval.headers); warnings.extend(decode_warnings)
        sections = (("TEXT", 0, len(text)),) if text else ()
    else:
        raise ValueError(f"unsupported normalized media type: {media}")
    derivative_hash = sha256(text)
    mapping = DerivativeMapping(
        stable_id("mapping", source.source_object_id, derivative_hash), source.source_object_id,
        source.content_hash, derivative_hash, source.content_path, 0, len(text), precision,
    )
    document = NormalizedDocument(
        stable_id("document", source.source_object_id, derivative_hash), source.source_object_id,
        source.content_hash, derivative_hash, parser, NORMALIZER_VERSION, source.language, text,
        sections, pages, (mapping,) if text else (), tuple(warnings), tuple(omitted), now_utc(),
    )
    if output_dir is not None:
        write_json(Path(output_dir) / f"{document.document_id}.json", document.to_record())
    return document


def create_translation(document: NormalizedDocument, *, target_language: str, translated_text: str,
                       provider: str, provider_version: str, alignment_precision: str,
                       warnings: tuple[str, ...] = (), review_state: str = "AI_SECONDARY_REVIEW") -> TranslationDerivative:
    if not translated_text.strip():
        raise ValueError("translation derivative cannot be empty")
    return TranslationDerivative(
        stable_id("translation", document.document_id, target_language, sha256(translated_text)),
        document.document_id, document.language, target_language, provider, provider_version,
        translated_text, alignment_precision, warnings, sha256(translated_text), review_state, False,
    )
