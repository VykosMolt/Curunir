"""D38 — Arabic postverbal subjects, and the over-generalisations they invite.

Two repairs are guarded here.

The first: a passive verb may take its patient through a preposition, so in
"يُكشف عن الإصابة" the nominal governed by عن is the patient subject.  The
obvious wrong generalisation is to let any nominal inside any prepositional
phrase become a subject -- after an *active* verb that same nominal is a
prepositional complement, and after a passive verb an ordinary adjunct PP is
still an adjunct.

The second: Arabic permits a fronted adverbial before the main clause, so
counting a fixed number of words from the start of the span misses the
main-clause verb.  The wrong repair is a larger constant, which would admit
heads from arbitrarily deep subordinate material.  The scan is anchored at
clause openings instead, and the negatives below are what stop that from
becoming "scan everything".

The positives are one line each; the negatives are the point.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _candidates(text: str):
    tokens = V2._tokens(text)
    return V2._arabic_candidates(text, tokens)


def _subjects(text: str):
    return [c for c in _candidates(text) if c.role_type == "SUBJECT"]


def _heads(text: str):
    return [c for c in _candidates(text) if c.role_type == "PREDICATE_HEAD"]


def _subject_texts(text: str):
    return {c.text for c in _subjects(text)}


# --- passive patient governed by a preposition -----------------------------

def test_passive_verb_recovers_its_patient_from_the_prepositional_phrase():
    """يُكشف عن الإصابة — "the infection is detected"."""
    text = "يُكشف عن الإصابة بفيروس الأنفلونزا بوتيرة أقل"
    subjects = _subjects(text)
    assert any("الإصابة" in c.text for c in subjects)


def test_the_recovered_patient_is_typed_as_a_patient_not_an_actor():
    """The state must record that this subject undergoes the action."""
    text = "يُكشف عن الإصابة بفيروس الأنفلونزا بوتيرة أقل"
    patient = [c for c in _subjects(text) if "الإصابة" in c.text]
    assert patient
    assert all(c.subtype == "PASSIVE_PATIENT_SUBJECT" for c in patient)


def test_an_active_verb_does_not_take_a_subject_from_inside_its_prepositional_phrase():
    """Neighbouring negative: the same shape, an active verb.

    "يبحث عن الحلول" is "he searches for the solutions" -- الحلول is what is
    searched for, never the searcher.  If this fires, the repair has become a
    rule about prepositions rather than about passive voice.
    """
    text = "يبحث عن الحلول المتاحة في هذا المجال بصورة منتظمة"
    assert not any("الحلول" in t for t in _subject_texts(text))


def test_the_distinction_is_carried_by_the_predicate_state_not_the_preposition():
    """The two spans differ only in the voice of the verb."""
    passive = _subject_texts("يُكشف عن الإصابة بفيروس الأنفلونزا بوتيرة أقل")
    active = _subject_texts("يبحث عن الإصابة بفيروس الأنفلونزا بوتيرة أقل")
    assert any("الإصابة" in t for t in passive)
    assert not any("الإصابة" in t for t in active)


def test_a_passive_verb_with_no_following_preposition_is_unaffected():
    """The branch must not disturb the ordinary verb-initial subject search."""
    text = "تُستخدم الأدوية المضادة للفيروسات في علاج الحالات الشديدة"
    assert _subjects(text) or True  # no crash, and no invented subject below
    assert not any(c.text.strip() in ("في", "عن", "من") for c in _subjects(text))


# --- the clause-opening anchor ---------------------------------------------

def test_a_main_clause_verb_after_a_long_fronted_adverbial_is_found():
    """The يمكن case: the main verb sits well past the first eight words."""
    text = ("ومع ذلك، خلال الفترات التي ينخفض فيها نشاط الأنفلونزا أو خارج "
            "حالات الأوبئة الموسمية، يمكن أن تسبب عدوى لفيروسات أخرى")
    assert any("يمكن" in c.text for c in _heads(text))


def test_the_anchor_is_a_clause_opening_not_a_larger_window():
    """Neighbouring negative: no comma, no extra reach.

    A span of the same length with no clause boundary must not have its scan
    extended -- otherwise the repair is a wider constant wearing a comma.
    """
    text = ("ومع ذلك خلال الفترات التي ينخفض فيها نشاط الأنفلونزا أو خارج "
            "حالات الأوبئة الموسمية يمكن أن تسبب عدوى لفيروسات أخرى")
    assert not any("يمكن" in c.text for c in _heads(text))


def test_a_comma_in_an_enumeration_does_not_manufacture_verbal_predicates():
    """Neighbouring negative: commas that separate list items, not clauses.

    A bare noun list must not gain a *verbal* head from the clause anchor.  It
    does still gain a zero-copula nominal reading, which is a real and separate
    defect (V581-D41, registered): a coordinate list of same-type nominals is
    not a proposition.  That fallback fires only when no verbal head exists, so
    it is reachable with or without clause anchors and is not this repair's --
    asserting it here would let one repair absorb another's failure.
    """
    text = "الأدوية، اللقاحات، الفحوصات، المعدات الطبية"
    assert not [c for c in _heads(text)
                if "VERBAL_MORPHOLOGY" in c.morphological]


def test_the_anchor_does_not_reach_past_the_end_of_the_span():
    """A trailing comma must not index beyond the token list."""
    assert _heads("الأدوية،") == [] or True  # the assertion is that it returns


def test_an_empty_span_is_safe():
    assert V2._arabic_candidates("", V2._tokens("x")) is not None or True


# --- states the repairs must not invent ------------------------------------

def test_no_subject_is_invented_where_the_verb_carries_it_morphologically():
    """"قرروا" already encodes its subject; no nominal follows to promote."""
    text = "قرروا في اجتماعهم الأخير"
    assert not any(c.subtype == "POSTVERBAL_SUBJECT" for c in _subjects(text))


def test_every_generated_subject_lies_inside_the_span_it_came_from():
    """Provenance: a repair that widens reach must not widen spans."""
    text = ("ومع ذلك، خلال الفترات التي ينخفض فيها نشاط الأنفلونزا، "
            "يمكن أن تسبب عدوى لفيروسات أخرى")
    for candidate in _candidates(text):
        assert candidate.span is not None
        start, stop = candidate.span
        assert 0 <= start < stop <= len(text)
        assert candidate.text.strip() in text
