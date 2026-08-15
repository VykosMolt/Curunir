"""Dossier architecture and sufficiency tests (contract Section 10).

The two V5.1 surfaces that scored zero did so because their review unit could
not carry the evidence the decision needs.  These tests assert that the
rebuilt units carry it and that the validator refuses anything that does not.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_2 import dossiers as D

pytestmark = pytest.mark.no_db


def _filled(kind: str, **overrides) -> dict:
    """A dossier whose every required section records evidence or its absence."""
    sections = {name: {"state": "NOT_PRESENT",
                       "checked": "the section was looked for and is not stated"}
                for name in D.REQUIRED_SECTIONS[kind]}
    sections.update(overrides)
    return sections


def _build(kind: str, sections: dict, **kwargs) -> D.Dossier:
    return D.build_dossier(
        kind=kind, decision_question="What does the evidence establish?",
        decision_options=["OPTION_A", "OPTION_B"], sections=sections, **kwargs)


def test_all_seven_dossier_kinds_are_defined():
    assert len(D.DOSSIER_KINDS) == 7
    assert set(D.REQUIRED_SECTIONS) == set(D.DOSSIER_KINDS)
    assert set(D.SURFACE_FOR_KIND) == set(D.DOSSIER_KINDS)


def test_every_dossier_offers_an_unresolvable_escape():
    dossier = _build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER"))
    assert "EPISTEMICALLY_UNRESOLVABLE" in dossier.decision_options
    assert "CANNOT_ADJUDICATE" in dossier.decision_options


def test_a_dossier_must_offer_alternatives():
    with pytest.raises(ValueError, match="alternatives"):
        D.build_dossier(kind="SOURCE_DOSSIER", decision_question="Which role?",
                        decision_options=[], sections=_filled("SOURCE_DOSSIER"))


def test_content_hash_binds_the_payload():
    dossier = _build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER"))
    assert dossier.content_hash
    with pytest.raises(ValueError, match="content hash mismatch"):
        D.Dossier(**{**dossier.public_payload(), "content_hash": "0" * 64})


# ---------------------------------------------------------------------------
# Section 10.8 — sufficiency certification
# ---------------------------------------------------------------------------

def test_source_dossier_with_every_section_is_self_sufficient():
    sections = _filled(
        "SOURCE_DOSSIER",
        url="https://example.europa.eu/press/x",
        domain="example.europa.eu",
        title_page="Press release of the issuing directorate-general",
        issuing_institution="the issuing directorate-general")
    certificate = D.certify_sufficiency(_build("SOURCE_DOSSIER", sections))
    assert certificate.result == "SELF_SUFFICIENT"
    assert not certificate.missing_sections


def test_the_v5_1_source_packet_shape_is_a_construction_defect():
    # V5.1 shipped one body excerpt and asked for a publisher.  Every other
    # section the decision needs is simply absent.
    certificate = D.certify_sufficiency(
        _build("SOURCE_DOSSIER", {"title_page": "an excerpt of body prose"}))
    assert certificate.result == "CONSTRUCTION_DEFECT"
    assert "MISSING_REQUIRED_SECTION" in certificate.defect_classes
    assert "url" in certificate.missing_sections
    assert "redirect_chain" in certificate.missing_sections


def test_the_v5_1_claim_packet_shape_is_circular():
    sections = _filled(
        "CLAIM_EVIDENCE_DOSSIER",
        normalized_claim={"normalized_statement": "The provider is the best."},
        supporting_spans=[{"text": "The provider is the best."}])
    certificate = D.certify_sufficiency(_build("CLAIM_EVIDENCE_DOSSIER", sections))
    assert certificate.result == "CONSTRUCTION_DEFECT"
    assert "CIRCULAR_EVIDENCE" in certificate.defect_classes


def test_distinct_supporting_evidence_repairs_the_circularity():
    sections = _filled(
        "CLAIM_EVIDENCE_DOSSIER",
        normalized_claim={"normalized_statement": "The provider is the best."},
        supporting_spans=[{"text": "An independent regulator ranked it first."}])
    assert D.certify_sufficiency(
        _build("CLAIM_EVIDENCE_DOSSIER", sections)).result == "SELF_SUFFICIENT"


def test_an_explicit_absence_counts_as_evidence_but_silence_does_not():
    present = _filled("SOURCE_DOSSIER")
    assert D.certify_sufficiency(_build("SOURCE_DOSSIER", present)).result == \
        "SELF_SUFFICIENT"
    silent = dict(present)
    silent["issuing_institution"] = None
    assert D.certify_sufficiency(_build("SOURCE_DOSSIER", silent)).result == \
        "CONSTRUCTION_DEFECT"


def test_unresolvable_requires_a_substantive_demonstration():
    dossier = _build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER"))
    with pytest.raises(ValueError, match="demonstrate"):
        D.SufficiencyCertificate(
            "certificate-1", dossier.dossier_id, dossier.kind,
            "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE", (), (),
            "rationale", "too short", D.VALIDATOR_VERSION, "2026-07-24T00:00:00+00:00")


def test_unresolvable_is_available_when_the_evidence_genuinely_cannot_settle_it():
    dossier = _build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER"))
    certificate = D.certify_sufficiency(
        dossier,
        unresolvability_demonstration=(
            "No publisher is stated on the manifestation, the domain registrant "
            "is redacted, and the document series has no register entry."))
    assert certificate.result == "INHERENTLY_UNRESOLVABLE_FROM_PUBLIC_EVIDENCE"


def test_answer_leakage_is_a_construction_defect():
    sections = _filled("SOURCE_DOSSIER",
                       title_page="the correct answer here is PUBLISHED_BY")
    certificate = D.certify_sufficiency(
        _build("SOURCE_DOSSIER", sections), sealed_answer="PUBLISHED_BY")
    assert certificate.result == "CONSTRUCTION_DEFECT"
    assert "ANSWER_LEAKAGE" in certificate.defect_classes


def test_the_decision_menu_is_not_treated_as_leakage():
    # The menu necessarily contains the right option; that is what a menu is.
    dossier = D.build_dossier(
        kind="SOURCE_DOSSIER", decision_question="Which role?",
        decision_options=["PUBLISHED_BY", "HOSTED_BY", "ISSUED_BY"],
        sections=_filled("SOURCE_DOSSIER"))
    assert D.certify_sufficiency(dossier, sealed_answer="PUBLISHED_BY").result == \
        "SELF_SUFFICIENT"


def test_a_corpus_with_a_defect_is_not_sealable():
    good = _build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER"))
    bad = _build("SOURCE_DOSSIER", {"url": "https://example.org/x"})
    result = D.certify_corpus([good, bad])
    assert result["counts"]["CONSTRUCTION_DEFECT"] == 1
    assert result["sealable"] is False
    assert bad.dossier_id in result["construction_defect_ids"]


def test_a_clean_corpus_is_sealable():
    dossiers = [_build("SOURCE_DOSSIER", _filled("SOURCE_DOSSIER")),
                _build("SPAN_DOSSIER", _filled("SPAN_DOSSIER"))]
    result = D.certify_corpus(dossiers)
    assert result["sealable"] is True
    assert result["counts"]["SELF_SUFFICIENT"] == 2


@pytest.mark.parametrize("kind", D.DOSSIER_KINDS)
def test_every_kind_can_be_built_and_certified(kind):
    certificate = D.certify_sufficiency(_build(kind, _filled(kind)))
    assert certificate.result in D.SUFFICIENCY_RESULTS


def test_no_review_anyway_result_exists():
    # The contract forbids NOT_SELF_SUFFICIENT_BUT_REVIEW_ANYWAY; there are
    # exactly three results and none of them is it.
    assert len(D.SUFFICIENCY_RESULTS) == 3
    assert "NOT_SELF_SUFFICIENT_BUT_REVIEW_ANYWAY" not in D.SUFFICIENCY_RESULTS
