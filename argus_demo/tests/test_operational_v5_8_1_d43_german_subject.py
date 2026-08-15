"""D43/D44 — German subject construction, span plausibility, and Russian rejection.

D43 was found by refusing a false pass.  The German unit could have been closed
by enlarging a 400-character pairing window; that would have counted a
401-character span covering the whole protasis as the subject `die Strafe`,
because compatibility was word-set overlap and a big enough span overlaps
anything.  The repairs here are constructional instead:

  * the preverbal subject is bounded to its own constituent, emitted alongside
    the full run rather than replacing it;
  * German V2 inversion is constructed, because after "..., so ist ..." the
    subject is not before the verb at any distance;
  * subject options are the union of bounded constituents and nearest
    candidates, because ranking on proximity alone promotes the longest prefix.

D44 is the opposite operation: rejecting a candidate that should never have been
generated.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _subjects(text: str, language: str = "de"):
    return [c for c in V2._latin_candidates(text, V2._tokens(text))
            if c.role_type == "SUBJECT"]


def _ru_subjects(text: str):
    return [c for c in V2._cyrillic_candidates(text, V2._tokens(text))
            if c.role_type == "SUBJECT"]


VERB_FINAL = "sie hierzu aus rechtlichen Gründen nicht in der Lage ist;"
INVERTED = ("(1) Hat sich ein Abkömmling in solchem Maße der Verschwendung "
            "ergeben, so kann der Erblasser den Pflichtteil entziehen.")


# --- bounded constituents ---------------------------------------------------

def test_a_verb_final_clause_yields_a_bounded_subject_constituent():
    """"sie", not "sie hierzu aus rechtlichen Gründen nicht in der Lage"."""
    assert any(c.text.strip() == "sie" for c in _subjects(VERB_FINAL))


def test_the_full_preverbal_run_is_still_offered():
    """The bounded reading is added, not substituted.

    Replacing the full run truncated subjects that were correctly long and cost
    four units, so both readings stay in the lattice and selection decides.
    """
    subjects = _subjects(VERB_FINAL)
    assert any("BOUNDED_CONSTITUENT" in c.syntactic for c in subjects)
    assert any("BOUNDED_CONSTITUENT" not in c.syntactic for c in subjects)


def test_a_capitalised_noun_closes_the_constituent():
    """German capitalises nouns, so "der Erblasser" ends at Erblasser."""
    assert any(c.text.strip() == "der Erblasser" for c in _subjects(INVERTED))


def test_a_pronoun_stands_alone_as_a_constituent():
    assert any(c.text.strip() == "sie" for c in _subjects(VERB_FINAL))


# --- V2 inversion -----------------------------------------------------------

def test_the_subject_after_a_resumptive_so_is_constructed():
    """No window reaches it: the subject is not before the verb at all."""
    subjects = _subjects(INVERTED)
    assert any(c.origin_rule == "LAT_POSTVERBAL_V2"
               and "Erblasser" in c.text for c in subjects)


def test_the_inverted_subject_is_marked_as_such():
    inverted = [c for c in _subjects(INVERTED)
                if c.origin_rule == "LAT_POSTVERBAL_V2"]
    assert inverted
    assert all("V2_INVERSION" in c.syntactic for c in inverted)


def test_no_inversion_subject_without_a_fronted_trigger():
    """Neighbouring negative: an ordinary main clause is not inverted."""
    text = "Der Erblasser kann den Pflichtteil entziehen."
    assert not any(c.origin_rule == "LAT_POSTVERBAL_V2" for c in _subjects(text))


def test_a_preposition_after_the_verb_is_not_taken_as_a_subject():
    """Neighbouring negative: inversion does not promote a prepositional phrase."""
    text = "Hat er die Frist versäumt, so gilt für ihn die längere Frist."
    postverbal = [c for c in _subjects(text)
                  if c.origin_rule == "LAT_POSTVERBAL_V2"]
    assert not any(c.text.strip().startswith("für") for c in postverbal)


# --- span plausibility ------------------------------------------------------

def test_no_subject_candidate_swallows_the_whole_clause():
    """The defect that started D43: a span that contains everything.

    Every emitted subject must be shorter than the clause it sits in, or the
    overlap test it is later scored against is vacuous.
    """
    for candidate in _subjects(VERB_FINAL):
        assert len(candidate.text) < len(VERB_FINAL)


def test_bounded_constituents_are_short_enough_to_mean_something():
    bounded = [c for c in _subjects(INVERTED)
               if "BOUNDED_CONSTITUENT" in c.syntactic]
    assert bounded
    assert all(len(c.text.split()) <= V2.MAX_SUBJECT_CONSTITUENT_TOKENS
               for c in bounded)


# --- D44: rejection, not expansion ------------------------------------------

def test_a_finite_verb_is_not_a_subject_candidate():
    cands = _ru_subjects("что матери-подростки подвергаются более высокому риску")
    assert not any(c.text.strip() == "подвергаются" for c in cands)


def test_the_compound_subject_survives_the_rejection():
    """The guard must not take the repair with it."""
    cands = _ru_subjects("что матери-подростки подвергаются более высокому риску")
    assert any("матери-подростки" in c.text for c in cands)


def test_an_ordinary_nominal_is_still_admitted():
    """Neighbouring negative: the guard targets finite morphology only."""
    cands = _ru_subjects("Комитет принимает решение о мерах")
    assert any(c.text.strip() == "Комитет" for c in cands)


def test_a_deverbal_noun_is_not_rejected_as_a_verb():
    """Substantivised and deverbal forms legitimately head nominals."""
    cands = _ru_subjects("Обеспечение безопасности требует ресурсов")
    assert any("беспечение" in c.text or "Обеспечение" in c.text for c in cands)
