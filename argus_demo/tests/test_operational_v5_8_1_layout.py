"""PDF reading-order integrity (V5.8.1 sections 8-11).

The mandatory fixture is the real interleaved-column packet: three independent
reviewer seats judged unit v5-8-1-d25unit-ac925d636878ac259fadcbeb unfaithful
and an adjudicator classified it a packet construction defect, because the
extracted line reads "the prison regime is tion is examined" — two printed
columns merged line by line.

These tests work from geometry, not from prose, because the corrupted result is
perfectly grammatical and only the layout reveals it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from curunir_operational.v5_8_1 import layout as LY
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db

ART = (pathlib.Path(__file__).resolve().parents[1] / "artifacts"
       / "curunir_autonomous_completion_v5_8_1_20260725")


def _custody():
    rows = []
    for path in ART.glob("08_acquisition_attempts/*_source_custody.jsonl"):
        rows += [json.loads(line) for line in path.open()]
    return rows


def _pdf_bytes(family: str):
    for row in _custody():
        if row.get("declared_format") != "PDF" or row.get("access_result") != "OK":
            continue
        if row["source_family_id"] != family:
            continue
        path = ART / row["custody_path"]
        if path.exists():
            return path.read_bytes()
    return None


needs_corpus = pytest.mark.skipif(
    _pdf_bytes("rm.coe.int") is None,
    reason="PDF custody not present in this checkout")


# --- synthetic geometry: the mechanism, independent of any corpus ----------

def _word(text, xmin, ymin, xmax, ymax):
    return LY.WordBox(text, xmin, ymin, xmax, ymax)


def _two_column_page(rows: int = 20):
    words = []
    for row in range(rows):
        y = 100 + row * 20
        words.append(_word(f"left{row}", 50, y, 240, y + 12))
        words.append(_word(f"right{row}", 330, y, 520, y + 12))
    return words


def test_a_two_column_page_is_detected_from_its_gutter():
    words = _two_column_page()
    bounds, _ = LY.detect_columns(words, 595.0, 842.0)
    assert len(bounds) == 2


def test_a_single_column_page_reports_one_column():
    """Neighbouring negative: the gutter test must not split ordinary prose."""
    words = [_word(f"w{n}", 50 + (n % 8) * 60, 100 + (n // 8) * 20,
                   105 + (n % 8) * 60, 112 + (n // 8) * 20) for n in range(80)]
    bounds, _ = LY.detect_columns(words, 595.0, 842.0)
    assert len(bounds) == 1


def test_column_major_order_does_not_interleave():
    page = LY.assess_page(1, 595.0, 842.0, _two_column_page(), "doc")
    assert page.detected_column_count == 2
    ordered = page.reading_order_text.split()
    # every left-column word precedes every right-column word
    assert ordered.index("left19") < ordered.index("right0")
    # and the flat line-major text is the interleaving we must not use
    flat = page.flat_text.split()
    assert flat.index("right0") < flat.index("left19")


def test_line_major_interleaving_is_detected_not_assumed():
    page = LY.assess_page(1, 595.0, 842.0, _two_column_page(), "doc")
    assert page.cross_column_line_count == page.line_count
    assert page.state == "READING_ORDER_RECOVERABLE"


# --- the corpus fixtures ---------------------------------------------------

@needs_corpus
def test_the_known_interleaved_manifestation_is_flagged():
    assessment = LY.assess(_pdf_bytes("rm.coe.int"), document_id="rm-coe")
    assert assessment.detected_column_count >= 2
    assert assessment.state in ("READING_ORDER_RECOVERABLE",
                                "LAYOUT_RECONSTRUCTION_REQUIRED")
    assert any(page.cross_column_line_count > 0 for page in assessment.pages)


@needs_corpus
def test_a_single_column_manifestation_is_established():
    """Neighbouring negative: the gate must not condemn ordinary PDFs."""
    assessment = LY.assess(_pdf_bytes("apps.who.int"), document_id="who-gb")
    assert assessment.state == "READING_ORDER_ESTABLISHED"
    assert assessment.admits_propositions


@needs_corpus
def test_reconstruction_changes_the_text_it_would_have_used():
    assessment = LY.assess(_pdf_bytes("rm.coe.int"), document_id="rm-coe")
    multi = [p for p in assessment.pages if p.cross_column_line_count > 5]
    assert multi, "expected at least one interleaved page"
    page = multi[0]
    assert page.reading_order_text != page.flat_text


# --- production refusal ----------------------------------------------------

def test_unsound_layout_is_refused_by_the_binder():
    context = ST.StructuralClauseContext(
        context_id="c", document_id="d", manifestation_id="m",
        current_region_id="r", current_proposition_id="p",
        reading_order_state="PACKET_CONSTRUCTION_DEFECT")
    result = V2.bind(candidate_id="u", language="en",
                     text="the prison regime is tion is examined",
                     structural_context=context)
    assert result.role_binding_state == "ROLE_BINDING_INVALID"
    assert result.final_extraction_disposition == "REJECTED"
    assert "reading order" in result.binding_reason


def test_reconstruction_required_is_also_refused():
    context = ST.StructuralClauseContext(
        context_id="c", document_id="d", manifestation_id="m",
        current_region_id="r", current_proposition_id="p",
        reading_order_state="LAYOUT_RECONSTRUCTION_REQUIRED")
    result = V2.bind(candidate_id="u", language="en",
                     text="The Assembly adopted the report.",
                     structural_context=context)
    assert result.role_binding_state == "ROLE_BINDING_INVALID"


def test_sound_layout_is_not_refused():
    """Neighbouring negative: the refusal is about unsound order only."""
    context = ST.StructuralClauseContext(
        context_id="c", document_id="d", manifestation_id="m",
        current_region_id="r", current_proposition_id="p",
        reading_order_state="READING_ORDER_ESTABLISHED")
    result = V2.bind(candidate_id="u", language="en",
                     text="The Assembly adopted the report.",
                     structural_context=context)
    assert result.role_binding_state != "ROLE_BINDING_INVALID"


def test_grammar_may_not_repair_layout():
    """A merged line is grammatical; only geometry exposes it."""
    merged = "the prison regime is tion is examined"
    without_layout = V2.bind(candidate_id="u", language="en", text=merged)
    # with no layout knowledge the binder has no basis to refuse on those grounds
    assert without_layout.binding_reason.count("reading order") == 0
    context = ST.StructuralClauseContext(
        context_id="c", document_id="d", manifestation_id="m",
        current_region_id="r", current_proposition_id="p",
        reading_order_state="PACKET_CONSTRUCTION_DEFECT")
    with_layout = V2.bind(candidate_id="u", language="en", text=merged,
                          structural_context=context)
    assert with_layout.role_binding_state == "ROLE_BINDING_INVALID"


# --- attacks ---------------------------------------------------------------

def test_attack_empty_geometry_is_a_construction_defect():
    assessment = LY.assess(b"%PDF-1.4 not really a pdf", document_id="broken")
    assert assessment.state == "PACKET_CONSTRUCTION_DEFECT"
    assert not assessment.admits_propositions


def test_attack_low_confidence_never_admits_propositions():
    assessment = LY.DocumentReadingOrderAssessment(
        assessment_id="a", document_id="d", manifestation_id="m", pages=(),
        detected_column_count=2, overall_layout_confidence=0.4,
        cross_page_continuity_confidence=0.4,
        state="LAYOUT_RECONSTRUCTION_REQUIRED", repair_actions=(),
        source_lineage="x", recorded_time="2026-07-26T00:00:00+00:00")
    assert not assessment.admits_propositions


def test_attack_unknown_state_is_refused_at_construction():
    with pytest.raises(ValueError):
        LY.DocumentReadingOrderAssessment(
            assessment_id="a", document_id="d", manifestation_id="m", pages=(),
            detected_column_count=1, overall_layout_confidence=0.9,
            cross_page_continuity_confidence=0.9, state="LOOKS_FINE",
            repair_actions=(), source_lineage="x",
            recorded_time="2026-07-26T00:00:00+00:00")
