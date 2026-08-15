"""V5.1 extraction capability tests — synthetic neutral fixtures only."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from curunir_operational.v4.models import (
    DerivativeMapping, ExtractionCandidate, NormalizedDocument, sha256, stable_id,
)
from curunir_operational.v5_1.extraction import (
    AdmissionResult, ContextExpansion, SemanticParse, admit_candidate,
    deduplicate, expand_context, parse_semantics,
)

pytestmark = pytest.mark.no_db

_TIME = "2026-07-24T00:00:00+00:00"


def _doc(text: str, language: str = "en") -> NormalizedDocument:
    source_hash = sha256(text.encode("utf-8"))
    derivative_hash = sha256(text)
    source_id = stable_id("source-object", source_hash)
    mapping = DerivativeMapping(
        stable_id("mapping", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "memory://fixture", 0, len(text), "EXACT_CHARACTER")
    return NormalizedDocument(
        stable_id("document", source_id, derivative_hash), source_id, source_hash,
        derivative_hash, "fixture-parser", "test-1", language, text,
        (("BODY", 0, len(text)),), (), (mapping,), (), (), _TIME)


def _cand(document: NormalizedDocument, start: int, end: int, ctype: str,
          page: str = "BODY", precision: str = "EXACT_CHARACTER") -> ExtractionCandidate:
    return ExtractionCandidate(
        stable_id("candidate", document.document_id, start, end, ctype, page),
        "case-fixture", document.document_id, document.source_object_id, start, end,
        page, ctype, document.text[start:end], {}, "fixture-provider", "1.0",
        {"heuristic": 1.0}, precision, (), {"releasability": ["PUBLIC"]}, _TIME,
        "UNREVIEWED")


def _span(document: NormalizedDocument, needle: str) -> tuple[int, int]:
    start = document.text.index(needle)
    return start, start + len(needle)


# --- polarity ---------------------------------------------------------------

def test_negated_claim_polarity_preserved():
    parse = parse_semantics(
        "The registry does not operate a biometric matching service.", "", "en")
    assert parse.polarity == "NEGATIVE"
    assert parse.finite_clause


def test_double_negation_guard():
    parse = parse_semantics("The agency does not deny the outage report.", "", "en")
    assert parse.polarity == "POSITIVE"


def test_german_negation():
    parse = parse_semantics(
        "Die Landesbehörde hat das Meldesystem am 15. März 2027 nicht eingeführt.",
        "", "de")
    assert parse.polarity == "NEGATIVE"


def test_french_negation():
    parse = parse_semantics("L'autorité régionale n'a pas validé le registre.", "", "fr")
    assert parse.polarity == "NEGATIVE" and parse.finite_clause


def test_spanish_negation():
    parse = parse_semantics(
        "El ayuntamiento no renovó la licencia del sistema de sensores.", "", "es")
    assert parse.polarity == "NEGATIVE"


# --- modality and attribution ----------------------------------------------

def test_reported_vs_asserted_modality():
    attributed = parse_semantics(
        "According to the transport ministry, the tunnel reopened in March 2026.",
        "", "en")
    asserted = parse_semantics("The tunnel reopened in March 2026.", "", "en")
    bare = parse_semantics("The facility is reportedly closed.", "", "en")
    assert attributed.modality == "ATTRIBUTED"
    assert asserted.modality == "ASSERTED"
    assert bare.modality == "REPORTED" and bare.attribution is None


def test_attribution_capture_multilingual():
    english = parse_semantics(
        "According to the transport ministry, the tunnel reopened in March 2026.",
        "", "en")
    spanish = parse_semantics(
        "Según la oficina regional, el sistema está en funcionamiento.", "", "es")
    assert english.attribution == "the transport ministry"
    assert spanish.attribution == "la oficina regional"
    assert spanish.modality == "ATTRIBUTED"


def test_plan_vs_deployment():
    planned = parse_semantics(
        "The municipality plans to install a document scanner network.", "", "en")
    deployed = parse_semantics(
        "The document scanner network has been deployed across the municipality.",
        "", "en")
    assert planned.modality == "PLANNED"
    assert deployed.modality == "DEPLOYED"


def test_vendor_description():
    parse = parse_semantics(
        "The manufacturer describes the scanner as fully automated.", "", "en")
    assert parse.modality == "VENDOR_DESCRIBED"


def test_exercise_modality_german():
    parse = parse_semantics(
        "Die Behörde testete das Warnsystem während einer Übung im April 2026.",
        "", "de")
    assert parse.modality == "EXERCISED"
    assert parse.temporal_scope == ("2026-04-01", "2026-04-30")


# --- temporal scope ---------------------------------------------------------

def test_temporal_from_german_date():
    parse = parse_semantics(
        "Die Landesbehörde hat das Meldesystem am 15. März 2027 nicht eingeführt.",
        "", "de")
    assert parse.temporal_scope == ("2027-03-15", "2027-03-15")
    assert parse.temporal_basis == "EXPLICIT_DATE"


def test_temporal_from_french_date():
    parse = parse_semantics(
        "Selon l’agence régionale, le portail sera fermé le 3 juillet 2026.", "", "fr")
    assert parse.temporal_scope == ("2026-07-03", "2026-07-03")
    assert parse.temporal_basis == "EXPLICIT_DATE"
    assert parse.attribution == "l’agence régionale"


def test_temporal_from_spanish_date():
    parse = parse_semantics(
        "La oficina municipal publicó el informe el 12 de octubre de 2025.", "", "es")
    assert parse.temporal_scope == ("2025-10-12", "2025-10-12")
    assert parse.temporal_basis == "EXPLICIT_DATE"


def test_quarter_period():
    parse = parse_semantics("The reporting portal will launch in Q2 2026.", "", "en")
    assert parse.temporal_scope == ("2026-04-01", "2026-06-30")
    assert parse.temporal_basis == "EXPLICIT_PERIOD"
    assert parse.modality == "PLANNED"


def test_unstated_temporal_never_invented():
    parse = parse_semantics("The archive service is storing registration files.", "", "en")
    assert parse.temporal_scope == (None, None)
    assert parse.temporal_basis == "UNSTATED"
    assert "temporal_scope" in parse.completeness_missing


def test_document_date_fallback_is_labelled():
    parse = parse_semantics("The portal is unavailable.", "", "en",
                            document_date="2026-02-10")
    assert parse.temporal_scope == ("2026-02-10", "2026-02-10")
    assert parse.temporal_basis == "DOCUMENT_DATE_FALLBACK"


def test_geographic_scope_capture():
    parse = parse_semantics(
        "The monitoring station operates in Northland during winter.", "", "en")
    assert parse.geographic_scope == ("Northland",)


# --- numeric guard and context expansion ------------------------------------

def test_bare_number_lists_missing_unit():
    parse = parse_semantics("Coverage of the alert network increased to 45", "", "en")
    assert parse.numeric_values == ("45",)
    assert "unit" in parse.completeness_missing


def test_numeric_unit_truncation_expansion_recovers_unit():
    text = "Coverage of the alert network increased to 45 percent of districts during 2025."
    document = _doc(text)
    start, end = _span(document, "Coverage of the alert network increased to 45")
    candidate = _cand(document, start, end, "CLAIM_CANDIDATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    assert result.stage == "SEMANTICALLY_PARSED"
    assert "unit" in result.expansion_request
    expansion = expand_context(document, start, end, result.expansion_request)
    assert "unit" in expansion.recovered_fields
    assert expansion.original_text == candidate.original_text
    assert expansion.expanded_text.startswith(expansion.original_text[:8])


def test_truncated_claim_boundary_gets_expansion_then_recovers():
    text = "The oversight board has suspended the pilot programme."
    document = _doc(text)
    start, end = _span(document, "The oversight board has")
    candidate = _cand(document, start, end, "CLAIM_CANDIDATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    assert result.stage == "SEMANTICALLY_PARSED"
    assert "object_or_value" in result.expansion_request
    expansion = expand_context(document, start, end, result.expansion_request)
    assert "object_or_value" in expansion.recovered_fields
    assert expansion.reparse.object_or_value


def test_expansion_never_replaces_original_span():
    document = _doc("First sentence stands alone. The board has approved the budget.")
    start, end = _span(document, "The board has")
    expansion = expand_context(document, start, end, ("object_or_value",))
    assert (expansion.original_start, expansion.original_end) == (start, end)
    with pytest.raises(ValueError, match="contain the original"):
        dataclasses.replace(expansion, expanded_start=start + 2)


# --- admission pipeline -----------------------------------------------------

def test_noun_phrase_claim_rejected_with_reclassification():
    document = _doc("The Central Licensing Directorate. It handles permits.")
    start, end = _span(document, "The Central Licensing Directorate")
    candidate = _cand(document, start, end, "CLAIM_CANDIDATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    assert result.stage == "REJECTED"
    assert result.reclassification_suggestion == "ENTITY_MENTION"
    assert result.capability_failure is None


def test_accepted_claim_is_analytically_material():
    document = _doc("The licensing authority operates the appeals portal.")
    candidate = _cand(document, 0, len(document.text), "CLAIM_CANDIDATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    assert result.stage == "ACCEPTED_CANDIDATE"
    assert result.candidate_function == "ANALYTICALLY_MATERIAL"
    assert result.expansion_request == ()


def test_page_number_span_quarantined():
    document = _doc("7\nThe registry operates the permit desk.")
    candidate = _cand(document, 0, 1, "CLAIM_CANDIDATE")
    layout = SimpleNamespace(quarantined_regions=((0, 1, "PAGE_NUMBER"),))
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, layout, parse)
    assert result.stage == "QUARANTINED"
    assert result.candidate_function == "NAVIGATIONAL_METADATA"


def test_structural_region_quarantined_as_document_structure():
    document = _doc("col1 col2 joined fragment\nThe registry operates the permit desk.")
    candidate = _cand(document, 0, 25, "CLAIM_CANDIDATE")
    layout = SimpleNamespace(quarantined_regions=((0, 25, "COLUMN_JOIN_FRAGMENT"),))
    result = admit_candidate(candidate, document, layout,
                             parse_semantics(candidate.original_text, "", "en"))
    assert result.stage == "QUARANTINED"
    assert result.candidate_function == "DOCUMENT_STRUCTURE"


def test_numeric_noise_span_rejected():
    document = _doc("— 7 —\nThe registry operates the permit desk.")
    candidate = _cand(document, 0, 5, "CLAIM_CANDIDATE")
    result = admit_candidate(candidate, document, None,
                             parse_semantics(candidate.original_text, "", "en"))
    assert result.stage == "REJECTED"
    assert result.candidate_function == "NOISE"


def test_capability_failure_on_sufficient_but_unparsed():
    document = _doc("Is regulated by the regional supervisory authority.")
    candidate = _cand(document, 0, len(document.text), "CLAIM_CANDIDATE")
    parse = parse_semantics(candidate.original_text, "", "en")
    result = admit_candidate(candidate, document, None, parse)
    assert result.stage == "REJECTED"
    assert result.capability_failure is not None
    assert result.capability_failure.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert result.capability_failure.capability_failure_class == "PARSER_INCAPABILITY"


def test_unknown_candidate_type_is_semantic_type_error():
    document = _doc("The registry operates the permit desk.")
    candidate = _cand(document, 0, len(document.text), "MOOD_CANDIDATE")
    result = admit_candidate(candidate, document, None,
                             parse_semantics(candidate.original_text, "", "en"))
    assert result.capability_failure is not None
    assert result.capability_failure.capability_failure_class == "SEMANTIC_TYPE_ERROR"


def test_span_fabrication_raises():
    document = _doc("The registry operates the permit desk.")
    candidate = dataclasses.replace(
        _cand(document, 0, 12, "CLAIM_CANDIDATE"), original_text="tampered text")
    with pytest.raises(ValueError, match="fabrication"):
        admit_candidate(candidate, document, None,
                        parse_semantics("tampered text", "", "en"))


def test_mapping_precision_gate_for_quotations():
    text = "\"The rollout is complete,\" the project director said."
    document = _doc(text)
    parse = parse_semantics(text, "", "en")
    assert parse.attribution is not None and "director" in parse.attribution
    approximate = _cand(document, 0, len(text), "QUOTATION_CANDIDATE",
                        precision="APPROXIMATE_SECTION")
    exact = _cand(document, 0, len(text), "QUOTATION_CANDIDATE")
    assert admit_candidate(approximate, document, None, parse).stage == "EVIDENCE_BOUND"
    assert admit_candidate(exact, document, None, parse).stage == "ACCEPTED_CANDIDATE"


# --- deduplication ----------------------------------------------------------

def _claim_pair(document, needle, occurrence=0, page="BODY"):
    start = -1
    for _ in range(occurrence + 1):
        start = document.text.index(needle, start + 1)
    candidate = _cand(document, start, start + len(needle), "CLAIM_CANDIDATE", page=page)
    return candidate, parse_semantics(candidate.original_text, "", "en")


def test_dedup_marks_later_same_assertion_duplicate():
    text = ("The permit office issues the digital badges. A separate remark. "
            "The permit office issues the digital badges.")
    document = _doc(text)
    first = _claim_pair(document, "The permit office issues the digital badges", 0)
    second = _claim_pair(document, "The permit office issues the digital badges", 1)
    duplicates = deduplicate([second, first])
    assert duplicates == {second[0].candidate_id: first[0].candidate_id}


def test_dedup_keeps_distinct_polarity():
    text = ("The permit office issues the digital badges. "
            "The permit office does not issue the digital badges.")
    document = _doc(text)
    positive = _claim_pair(document, "The permit office issues the digital badges")
    negative = _claim_pair(document, "The permit office does not issue the digital badges")
    assert positive[1].polarity != negative[1].polarity
    assert deduplicate([positive, negative]) == {}


def test_dedup_keeps_distinct_pages():
    text = ("The permit office issues the digital badges. "
            "The permit office issues the digital badges.")
    document = _doc(text)
    page_one = _claim_pair(document, "The permit office issues the digital badges", 0,
                           page="PAGE:1")
    page_two = _claim_pair(document, "The permit office issues the digital badges", 1,
                           page="PAGE:2")
    assert deduplicate([page_one, page_two]) == {}


# --- record guards ----------------------------------------------------------

def test_semantic_parse_guards():
    good = parse_semantics("The registry operates the permit desk.", "", "en")
    with pytest.raises(ValueError, match="polarity"):
        dataclasses.replace(good, polarity="MAYBE")
    with pytest.raises(ValueError, match="ATTRIBUTED"):
        dataclasses.replace(good, modality="ATTRIBUTED", attribution=None)
    with pytest.raises(ValueError, match="invented"):
        dataclasses.replace(good, temporal_basis="UNSTATED",
                            temporal_scope=("2026-01-01", None))


def test_admission_result_guards():
    document = _doc("The licensing authority operates the appeals portal.")
    candidate = _cand(document, 0, len(document.text), "CLAIM_CANDIDATE")
    accepted = admit_candidate(candidate, document, None,
                               parse_semantics(candidate.original_text, "", "en"))
    with pytest.raises(ValueError, match="expansion"):
        dataclasses.replace(accepted, expansion_request=("unit",))
    with pytest.raises(ValueError, match="structural"):
        dataclasses.replace(accepted, stage="QUARANTINED")
    with pytest.raises(ValueError, match="analytical"):
        dataclasses.replace(accepted, candidate_function="NOISE")


def test_expansion_input_guards():
    document = _doc("The board has approved the budget.")
    with pytest.raises(ValueError, match="needed"):
        expand_context(document, 0, 10, ())
    with pytest.raises(ValueError, match="bounded"):
        expand_context(document, 0, 10, ("unit",), max_radius=0)
