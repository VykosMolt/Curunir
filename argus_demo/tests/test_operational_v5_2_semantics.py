"""Semantic resolver and candidate-lattice tests (contract Section 9).

Each of the three V5.1 Surface-1 failure mechanisms is asserted closed with
the span that produced it, and the boundary-reasoning cases Section 9.3
enumerates are asserted individually.
"""
from __future__ import annotations

import pytest

from conftest_v5_2_helpers import candidate_over, make_candidate, make_document
from curunir_operational.v5_2 import chrome
from curunir_operational.v5_2 import semantics as S

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Section 9.3 — segmentation
# ---------------------------------------------------------------------------

def test_abbreviation_does_not_end_a_sentence():
    text = ("A detailed description of adversarial testing (e.g. red teaming), "
            "model adaptations, including alignment and fine-tuning.")
    spans = S.segment_sentences(text)
    assert len(spans) == 1


@pytest.mark.parametrize("abbreviation", ["i.e.", "cf.", "Art.", "Nr.", "z.B.", "p.ej."])
def test_common_abbreviations_survive(abbreviation):
    text = f"The rule applies {abbreviation} to every provider in scope."
    assert len(S.segment_sentences(text)) == 1


def test_ordinal_full_stop_does_not_end_a_sentence():
    assert len(S.segment_sentences("Am 16. Januar 2026 wurde die Verordnung erlassen.")) == 1


def test_newline_ends_an_unpunctuated_heading():
    spans = S.segment_sentences("ANNEX XI\nThe annex applies to providers.")
    assert len(spans) == 2


# ---------------------------------------------------------------------------
# Section 9.1 — role recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,language,finite", [
    # V5.1 rejected this: German verb-second put a pronoun after the modal,
    # and the finite-verb test required a determiner there.
    ("BahnCard kaufen: Auf der Schnaeppchensuche sollte man auch darueber "
     "nachdenken, ob sich eine BahnCard lohnt.", "de", True),
    ("Stuttgart halted everything on its network.", "en", True),
    ("AI Factories leverage the supercomputing capacity of the undertaking.", "en", True),
    ("El Consejo adoptó el reglamento.", "es", True),
    ("La Commission a lancé un appel.", "fr", True),
    # A noun phrase governed by a preposition is not a clause.
    ("a detailed description of the measures put in place for the purpose of "
     "conducting internal testing", "en", False),
    ("Council Regulation (EU) 2026/150 of 16 January 2026", "en", False),
    ("proyectos con valor añadido de la UE.", "es", False),
])
def test_finite_clause_detection(text, language, finite):
    assert S.split_roles(text, language).finite_clause is finite


def test_non_referential_subject_is_reported():
    split = S.split_roles(
        "Damit ist die Bahn der Anbieter mit den besten Regeln.", "de")
    assert "NON_REFERENTIAL_SUBJECT" in split.warnings


# ---------------------------------------------------------------------------
# Section 9.2 — the lattice
# ---------------------------------------------------------------------------

def test_lattice_offers_alternative_boundaries():
    text = ("The Commission published a plan. It plans to deploy the system in "
            "2027 across all member states.")
    document, candidate = candidate_over(text, "the system in 2027")
    lattice = S.build_lattice(candidate, document)
    levels = {item.level for item in lattice.interpretations}
    assert {"NARROW", "SENTENCE"} <= levels
    assert lattice.selected_interpretation_id is not None
    assert len(lattice.interpretations) > 1


def test_losing_interpretations_are_retained():
    text = "The Council met. The Council adopted the regulation on 16 January 2026."
    document, candidate = candidate_over(
        text, "The Council adopted the regulation on 16 January 2026.")
    decision = S.resolve_candidate(candidate, document)
    assert decision.alternative_count >= 1


def test_a_wider_boundary_may_not_import_a_neighbouring_assertion():
    # The seed is already a whole sentence; widening could only borrow the
    # previous sentence's predicate, which would change what is asserted.
    text = "The programme is described below. EuroHPC Joint Undertaking"
    document, candidate = candidate_over(text, "EuroHPC Joint Undertaking")
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "REJECTED"


# ---------------------------------------------------------------------------
# Section 9.5 — the admission contract, per V5.1 failure mechanism
# ---------------------------------------------------------------------------

def test_german_modal_clause_is_admitted():
    text = ("BahnCard kaufen: Auf der Schnaeppchensuche sollte man auch darueber "
            "nachdenken, ob sich eine BahnCard lohnt. Fuer Kunden gilt das auch.")
    document, candidate = candidate_over(
        text,
        "BahnCard kaufen: Auf der Schnaeppchensuche sollte man auch darueber "
        "nachdenken, ob sich eine BahnCard lohnt.", language="de")
    assert S.resolve_candidate(candidate, document, language="de").stage == \
        "ACCEPTED_CANDIDATE"


def test_truncated_span_asks_for_context_rather_than_being_rejected():
    text = ("ANNEX XI\n2.\nWhere applicable, a detailed description of the measures "
            "put in place for the purpose of conducting adversarial testing (e.g. "
            "red teaming), model adaptations.\n3.\nWhere applicable, a description "
            "of the system architecture.")
    needle = ("Where applicable, a detailed description of the measures put in "
              "place for the purpose of conducting adversarial testing (e.")
    document, candidate = candidate_over(text, needle)
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "SEMANTICALLY_PARSED"
    assert decision.expansion_request


def test_navigational_furniture_is_quarantined_not_rejected():
    text = ("The undertaking runs the programme.\n"
            "Read more information on the undertaking's website.\nLast update\n"
            "19 January 2026")
    document, candidate = candidate_over(
        text, "Read more information on the undertaking's website.")
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "QUARANTINED"
    assert decision.candidate_function == "NAVIGATIONAL_METADATA"


def test_archive_wrapper_is_quarantined():
    text = ("The Wayback Machine - https://web.archive.org/web/20260626191124/x\n"
            "The Council adopted the regulation.")
    document, candidate = candidate_over(
        text, "The Wayback Machine - https://web.archive.org/web/20260626191124/x")
    assert S.resolve_candidate(candidate, document).stage == "QUARANTINED"


def test_interrogative_cannot_carry_a_claim():
    text = "Hat diese Stoerung Auswirkungen fuer die Leute im Zug gehabt?"
    document, candidate = candidate_over(text, text, language="de")
    decision = S.resolve_candidate(candidate, document, language="de")
    assert decision.stage == "REJECTED"
    assert "question" in decision.rationale


def test_legal_citation_cannot_carry_a_claim():
    text = ("( 8 ) Decision No 768/2008/EC of the European Parliament and of the "
            "Council of 9 July 2008 on a common framework.")
    document, candidate = candidate_over(text, text)
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "REJECTED"
    assert decision.candidate_function == "SUPPORTING_DETAIL"


def test_line_wrap_fragment_is_rejected():
    # A PDF text layer wraps mid-sentence; the recorded span is the wrong unit
    # of evidence and no bounded expansion repairs it.
    text = ("las autoridades competentes del\nEstado miembro en cuestion.\n"
            "El reglamento se aplica.")
    document, candidate = candidate_over(
        text, "Estado miembro en cuestion.", language="es")
    assert S.resolve_candidate(candidate, document, language="es").stage == "REJECTED"


def test_context_supplied_subject_asks_for_context_rather_than_accepting():
    text = ("Die Bahn bietet kinderfreundliche Regeln. Damit ist die Bahn der "
            "Anbieter mit den besten Regeln.")
    document, candidate = candidate_over(
        text, "Damit ist die Bahn der Anbieter mit den besten Regeln.",
        language="de")
    decision = S.resolve_candidate(candidate, document, language="de")
    assert decision.stage == "SEMANTICALLY_PARSED"
    assert "ANTECEDENT_RESOLVED_FROM_CONTEXT" in decision.boundary_warnings
    assert decision.subject and "Bahn" in decision.subject


def test_a_well_formed_sentence_is_admitted():
    text = ("The European Commission and the European Investment Bank Group have "
            "joined forces to support the initiative.")
    document, candidate = candidate_over(text, text)
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "ACCEPTED_CANDIDATE"
    assert decision.candidate_function == "ANALYTICALLY_MATERIAL"
    assert not decision.missing_roles


def test_accepted_candidate_carries_its_lifecycle_state():
    text = "The Commission plans to deploy the system in 2027 across the Union."
    document, candidate = candidate_over(text, text)
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "ACCEPTED_CANDIDATE"
    assert decision.lifecycle_state == "DEPLOYMENT_PLANNED"


def test_exact_span_type_without_exact_mapping_is_evidence_bound():
    text = 'The spokesperson said: "The programme is funded."'
    document, candidate = candidate_over(
        text, '"The programme is funded."', candidate_type="QUOTATION",
        mapping_precision="APPROXIMATE_SECTION")
    assert S.resolve_candidate(candidate, document).stage == "EVIDENCE_BOUND"


def test_claim_with_a_number_does_not_require_a_unit():
    # V5.1 required a unit and referent from any CLAIM carrying a number, so
    # every claim citing an article or a regulation number was incomplete.
    text = "The Council adopted Regulation 2026/150 on 16 January 2026."
    document, candidate = candidate_over(text, text)
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage == "ACCEPTED_CANDIDATE"
    assert "unit" not in decision.missing_roles


def test_provenance_mismatch_is_refused():
    document = make_document("The Council adopted the regulation.")
    other = make_document("A different document entirely, with other text.")
    candidate = make_candidate(document, 0, 10)
    with pytest.raises(ValueError, match="provenance mismatch"):
        S.resolve_candidate(candidate, other)


def test_a_decision_never_accepts_with_a_capability_failure():
    text = "42"
    document, candidate = candidate_over(text, "42", candidate_type="NUMERIC_VALUE")
    decision = S.resolve_candidate(candidate, document)
    assert decision.stage != "ACCEPTED_CANDIDATE"


# ---------------------------------------------------------------------------
# Section 9.3 — boundary evidence
# ---------------------------------------------------------------------------

def test_caption_supplies_object_identity():
    text = ("Figure 3: the national rail network\nThe map shows every line.\n"
            "It covers the whole network.")
    document, candidate = candidate_over(text, "The map shows every line.")
    lattice = S.build_lattice(candidate, document)
    assert lattice.boundary_evidence.caption == "the national rail network"


def test_post_quotation_attribution_is_recovered():
    text = '"The programme is funded," said the spokesperson for the ministry.'
    document, candidate = candidate_over(text, '"The programme is funded,"')
    lattice = S.build_lattice(candidate, document)
    assert lattice.boundary_evidence.post_quotation_attribution


def test_correction_notice_in_context_is_flagged():
    text = ("Correction: an earlier version misstated the figure.\n"
            "The programme received 500 million euro.")
    document, candidate = candidate_over(
        text, "The programme received 500 million euro.")
    lattice = S.build_lattice(candidate, document)
    assert lattice.boundary_evidence.correction_notice


def test_enumerated_context_is_detected():
    text = "ANNEX XI\n2.\nA description of the testing measures.\n3.\nAnother item."
    document, candidate = candidate_over(text, "A description of the testing measures.")
    lattice = S.build_lattice(candidate, document)
    assert lattice.boundary_evidence.enumerated_context


# ---------------------------------------------------------------------------
# Section 9.6 — decision-quality accounting
# ---------------------------------------------------------------------------

def test_decision_quality_separates_wrong_admission_from_over_rejection():
    decisions = [
        {"candidate_id": "a", "stage": "ACCEPTED_CANDIDATE"},
        {"candidate_id": "b", "stage": "ACCEPTED_CANDIDATE"},
        {"candidate_id": "c", "stage": "REJECTED"},
        {"candidate_id": "d", "stage": "QUARANTINED"},
    ]
    truth = {"a": "ACCEPTED_CANDIDATE", "b": "REJECTED", "c": "ACCEPTED_CANDIDATE",
             "d": "QUARANTINED"}
    result = S.decision_quality(decisions, truth)
    assert result["correct_acceptances"] == 1
    assert result["wrong_admissions"] == 1
    assert result["missed_recoverable_assertions"] == 1
    assert result["correct_quarantines"] == 1
    assert result["semantic_correctness"] == 0.5


# ---------------------------------------------------------------------------
# Chrome classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("We use cookies to improve your browsing experience.", "COOKIE_NOTICE"),
    ("Wir verwenden Cookies auf dieser Website.", "COOKIE_NOTICE"),
    ("The Wayback Machine - https://web.archive.org/web/2026/x", "ARCHIVAL_WRAPPER"),
    ("50 captures\n15 Jan 2026", "ARCHIVAL_WRAPPER"),
    ("Skip to main content", "NAVIGATION_CHROME"),
    ("Visit our cookies policy page for more information.", "COOKIE_NOTICE"),
    ("If you are not a journalist, please contact the press office.",
     "CONTACT_BOILERPLATE"),
    ("Print as PDF", "PRINT_OR_EXPORT"),
    ("The Council adopted the regulation on 16 January 2026.", None),
    ("Die Stoerung traf den gesamten Fernverkehr.", None),
])
def test_chrome_classification(text, expected):
    assert chrome.classify_chrome(text) == expected


def test_chrome_regions_preserve_offsets():
    text = "Body sentence one.\nWe use cookies here.\nBody sentence two."
    regions = chrome.chrome_regions(text)
    assert regions
    start, end, kind = regions[0]
    assert kind == "COOKIE_NOTICE"
    assert "cookies" in text[start:end]
    assert len(chrome.strip_chrome(text)) == len(text)
