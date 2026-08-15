"""D38 — the remaining construction repairs, and the shortcuts each one invites.

Four repairs are guarded here.  Each closed exactly one witness, so each is one
neighbouring-negative away from being a rule about that witness:

  * Arabic fronted لـ-predication -- must not turn every initial لـ into a
    predicate, and its delayed subject must stop at the clause boundary;
  * French reflexive infinitives -- the clitic belongs to the verb, and must not
    become an argument;
  * Russian hyphenated compounds -- admitted on agreement, not on a suffix;
  * V581-D41, the zero-copula fallback reading a coordinate list as predication.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _ar(text: str):
    return V2._arabic_candidates(text, V2._tokens(text))


def _ru(text: str):
    return V2._cyrillic_candidates(text, V2._tokens(text))


def _lat(text: str):
    return V2._latin_candidates(text, V2._tokens(text))


def _heads(cands):
    return [c for c in cands if c.role_type == "PREDICATE_HEAD"]


def _subjects(cands):
    return [c for c in cands if c.role_type == "SUBJECT"]


# --- Arabic fronted لـ-predication -----------------------------------------

FRONTED = "لمجلس الأمن أن يقرر ما يجب اتخاذه من التدابير، وله أن يطلب"


def test_a_fronted_lam_phrase_with_a_delayed_subject_predicates():
    heads = _heads(_ar(FRONTED))
    assert any(h.origin_rule == "AR_FRONTED_PREDICATE" for h in heads)


def test_the_delayed_subject_stops_at_the_clause_boundary():
    """The subject is the أن-clause, not the rest of the span.

    The first version of this rule ran to the end of the span -- 329 characters
    where the reference names 85 -- which would have passed the unit on a
    candidate wide enough to contain almost anything.
    """
    subjects = [c for c in _subjects(_ar(FRONTED))
                if c.origin_rule == "AR_DELAYED_SUBJECT"]
    assert subjects
    assert all("،" not in c.text for c in subjects)
    assert all(len(c.text) < len(FRONTED) * 0.8 for c in subjects)


def test_a_lam_phrase_that_does_not_open_the_span_does_not_predicate():
    """Neighbouring negative: later in the clause it is an argument."""
    text = "قدم الأمين العام التقرير لمجلس الأمن أن ينظر فيه"
    assert not any(h.origin_rule == "AR_FRONTED_PREDICATE" for h in _heads(_ar(text)))


def test_a_fronted_lam_phrase_with_no_delayed_subject_does_not_predicate():
    """Neighbouring negative: a beneficiary with nothing predicated of it."""
    text = "لمجلس الأمن الدور الرئيسي في حفظ السلام والأمن الدوليين"
    assert not any(h.origin_rule == "AR_FRONTED_PREDICATE" for h in _heads(_ar(text)))


def test_a_verbal_sentence_with_a_fronted_lam_adjunct_does_not_predicate():
    """Neighbouring negative: a finite verb makes the لـ-phrase an adjunct."""
    text = "لمجلس الأمن يقرر أن يتخذ التدابير اللازمة"
    assert not any(h.origin_rule == "AR_FRONTED_PREDICATE" for h in _heads(_ar(text)))


def test_the_preposition_alone_never_licenses_the_reading():
    """All three conditions are required, not any one of them."""
    for text in ("لمجلس الأمن التدابير اللازمة",
                 "التقرير لمجلس الأمن أن ينظر",
                 "لمجلس الأمن تتطلب أن يقرر"):
        assert not any(h.origin_rule == "AR_FRONTED_PREDICATE"
                       for h in _heads(_ar(text))), text


# --- V581-D41: coordinate lists are not predications ------------------------

def test_a_coordinate_nominal_list_is_not_a_proposition():
    assert not _heads(_ar("الأدوية، اللقاحات، الفحوصات، المعدات الطبية"))


def test_a_genuine_zero_copula_nominal_sentence_still_predicates():
    """Neighbouring negative for the guard itself: it must not eat real ones."""
    assert _heads(_ar("الأدوية المضادة للفيروسات مفيدة جدا في العلاج"))


def test_a_list_containing_a_predicative_segment_is_still_a_sentence():
    """One predicative element is enough to make this not a list."""
    assert _heads(_ar("الأدوية، اللقاحات، الفحوصات تُستخدم في العلاج"))


def test_two_segments_are_not_enough_to_call_it_a_list():
    """A short apposition is not an inventory."""
    assert _heads(_ar("الأدوية المضادة، اللقاحات الحديثة مفيدة"))


# --- French reflexive infinitives ------------------------------------------

def test_a_reflexive_infinitive_head_spans_both_tokens():
    heads = _heads(_lat("se couvrir la bouche et le nez lorsque l’on tousse"))
    assert any(h.text.strip().startswith("se ") and "couvrir" in h.text
               for h in heads)


def test_the_clitic_is_marked_as_part_of_the_predicate():
    heads = [h for h in _heads(_lat("se couvrir la bouche et le nez"))
             if "couvrir" in h.text]
    assert heads
    assert any("REFLEXIVE_CLITIC" in h.syntactic for h in heads)


def test_the_clitic_does_not_become_a_subject():
    """Neighbouring negative: se is not an argument."""
    subjects = _subjects(_lat("se couvrir la bouche et le nez"))
    assert not any(c.text.strip().lower() in ("se", "s'", "s’") for c in subjects)


def test_a_non_reflexive_infinitive_head_is_unchanged():
    """The widening applies only where a clitic actually precedes the verb."""
    heads = [h for h in _heads(_lat("couvrir la bouche et le nez")) if "couvrir" in h.text]
    assert heads
    assert all("REFLEXIVE_CLITIC" not in h.syntactic for h in heads)


# --- Russian hyphenated compounds ------------------------------------------

def test_a_hyphenated_compound_before_a_finite_verb_is_a_subject():
    cands = _ru("что матери-подростки подвергаются более высокому риску")
    assert any("матери-подростки" in c.text for c in _subjects(cands))


def test_the_compound_is_one_candidate_with_its_full_offsets():
    text = "что матери-подростки подвергаются более высокому риску"
    matching = [c for c in _subjects(_ru(text)) if "матери-подростки" in c.text]
    assert matching
    for candidate in matching:
        start, stop = candidate.span
        assert text[start:stop].strip() == "матери-подростки"


def test_a_hyphenated_compound_with_no_following_verb_is_not_admitted():
    """Neighbouring negative: agreement is the evidence, not the hyphen.

    Without a finite verb to agree with, the ambiguous ending stays ambiguous
    and the compound must not be promoted to subject.
    """
    cands = _ru("доклад о положении матери-подростки в регионе")
    assert not any("матери-подростки" in c.text for c in _subjects(cands))


def test_the_compound_route_does_not_admit_ordinary_oblique_nominals():
    """Neighbouring negative: an oblique noun before a verb stays out."""
    cands = _ru("в беременности подвергаются риску")
    assert not any(c.text.strip() == "беременности" for c in _subjects(cands))
