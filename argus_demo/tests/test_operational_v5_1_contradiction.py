from __future__ import annotations

import pytest

from curunir_operational.v4.models import ClaimUnit
from curunir_operational.v5_1.contradiction import (
    RelationAssessment, apply_notice, definition_evidence, initial_claim_state,
    notice_record, relate, subject_identity, temporal_frame,
)
from curunir_operational.v5_1.models import EvidenceRef, capability_outcome, now_utc

pytestmark = pytest.mark.no_db


def _claim(claim_id, subject, predicate, value, *, temporal=(None, None), geo=(),
           modality="ASSERTED", polarity="POSITIVE"):
    text = f"{subject} {predicate} {value}"
    return ClaimUnit(claim_id, "case-syn", text, text, subject, predicate, value,
                     temporal, geo, modality, polarity, "EXTRACTED",
                     ("basis-syn",), ("cand-syn",), "UNREVIEWED", 1)


def _ev(kind="EXPLICIT_TEXT_SPAN", detail="notice span quoted from the issuing document"):
    return EvidenceRef(kind, "source-syn-1", None, detail)


def _outcome(state="RESOLVED_CORRECTLY", **extra):
    return capability_outcome(subject_kind="CONTRADICTION_UPDATE_RELATION",
                              subject_id="pair-syn", outcome=state,
                              rationale="synthetic", **extra)


# --- temporal frames -------------------------------------------------------

def test_temporal_frame_separates_event_and_publication_time():
    early = temporal_frame(claim_id="c1", event_time=("2023", "2023"), publication_time="2026-02-01")
    late = temporal_frame(claim_id="c2", event_time=("2023", "2023"), publication_time="2026-05-01")
    assert early.same_event_time(late)
    assert early.publication_order(late) == "EARLIER"
    assert late.publication_order(early) == "LATER"


def test_temporal_frame_rejects_inverted_or_unparseable_intervals():
    with pytest.raises(ValueError, match="inverted"):
        temporal_frame(claim_id="c1", event_time=("2024-05", "2023"))
    with pytest.raises(ValueError, match="unparseable"):
        temporal_frame(claim_id="c1", event_time=("demnächst", None))


def test_event_relation_granularity_and_subset():
    year = temporal_frame(claim_id="c1", event_time=("2023", "2023"))
    month = temporal_frame(claim_id="c2", event_time=("2023-06", "2023-06"))
    other = temporal_frame(claim_id="c3", event_time=("2024", "2024"))
    assert year.event_relation(month) == "RIGHT_WITHIN_LEFT"
    assert month.event_relation(year) == "LEFT_WITHIN_RIGHT"
    assert year.event_relation(other) == "DISJOINT"


# --- update vs contradiction ----------------------------------------------

def test_update_not_contradiction_for_different_event_times():
    left = _claim("c1", "Fernwärmenetz Nordstadt", "anzahl übergabestationen", "40 Stationen",
                  temporal=("2022", "2022"))
    right = _claim("c2", "Fernwärmenetz Nordstadt", "anzahl übergabestationen", "55 Stationen",
                   temporal=("2024", "2024"))
    result = relate(left, right)
    assert result.relation == "TEMPORAL_UPDATE"
    assert result.temporal_basis == "DIFFERENT_EVENT_TIME"
    assert result.outcome.outcome == "RESOLVED_CORRECTLY"


def test_contradiction_not_update_for_same_event_time():
    left = _claim("c1", "réseau de capteurs Delta", "état opérationnel", "en service",
                  temporal=("2023", "2023"))
    right = _claim("c2", "réseau de capteurs Delta", "état opérationnel", "hors service",
                   temporal=("2023", "2023"))
    result = relate(left, right)
    assert result.relation == "LOGICAL_CONTRADICTION"
    assert result.temporal_basis == "SAME_EVENT_TIME"


def test_publication_order_alone_never_makes_temporal_update():
    left = _claim("c1", "Messstelle Ost", "jahresdurchsatz", "900 t")
    right = _claim("c2", "Messstelle Ost", "jahresdurchsatz", "1200 t")
    frames = (temporal_frame(claim_id="c1", publication_time="2026-01-10"),
              temporal_frame(claim_id="c2", publication_time="2026-03-01"))
    result = relate(left, right, (), frames)
    assert result.relation == "UNRESOLVED"
    assert result.relation != "TEMPORAL_UPDATE"
    assert result.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    assert "publication order" in result.outcome.evidence_insufficiency_demonstration


def test_compatible_claims_are_no_conflict():
    left = _claim("c1", "Anlage West", "genehmigungsstatus", "erteilt", temporal=("2024", "2024"))
    right = _claim("c2", "Anlage West", "genehmigungsstatus", "erteilt", temporal=("2024", "2024"))
    assert relate(left, right).relation == "NO_CONFLICT"


def test_same_value_across_times_is_not_an_update():
    left = _claim("c1", "Anlage West", "kapazität", "120 mw", temporal=("2022", "2022"))
    right = _claim("c2", "Anlage West", "kapazität", "120 mw", temporal=("2024", "2024"))
    result = relate(left, right)
    assert result.relation == "NO_CONFLICT"


# --- numeric comparison with unit normalization ---------------------------

def test_unit_normalized_equal_values_do_not_conflict():
    left = _claim("c1", "Trasse Nord", "gesamtlänge", "3 km", temporal=("2024", "2024"))
    right = _claim("c2", "Trasse Nord", "gesamtlänge", "3 000 m", temporal=("2024", "2024"))
    assert relate(left, right).relation == "NO_CONFLICT"


def test_numeric_disagreement_with_multiplier_normalization():
    left = _claim("c1", "programme régional", "budget total", "1,2 millions euros",
                  temporal=("2025", "2025"))
    right = _claim("c2", "programme régional", "budget total", "1 150 000 euros",
                   temporal=("2025", "2025"))
    result = relate(left, right)
    assert result.relation == "NUMERIC_DISAGREEMENT"
    assert result.temporal_basis == "SAME_EVENT_TIME"


def test_unknown_unit_is_capability_failure_not_silent():
    left = _claim("c1", "Strecke Süd", "länge", "5 km", temporal=("2024", "2024"))
    right = _claim("c2", "Strecke Süd", "länge", "5 Wegstunden", temporal=("2024", "2024"))
    result = relate(left, right)
    assert result.relation == "UNRESOLVED"
    assert result.outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert result.outcome.capability_failure_class == "SEMANTIC_TYPE_ERROR"


def test_currency_dimension_mismatch_is_capability_failure():
    left = _claim("c1", "Vertrag Alpha", "auftragswert", "10 eur", temporal=("2024", "2024"))
    right = _claim("c2", "Vertrag Alpha", "auftragswert", "10 usd", temporal=("2024", "2024"))
    result = relate(left, right)
    assert result.outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"


# --- polarity --------------------------------------------------------------

def test_polarity_conflict_same_scope_and_time():
    left = _claim("c1", "Anlage Ost", "in betrieb", "betriebsstatus",
                  temporal=("2024", "2024"), polarity="POSITIVE")
    right = _claim("c2", "Anlage Ost", "in betrieb", "betriebsstatus",
                   temporal=("2024", "2024"), polarity="NEGATIVE")
    assert relate(left, right).relation == "POLARITY_CONFLICT"


def test_polarity_flip_across_disjoint_event_times_is_update():
    left = _claim("c1", "Anlage Ost", "in betrieb", "betriebsstatus",
                  temporal=("2022", "2022"), polarity="POSITIVE")
    right = _claim("c2", "Anlage Ost", "in betrieb", "betriebsstatus",
                   temporal=("2024", "2024"), polarity="NEGATIVE")
    assert relate(left, right).relation == "TEMPORAL_UPDATE"


# --- definitions -----------------------------------------------------------

def test_definition_difference_wins_over_contradiction():
    left = _claim("c1", "Messnetz Ost", "aktive standorte", "48", temporal=("2024", "2024"))
    right = _claim("c2", "Messnetz Ost", "aktive standorte", "31", temporal=("2024", "2024"))
    divergence = definition_evidence(
        term="aktive Standorte",
        left_definition="Standorte mit täglicher Datenmeldung",
        right_definition="Standorte mit mindestens einer Meldung pro Quartal",
        evidence=(_ev(detail="definition stated in methodology section"),))
    result = relate(left, right, definitions=(divergence,))
    assert result.relation == "DEFINITION_DIFFERENCE"
    assert result.evidence


def test_definition_evidence_requires_divergence():
    with pytest.raises(ValueError, match="divergent"):
        definition_evidence(term="standorte", left_definition="gemeldete Standorte",
                            right_definition="gemeldete Standorte", evidence=(_ev(),))


# --- identity gate ---------------------------------------------------------

def test_identity_unresolved_blocks_forced_conflict_class():
    left = _claim("c1", "Nordwerk AG", "eigentümer", "Kommune Seestadt", temporal=("2024", "2024"))
    right = _claim("c2", "NW Holding SA", "eigentümer", "Privatkonsortium", temporal=("2024", "2024"))
    result = relate(left, right)
    assert result.relation == "IDENTITY_DISAGREEMENT"
    assert result.identity_blocker
    assert result.outcome.outcome == "RESOLVED_WITH_MATERIAL_QUALIFICATION"
    assert result.relation not in {"LOGICAL_CONTRADICTION", "NUMERIC_DISAGREEMENT"}


def test_accepted_identity_allows_conflict_classification():
    left = _claim("c1", "Nordwerk AG", "eigentümer", "Kommune Seestadt", temporal=("2024", "2024"))
    right = _claim("c2", "NW Holding SA", "eigentümer", "Privatkonsortium", temporal=("2024", "2024"))
    link = subject_identity(left_subject="Nordwerk AG", right_subject="NW Holding SA",
                            outcome="SAME_ENTITY_ACCEPTED",
                            evidence=(_ev("OFFICIAL_IDENTIFIER", "shared registry identifier"),))
    assert relate(left, right, identity=link).relation == "LOGICAL_CONTRADICTION"


def test_different_entities_are_no_conflict():
    left = _claim("c1", "Nordwerk AG", "eigentümer", "Kommune Seestadt", temporal=("2024", "2024"))
    right = _claim("c2", "NW Holding SA", "eigentümer", "Privatkonsortium", temporal=("2024", "2024"))
    link = subject_identity(left_subject="Nordwerk AG", right_subject="NW Holding SA",
                            outcome="DIFFERENT_ENTITY",
                            evidence=(_ev("OFFICIAL_IDENTIFIER", "distinct registry identifiers"),))
    assert relate(left, right, identity=link).relation == "NO_CONFLICT"


def test_unresolved_identity_with_compatible_values_is_no_conflict():
    left = _claim("c1", "Nordwerk AG", "sitz", "Seestadt")
    right = _claim("c2", "NW Holding SA", "sitz", "Seestadt")
    assert relate(left, right).relation == "NO_CONFLICT"


# --- qualification and scope ----------------------------------------------

def test_conditional_claim_is_qualification_not_update():
    plain = _claim("c1", "Projekt Uferpark", "fertigstellung", "2027", temporal=("2027", "2027"))
    conditional = _claim("c2", "Projekt Uferpark", "fertigstellung", "2028",
                         temporal=("2028", "2028"), modality="CONDITIONAL")
    result = relate(plain, conditional)
    assert result.relation == "QUALIFICATION"


def test_nested_geographic_scope_with_different_values_is_scope_difference():
    national = _claim("c1", "Förderprogramm Solar", "geförderte anlagen", "1 200",
                      temporal=("2024", "2024"))
    regional = _claim("c2", "Förderprogramm Solar", "geförderte anlagen", "180",
                      temporal=("2024", "2024"), geo=("region-küste",))
    assert relate(national, regional).relation == "SCOPE_DIFFERENCE"


def test_temporal_subset_with_different_values_is_scope_difference():
    year = _claim("c1", "Zählstelle Brücke", "fahrzeuge gesamt", "800 000",
                  temporal=("2023", "2023"))
    month = _claim("c2", "Zählstelle Brücke", "fahrzeuge gesamt", "60 000",
                   temporal=("2023-06", "2023-06"))
    assert relate(year, month).relation == "SCOPE_DIFFERENCE"


def test_different_predicates_are_no_conflict():
    left = _claim("c1", "Anlage West", "baubeginn", "2021", temporal=("2021", "2021"))
    right = _claim("c2", "Anlage West", "inbetriebnahme", "2024", temporal=("2024", "2024"))
    assert relate(left, right).relation == "NO_CONFLICT"


# --- notices ---------------------------------------------------------------

def test_correction_notice_yields_correction():
    old = _claim("c1", "Bericht Q1", "gesamtvolumen", "10 000 t", temporal=("2025", "2025"))
    new = _claim("c2", "Bericht Q1", "gesamtvolumen", "12 000 t", temporal=("2025", "2025"))
    notice = notice_record(content_relation="CORRECTS", issuing_document_id="doc-syn-2",
                           target_claim_ids=("c1",), replacement_claim_id="c2",
                           evidence=(_ev(detail="erratum paragraph naming the corrected figure"),))
    result = relate(old, new, (notice,))
    assert result.relation == "CORRECTION"
    assert result.notice_ids == (notice.notice_id,)
    assert result.governed_claim_ids == ("c1",)


def test_retraction_notice_yields_retraction():
    claim = _claim("c1", "Studie Alpha", "hauptbefund", "wirksamkeit belegt")
    other = _claim("c2", "Studie Alpha", "hauptbefund", "wirksamkeit nicht belegt")
    notice = notice_record(content_relation="RETRACTS", issuing_document_id="doc-syn-3",
                           target_claim_ids=("c1",),
                           evidence=(_ev("ARCHIVE_BANNER", "retraction banner on the record"),))
    assert relate(claim, other, (notice,)).relation == "RETRACTION"


def test_notice_requires_evidence():
    with pytest.raises(ValueError, match="notice evidence"):
        notice_record(content_relation="CORRECTS", issuing_document_id="doc-syn-2",
                      target_claim_ids=("c1",), evidence=())


def test_correction_assessment_without_notice_is_rejected():
    with pytest.raises(ValueError, match="notice"):
        RelationAssessment("a-1", "c1", "c2", "CORRECTION", "synthetic", "NOTICE_GOVERNED",
                           (), (), None, None, (), (), _outcome(), now_utc())


def test_supersession_whole_document_scope_guard():
    with pytest.raises(ValueError, match="whole-document"):
        notice_record(content_relation="SUPERSEDES", issuing_document_id="doc-new",
                      target_document_id="doc-old", scope_kind="WHOLE_DOCUMENT_SCOPE",
                      notice_span_coverage="CLAIM_LEVEL", evidence=(_ev(),))


def test_whole_document_supersession_carries_explicit_scope():
    old = _claim("c1", "Lagebild Region", "risikoeinstufung", "stufe 2", temporal=("2025", "2025"))
    new = _claim("c2", "Lagebild Region", "risikoeinstufung", "stufe 3", temporal=("2025", "2025"))
    notice = notice_record(content_relation="SUPERSEDES", issuing_document_id="doc-new",
                           target_document_id="doc-old", scope_kind="WHOLE_DOCUMENT_SCOPE",
                           notice_span_coverage="DOCUMENT_LEVEL",
                           evidence=(_ev("DOCUMENT_COVER", "cover states the report replaces the prior edition"),))
    with pytest.raises(ValueError, match="claim-to-document"):
        relate(old, new, (notice,))
    result = relate(old, new, (notice,), claim_documents={"c1": "doc-old", "c2": "doc-new"})
    assert result.relation == "SUPERSESSION"
    assert result.supersession_scope_kind == "WHOLE_DOCUMENT_SCOPE"
    assert result.supersession_target_ids == ("c1",)


def test_supersession_assessment_requires_scope():
    with pytest.raises(ValueError, match="scope"):
        RelationAssessment("a-1", "c1", "c2", "SUPERSESSION", "synthetic", "NOTICE_GOVERNED",
                           ("n-1",), ("c1",), None, None, (), (), _outcome(), now_utc())


# --- history preservation --------------------------------------------------

def test_history_preserved_through_notices():
    old = _claim("c1", "Bericht Q1", "gesamtvolumen", "10 000 t", temporal=("2025", "2025"))
    new = _claim("c2", "Bericht Q1", "gesamtvolumen", "12 000 t", temporal=("2025", "2025"))
    correction = relate(old, new, (notice_record(
        content_relation="CORRECTS", issuing_document_id="doc-syn-2", target_claim_ids=("c1",),
        evidence=(_ev(),)),))
    supersession = relate(old, new, (notice_record(
        content_relation="SUPERSEDES", issuing_document_id="doc-syn-4", target_claim_ids=("c1",),
        scope_kind="SECTION_SCOPE", notice_span_coverage="SECTION_LEVEL",
        evidence=(_ev("EXPLICIT_METADATA", "section header marks the earlier figures as replaced"),)),))
    first = initial_claim_state("c1")
    second = apply_notice(first, correction)
    third = apply_notice(second, supersession)
    assert (second.status, third.status) == ("CORRECTED", "SUPERSEDED")
    assert third.version == 3
    assert first.state_id in third.history and second.state_id in third.history
    assert second.history == (first.state_id,)


def test_history_can_never_be_truncated():
    from curunir_operational.v5_1.contradiction import ClaimVersionState
    with pytest.raises(ValueError, match="history"):
        ClaimVersionState("s-2", "c1", "CORRECTED", 3, (), (), now_utc())


def test_apply_notice_rejects_out_of_scope_claim_and_terminal_retraction():
    old = _claim("c1", "Bericht Q1", "gesamtvolumen", "10 000 t")
    new = _claim("c2", "Bericht Q1", "gesamtvolumen", "12 000 t")
    retraction = relate(old, new, (notice_record(
        content_relation="RETRACTS", issuing_document_id="doc-syn-3", target_claim_ids=("c1",),
        evidence=(_ev(),)),))
    with pytest.raises(ValueError, match="govern"):
        apply_notice(initial_claim_state("c2"), retraction)
    retracted = apply_notice(initial_claim_state("c1"), retraction)
    with pytest.raises(ValueError, match="terminal"):
        apply_notice(retracted, retraction)


# --- structural guards -----------------------------------------------------

def test_unresolved_requires_demonstration_or_failure():
    with pytest.raises(ValueError, match="UNRESOLVED"):
        RelationAssessment("a-1", "c1", "c2", "UNRESOLVED", "synthetic", "EVENT_TIME_UNKNOWN",
                           (), (), None, None, (), (), _outcome(), now_utc())


def test_temporal_update_cannot_rest_on_publication_order():
    with pytest.raises(ValueError, match="publication order"):
        RelationAssessment("a-1", "c1", "c2", "TEMPORAL_UPDATE", "synthetic", "SAME_EVENT_TIME",
                           (), (), None, None, (), (), _outcome(), now_utc())


def test_conflict_class_requires_established_event_time():
    with pytest.raises(ValueError, match="event time"):
        RelationAssessment("a-1", "c1", "c2", "LOGICAL_CONTRADICTION", "synthetic",
                           "EVENT_TIME_UNKNOWN", (), (), None, None, (), (), _outcome(), now_utc())


def test_assessment_serializes_to_record():
    left = _claim("c1", "Anlage West", "genehmigungsstatus", "erteilt", temporal=("2024", "2024"))
    right = _claim("c2", "Anlage West", "genehmigungsstatus", "erteilt", temporal=("2024", "2024"))
    record = relate(left, right).to_record()
    assert record["relation"] == "NO_CONFLICT"
    assert record["outcome"]["outcome"] == "RESOLVED_CORRECTLY"
