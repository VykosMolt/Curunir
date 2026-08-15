"""Campaign C — typed enclosures, and why parity is the wrong test.

35 analyses reached selection whose role span opens a bracket or a quotation it
never closes: "(cardiacas,", "«Entro", "6 «The Madrid resolution on".  A span
that begins inside a parenthesis and ends outside it is not a constituent.

The obvious fix -- count the quote marks -- is wrong in four separate ways that
all occur in this corpus, and each has a fixture below: the French apostrophe is
a closing-quote character, an enumerator carries a parenthesis that never
opened, a citation may be truncated by extraction rather than malformed, and OCR
loses delimiters.  So the invariant is improper INTERSECTION of an ESTABLISHED
boundary, and where the boundary is not established nothing is concluded.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import enclosures as EN

pytestmark = pytest.mark.no_db


def _span_of(text: str, phrase: str) -> tuple[int, int]:
    start = text.index(phrase)
    return (start, start + len(phrase))


def _verdict(text: str, phrase: str) -> str:
    return EN.assess_span(_span_of(text, phrase), text)["verdict"]


# --- what must be refused ---------------------------------------------------

def test_a_span_that_opens_a_parenthesis_and_abandons_it_is_refused():
    text = "los pacientes con enfermedades (cardiacas, pulmonares) y otras"
    assert _verdict(text, "(cardiacas,") == "IMPROPERLY_INTERSECTS_ENCLOSURE"


def test_a_span_that_cuts_a_guillemet_quotation_is_refused():
    text = "le parole: «Entro lo stesso termine» sono sostituite"
    assert _verdict(text, "«Entro") == "IMPROPERLY_INTERSECTS_ENCLOSURE"


def test_a_span_that_starts_outside_and_ends_inside_is_refused():
    text = "6 «The Madrid resolution on organ transplantation» was adopted"
    assert _verdict(text, "6 «The Madrid resolution on") == \
        "IMPROPERLY_INTERSECTS_ENCLOSURE"


# --- what must NOT be refused -----------------------------------------------

def test_the_french_apostrophe_is_not_a_closing_quote():
    """U+2019 is the recommended apostrophe of French typography.

    Parity convicts every elision: "L'expression", "aujourd'hui".  This fixture
    is the reason the module tests intersection rather than counting.
    """
    text = "3 L’expression anglaise « emergency care » désigne les soins"
    assert _verdict(text, "3 L’expression") == "NO_ENCLOSURE_VIOLATION"


def test_an_enumerator_parenthesis_never_opened():
    """"g)" is a list marker; its ")" closes nothing."""
    text = "g) al comma 13, le parole sono sostituite"
    assert _verdict(text, "g) al comma 13,") == "NO_ENCLOSURE_VIOLATION"


def test_a_span_containing_a_whole_quotation_is_one_constituent():
    text = "L’expression anglaise « emergency care » désigne les soins urgents"
    assert _verdict(text, "L’expression anglaise « emergency care »") == \
        "NO_ENCLOSURE_VIOLATION"


def test_a_span_wholly_inside_a_quotation_is_admissible():
    text = "le parole: «Entro lo stesso termine» sono sostituite"
    assert _verdict(text, "lo stesso termine") == "NO_ENCLOSURE_VIOLATION"


def test_a_span_disjoint_from_every_enclosure_is_admissible():
    text = "le parole: «Entro lo stesso termine» sono sostituite oggi"
    assert _verdict(text, "sono sostituite") == "NO_ENCLOSURE_VIOLATION"


def test_an_unterminated_opener_concludes_nothing():
    """Truncation and OCR damage are not malformation.

    An opener with no closer means the extract was cut or the delimiter was
    lost.  A layer that cannot establish a boundary must not reject on it.
    """
    text = "folgenden Eid: „Ich schwöre, dass ich meine Kraft"
    found = EN.build_enclosures(text)
    unterminated = [e for e in found if not e.established]
    assert unterminated
    assert all(e.ocr_uncertainty for e in unterminated)
    assert _verdict(text, "dass ich meine Kraft") == "NO_ENCLOSURE_VIOLATION"


def test_nested_parentheses_are_tracked_by_depth():
    text = "the measure (see Article 5 (as amended) below) applies"
    found = EN.build_enclosures(text)
    assert len(found) >= 2
    assert max(e.nesting_depth for e in found) >= 1


# --- typed roles ------------------------------------------------------------

def test_a_metalinguistic_mention_is_typed_as_such():
    """A finite-looking token inside a mention is not the matrix predicate."""
    text = 'The word "is" appears in the provision twice.'
    found = EN.build_enclosures(text)
    assert any(e.quoted_content_role == "METALINGUISTIC_TOKEN" for e in found)


def test_a_title_is_typed_as_a_title():
    text = "the resolution «The Madrid resolution on organ transplantation» applies"
    found = EN.build_enclosures(text)
    assert any(e.quoted_content_role == "TITLE" for e in found)


def test_direct_speech_is_typed_from_its_reporting_verb():
    text = 'The delegate said "the report is adopted" and sat down.'
    found = EN.build_enclosures(text)
    assert any(e.quoted_content_role == "DIRECT_SPEECH" for e in found)


def test_a_citation_is_typed_from_its_shape():
    text = "the provision (Article 5 and Article 6) applies to carriers"
    found = EN.build_enclosures(text)
    assert any(e.quoted_content_role == "CITATION" for e in found)


def test_every_role_in_the_vocabulary_is_declared():
    for role in ("DIRECT_SPEECH", "TITLE", "TERM_MENTION", "DEFINITION",
                 "CITATION", "EXAMPLE", "METALINGUISTIC_TOKEN",
                 "UNKNOWN_QUOTED_CONTENT"):
        assert role in EN.QUOTED_CONTENT_ROLES


def test_an_unknown_role_is_refused_at_construction():
    with pytest.raises(EN.EnclosureError):
        EN.Enclosure(enclosure_id="e", region_id="r", enclosure_type="PARENTHESIS",
                     open_offset=0, close_offset=4, open_event="(",
                     close_event=")", nesting_depth=0,
                     quoted_content_role="VIBES", source_convention="ASCII")


def test_an_unknown_enclosure_type_is_refused_at_construction():
    with pytest.raises(EN.EnclosureError):
        EN.Enclosure(enclosure_id="e", region_id="r", enclosure_type="SQUIGGLE",
                     open_offset=0, close_offset=4, open_event="(",
                     close_event=")", nesting_depth=0,
                     quoted_content_role="CITATION", source_convention="ASCII")


# --- conventions ------------------------------------------------------------

def test_german_low_high_quotation_marks_are_paired():
    text = "der Begriff „Verarbeitung“ bezeichnet jeden Vorgang"
    found = EN.build_enclosures(text)
    german = [e for e in found if e.enclosure_type == "GERMAN_QUOTE"]
    assert german and german[0].established


def test_guillemets_are_paired():
    text = "les mots « emergency care » sont remplacés"
    found = EN.build_enclosures(text)
    assert any(e.enclosure_type == "GUILLEMET" and e.established for e in found)


def test_straight_quotes_are_paired_by_parity():
    text = 'the phrase "organ transplantation" is used throughout'
    found = EN.build_enclosures(text)
    assert any(e.enclosure_type == "STRAIGHT_QUOTE" and e.established
               for e in found)


def test_the_invariant_is_not_quote_mark_parity():
    """The module must not decide anything by counting delimiters."""
    import inspect
    body = inspect.getsource(EN.assess_span) + inspect.getsource(
        EN.intersection_state)
    assert "count(" not in body
    assert "% 2" not in body
