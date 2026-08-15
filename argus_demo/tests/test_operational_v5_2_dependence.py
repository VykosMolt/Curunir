from __future__ import annotations

import pytest

from curunir_operational.v5_1.dependence import (
    classify_dependence as v5_1_classify, dependence_signal,
)
from curunir_operational.v5_1.models import EvidenceRef, NON_CORROBORATING_STATES
from curunir_operational.v5_1.source_identity import (
    document_manifestation, publication_record,
)
from curunir_operational.v5_2.dependence import (
    CORROBORATION_ELIGIBLE_STATES, DEPENDENCE_TRIGGERS, FULL_DERIVATION_COVERAGE,
    PublicationRef, classify_dependence, content_profile, corroboration_arithmetic,
    dependence_class_coverage, measure_overlap, overlap_measurement,
    translation_alignment,
)

pytestmark = pytest.mark.no_db

HASH_ONE = "1" * 64
HASH_TWO = "2" * 64


def pub(pid, family="family-alpha", publisher="Beispiel Rundschau", language="de",
        fingerprint=None):
    return PublicationRef(pid, family, publisher, language, fingerprint)


def refs(detail="mapped span evidence"):
    return (EvidenceRef("EXPLICIT_TEXT_SPAN", "source-object-1", None, detail),)


def signal(kind, strength="STRONG", direction="UNDIRECTED", detail=None, **kw):
    return dependence_signal(
        kind=kind, strength=strength, direction=direction,
        detail=detail or f"synthetic {kind.lower()} evidence", evidence_refs=refs(), **kw)


# --- synthetic corpora (invented text; no campaign, source or entity) -------

SOURCE_TEXT = (
    "The municipal council approved the renovation plan for the river bridge.\n"
    "Work on the eastern span will begin in the spring of 2026.\n"
    "The budget report lists 1,240 inspected structures across the district.\n"
    "Three contractors submitted tenders before the published deadline closed.\n"
    "The council expects the crossing to reopen after two winters.\n"
    "Residents were consulted at four public meetings last year.\n"
)

PARTIAL_REUSE_TEXT = (
    "The municipal council approved the renovation plan for the river bridge.\n"
    "Work on the eastern span will begin in the spring of 2026.\n"
    "The budget report lists 1,240 inspected structures across the district.\n"
    "Our reporter visited the site office on a rainy Tuesday morning.\n"
    "The site engineer described the scaffolding works in her own words.\n"
    "Neighbouring districts have not published comparable inspection figures.\n"
)

FULL_REUSE_TEXT = SOURCE_TEXT + "The report was republished without further comment.\n"

RELEASE_DE = (
    "Die Verwaltung von Lindenstadt hat am 2024-03-12 einen Bericht veroeffentlicht.\n"
    "Der Bericht nennt 1.240 geprüfte Anlagen und 87 offene Vorgaenge.\n"
    "\n"
    "Lindenstadt und Ahornfeld teilen sich die Kosten von 4,5 Millionen Euro.\n"
    "Die Arbeiten sollen bis 2026 abgeschlossen sein.\n"
)

RELEASE_FR = (
    "L'administration de Lindenstadt a publie un rapport le 2024-03-12.\n"
    "Le rapport cite 1 240 installations verifiees et 87 dossiers ouverts.\n"
    "\n"
    "Lindenstadt et Ahornfeld partagent un cout de 4,5 millions d'euros.\n"
    "Les travaux doivent s'achever en 2026.\n"
)

# A partial rendering of the release: some invariants survive, others belong to
# material the source never carried.
ABRIDGED_FR = (
    "L'administration de Lindenstadt a publie un rapport le 2024-03-12.\n"
    "Le rapport cite 87 dossiers ouverts et 19 recours deposes.\n"
    "\n"
    "Le budget municipal de 7,2 millions d'euros couvre 3 chantiers voisins.\n"
    "Les travaux doivent s'achever en 2029.\n"
)

# Same shared annex, but an unrelated shape and figures: an independent report
# in another language, not a translation.
INDEPENDENT_FR = (
    "Le conseil d'Ahornfeld a examine 9 dossiers le 2023-11-04.\n"
    "\n"
    "Une association locale conteste le calendrier des travaux.\n"
    "\n"
    "Le cout total atteindrait 12,8 millions d'euros selon ce collectif.\n"
    "\n"
    "Aucune date de reouverture n'a ete confirmee par la mairie.\n"
)


def profile(pid, language, text):
    return content_profile(publication_id=pid, language=language, text=text)


# --- one constructed example per dependence class ---------------------------

def case_mirror_manifestation():
    """Two manifestations of one publication, plus reuse evidence V5.1 read as
    derivation between two publications."""
    record = publication_record(work_id="work-1", venue_entity_id="venue-1", language="de")
    left = document_manifestation(publication_id=record.publication_id,
                                  content_hash=HASH_ONE, media_type="text/html")
    right = document_manifestation(publication_id=record.publication_id,
                                   content_hash=HASH_TWO, media_type="application/pdf")
    return classify_dependence(
        pub("pub-mirror-a"), pub("pub-mirror-b", family="family-beta"),
        (signal("EXPLICIT_REUSE_STATEMENT", direction="LEFT_FROM_RIGHT"),),
        left_manifestations=(left,), right_manifestations=(right,))


def case_translation_derivative():
    """Official multilingual issue detected from content, not metadata."""
    return classify_dependence(
        pub("pub-de", language="de"),
        pub("pub-fr", family="family-beta", publisher="Gazette Exemple", language="fr"),
        (signal("COMMON_OFFICIAL_ANNEX", strength="MODERATE"),),
        left_profile=profile("pub-de", "de", RELEASE_DE),
        right_profile=profile("pub-fr", "fr", RELEASE_FR))


def case_syndication_derivative():
    return classify_dependence(
        pub("pub-wire", language="es", publisher="Agencia Ejemplo"),
        pub("pub-paper", family="family-beta", language="es"),
        (signal("WIRE_SERVICE_INDICATION", direction="RIGHT_FROM_LEFT"),))


def case_derivative_confirmed():
    return classify_dependence(
        pub("pub-source", language="en"), pub("pub-copy", family="family-beta", language="en"),
        (signal("EXPLICIT_REUSE_STATEMENT", direction="RIGHT_FROM_LEFT"),))


def case_partial_dependence():
    """Confirmed reuse covering half of the derivative publication."""
    return classify_dependence(
        pub("pub-source", language="en"), pub("pub-mixed", family="family-beta", language="en"),
        (signal("EXPLICIT_REUSE_STATEMENT", direction="RIGHT_FROM_LEFT"),),
        left_profile=profile("pub-source", "en", SOURCE_TEXT),
        right_profile=profile("pub-mixed", "en", PARTIAL_REUSE_TEXT))


def case_common_evidence_basis():
    """A named annex both publications rest on, graded WEAK."""
    return classify_dependence(
        pub("pub-annex-a"), pub("pub-annex-b", family="family-beta"),
        (signal("COMMON_OFFICIAL_ANNEX", strength="WEAK"),))


def case_shared_data_independent_analysis():
    return classify_dependence(
        pub("pub-data-a"), pub("pub-data-b", family="family-beta"),
        (signal("COMMON_PRIMARY_DATASET", strength="MODERATE"),
         signal("DISTINCT_REPORTING_DETAIL", strength="STRONG"),
         signal("DISTINCT_AUTHORSHIP_EVIDENCE", strength="MODERATE")))


def case_independence_supported():
    return classify_dependence(
        pub("pub-indep-a"), pub("pub-indep-b", family="family-beta"),
        (signal("DISTINCT_AUTHORSHIP_EVIDENCE"),
         signal("ON_THE_RECORD_ORIGINAL_INTERVIEW", strength="MODERATE")))


def case_no_dependence_found():
    return classify_dependence(pub("pub-none-a"), pub("pub-none-b", family="family-beta"), ())


def case_independence_unknown():
    return classify_dependence(
        pub("pub-open-a"), pub("pub-open-b", family="family-beta"),
        (signal("PUBLICATION_TIMING", strength="WEAK"),))


def case_dependence_disputed():
    return classify_dependence(
        pub("pub-contest-a"), pub("pub-contest-b", family="family-beta"),
        (signal("EXPLICIT_CITATION", direction="LEFT_FROM_RIGHT", contested=True,
                contest_basis="the cited passage is absent from the archived capture"),))


CLASS_EXAMPLES = {
    "MIRROR_MANIFESTATION": case_mirror_manifestation,
    "TRANSLATION_DERIVATIVE": case_translation_derivative,
    "SYNDICATION_DERIVATIVE": case_syndication_derivative,
    "DERIVATIVE_CONFIRMED": case_derivative_confirmed,
    "PARTIAL_DEPENDENCE": case_partial_dependence,
    "COMMON_EVIDENCE_BASIS_CONFIRMED": case_common_evidence_basis,
    "SHARED_DATA_INDEPENDENT_ANALYSIS": case_shared_data_independent_analysis,
    "INDEPENDENCE_SUPPORTED": case_independence_supported,
    "NO_DEPENDENCE_FOUND": case_no_dependence_found,
    "INDEPENDENCE_UNKNOWN": case_independence_unknown,
    "DEPENDENCE_DISPUTED": case_dependence_disputed,
}


# --- class coverage: all eleven classes are reachable ----------------------

@pytest.mark.parametrize("state", sorted(CLASS_EXAMPLES))
def test_every_dependence_class_is_reachable(state):
    assert CLASS_EXAMPLES[state]().state == state


def test_trigger_table_documents_every_class():
    assert set(DEPENDENCE_TRIGGERS) == set(CLASS_EXAMPLES)
    assert all(text.strip() for text in DEPENDENCE_TRIGGERS.values())


def test_class_coverage_reports_complete_and_missing_classes():
    complete = dependence_class_coverage(build() for build in CLASS_EXAMPLES.values())
    assert complete["classes_exercised"] == 11
    assert complete["missing_classes"] == ()
    assert complete["coverage_complete"] is True

    partial = dependence_class_coverage(
        (case_derivative_confirmed(), case_no_dependence_found()))
    assert partial["exercised_classes"] == ("DERIVATIVE_CONFIRMED", "NO_DEPENDENCE_FOUND")
    assert len(partial["missing_classes"]) == 9
    assert "TRANSLATION_DERIVATIVE" in partial["missing_classes"]
    assert partial["coverage_complete"] is False
    # Every missing class is reported with the condition that would reach it.
    assert set(partial["missing_class_triggers"]) == set(partial["missing_classes"])


# --- the four V5.1 confusions ---------------------------------------------

def test_multilingual_release_is_translation_not_common_basis():
    signals = (signal("COMMON_OFFICIAL_ANNEX", strength="MODERATE"),)
    left, right = pub("pub-de", language="de"), pub("pub-fr", family="family-beta",
                                                    language="fr")
    assert v5_1_classify(left, right, signals).state == "COMMON_EVIDENCE_BASIS_CONFIRMED"
    repaired = case_translation_derivative()
    assert repaired.state == "TRANSLATION_DERIVATIVE"
    assert repaired.state in NON_CORROBORATING_STATES
    assert repaired.explanation.confidence_dimensions[
        "translation_alignment_score_uncalibrated"] >= 0.75


def test_partial_reuse_is_partial_dependence_not_full_derivation():
    signals = (signal("EXPLICIT_REUSE_STATEMENT", direction="RIGHT_FROM_LEFT"),)
    left = pub("pub-source", language="en")
    right = pub("pub-mixed", family="family-beta", language="en")
    assert v5_1_classify(left, right, signals).state == "DERIVATIVE_CONFIRMED"
    repaired = case_partial_dependence()
    assert repaired.state == "PARTIAL_DEPENDENCE"
    dimensions = repaired.explanation.confidence_dimensions
    assert dimensions["overlap_coverage_ratio_uncalibrated"] == pytest.approx(0.5)
    assert dimensions["overlap_full_derivation_threshold"] == FULL_DERIVATION_COVERAGE
    assert "OVERLAP_UNITS:3/6" in repaired.explanation.positive_evidence


def test_mirror_pair_is_manifestation_not_derivation():
    signals = (signal("EXPLICIT_REUSE_STATEMENT", direction="LEFT_FROM_RIGHT"),)
    left, right = pub("pub-mirror-a"), pub("pub-mirror-b", family="family-beta")
    assert v5_1_classify(left, right, signals).state == "DERIVATIVE_CONFIRMED"
    repaired = case_mirror_manifestation()
    assert repaired.state == "MIRROR_MANIFESTATION"
    assert any(item.startswith("SHARED_PUBLICATION_MANIFESTATION_GROUP")
               for item in repaired.explanation.positive_evidence)


def test_shared_official_annex_is_common_basis_not_unknown():
    signals = (signal("COMMON_OFFICIAL_ANNEX", strength="WEAK"),)
    left, right = pub("pub-annex-a"), pub("pub-annex-b", family="family-beta")
    assert v5_1_classify(left, right, signals).state == "INDEPENDENCE_UNKNOWN"
    repaired = case_common_evidence_basis()
    assert repaired.state == "COMMON_EVIDENCE_BASIS_CONFIRMED"
    assert repaired.explanation.positive_evidence
    assert not repaired.explanation.independence_affirmatively_supported


# --- translation detection does not over-fire ------------------------------

def test_independent_cross_language_report_is_not_a_translation():
    value = classify_dependence(
        pub("pub-de", language="de"),
        pub("pub-fr-indep", family="family-beta", language="fr"),
        (signal("COMMON_OFFICIAL_ANNEX", strength="MODERATE"),),
        left_profile=profile("pub-de", "de", RELEASE_DE),
        right_profile=profile("pub-fr-indep", "fr", INDEPENDENT_FR))
    assert value.state == "COMMON_EVIDENCE_BASIS_CONFIRMED"


def test_declared_translation_routes_still_reach_the_class():
    left, right = pub("pub-w-de", language="de"), pub("pub-w-fr", family="family-beta",
                                                      language="fr")
    same_work = classify_dependence(
        left, right, (),
        left_publication_record=publication_record(
            work_id="work-2", venue_entity_id="venue-1", language="de"),
        right_publication_record=publication_record(
            work_id="work-2", venue_entity_id="venue-2", language="fr"))
    same_series = classify_dependence(
        left, right, (), left_document_series="series-1", right_document_series="series-1")
    notice = classify_dependence(left, right, (), translation_notice=True)
    assert {same_work.state, same_series.state, notice.state} == {"TRANSLATION_DERIVATIVE"}


def test_abridged_translation_is_partial_dependence():
    value = classify_dependence(
        pub("pub-de", language="de"),
        pub("pub-fr-short", family="family-beta", language="fr"),
        (), translation_notice=True,
        left_profile=profile("pub-de", "de", RELEASE_DE),
        right_profile=profile("pub-fr-short", "fr", ABRIDGED_FR))
    assert value.state == "PARTIAL_DEPENDENCE"
    ratio = value.explanation.confidence_dimensions["overlap_coverage_ratio_uncalibrated"]
    assert ratio < FULL_DERIVATION_COVERAGE


def test_same_language_pair_is_never_a_translation():
    alignment = translation_alignment(profile("pub-a", "en", SOURCE_TEXT),
                                      profile("pub-b", "en", SOURCE_TEXT))
    assert alignment.aligned is False
    assert "same language" in alignment.rationale


# --- coverage arithmetic ---------------------------------------------------

def test_full_coverage_confirms_derivation_without_a_supplied_signal():
    value = classify_dependence(
        pub("pub-source", language="en"), pub("pub-republished", family="family-beta",
                                              language="en"),
        left_profile=profile("pub-source", "en", SOURCE_TEXT),
        right_profile=profile("pub-republished", "en", FULL_REUSE_TEXT))
    assert value.state == "DERIVATIVE_CONFIRMED"
    ratio = value.explanation.confidence_dimensions["overlap_coverage_ratio_uncalibrated"]
    assert ratio >= FULL_DERIVATION_COVERAGE


def test_immaterial_overlap_does_not_manufacture_dependence():
    value = classify_dependence(
        pub("pub-a", language="en"), pub("pub-b", family="family-beta", language="en"),
        overlap=overlap_measurement(derivative_publication_id="pub-b",
                                    source_publication_id="pub-a",
                                    covered_units=1, total_units=20))
    assert value.state == "NO_DEPENDENCE_FOUND"


def test_measured_coverage_is_a_fraction_of_the_derivative():
    derivative = profile("pub-mixed", "en", PARTIAL_REUSE_TEXT)
    source = profile("pub-source", "en", SOURCE_TEXT)
    measurement = measure_overlap(derivative, source)
    assert measurement.covered_units == 3
    assert measurement.total_units == 6
    assert measurement.coverage_ratio == pytest.approx(0.5)
    assert measurement.basis == "LEXICAL_UNIT_COVERAGE"


def test_overlap_must_describe_the_assessed_pair():
    with pytest.raises(ValueError, match="publication pair"):
        classify_dependence(
            pub("pub-a"), pub("pub-b", family="family-beta"),
            overlap=overlap_measurement(derivative_publication_id="pub-b",
                                        source_publication_id="pub-c",
                                        covered_units=5, total_units=6))


# --- independence remains a positive finding -------------------------------

def test_absence_of_dependence_is_no_dependence_found():
    value = case_no_dependence_found()
    assert value.state == "NO_DEPENDENCE_FOUND"
    assert value.explanation.only_absence_of_discovered_dependence
    assert not value.explanation.independence_affirmatively_supported
    assert not value.explanation.positive_evidence


def test_single_independence_signal_never_supports_independence():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("DISTINCT_AUTHORSHIP_EVIDENCE"),))
    assert value.state == "NO_DEPENDENCE_FOUND"


def test_two_signals_of_one_kind_never_support_independence():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("DISTINCT_AUTHORSHIP_EVIDENCE", detail="separate on-site byline"),
         signal("DISTINCT_AUTHORSHIP_EVIDENCE", detail="separate named staff photographer")))
    assert value.state == "NO_DEPENDENCE_FOUND"


def test_surviving_dependence_signal_blocks_independence():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("DISTINCT_AUTHORSHIP_EVIDENCE"),
         signal("ON_THE_RECORD_ORIGINAL_INTERVIEW", strength="MODERATE"),
         signal("PUBLICATION_TIMING", strength="WEAK")))
    assert value.state == "INDEPENDENCE_UNKNOWN"
    assert not value.explanation.independence_affirmatively_supported


def test_affirmative_multi_signal_evidence_supports_independence():
    value = case_independence_supported()
    assert value.state == "INDEPENDENCE_SUPPORTED"
    assert value.explanation.independence_affirmatively_supported
    assert not value.explanation.only_absence_of_discovered_dependence


# --- corroboration discipline ---------------------------------------------

@pytest.mark.parametrize("state", sorted(CLASS_EXAMPLES))
def test_non_corroborating_states_are_never_counted_as_corroboration(state):
    assessment = CLASS_EXAMPLES[state]()
    left = pub(assessment.left_publication_id)
    right = pub(assessment.right_publication_id, family="family-beta")
    summary = corroboration_arithmetic(
        claim_family_id="family-test", publications=(left, right),
        assessments=(assessment,))
    expected = 1 if state in CORROBORATION_ELIGIBLE_STATES else 0
    assert summary.independent_corroboration_count == expected
    if state in NON_CORROBORATING_STATES:
        assert summary.corroborating_pair_ids == ()


def test_corroboration_eligibility_excludes_every_non_corroborating_state():
    assert not CORROBORATION_ELIGIBLE_STATES & NON_CORROBORATING_STATES


# --- V5.8.1 defect D26: identical text under two language labels -------------
#
# The pilot panel found two identical English strings carried by the Arabic and
# Spanish manifestations of one page classified TRANSLATION_DERIVATIVE.  Nothing
# had been translated; the sentence was republished twice.  Language difference
# plus identical text is evidence against translation, not for it.

REPEATED_ENGLISH = (
    "The Secretariat published the consolidated inspection register on 2024-03-12.\n"
    "The register lists 1,240 verified installations and 87 open proceedings.\n"
    "Member States shared costs of 4.5 million units for the 2026 programme.\n"
    "Four regional offices confirmed receipt before the published deadline.\n"
)


def test_identical_text_under_two_language_labels_is_not_a_translation():
    from curunir_operational.v5_2.dependence import untranslated_repetition

    left = profile("pub-ar", "ar", REPEATED_ENGLISH)
    right = profile("pub-es", "es", REPEATED_ENGLISH)
    repeated, detail = untranslated_repetition(left, right)
    assert repeated
    assert "repeated, not translated" in detail

    alignment = translation_alignment(left, right)
    assert not alignment.aligned
    assert "repeated, not translated" in alignment.rationale

    assessment = classify_dependence(
        pub("pub-ar", language="ar"),
        pub("pub-es", family="family-beta", language="es"),
        (signal("COMMON_OFFICIAL_ANNEX", strength="MODERATE"),),
        left_profile=left, right_profile=right,
        left_publication_record=publication_record(
            work_id="work-one", venue_entity_id="entity-secretariat", language="ar"),
        right_publication_record=publication_record(
            work_id="work-one", venue_entity_id="entity-secretariat", language="es"))
    assert assessment.state != "TRANSLATION_DERIVATIVE"


def test_declared_language_metadata_alone_no_longer_licenses_translation():
    """The metadata route is withdrawn only when the text contradicts it."""
    from curunir_operational.v5_2.dependence import _translation_basis

    left, right = pub("pub-ar", language="ar"), pub("pub-es", language="es")
    records = dict(
        left_record=publication_record(
            work_id="work-one", venue_entity_id="entity-secretariat", language="ar"),
        right_record=publication_record(
            work_id="work-one", venue_entity_id="entity-secretariat", language="es"))
    kept = _translation_basis(left, right, (), left_document_series=None,
                              right_document_series=None, alignment=None,
                              translation_notice=False, untranslated=False,
                              **records)
    assert "SAME_INTELLECTUAL_WORK_DIFFERENT_LANGUAGE" in kept
    withdrawn = _translation_basis(left, right, (), left_document_series=None,
                                   right_document_series=None, alignment=None,
                                   translation_notice=False, untranslated=True,
                                   **records)
    assert "SAME_INTELLECTUAL_WORK_DIFFERENT_LANGUAGE" not in withdrawn


def test_a_real_translation_is_still_a_translation():
    """Neighbouring negative: different wording, same invariants, still aligns."""
    from curunir_operational.v5_2.dependence import untranslated_repetition

    left = profile("pub-de", "de", RELEASE_DE)
    right = profile("pub-fr", "fr", RELEASE_FR)
    repeated, _ = untranslated_repetition(left, right)
    assert not repeated
    assert case_translation_derivative().state == "TRANSLATION_DERIVATIVE"


def test_one_shared_dateline_is_not_untranslated_repetition():
    """Neighbouring negative: a single coinciding unit is not a repeated page."""
    from curunir_operational.v5_2.dependence import untranslated_repetition

    shared_line = "The register lists 1,240 verified installations and 87 open proceedings.\n"
    left = profile("pub-ar", "ar", shared_line + "\n".join(
        f"Arabic edition sentence number {n} carries its own wording here." for n in range(4)))
    right = profile("pub-es", "es", shared_line + "\n".join(
        f"Spanish edition sentence number {n} carries different wording here." for n in range(4)))
    repeated, _ = untranslated_repetition(left, right)
    assert not repeated


def test_same_language_identical_text_is_not_reached_by_the_d26_guard():
    """The guard is about a language label contradicted by the text."""
    from curunir_operational.v5_2.dependence import untranslated_repetition

    left = profile("pub-one", "en", REPEATED_ENGLISH)
    right = profile("pub-two", "en", REPEATED_ENGLISH)
    assert untranslated_repetition(left, right) == (False, "")
