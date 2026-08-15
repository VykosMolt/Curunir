from __future__ import annotations

import pytest

from curunir_operational.v4.analysis import evidence_basis
from curunir_operational.v4.models import EvidenceBasis
from curunir_operational.v5_1.dependence import (
    ClaimFamilySummary, DependenceAssessment, DependenceCapabilityError,
    DependenceExplanation, DependenceSignal, FamilyDependenceRecord,
    PublicationRef, classify_dependence, corroboration_arithmetic,
    dependence_signal, family_state, from_legacy_basis,
)
from curunir_operational.v5_1.models import (
    NON_CORROBORATING_STATES, EvidenceRef, capability_outcome,
)

pytestmark = pytest.mark.no_db

AWARE = "2026-07-24T00:00:00+00:00"


def pub(pid, family="family-alpha", publisher="Beispiel Rundschau", language="de",
        fingerprint=None):
    return PublicationRef(pid, family, publisher, language, fingerprint)


def refs(detail="mapped span evidence"):
    return (EvidenceRef("EXPLICIT_TEXT_SPAN", "source-object-1", None, detail),)


def signal(kind, strength="STRONG", direction="UNDIRECTED", detail=None, **kw):
    return dependence_signal(
        kind=kind, strength=strength, direction=direction,
        detail=detail or f"synthetic {kind.lower()} evidence", evidence_refs=refs(), **kw)


def outcome_ok():
    return capability_outcome(subject_kind="SOURCE_DEPENDENCE_RELATION",
                              subject_id="pair-test", outcome="RESOLVED_CORRECTLY",
                              rationale="test fixture")


INDEPENDENCE_PAIR = (
    signal("DISTINCT_AUTHORSHIP_EVIDENCE",
           detail="separate on-site byline: 'Von unserer Korrespondentin in Lindenstadt'"),
    signal("ON_THE_RECORD_ORIGINAL_INTERVIEW", strength="MODERATE",
           detail="named engineer interviewed on the record about the bridge renovation"),
)


# --- derivative states are never independent -------------------------------

def test_translation_alignment_never_independent():
    left = pub("pub-de", language="de")
    right = pub("pub-fr", family="family-beta", publisher="Gazette Exemple", language="fr")
    value = classify_dependence(left, right, (signal(
        "TRANSLATION_ALIGNMENT", direction="RIGHT_FROM_LEFT",
        detail="sentence-aligned: 'Die Bruecke wurde 2019 saniert' / 'Le pont a ete renove en 2019'"),))
    assert value.state == "TRANSLATION_DERIVATIVE"
    assert value.state in NON_CORROBORATING_STATES
    assert not value.explanation.independence_affirmatively_supported
    assert value.explanation.positive_evidence


def test_mirror_manifestation_never_independent():
    fingerprint = "f" * 64
    left = pub("pub-orig", fingerprint=fingerprint)
    right = pub("pub-copy", family="family-beta", fingerprint=fingerprint)
    value = classify_dependence(left, right, ())
    assert value.state == "MIRROR_MANIFESTATION"
    assert value.state in NON_CORROBORATING_STATES
    assert not value.explanation.independence_affirmatively_supported


def test_syndication_never_independent():
    left = pub("pub-wire", language="es", publisher="Agencia Ejemplo")
    right = pub("pub-paper", family="family-beta", language="es")
    value = classify_dependence(left, right, (signal(
        "WIRE_SERVICE_INDICATION", direction="RIGHT_FROM_LEFT",
        detail="dateline credits the wire agency for the municipal budget story"),))
    assert value.state == "SYNDICATION_DERIVATIVE"
    assert value.state in NON_CORROBORATING_STATES


def test_explicit_citation_confirms_derivative():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"), (signal(
        "EXPLICIT_REUSE_STATEMENT", direction="LEFT_FROM_RIGHT",
        detail="article states the figures are reproduced from the other outlet's report"),))
    assert value.state == "DERIVATIVE_CONFIRMED"
    assert value.outcome.outcome == "RESOLVED_CORRECTLY"


# --- NO_DEPENDENCE_FOUND vs INDEPENDENCE_SUPPORTED -------------------------

def test_constructor_forbids_independence_from_absence():
    explanation = DependenceExplanation(
        ("some-positive",), (), (), (), {}, False, True)
    with pytest.raises(ValueError, match="absence"):
        DependenceAssessment("a-1", "pub-a", "pub-b", "INDEPENDENCE_SUPPORTED", (),
                             explanation, outcome_ok(), AWARE)


def test_explanation_forbids_affirmative_and_absence_together():
    with pytest.raises(ValueError, match="contradicts"):
        DependenceExplanation(("evidence",), (), (), (), {}, True, True)


def test_no_signals_is_no_dependence_found_not_independence():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"), ())
    assert value.state == "NO_DEPENDENCE_FOUND"
    assert value.state != "INDEPENDENCE_SUPPORTED"
    assert value.explanation.only_absence_of_discovered_dependence
    assert not value.explanation.independence_affirmatively_supported
    assert value.outcome.outcome == "RESOLVED_WITH_MATERIAL_QUALIFICATION"
    assert "ONLY_ABSENCE_OF_DISCOVERED_DEPENDENCE" in value.outcome.material_qualifications


def test_single_weak_signal_cannot_confirm_dependence():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                (signal("PUBLICATION_TIMING", strength="WEAK",
                                        detail="published within the same afternoon"),))
    assert value.state == "INDEPENDENCE_UNKNOWN"
    assert value.state not in {"DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE",
                               "SYNDICATION_DERIVATIVE", "MIRROR_MANIFESTATION"}
    assert value.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    assert len(value.outcome.evidence_insufficiency_demonstration) >= 40


def test_single_weak_independence_signal_is_insufficient():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                (signal("DISTINCT_REPORTING_DETAIL", strength="WEAK",
                                        detail="one extra sentence of colour"),))
    assert value.state == "NO_DEPENDENCE_FOUND"
    assert value.explanation.only_absence_of_discovered_dependence


def test_single_strong_independence_signal_is_insufficient():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                (signal("DISTINCT_AUTHORSHIP_EVIDENCE"),))
    assert value.state == "NO_DEPENDENCE_FOUND"


def test_independence_supported_needs_positive_multi_signal():
    value = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                INDEPENDENCE_PAIR)
    assert value.state == "INDEPENDENCE_SUPPORTED"
    assert value.explanation.independence_affirmatively_supported
    assert not value.explanation.only_absence_of_discovered_dependence
    assert value.explanation.positive_evidence


def test_unnegated_dependence_signal_blocks_independence():
    overlap = signal("NORMALIZED_PARAGRAPH_OVERLAP", strength="MODERATE",
                     detail="two normalized paragraphs overlap between the articles")
    blocked = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                  INDEPENDENCE_PAIR + (overlap,))
    assert blocked.state != "INDEPENDENCE_SUPPORTED"
    assert blocked.state == "INDEPENDENCE_UNKNOWN"
    negated = signal("NORMALIZED_PARAGRAPH_OVERLAP", strength="MODERATE",
                     detail="two normalized paragraphs overlap between the articles",
                     negated=True,
                     negation_basis="overlap traced to a statute both articles quote verbatim")
    cleared = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                  INDEPENDENCE_PAIR + (negated,))
    assert cleared.state == "INDEPENDENCE_SUPPORTED"
    assert cleared.explanation.negative_evidence


# --- partial dependence is never collapsed ---------------------------------

def test_partial_dependence_not_collapsed():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("EXPLICIT_REUSE_STATEMENT", direction="LEFT_FROM_RIGHT",
                detail="middle section reproduced with credit from the other outlet"),)
        + INDEPENDENCE_PAIR)
    assert value.state == "PARTIAL_DEPENDENCE"
    assert value.outcome.outcome == "RESOLVED_WITH_MATERIAL_QUALIFICATION"
    assert value.outcome.material_qualifications


def test_partial_dependence_constructor_guard():
    explanation = DependenceExplanation((), (), (), (), {}, False, False)
    with pytest.raises(ValueError, match="collapsed"):
        DependenceAssessment("a-2", "pub-a", "pub-b", "PARTIAL_DEPENDENCE",
                             (signal("EXPLICIT_CITATION"),), explanation, outcome_ok(), AWARE)


# --- shared data vs shared analysis ----------------------------------------

def test_shared_data_requires_distinct_analysis_evidence():
    dataset = signal("COMMON_PRIMARY_DATASET", strength="MODERATE",
                     detail="both cite the same public air-quality measurement table")
    bare = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"), (dataset,))
    assert bare.state == "COMMON_EVIDENCE_BASIS_CONFIRMED"
    weak_analysis = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (dataset, signal("DISTINCT_REPORTING_DETAIL", strength="WEAK",
                         detail="slightly different headline")))
    assert weak_analysis.state == "COMMON_EVIDENCE_BASIS_CONFIRMED"
    lifted = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (dataset, signal("DISTINCT_AUTHORSHIP_EVIDENCE",
                         detail="separate methodology sections by different named analysts")))
    assert lifted.state == "SHARED_DATA_INDEPENDENT_ANALYSIS"
    assert lifted.explanation.independence_affirmatively_supported


# --- disputed, unknown, capability failure ---------------------------------

def test_contested_signal_yields_disputed():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("EXPLICIT_CITATION", contested=True,
                contest_basis="publisher denies the citation; archived copy is inconclusive"),))
    assert value.state == "DEPENDENCE_DISPUTED"
    assert value.state in NON_CORROBORATING_STATES
    assert value.explanation.unresolved_signals
    assert "DISPUTED" in value.outcome.material_qualifications


def test_possible_undisclosed_briefing_is_unknown_with_demonstration():
    value = classify_dependence(
        pub("pub-a"), pub("pub-b", family="family-beta"),
        (signal("COMMON_ANONYMOUS_ATTRIBUTION", strength="MODERATE",
                detail="both attribute the figure to an unnamed ministry official"),))
    assert value.state == "INDEPENDENCE_UNKNOWN"
    assert value.outcome.outcome == "EPISTEMICALLY_UNRESOLVABLE"
    assert "underdetermined" in value.outcome.evidence_insufficiency_demonstration
    assert value.explanation.unresolved_signals


def test_opposed_strong_directions_raise_capability_failure():
    signals = (
        signal("TRANSLATION_ALIGNMENT", direction="LEFT_FROM_RIGHT",
               detail="aligned sentences suggest the left text translates the right"),
        signal("EXPLICIT_REUSE_STATEMENT", direction="RIGHT_FROM_LEFT",
               detail="right text states it reproduces the left text"),
    )
    with pytest.raises(DependenceCapabilityError) as err:
        classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"), signals)
    assert err.value.outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert err.value.outcome.capability_failure_class == "DEPENDENCE_EVIDENCE_UNINTERPRETED"


def test_same_publication_pair_raises():
    with pytest.raises(ValueError, match="singleton"):
        classify_dependence(pub("pub-a"), pub("pub-a"), ())


# --- corroboration arithmetic ----------------------------------------------

def _states_fixture():
    a, b, c, d = (pub("pub-a"), pub("pub-b", family="family-beta"),
                  pub("pub-c", family="family-gamma"), pub("pub-d", family="family-delta"))
    derivative = classify_dependence(a, b, (signal("EXPLICIT_REUSE_STATEMENT"),))
    unknown = classify_dependence(a, c, (signal("PUBLICATION_TIMING", strength="WEAK",
                                                detail="same-day publication"),))
    nothing = classify_dependence(b, c, ())
    disputed = classify_dependence(a, d, (signal(
        "EXPLICIT_CITATION", contested=True,
        contest_basis="the cited passage cannot be located in any archived version"),))
    return (a, b, c, d), (derivative, unknown, nothing, disputed)


def test_corroboration_zeroes_for_non_corroborating_states():
    pubs, assessments = _states_fixture()
    assert all(item.state in NON_CORROBORATING_STATES for item in assessments)
    summary = corroboration_arithmetic(claim_family_id="claim-family-1",
                                       publications=pubs, assessments=assessments,
                                       evidence_basis_ids=("basis-1", "basis-2"))
    assert summary.independent_corroboration_count == 0
    assert summary.corroborating_pair_ids == ()
    assert summary.publication_count == 4
    assert summary.manifestation_count == 4
    assert summary.source_family_count == 4
    assert summary.evidence_basis_count == 2
    assert summary.confirmed_derivative_count == 1
    assert summary.independence_unknown_count == 1
    assert summary.no_dependence_found_count == 1


def test_corroboration_counts_only_supported_pairs():
    a, b, c = (pub("pub-a"), pub("pub-b", family="family-beta"),
               pub("pub-c", family="family-gamma"))
    supported = classify_dependence(a, b, INDEPENDENCE_PAIR)
    shared = classify_dependence(a, c, (
        signal("COMMON_PRIMARY_DATASET", strength="MODERATE",
               detail="both analyse the same procurement register extract"),
        signal("DISTINCT_AUTHORSHIP_EVIDENCE",
               detail="different named analysts with separate methods sections")))
    derivative = classify_dependence(b, c, (signal("EXPLICIT_REUSE_STATEMENT"),))
    summary = corroboration_arithmetic(claim_family_id="claim-family-2",
                                       publications=(a, b, c),
                                       assessments=(supported, shared, derivative))
    assert supported.state == "INDEPENDENCE_SUPPORTED"
    assert shared.state == "SHARED_DATA_INDEPENDENT_ANALYSIS"
    assert summary.independent_corroboration_count == 2
    assert len(summary.corroborating_pair_ids) == 2
    assert summary.independence_supported_count == 1


def test_singleton_family_zero_corroboration():
    summary = corroboration_arithmetic(claim_family_id="claim-family-3",
                                       publications=(pub("pub-solo"),), assessments=())
    assert summary.publication_count == 1
    assert summary.independent_corroboration_count == 0


def test_singleton_summary_constructor_guard():
    with pytest.raises(ValueError, match="singleton"):
        ClaimFamilySummary("s-1", "claim-family-4", 1, 1, 1, 0, 0, 0, 0, 0, 0, 1,
                           ("assessment-x",), AWARE)


def test_summary_count_must_match_pair_records():
    with pytest.raises(ValueError, match="per-pair"):
        ClaimFamilySummary("s-2", "claim-family-5", 3, 3, 3, 0, 0, 0, 1, 0, 0, 2,
                           ("assessment-x",), AWARE)


def test_mirrored_publications_collapse_manifestation_count():
    fingerprint = "a" * 64
    pubs = (pub("pub-a", fingerprint=fingerprint),
            pub("pub-b", family="family-beta", fingerprint=fingerprint))
    mirror = classify_dependence(*pubs, ())
    summary = corroboration_arithmetic(claim_family_id="claim-family-6",
                                       publications=pubs, assessments=(mirror,))
    assert summary.publication_count == 2
    assert summary.manifestation_count == 1
    assert summary.independent_corroboration_count == 0


# --- family vs member ------------------------------------------------------

def test_family_state_requires_complete_pair_records():
    members = ("pub-a", "pub-b", "pub-c")
    only_one = classify_dependence(pub("pub-a"), pub("pub-b", family="family-beta"),
                                   (signal("EXPLICIT_REUSE_STATEMENT"),))
    with pytest.raises(ValueError, match="per-pair"):
        family_state(family_id="family-alpha", member_publication_ids=members,
                     pair_assessments=(only_one,))


def test_one_dependent_pair_blocks_family_independence():
    a, b, c = (pub("pub-a"), pub("pub-b", family="family-beta"),
               pub("pub-c", family="family-gamma"))
    record = family_state(
        family_id="family-alpha", member_publication_ids=("pub-a", "pub-b", "pub-c"),
        pair_assessments=(
            classify_dependence(a, b, (signal("EXPLICIT_REUSE_STATEMENT"),)),
            classify_dependence(a, c, INDEPENDENCE_PAIR),
            classify_dependence(b, c, INDEPENDENCE_PAIR)))
    assert record.family_state == "PARTIAL_DEPENDENCE"
    assert len(record.pair_states) == 3


def test_family_all_pairs_independent():
    a, b, c = (pub("pub-a"), pub("pub-b", family="family-beta"),
               pub("pub-c", family="family-gamma"))
    record = family_state(
        family_id="family-alpha", member_publication_ids=("pub-a", "pub-b", "pub-c"),
        pair_assessments=tuple(classify_dependence(left, right, INDEPENDENCE_PAIR)
                               for left, right in ((a, b), (a, c), (b, c))))
    assert record.family_state == "INDEPENDENCE_SUPPORTED"


def test_singleton_family_never_independent():
    record = family_state(family_id="family-alpha", member_publication_ids=("pub-solo",),
                          pair_assessments=())
    assert record.family_state == "NO_DEPENDENCE_FOUND"
    with pytest.raises(ValueError, match="singleton"):
        FamilyDependenceRecord("f-1", "family-alpha", ("pub-solo",),
                               "INDEPENDENCE_SUPPORTED", (), "invalid", AWARE)


# --- legacy bridge ---------------------------------------------------------

def test_legacy_independent_maps_conservatively():
    basis = evidence_basis(case_id="case-neutral", source_object_ids=("src-1",),
                           candidate_ids=("cand-1",), source_family_id="family-alpha",
                           independence_state="INDEPENDENT")
    bridge = from_legacy_basis(basis)
    assert bridge.mapped_state == "NO_DEPENDENCE_FOUND"
    upgraded = from_legacy_basis(basis, positive_independence_signals=INDEPENDENCE_PAIR)
    assert upgraded.mapped_state == "INDEPENDENCE_SUPPORTED"
    assert upgraded.positive_evidence_signal_ids


def test_legacy_dependent_and_unknown_mapping():
    dependent = evidence_basis(case_id="case-neutral", source_object_ids=("src-1",),
                               candidate_ids=("cand-1",), source_family_id="family-alpha",
                               independence_state="DEPENDENT")
    unknown = evidence_basis(case_id="case-neutral", source_object_ids=("src-2",),
                             candidate_ids=("cand-2",), source_family_id="family-alpha",
                             independence_state="UNKNOWN_DEPENDENCE")
    assert from_legacy_basis(dependent).mapped_state == "DERIVATIVE_CONFIRMED"
    assert from_legacy_basis(unknown).mapped_state == "INDEPENDENCE_UNKNOWN"
    assert from_legacy_basis(unknown.to_record()).mapped_state == "INDEPENDENCE_UNKNOWN"


def test_legacy_invalid_label_raises():
    bad = EvidenceBasis("basis-x", "case-x", ("src-1",), ("cand-1",), "family-alpha",
                        "TOTALLY_SURE", True, "CURRENT", "UNREVIEWED")
    with pytest.raises(ValueError, match="legacy"):
        from_legacy_basis(bad)


def test_legacy_single_weak_signal_does_not_upgrade():
    basis = evidence_basis(case_id="case-neutral", source_object_ids=("src-1",),
                           candidate_ids=("cand-1",), source_family_id="family-alpha",
                           independence_state="INDEPENDENT")
    weak = (signal("DISTINCT_REPORTING_DETAIL", strength="WEAK",
                   detail="marginally different phrasing"),)
    assert from_legacy_basis(basis, positive_independence_signals=weak).mapped_state == \
        "NO_DEPENDENCE_FOUND"


# --- signal contract guards ------------------------------------------------

def test_signal_guards():
    with pytest.raises(ValueError, match="kind"):
        signal("MADE_UP_KIND")
    with pytest.raises(ValueError, match="evidence"):
        dependence_signal(kind="EXPLICIT_CITATION", strength="STRONG",
                          detail="citation", evidence_refs=())
    with pytest.raises(ValueError, match="negation basis"):
        signal("EXPLICIT_CITATION", negated=True)
    with pytest.raises(ValueError, match="strength"):
        signal("EXPLICIT_CITATION", strength="OVERWHELMING")
    with pytest.raises(ValueError, match="direction"):
        signal("DISTINCT_AUTHORSHIP_EVIDENCE", direction="LEFT_FROM_RIGHT")
