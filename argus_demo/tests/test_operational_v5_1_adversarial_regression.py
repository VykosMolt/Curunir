"""Regression tests encoding the V5.1 adversarial-review bypasses.

Each test reproduces a verified guard bypass from the Phase 3 adversarial
correctness review and asserts the repaired behavior.  Synthetic neutral
fixtures only.
"""
from __future__ import annotations

import pytest

from curunir_operational.v4.models import (
    DerivativeMapping, ExtractionCandidate, NormalizedDocument, sha256, stable_id,
)
from curunir_operational.v5_1.dependence import (
    DependenceAssessment, DependenceExplanation, dependence_signal,
)
from curunir_operational.v5_1.extraction import admit_candidate, parse_semantics
from curunir_operational.v5_1.layout import analyze_layout, reading_order_failure
from curunir_operational.v5_1.models import EvidenceRef, capability_outcome, stable_id as sid
from curunir_operational.v5_1.packets import build_packet, leakage_scan
from curunir_operational.v5_1.report_planning import decompose_sentence

pytestmark = pytest.mark.no_db

_TIME = "2026-07-24T00:00:00+00:00"


def _doc(text: str, pages=(), language: str = "en") -> NormalizedDocument:
    source_hash = sha256(text.encode("utf-8"))
    derivative_hash = sha256(text)
    source_id = stable_id("source-object", source_hash)
    mapping = DerivativeMapping(
        stable_id("mapping", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "memory://fixture", 0, len(text), "EXACT_CHARACTER")
    return NormalizedDocument(
        stable_id("document", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "pdftotext-layout", "test-1", language, text,
        (("BODY", 0, len(text)),), tuple(pages), (mapping,), (), (), _TIME)


def _cand(document: NormalizedDocument, start: int, end: int,
          ctype: str) -> ExtractionCandidate:
    return ExtractionCandidate(
        stable_id("candidate", document.document_id, start, end, ctype),
        "case-fixture", document.document_id, document.source_object_id, start, end,
        "BODY", ctype, document.text[start:end], {}, "fixture-provider", "1.0",
        {"heuristic": 1.0}, "EXACT_CHARACTER", (), {"releasability": ["PUBLIC"]},
        _TIME, "UNREVIEWED")


def _paged_document() -> NormalizedDocument:
    pages = []
    parts = []
    cursor = 0
    for number in range(1, 5):
        page = (
            "HARBOUR DISTRICT WEEKLY CIRCULAR\n"
            f"The maintenance committee approved the dredging schedule on 3 May 2027 "
            f"for basin {number}.\n"
            f"Page {number} of 4\n")
        parts.append(page)
        pages.append((number, cursor, cursor + len(page)))
        cursor += len(page)
    return _doc("".join(parts), pages=pages)


def test_real_layout_annotation_quarantines_page_furniture():
    """CRITICAL bypass: real analyze_layout regions were unreadable by
    extraction, so headers and page numbers became accepted candidates."""
    document = _paged_document()
    annotation = analyze_layout(document.text, document.pages,
                                "application/pdf", "pdftotext-layout")
    classes = {region_class for _s, _e, region_class in annotation.regions}
    assert "REPEATED_HEADER" in classes and "PAGE_NUMBER" in classes

    header_start = document.text.index("HARBOUR DISTRICT WEEKLY CIRCULAR")
    header = _cand(document, header_start,
                   header_start + len("HARBOUR DISTRICT WEEKLY CIRCULAR"), "CLAIM")
    header_parse = parse_semantics(header.original_text, "", "en")
    result = admit_candidate(header, document, annotation, header_parse)
    assert result.stage == "QUARANTINED"
    assert result.candidate_function in {"NAVIGATIONAL_METADATA", "DOCUMENT_STRUCTURE"}

    page_start = document.text.index("Page 2 of 4")
    page_candidate = _cand(document, page_start, page_start + len("Page 2 of 4"), "DATE")
    page_parse = parse_semantics(page_candidate.original_text, "", "en")
    page_result = admit_candidate(page_candidate, document, annotation, page_parse)
    assert page_result.stage == "QUARANTINED"
    assert page_result.candidate_function == "NAVIGATIONAL_METADATA"


def test_parse_must_be_bound_to_candidate_text():
    """HIGH bypass: a parse of unrelated text could be attached to any span."""
    document = _doc("the dredging schedule. The committee approved the plan on 3 May 2027.")
    start = document.text.index("the dredging schedule")
    candidate = _cand(document, start, start + len("the dredging schedule"), "CLAIM")
    unrelated = parse_semantics(
        "The committee approved the plan on 3 May 2027.", "", "en")
    with pytest.raises(ValueError, match="not bound"):
        admit_candidate(candidate, document, None, unrelated)


def _explanation(**overrides):
    values = dict(
        positive_evidence=("fabricated free text",), negative_evidence=(),
        unresolved_signals=(), alternative_explanations=(),
        confidence_dimensions={}, independence_affirmatively_supported=True,
        only_absence_of_discovered_dependence=False)
    values.update(overrides)
    return DependenceExplanation(**values)


def _resolved_outcome(subject: str):
    return capability_outcome(
        subject_kind="SOURCE_DEPENDENCE_RELATION", subject_id=subject,
        outcome="RESOLVED_CORRECTLY", rationale="fixture outcome for regression test")


def _independence_signal(kind: str, strength: str, suffix: str = "", **extra):
    return dependence_signal(
        kind=kind, strength=strength, detail=f"{kind.lower()} evidence {suffix}",
        evidence_refs=(EvidenceRef(kind, "src-a", None, f"{kind} ref {suffix}"),),
        **extra)


def _assert_unconstructible(signals, ident):
    with pytest.raises(ValueError, match="multi-signal rule|cannot coexist"):
        DependenceAssessment(
            sid("assessment", ident), "publication-a", "publication-b",
            "INDEPENDENCE_SUPPORTED", tuple(signals), _explanation(),
            _resolved_outcome("publication-a|publication-b"), _TIME)


def test_independence_supported_requires_typed_signals():
    """HIGH bypass: affirmative independence was constructible from free text."""
    _assert_unconstructible((), "no-signals")


def test_independence_multi_signal_rule_enforced_in_constructor():
    """Re-verify round: the constructor must enforce the FULL Section 14.2
    rule (>=2 signals, >=2 distinct kinds, >=1 STRONG), not a weaker check."""
    _assert_unconstructible(
        (_independence_signal("DISTINCT_REPORTING_DETAIL", "WEAK", "1"),),
        "single-weak")
    _assert_unconstructible(
        (_independence_signal("DISTINCT_REPORTING_DETAIL", "WEAK", "1"),
         _independence_signal("DISTINCT_REPORTING_DETAIL", "WEAK", "2")),
        "two-weak-same-kind")
    _assert_unconstructible(
        (_independence_signal("DISTINCT_REPORTING_DETAIL", "WEAK", "1"),
         _independence_signal("DISTINCT_AUTHORSHIP_EVIDENCE", "WEAK", "2")),
        "two-weak-distinct-no-strong")


def test_independence_blocked_by_surviving_dependence_signals():
    """Re-verify round: contested citations and un-negated timing signals
    block affirmative independence (they belong to DISPUTED/UNKNOWN)."""
    strong_pair = (
        _independence_signal("DISTINCT_AUTHORSHIP_EVIDENCE", "STRONG", "1"),
        _independence_signal("ON_THE_RECORD_ORIGINAL_INTERVIEW", "MODERATE", "2"))
    contested_citation = dependence_signal(
        kind="EXPLICIT_CITATION", strength="STRONG", detail="contested citation",
        evidence_refs=(EvidenceRef("EXPLICIT_CITATION", "src-a", None, "cite"),),
        contested=True, contest_basis="authorship disputed")
    _assert_unconstructible(strong_pair + (contested_citation,), "contested-citation")
    timing = dependence_signal(
        kind="PUBLICATION_TIMING", strength="WEAK", detail="published hours apart",
        evidence_refs=(EvidenceRef("PUBLICATION_TIMING", "src-a", None, "timing"),))
    _assert_unconstructible(strong_pair + (timing,), "unnegated-timing")


def test_independence_supported_constructible_with_full_rule():
    assessment = DependenceAssessment(
        sid("assessment", "valid"), "publication-a", "publication-b",
        "INDEPENDENCE_SUPPORTED",
        (_independence_signal("DISTINCT_AUTHORSHIP_EVIDENCE", "STRONG", "1"),
         _independence_signal("ON_THE_RECORD_ORIGINAL_INTERVIEW", "MODERATE", "2")),
        _explanation(), _resolved_outcome("publication-a|publication-b"), _TIME)
    assert assessment.state == "INDEPENDENCE_SUPPORTED"


_MENU = ("Permitted decisions: CORRECT, INCORRECT, PARTIALLY_CORRECT, AMBIGUOUS, "
         "CANNOT_ADJUDICATE, and EPISTEMICALLY_UNRESOLVABLE.")


def test_stratum_value_in_payload_blocks_build():
    """HIGH bypass: stratum text under renamed keys or inside values froze cleanly."""
    with pytest.raises(ValueError, match="leaks builder metadata"):
        build_packet(
            surface="SURFACE_3_FALSE_CORROBORATION_ACCURACY",
            material={"excerpts": [], "reviewer_instructions": _MENU,
                      "cohort": "HARD_NEGATIVE_CONFOUND"},
            context_class="CONTEXT_SUFFICIENT", stratum="HARD_NEGATIVE_CONFOUND")
    with pytest.raises(ValueError, match="leaks builder metadata"):
        build_packet(
            surface="SURFACE_3_FALSE_CORROBORATION_ACCURACY",
            material={"excerpts": [], "reviewer_instructions": _MENU,
                      "notes": "sampling stratum = tier three; reserve pool entry"},
            context_class="CONTEXT_SUFFICIENT", stratum="ordinary_pair")


def test_history_prose_with_menu_padding_still_flagged():
    """MEDIUM bypass: appending a label menu exempted answer-revealing prose."""
    payload = {"blinded_material": {
        "note": ("Prior reviewers found this one CANNOT_ADJUDICATE. "
                 "Labels available: CORRECT, INCORRECT, AMBIGUOUS, CANNOT_ADJUDICATE.")}}
    findings = leakage_scan(payload, "CANNOT_ADJUDICATE")
    assert any(finding["kind"] == "ANSWER_VERBATIM" for finding in findings)


def test_instruction_menu_and_definitions_remain_exempt():
    payload = {"blinded_material": {"reviewer_instructions": (
        _MENU + " Choose CANNOT_ADJUDICATE when you cannot reach a verdict "
        "for any other reason.")}}
    assert leakage_scan(payload, "CANNOT_ADJUDICATE") == ()


def test_stratum_separator_variants_blocked():
    """Re-verify round: underscore/hyphen/space variants of the sealed
    stratum must all be caught, in both directions."""
    for leaked in ("hard negative confound", "hard-negative-confound",
                   "HARD_NEGATIVE_CONFOUND"):
        with pytest.raises(ValueError, match="leaks builder metadata"):
            build_packet(
                surface="SURFACE_3_FALSE_CORROBORATION_ACCURACY",
                material={"excerpts": [], "reviewer_instructions": _MENU,
                          "note": f"grouping {leaked} applies"},
                context_class="CONTEXT_SUFFICIENT", stratum="HARD_NEGATIVE_CONFOUND")
    with pytest.raises(ValueError, match="leaks builder metadata"):
        build_packet(
            surface="SURFACE_3_FALSE_CORROBORATION_ACCURACY",
            material={"excerpts": [], "reviewer_instructions": _MENU,
                      "note": "grouping HARD_NEGATIVE_CONFOUND applies"},
            context_class="CONTEXT_SUFFICIENT", stratum="hard negative confound")


def test_plural_builder_vocabulary_blocked():
    with pytest.raises(ValueError, match="leaks builder metadata"):
        build_packet(
            surface="SURFACE_3_FALSE_CORROBORATION_ACCURACY",
            material={"excerpts": [], "reviewer_instructions": _MENU,
                      "note": "the answer keys are stored separately"},
            context_class="CONTEXT_SUFFICIENT", stratum="ordinary_pair")


def test_substring_label_padding_not_a_menu():
    """Re-verify round: CORRECT inside INCORRECT/PARTIALLY_CORRECT must not
    count toward the menu exemption, and a bare 'choose X' directive is a
    leak, not a definition."""
    for text in (
        "The right label here is CANNOT_ADJUDICATE, not INCORRECT or PARTIALLY_CORRECT.",
        "Our automated pre-screen suggests CANNOT_ADJUDICATE over INCORRECT "
        "and PARTIALLY_CORRECT.",
        "For this packet choose CANNOT_ADJUDICATE.",
    ):
        findings = leakage_scan({"blinded_material": {"note": text}},
                                "CANNOT_ADJUDICATE")
        assert any(finding["kind"] == "ANSWER_VERBATIM" for finding in findings), text


def _claim(claim_id: str, statement: str, state: str, modality: str = "ASSERTED"):
    return {
        "claim_id": claim_id, "normalized_statement": statement,
        "epistemic_state": state, "modality": modality,
        "temporal_scope": (None, None), "geographic_scope": (),
        "candidate_ids": (f"span-{claim_id}",),
        "evidence_basis_ids": (f"basis-{claim_id}",),
    }


def test_decomposition_is_order_independent_and_conservative():
    """HIGH bypass: claims[0] governed certainty, so claim order flipped results."""
    sentence = "The harbour authority approved the dredging schedule"
    supported = _claim("claim-1", "harbour authority approved dredging schedule",
                       "SUPPORTED")
    retracted = _claim("claim-2", "harbour authority approved dredging schedule",
                       "RETRACTED")
    forward = decompose_sentence(sentence, [supported, retracted])
    reverse = decompose_sentence(sentence, [retracted, supported])
    assert forward.status == reverse.status == "DECOMPOSED"
    first, second = forward.propositions[0], reverse.propositions[0]
    assert first.certainty == second.certainty == "UNRESOLVED"
    assert first.evidence_state == second.evidence_state == "RETRACTED"
    assert first.contradiction_state == second.contradiction_state == "RETRACTION"
    assert first.supporting_claim_ids == second.supporting_claim_ids


def test_empty_text_layer_is_parser_incapability():
    """LOW bypass: an empty text layer degraded to a warning string."""
    annotation = analyze_layout("", (), "application/pdf", "pdftotext-layout")
    failure = reading_order_failure(annotation, subject_id="document-x")
    assert failure is not None
    assert failure.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert failure.capability_failure_class == "PARSER_INCAPABILITY"


def test_zero_width_only_text_is_empty_layer():
    """Re-verify round: zero-width characters survive str.strip() but are
    still an empty text layer."""
    annotation = analyze_layout("​‌﻿⁠‍",
                                (), "application/pdf", "pdftotext-layout")
    assert "EMPTY_TEXT_LAYER" in annotation.warnings
    failure = reading_order_failure(annotation, subject_id="document-z")
    assert failure is not None
    assert failure.capability_failure_class == "PARSER_INCAPABILITY"


def test_unknown_epistemic_state_aggregation_raises():
    """Re-verify round: unknown states must fail loudly, never default."""
    bogus = _claim("claim-9", "harbour authority approved dredging schedule",
                   "SUPPORTED")
    bogus["epistemic_state"] = "TOTALLY_BOGUS_STATE"
    with pytest.raises(ValueError, match="unknown epistemic states"):
        decompose_sentence("The harbour authority approved the dredging schedule",
                           [bogus])
