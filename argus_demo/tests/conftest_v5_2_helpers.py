"""Shared builders for V5.2 tests.

Test data only.  Contains exposed V5.1 development examples; production code
never imports this module.
"""
from __future__ import annotations

from curunir_operational.v4.models import (
    DerivativeMapping, ExtractionCandidate, NormalizedDocument, sha256, stable_id,
)
from curunir_operational.v5_1.models import now_utc


def make_document(text: str, *, language: str = "en",
                  document_id: str | None = None) -> NormalizedDocument:
    doc_id = document_id or stable_id("document", sha256(text))
    source_object_id = stable_id("source-object", sha256(text))
    source_hash, derivative_hash = sha256(text), sha256("derivative:" + text)
    mapping = DerivativeMapping(
        stable_id("mapping", doc_id), source_object_id, source_hash,
        derivative_hash, "byte:0", 0, len(text), "EXACT_CHARACTER")
    return NormalizedDocument(
        doc_id, source_object_id, source_hash, derivative_hash, "test-parser",
        "1.0", language, text, (("BODY", 0, len(text)),), ((1, 0, len(text)),),
        (mapping,), (), (), now_utc())


def make_candidate(document: NormalizedDocument, start: int, end: int, *,
                   candidate_type: str = "CLAIM",
                   mapping_precision: str = "EXACT_CHARACTER",
                   case_id: str = "case-v5-2-development") -> ExtractionCandidate:
    text = document.text[start:end]
    return ExtractionCandidate(
        stable_id("candidate", document.document_id, str(start), str(end)),
        case_id, document.document_id, document.source_object_id, start, end,
        "BODY", candidate_type, text, {}, "test-provider", "1.0",
        {"extraction": 0.9}, mapping_precision, (), {"releasability": ["PUBLIC"]},
        now_utc(), "PENDING")


def candidate_over(text: str, needle: str, *, language: str = "en",
                   candidate_type: str = "CLAIM",
                   mapping_precision: str = "EXACT_CHARACTER"):
    """Build (document, candidate) for ``needle`` inside ``text``."""
    document = make_document(text, language=language)
    start = text.index(needle)
    return document, make_candidate(
        document, start, start + len(needle), candidate_type=candidate_type,
        mapping_precision=mapping_precision)
