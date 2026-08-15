from __future__ import annotations

from types import SimpleNamespace

import pytest

from curunir_operational.v4.models import NormalizedDocument
from curunir_operational.v5_1.layout import (
    LAYOUT_REGION_CLASSES, analyze_layout, ocr_disclosure, quarantine_decision,
    reading_order_failure,
)
from curunir_operational.v5_1.models import sha256

pytestmark = pytest.mark.no_db

PDF = ("application/pdf", "pdftotext-layout")


def paged(*page_texts):
    """Join page texts with form feeds and compute custody-shaped offsets."""
    text = "\f".join(page_texts)
    pages, start = [], 0
    for number, page in enumerate(page_texts, 1):
        pages.append((number, start, start + len(page)))
        start += len(page) + 1
    return text, tuple(pages)


def slices(annotation, text, region_class):
    return [text[start:end] for start, end, cls in annotation.regions if cls == region_class]


FRAGMENT_PAGE = ("the commission met with\n"
                 "delegates from several\n"
                 "member states to discuss\n"
                 "the annual budget and\n"
                 "related procurement items\n"
                 "before adjourning the\n"
                 "session until the next\n"
                 "quarterly meeting concludes")


def test_repeated_header_detected_on_four_pages():
    pages_text = [
        f"Quarterly Bulletin\nDistinct body sentence number {i} closes cleanly here.\n"
        f"Another unique body line {i} also terminates with punctuation."
        for i in range(1, 5)]
    text, pages = paged(*pages_text)
    annotation = analyze_layout(text, pages, *PDF)
    assert slices(annotation, text, "REPEATED_HEADER") == ["Quarterly Bulletin"] * 4


def test_repeated_footer_detected_on_four_pages():
    pages_text = [
        f"Unique body sentence number {i} closes with a period.\n"
        f"Second unique line {i} also closes with a period.\n"
        "Institut für öffentliche Aufzeichnungen"
        for i in range(1, 5)]
    text, pages = paged(*pages_text)
    annotation = analyze_layout(text, pages, *PDF)
    assert slices(annotation, text, "REPEATED_FOOTER") == ["Institut für öffentliche Aufzeichnungen"] * 4


def test_page_numbers_detected_in_three_languages():
    text, pages = paged(
        "Der erste Absatz endet ordnungsgemäß mit einem Punkt.\nSeite 1 von 3",
        "El segundo párrafo termina correctamente con un punto.\npágina 2 de 3",
        "Trzeci akapit kończy się prawidłowo kropką.\nstr. 3")
    annotation = analyze_layout(text, pages, *PDF)
    assert sorted(slices(annotation, text, "PAGE_NUMBER")) == sorted(
        ["Seite 1 von 3", "página 2 de 3", "str. 3"])


def test_bare_roman_and_decorated_page_numbers():
    text, pages = paged(
        "First body sentence concludes properly right here.\n2",
        "Second body sentence concludes properly right here.\nii",
        "Third body sentence concludes properly right here.\n- 4 -")
    annotation = analyze_layout(text, pages, *PDF)
    assert sorted(slices(annotation, text, "PAGE_NUMBER")) == sorted(["2", "ii", "- 4 -"])


def test_hyphenation_repair_span_math_exact():
    text, pages = paged("Die euro-\npäische Union plant inter-\nnationale Abkommen.")
    annotation = analyze_layout(text, pages, "text/plain", "stdlib-text")
    assert annotation.repair_spans == (
        (text.index("euro"), "europäische"),
        (text.index("inter"), "internationale"))
    for position, joined in annotation.repair_spans:
        first_fragment = text[position:text.index("-", position)]
        assert joined.startswith(first_fragment)
    assert annotation.text_hash == sha256(text)  # original text untouched


def test_hyphenation_ignores_uppercase_continuation():
    text, pages = paged("Der Müller-\nBericht wurde veröffentlicht.")
    annotation = analyze_layout(text, pages, "text/plain", "stdlib-text")
    assert annotation.repair_spans == ()


def test_cross_page_sentence_join_and_page_break_hyphenation():
    text, pages = paged(
        "The ministry announced the plan\nfor continental infra-\n",
        "structure of the network.\nIt was published later.")
    annotation = analyze_layout(text, pages, *PDF)
    assert annotation.cross_page_joins == ((1, 2),)
    assert (text.index("infra"), "infrastructure") in annotation.repair_spans


def test_no_cross_page_join_after_terminal_punctuation():
    text, pages = paged(
        "The programme was completed on time.",
        "Final numbers were published later.")
    annotation = analyze_layout(text, pages, *PDF)
    assert annotation.cross_page_joins == ()


def test_column_fragment_run_downgrades_reading_order():
    text, pages = paged(FRAGMENT_PAGE)
    annotation = analyze_layout(text, pages, *PDF)
    assert [cls for _, _, cls in annotation.regions] == ["COLUMN_FRAGMENT_SUSPECT"]
    assert annotation.fragment_fraction > 0.15
    assert annotation.reading_order_confidence == "UNCERTAIN"
    assert "COLUMN_FRAGMENT_DENSITY_EXCEEDS_LIMIT" in annotation.warnings


def test_small_fragment_run_keeps_high_confidence():
    body = "\n".join(
        f"This body sentence number {i:02d} ends with a full stop and stays long today."
        for i in range(1, 25))
    text, pages = paged(body, FRAGMENT_PAGE)
    annotation = analyze_layout(text, pages, *PDF)
    assert any(cls == "COLUMN_FRAGMENT_SUSPECT" for _, _, cls in annotation.regions)
    assert annotation.fragment_fraction <= 0.15
    assert annotation.reading_order_confidence == "HIGH"


def test_clean_plain_text_has_exact_reading_order():
    text, pages = paged("A single clean paragraph sits here. It ends with proper punctuation.")
    annotation = analyze_layout(text, pages, "text/plain", "stdlib-text")
    assert annotation.reading_order_confidence == "EXACT"
    assert annotation.regions == ((0, len(text), "BODY_TEXT"),)
    assert annotation.warnings == ()


def test_table_caption_decorative_and_footnote_classes():
    text, pages = paged(
        "Overview of procurement follows immediately.\n"
        "Table 3: Procurement volumes by supplier\n"
        "Item        Count       Value\n"
        "Alpha       12          340\n"
        "Beta        7           120\n"
        "* * *\n"
        "The closing body sentence appears here.\n"
        "[1] register entry for the annex")
    annotation = analyze_layout(text, pages, *PDF)
    assert slices(annotation, text, "CAPTION_LIKE") == ["Table 3: Procurement volumes by supplier"]
    assert slices(annotation, text, "DECORATIVE") == ["* * *"]
    assert slices(annotation, text, "FOOTNOTE_LIKE") == ["[1] register entry for the annex"]
    table = slices(annotation, text, "TABLE_LIKE")
    assert len(table) == 1 and table[0].startswith("Item") and table[0].endswith("120")


def test_regions_ordered_typed_and_annotation_deterministic():
    text, pages = paged(
        "Der erste Absatz endet ordnungsgemäß mit einem Punkt.\nSeite 1 von 3",
        "El segundo párrafo termina correctamente con un punto.\npágina 2 de 3",
        "Trzeci akapit kończy się prawidłowo kropką.\nstr. 3")
    first = analyze_layout(text, pages, *PDF)
    second = analyze_layout(text, pages, *PDF)
    cursor = 0
    for start, end, cls in first.regions:
        assert cls in LAYOUT_REGION_CLASSES and start >= cursor and end > start
        cursor = end
    assert first.annotation_id == second.annotation_id
    assert first.regions == second.regions and first.text_hash == second.text_hash


def test_quarantine_mapping_for_all_region_classes():
    for region_class in ("PAGE_NUMBER", "REPEATED_HEADER", "REPEATED_FOOTER"):
        decision = quarantine_decision(region_class)
        assert decision.admission_stage == "QUARANTINED"
        assert decision.candidate_function == "NAVIGATIONAL_METADATA"
        assert not decision.pending_context_expansion
    decorative = quarantine_decision("DECORATIVE")
    assert decorative.admission_stage == "QUARANTINED"
    assert decorative.candidate_function == "DOCUMENT_STRUCTURE"
    fragment = quarantine_decision("COLUMN_FRAGMENT_SUSPECT")
    assert fragment.admission_stage == "QUARANTINED" and fragment.pending_context_expansion
    body = quarantine_decision("BODY_TEXT")
    assert body.admission_stage == "STRUCTURALLY_VALID"
    assert body.candidate_function == "ANALYTICALLY_MATERIAL"
    table = quarantine_decision("TABLE_LIKE")
    assert table.admission_stage == "STRUCTURALLY_VALID"
    assert table.candidate_function == "SUPPORTING_DETAIL"


def test_quarantine_rejects_unknown_region_class():
    with pytest.raises(ValueError, match="region class"):
        quarantine_decision("SIDEBAR")


def test_ocr_disclosure_for_empty_text_layer():
    document = NormalizedDocument(
        "document-empty", "source-object-empty", sha256(b"raw-bytes"), sha256(""),
        "pdftotext-layout", "curunir-document-normalizer-v4", "de", "", (), (), (),
        ("EMPTY_PDF_TEXT_OCR_MAY_BE_REQUIRED",), (), "2026-07-24T00:00:00+00:00")
    disclosure = ocr_disclosure(document)
    assert disclosure.marker == "OCR_REQUIRED"
    assert disclosure.capability_failure.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert disclosure.capability_failure.capability_failure_class == "PARSER_INCAPABILITY"
    assert disclosure.capability_failure.subject_id == "document-empty"
    record = disclosure.to_record()
    assert record["capability_failure"]["capability_failure_class"] == "PARSER_INCAPABILITY"


def test_ocr_disclosure_rejects_present_text_layer():
    record = SimpleNamespace(text="A recovered sentence exists.", document_id="document-x",
                             source_object_id="source-object-x", parser="pdftotext-layout")
    with pytest.raises(ValueError, match="empty text layer"):
        ocr_disclosure(record)


def test_invalid_page_offsets_raise():
    with pytest.raises(ValueError, match="ordered"):
        analyze_layout("abcdefghijklmnopqrstuvwxy", ((1, 0, 10), (2, 5, 20)), *PDF)
    with pytest.raises(ValueError, match="bounds"):
        analyze_layout("short", ((1, 0, 999),), *PDF)
    with pytest.raises(ValueError, match="strictly increase"):
        analyze_layout("abcdefghij", ((2, 0, 4), (1, 5, 10)), *PDF)


def test_reading_order_failure_emitted_only_when_uncertain():
    fragment_text, fragment_pages = paged(FRAGMENT_PAGE)
    uncertain = analyze_layout(fragment_text, fragment_pages, *PDF)
    outcome = reading_order_failure(uncertain, subject_id="document-fragmented")
    assert outcome is not None
    assert outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert outcome.capability_failure_class == "READING_ORDER_LOSS"
    assert outcome.subject_id == "document-fragmented"
    clean_text, clean_pages = paged("A clean sentence ends here without any trouble.")
    confident = analyze_layout(clean_text, clean_pages, "text/plain", "stdlib-text")
    assert reading_order_failure(confident, subject_id="document-clean") is None
