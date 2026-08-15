"""V5.5 — dependent adjudication makes cross-surface contradiction unrepresentable."""
from __future__ import annotations

import itertools

import pytest

from curunir_operational.v5_4 import publication as PUB
from curunir_operational.v5_4 import support as SUP
from curunir_operational.v5_5 import adjudication as ADJ

pytestmark = pytest.mark.no_db


class _Support:
    """A frozen Surface-4 verdict, as the dependent question consumes it."""

    def __init__(self, support_class, *, first_failing_stage=None):
        self.support_class = support_class
        self.support_id = f"support-{support_class.lower()}"
        self.first_failing_stage = first_failing_stage


def question_for(support_class, wording="Agency X operates system Y."):
    return ADJ.build_question(
        proposition_id="p1", claim_id="c1", support=_Support(support_class),
        wording=wording)


# ---------------------------------------------------------------- the property
@pytest.mark.parametrize("support_class", SUP.SUPPORT_CLASSES)
def test_no_question_offers_a_contradicting_answer(support_class):
    """The property this module exists to hold, over every support class."""
    question = question_for(support_class)
    for answer in question.allowed_answers:
        assert not ADJ.contradicts(support_class, answer), (
            f"{support_class} offers {answer}, which is a contradiction")


@pytest.mark.parametrize("support_class,disposition", list(itertools.product(
    SUP.SUPPORT_CLASSES, PUB.DISPOSITIONS)))
def test_contradicting_answers_are_refused_at_recording(support_class, disposition):
    question = question_for(support_class)
    if disposition in question.allowed_answers:
        # Permitted by Section 6.2: it must record cleanly and must not be a
        # contradiction.  Asserting that is the other half of the property.
        recorded = ADJ.record_answer(question, seat_id="REVIEWER_A",
                                     answer=disposition, reasoning="because")
        assert recorded.answer == disposition
        assert not ADJ.contradicts(support_class, disposition)
        return
    with pytest.raises(ADJ.AdjudicationViolation, match="not offered"):
        ADJ.record_answer(question, seat_id="REVIEWER_A", answer=disposition,
                          reasoning="because")


@pytest.mark.parametrize("support_class", sorted(PUB.NEVER_FACTUAL))
def test_negative_support_never_offers_a_factual_publication(support_class):
    offered = set(question_for(support_class).allowed_answers)
    assert not (offered & PUB.FACTUAL_DISPOSITIONS)


@pytest.mark.parametrize("support_class", sorted(SUP.AFFIRMATIVE_SUPPORT))
def test_affirmative_support_never_offers_rejected_unsupported(support_class):
    assert "REJECTED_UNSUPPORTED" not in question_for(support_class).allowed_answers


# ---------------------------------------------------------------- the channel
@pytest.mark.parametrize("support_class", SUP.SUPPORT_CLASSES)
def test_every_question_offers_the_upstream_referral(support_class):
    """Disagreement with Surface 4 must always be expressible — as a referral."""
    assert ADJ.UPSTREAM_REFERRAL in question_for(support_class).allowed_answers


def test_the_referral_is_not_a_publication_disposition():
    assert ADJ.UPSTREAM_REFERRAL not in PUB.DISPOSITIONS


def test_question_text_directs_disagreement_to_the_referral():
    text = question_for("NOT_SUPPORTED").text
    assert ADJ.UPSTREAM_REFERRAL in text
    assert "do not express that view through a publication decision" in text


def test_referral_is_recordable_and_audited_as_such():
    question = question_for("NOT_SUPPORTED")
    answer = ADJ.record_answer(
        question, seat_id="REVIEWER_A", answer=ADJ.UPSTREAM_REFERRAL,
        reasoning="the evidence does carry this; the support verdict looks wrong")
    audit = ADJ.audit_panel([answer], {question.question_id: question})
    assert audit["upstream_referrals"] == 1
    assert audit["contradictions"] == 0
    assert audit["verdict"] == "PASS"


# ---------------------------------------------------------------- construction
def test_a_question_without_a_frozen_verdict_is_refused():
    with pytest.raises(ADJ.AdjudicationViolation, match="frozen Surface-4 verdict"):
        ADJ.build_question(proposition_id="p", claim_id="c", support=None,
                           wording="x")


def test_a_hand_built_question_with_a_wider_answer_set_is_refused():
    with pytest.raises(ADJ.AdjudicationViolation, match="exactly the dispositions"):
        ADJ.DependentQuestion(
            "q", "p", "c", "NOT_SUPPORTED", "s", None, "wording",
            ("PUBLISHED", "REJECTED_UNSUPPORTED"), "V5_5_DEPENDENT_1",
            "2026-07-25T00:00:00+00:00")


def test_support_class_participates_in_question_identity():
    a = question_for("FULL_SUPPORT")
    b = question_for("NOT_SUPPORTED")
    assert a.identity != b.identity


def test_empty_reasoning_is_refused():
    question = question_for("FULL_SUPPORT")
    with pytest.raises(ADJ.AdjudicationViolation, match="requires reasoning"):
        ADJ.record_answer(question, seat_id="REVIEWER_A", answer="PUBLISHED",
                          reasoning="   ")


def test_answers_for_unknown_questions_fail_the_audit():
    question = question_for("FULL_SUPPORT")
    answer = ADJ.record_answer(question, seat_id="A", answer="PUBLISHED",
                               reasoning="fine")
    audit = ADJ.audit_panel([answer], {})
    assert audit["answers_for_unknown_questions"] == 1
    assert audit["verdict"] == "FAIL"


# ---------------------------------------------------------------- retrospective
def test_historic_contradictions_map_to_referrals():
    """The four V5.3 contradictions that were unanimous on both sides."""
    pairs = [
        ("claim-a", "FULL_SUPPORT", "REJECTED_UNSUPPORTED"),
        ("claim-b", "NOT_SUPPORTED", "PUBLISHED"),
        ("claim-c", "NOT_SUPPORTED", "PUBLISHED"),
        ("claim-d", "FULL_SUPPORT", "PUBLISHED"),
    ]
    result = ADJ.reclassify_historic(pairs)
    assert result["contradictions_representable_under_new_protocol"] == 0
    assert result["would_have_been_referrals"] == 3
    assert result["outcomes"]["EXPRESSIBLE_UNCHANGED"] == 1


def test_referral_worklist_names_the_claims_surface_four_must_revisit():
    question = question_for("NOT_SUPPORTED")
    answers = [ADJ.record_answer(question, seat_id=seat,
                                 answer=ADJ.UPSTREAM_REFERRAL,
                                 reasoning=f"{seat} disputes the support verdict")
               for seat in ("REVIEWER_A", "REVIEWER_B")]
    questions = {question.question_id: question}
    worklist = ADJ.referral_worklist(
        ADJ.audit_panel(answers, questions), answers, questions)
    assert worklist["claims_referred"] == 1
    assert worklist["claims"][0]["seats_referring"] == 2


def test_reclassify_reports_the_allowed_set_it_measured_against():
    result = ADJ.reclassify_historic([("c", "NOT_SUPPORTED", "PUBLISHED")])
    row = result["rows"][0]
    assert "PUBLISHED" not in row["allowed_under_dependent_protocol"]
    assert ADJ.UPSTREAM_REFERRAL in row["allowed_under_dependent_protocol"]
