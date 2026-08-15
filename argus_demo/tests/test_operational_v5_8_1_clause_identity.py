"""Campaign A — typed clause identity and the seven cross-clause licences.

232 analyses reached selection whose subject and predicate belong to different
clauses, because the only clause test in the pipeline asked whether a single
role span contained a terminator character.  Neither span in a cross-clause
pairing straddles a full stop, so the question could not see the defect.

Two things are being defended here, and the second is the harder one:

  * a pairing across a clause boundary with no licence is refused;
  * a pairing across a clause boundary WITH a licence is not.

Relative clauses, control, raising, reported speech, inherited governing
subjects, coordination and structural continuation are all lawful cross-clause
pairings.  A rule that rejected them would close the removal gates by breaking
three preservation gates that currently pass, which is not a repair.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import clause_identity as CI

pytestmark = pytest.mark.no_db


class _Candidate:
    def __init__(self, span, text, candidate_id="c"):
        self.span = span
        self.text = text
        self.candidate_id = candidate_id


def _at(text: str, phrase: str, after: int = 0) -> _Candidate:
    start = text.index(phrase, after)
    return _Candidate((start, start + len(phrase)), phrase)


def _pair(text: str, subject: str, predicate: str) -> dict:
    subject_candidate = _at(text, subject)
    predicate_candidate = _at(text, predicate, subject_candidate.span[1])
    return CI.assess_pairing(subject_candidate, predicate_candidate, text)


def _relations(text: str) -> list[str]:
    return [c.relation_to_parent
            for c in CI.build_clause_context(text, region_id="r").clauses]


# ===========================================================================
# Segmentation — the judgement the evaluator declines to make
# ===========================================================================

def test_a_coordinator_joining_two_noun_phrases_does_not_open_a_clause():
    """"The Council and the Commission shall report" is ONE clause.

    The evaluation ruler cannot decide this: it has no lexicon with which to ask
    whether each side of a coordinator has its own finite verb, and says so in
    its own source.  An earlier ruler version that split on every coordinator
    manufactured 701 of 722 reported violations.  Production has the finite-head
    resource, so it can answer.
    """
    assert len(CI.build_clause_context(
        "The Council and the Commission shall report annually.").clauses) == 1


def test_a_coordinator_joining_two_finite_clauses_does_open_one():
    text = "The Council shall report and the Commission shall decide."
    assert _relations(text) == ["MATRIX", "COORDINATE"]


def test_terminal_punctuation_opens_a_new_proposition():
    assert _relations("The Council adopted the report. "
                      "The Assembly noted it.") == ["MATRIX", "MATRIX"]


def test_an_enumerator_full_stop_does_not_open_a_proposition():
    """"Art. 5" ends in a stop that is orthographic, not propositional.

    The only boundary in this sentence is the complementiser "that"; if the
    abbreviation also opened one there would be three clauses, and "5 provides
    that the Council shall decide" would be a proposition of its own.
    """
    assert _relations("Art. 5 provides that the Council shall decide.") == [
        "MATRIX", "SUBORDINATE_COMPLEMENT"]


def test_a_german_definite_article_at_span_start_is_not_a_relativiser():
    """der/die/das open a relative clause only on evidence.

    Treating them as relativisers unconditionally fractured almost every German
    subject; the same defect, in its admissibility form, was caught last session
    by its own regression fixture.
    """
    assert _relations("Der Erblasser kann den Pflichtteil entziehen.") == ["MATRIX"]


def test_a_determiner_after_a_preposition_is_not_a_relativiser():
    """"in der Lage ist" is a prepositional phrase, not a relative clause."""
    assert len(CI.build_clause_context(
        "sie hierzu aus rechtlichen Gruenden nicht in der Lage ist;").clauses) == 1


def test_a_german_relative_pronoun_in_relative_position_does_open_a_clause():
    """German orthography requires the comma; it is strong, cheap evidence."""
    assert "RELATIVE" in _relations(
        "Der Beamte, der ernannt wurde, muss den Bericht vorlegen.")


def test_a_relative_clause_ends_and_the_matrix_resumes():
    """Otherwise the relative swallows the matrix predicate.

    "The officer who was appointed yesterday will report" must not leave the
    matrix subject in a different clause from its own verb.
    """
    assert _relations("The officer who was appointed yesterday "
                      "will report on the matter.") == [
        "MATRIX", "RELATIVE", "STRUCTURAL_CONTINUATION"]


def test_a_german_verb_final_subordinate_clause_is_not_fractured():
    """One subordinator, one subordinate clause.

    The clause-final finite verb "zustimmt" and the internal determiner "die"
    are both places an over-eager segmenter would split.
    """
    text = "Der Rat kann entscheiden, wenn die Kommission zustimmt."
    assert _relations(text) == ["MATRIX", "ADVERBIAL_SUBORDINATE"]


def test_a_quoted_title_containing_finite_looking_words_is_not_segmented():
    """Material inside an enclosure does not open top-level clauses."""
    text = 'The report "The Council is competent" was adopted yesterday.'
    assert len(CI.build_clause_context(text).clauses) == 1


# ===========================================================================
# Licensed cross-clause pairings — what must NOT be rejected
# ===========================================================================

def test_a_matrix_subject_binding_across_a_closed_relative_is_licensed():
    result = _pair("The officer who was appointed yesterday "
                   "will report on the matter.", "The officer", "will")
    assert result["verdict"] == "LICENSED_CROSS_CLAUSE"
    assert result["cross_clause_licence_type"] == \
        "STRUCTURAL_CONTINUATION_OR_SHARED_ROLE"


def test_a_relative_clause_predicate_is_licensed_to_its_head_noun():
    text = "Der Beamte, der ernannt wurde, muss den Bericht vorlegen."
    result = _pair(text, "Der Beamte", "wurde")
    assert result["verdict"] == "LICENSED_CROSS_CLAUSE"
    assert result["cross_clause_licence_type"] == "RELATIVE_CLAUSE_RELATION"


def test_reported_speech_licenses_the_speaker_to_the_reported_predicate():
    result = _pair("The Committee noted that the report was adopted.",
                   "The Committee", "was")
    assert result["cross_clause_licence_type"] == "REPORTED_SPEECH_ATTRIBUTION"


def test_control_licenses_the_matrix_subject_to_the_controlled_predicate():
    text = "The Parties agree, although the dispute is complex, to submit it."
    result = _pair(text, "The Parties", "submit")
    assert result["cross_clause_licence_type"] == "CONTROL_RELATION"


def test_a_governing_subject_is_inherited_by_a_headless_subordinate_clause():
    """The absence of an own finite head is the evidence for inheritance."""
    result = _pair("The Council shall act, unless otherwise decided "
                   "by the Assembly.", "The Council", "decided")
    assert result["cross_clause_licence_type"] == "GOVERNING_SUBJECT_INHERITANCE"


def test_typed_coordination_licenses_a_gapped_second_conjunct():
    result = _pair("The Council shall report and shall decide on the matter.",
                   "The Council", "decide")
    assert result["cross_clause_licence_type"] == "TYPED_COORDINATION"


def test_a_same_clause_pairing_needs_no_licence_at_all():
    result = _pair("The Council shall adopt the report.", "The Council", "shall")
    assert result["verdict"] == "SAME_CLAUSE"
    assert result["cross_clause_licence"] is None


# ===========================================================================
# Unlicensed cross-clause pairings — what must be refused
# ===========================================================================

def test_a_subject_from_one_sentence_may_not_bind_the_next_sentences_verb():
    """The defect this whole layer exists to remove."""
    result = _pair("The Council adopted the report. The Assembly noted it.",
                   "The Council", "noted")
    assert result["verdict"] == "UNLICENSED_CROSS_CLAUSE"
    assert result["cross_clause_licence"] is None


def test_a_matrix_subject_may_not_bind_an_unrelated_subordinate_predicate():
    """The subordinate clause has its own subject, so it inherits nothing."""
    result = _pair("The Council shall act although the Commission is opposed.",
                   "The Council", "is")
    assert result["verdict"] == "UNLICENSED_CROSS_CLAUSE"


def test_a_control_verb_does_not_license_the_subordinate_clauses_own_verb():
    """Neighbouring negative: the licence must attach to the CONTROLLED
    predicate, not to any predicate in the lower clause."""
    text = "The Parties agree, although the dispute is complex, to submit it."
    assert _pair(text, "The Parties", "is")["verdict"] == "UNLICENSED_CROSS_CLAUSE"


def test_proximity_is_not_a_licence():
    """Two adjacent tokens across a sentence boundary remain unlicensed."""
    text = "The Assembly decided. The Council is competent."
    assert _pair(text, "The Assembly", "is")["verdict"] == "UNLICENSED_CROSS_CLAUSE"


def test_independent_coordinated_clauses_are_not_collapsed():
    """Each conjunct has its own subject, so no subject is shared."""
    text = "The Council shall report and the Commission shall decide."
    result = _pair(text, "The Council", "decide")
    assert result["same_clause"] is False


# ===========================================================================
# The licence record itself
# ===========================================================================

def test_a_licence_without_evidence_cannot_be_constructed():
    """Proximity is not a licence, and the type system says so."""
    with pytest.raises(CI.ClauseIdentityError):
        CI.CrossClauseLicence(
            licence_id="l", licence_type="TYPED_COORDINATION",
            source_clause_id="a", target_clause_id="b",
            relation_type="COORDINATE", evidence_span=None, evidence_text="")


def test_an_unknown_licence_type_is_refused_at_construction():
    with pytest.raises(CI.ClauseIdentityError):
        CI.CrossClauseLicence(
            licence_id="l", licence_type="BECAUSE_THEY_ARE_CLOSE",
            source_clause_id="a", target_clause_id="b",
            relation_type="COORDINATE", evidence_span=(0, 3), evidence_text="and")


def test_every_licence_family_is_named_and_none_is_a_boolean():
    """A boolean would hide WHICH family over-fired."""
    assert len(CI.LICENCE_TYPES) == 7
    assert "GOVERNING_SUBJECT_INHERITANCE" in CI.LICENCE_TYPES
    assert "STRUCTURAL_CONTINUATION_OR_SHARED_ROLE" in CI.LICENCE_TYPES


def test_a_licence_carries_its_asserting_component_and_decisiveness():
    result = _pair("The Committee noted that the report was adopted.",
                   "The Committee", "was")
    assert result["cross_clause_licence_evidence"]
    assert result["cross_clause_licence"]


def test_an_unknown_clause_relation_is_refused():
    with pytest.raises(CI.ClauseIdentityError):
        CI.ClauseIdentity(clause_id="c", region_id="r", token_start=0,
                          token_end=3, character_start=0, character_end=10,
                          relation_to_parent="VIBES")


# ===========================================================================
# Refusal to over-claim
# ===========================================================================

def test_unresolved_clause_structure_is_not_reported_as_a_violation():
    """A layer that cannot see the structure must not reject on that basis."""
    result = CI.assess_pairing(_Candidate((0, 3), "abc"),
                               _Candidate((4, 7), "def"), "")
    assert result["verdict"] in ("CLAUSE_STRUCTURE_UNRESOLVED",
                                 "NOT_APPLICABLE_SINGLE_ROLE")


def test_a_single_role_analysis_is_not_a_cross_clause_question():
    result = CI.assess_pairing(None, _Candidate((0, 3), "abc"), "abc def")
    assert result["verdict"] == "NOT_APPLICABLE_SINGLE_ROLE"


def test_the_module_never_imports_the_evaluation_ruler():
    """Independence is the property that has caught six defects so far."""
    import inspect
    source = inspect.getsource(CI)
    assert "adjudicate" not in source.replace("adjudication", "")
    assert "234_independent" not in source


# ===========================================================================
# Per-licence mutation tests
# ===========================================================================
#
# Each licence family must be independently falsifiable: remove the evidence
# that licence depends on, and that licence -- and only that licence -- must
# stop firing.  A family that survives deletion of its own evidence is not
# reading the evidence.

def test_removing_the_reporting_verb_withdraws_the_reported_speech_licence():
    licensed = _pair("The Committee noted that the report was adopted.",
                     "The Committee", "was")
    mutated = _pair("The Committee document that the report was adopted.",
                    "The Committee", "was")
    assert licensed["cross_clause_licence_type"] == "REPORTED_SPEECH_ATTRIBUTION"
    assert mutated["cross_clause_licence_type"] != "REPORTED_SPEECH_ATTRIBUTION"


def test_removing_the_infinitive_marker_withdraws_the_control_licence():
    licensed = _pair("The Parties agree, although the dispute is complex, "
                     "to submit it.", "The Parties", "submit")
    mutated = _pair("The Parties agree, although the dispute is complex, "
                    "and submit it.", "The Parties", "submit")
    assert licensed["cross_clause_licence_type"] == "CONTROL_RELATION"
    assert mutated["cross_clause_licence_type"] != "CONTROL_RELATION"


def test_giving_the_subordinate_clause_its_own_head_withdraws_inheritance():
    """Inheritance is licensed by the ABSENCE of an own finite head."""
    licensed = _pair("The Council shall act, unless otherwise decided "
                     "by the Assembly.", "The Council", "decided")
    mutated = _pair("The Council shall act, unless the Assembly is "
                    "otherwise decided.", "The Council", "decided")
    assert licensed["cross_clause_licence_type"] == "GOVERNING_SUBJECT_INHERITANCE"
    assert mutated["cross_clause_licence_type"] != "GOVERNING_SUBJECT_INHERITANCE"


def test_giving_the_second_conjunct_its_own_subject_withdraws_coordination():
    licensed = _pair("The Council shall report and shall decide on the matter.",
                     "The Council", "decide")
    mutated = _pair("The Council shall report and the Commission shall decide.",
                    "The Council", "decide")
    assert licensed["cross_clause_licence_type"] == "TYPED_COORDINATION"
    assert mutated["cross_clause_licence_type"] != "TYPED_COORDINATION"


def test_detaching_the_relative_clause_withdraws_the_relative_licence():
    licensed = _pair("Der Beamte, der ernannt wurde, muss den Bericht vorlegen.",
                     "Der Beamte", "wurde")
    mutated = _pair("Der Beamte liegt vor. Der Bericht wurde vorgelegt.",
                    "Der Beamte", "wurde")
    assert licensed["cross_clause_licence_type"] == "RELATIVE_CLAUSE_RELATION"
    assert mutated["verdict"] == "UNLICENSED_CROSS_CLAUSE"


# ===========================================================================
# The apostrophe class — the input the enclosure depth was never tested on
# ===========================================================================
#
# `_enclosure_depth_at` exists so that "a full stop or subordinator inside a
# quotation or a parenthesis does not open a top-level clause".  It was
# defeated in BOTH directions by a character that is a quotation mark AND a
# letter, and until this block existed the function had no fixture carrying an
# apostrophe at all.
#
# The alphabets below are DERIVED FROM PRODUCTION'S OWN CHARACTER CLASSES, not
# transcribed here.  A future edit that adds a character to `_APOSTROPHE_ALSO`,
# or that drops one, is answered by these tests rather than by a list in a test
# file that has to be remembered.


def _depth_per_character(text: str) -> list[int]:
    """The live depth function read at character granularity.

    `_enclosure_depth_at` reports the depth ENTERING each element of the token
    list it is handed.  A token list of one character per position asks it the
    same question at a finer resolution, which is the only way to see WHERE an
    enclosure was opened or popped rather than merely in which token.
    """
    return CI._enclosure_depth_at([(c, i, i + 1) for i, c in enumerate(text)],
                                  text)


def _events(text: str) -> set[tuple[str, int]]:
    context = CI.build_clause_context(text, region_id="r")
    return {(e.event_type, e.token_index) for e in context.boundary_events}


# --- the class itself -------------------------------------------------------

def test_the_apostrophe_class_is_exactly_the_marks_with_a_letter_reading():
    """Every member is a quotation character production already knows.

    The class must not acquire a character that no other part of the module
    treats as a delimiter -- that would be a silent widening -- and it must not
    contain U+0022, which has no apostrophe reading in any language this module
    handles and whose quotation behaviour must be preserved exactly.
    """
    known_delimiters = (set(CI._SYMMETRIC_QUOTE)
                        | set(CI._ENCLOSURE_PAIRS)
                        | set(CI._ENCLOSURE_PAIRS.values()))
    assert CI._APOSTROPHE_ALSO <= known_delimiters, \
        sorted(hex(ord(c)) for c in CI._APOSTROPHE_ALSO - known_delimiters)
    assert '"' not in CI._APOSTROPHE_ALSO
    # Non-trivial: it covers the straight apostrophe, the typographic one and
    # the Unicode letter apostrophe, which is the whole population of marks
    # that are simultaneously a delimiter here and a letter in some language.
    assert len(CI._APOSTROPHE_ALSO) >= 3
    for character in CI._APOSTROPHE_ALSO:
        assert len(character) == 1, hex(ord(character))
    # U+02BC is itself ALPHABETIC to Python, U+0027 and U+2019 are not, and the
    # offset finder must not depend on which -- it asks about the NEIGHBOURS.
    # Pinned here because a reader would reasonably assume the class is uniform.
    assert {c.isalpha() for c in CI._APOSTROPHE_ALSO} == {True, False}


def test_every_member_of_the_apostrophe_class_is_a_letter_between_letters():
    """Derived, not transcribed: the property is asserted for the whole class.

    Adding a fourth character to `_APOSTROPHE_ALSO` without teaching the offset
    finder about it fails here.
    """
    for character in sorted(CI._APOSTROPHE_ALSO):
        word_internal = f"le mot a{character}b suit"
        assert CI._apostrophe_letter_offsets(word_internal) == \
            frozenset({word_internal.index(character)}), hex(ord(character))
        # word-initial, word-final and isolated are NOT letters
        for shape in (f"le mot {character}abc suit",
                      f"le mot abc{character} suit",
                      f"le mot {character} suit",
                      f"le mot 1{character}2 suit"):
            assert CI._apostrophe_letter_offsets(shape) == frozenset(), \
                (hex(ord(character)), shape)


def test_the_letter_reading_holds_in_every_script_the_module_handles():
    """`str.isalpha` is the test, so Cyrillic and Arabic get it for free."""
    for left, right in (("a", "b"), ("д", "е"), ("م", "ح"), ("É", "t")):
        for character in sorted(CI._APOSTROPHE_ALSO):
            text = f"xx {left}{character}{right} yy"
            assert CI._apostrophe_letter_offsets(text) == \
                frozenset({text.index(character)}), (left, hex(ord(character)))


# --- direction A: an apostrophe pair must not invent an enclosure -----------

def test_two_elisions_do_not_enclose_the_text_between_them():
    """ASCII-normalised French: there is no quotation anywhere in this string.

    Before the apostrophe class existed, the two U+0027 marks paired with each
    other, depth rose over every token between them, and the full stop after
    "acte." was skipped as "inside an enclosure".
    """
    text = ("L'expression designe un acte. Si le Conseil decide, "
            "l'autorite doit agir d'un commun accord.")
    assert max(_depth_per_character(text)) == 0
    assert ("TERMINAL_PUNCTUATION", 4) in _events(text)


def test_the_same_sentence_segments_the_same_way_in_every_orthography():
    """The boundary structure of a sentence is not a property of its encoding.

    This is the invariant the defect broke: transcribing U+2019 to U+0027 --
    which every plain-text extractor does -- changed the clause structure.
    """
    typographic = ("L’expression designe un acte. Si le Conseil decide, "
                   "l’autorite doit agir d’un commun accord.")
    variants = {typographic}
    for character in sorted(CI._APOSTROPHE_ALSO):
        variants.add(typographic.replace("’", character))
    structures = {frozenset(_events(v)) for v in variants}
    assert len(structures) == 1, {
        v: sorted(_events(v)) for v in sorted(variants)}


def test_an_italian_subordinator_between_two_elisions_still_opens_a_clause():
    text = ("L'articolo 5 e applicabile. Quando l'autorita competente "
            "decide, il termine e sospeso.")
    assert ("TERMINAL_PUNCTUATION", 4) in _events(text)


def test_english_possessives_are_not_a_quotation_either():
    """No French needed: "the Council's ... the Member State's" did it too."""
    text = ("The Council's decision is final. If the Commission objects, "
            "the Member State's authority shall report.")
    assert max(_depth_per_character(text)) == 0
    assert ("TERMINAL_PUNCTUATION", 5) in _events(text)


# --- direction B: an apostrophe must not close a real quotation -------------

def test_an_elision_does_not_close_an_open_single_quotation():
    """U+2019 is the closer of U+2018 AND the French apostrophe.

    The quotation runs to the closing mark, not to the first apostrophe inside
    it.  Before the repair the depth fell to zero at "d’Etat" and the full stop
    after "competent." opened a top-level clause INSIDE the quotation.
    """
    text = ("‘Le Conseil d’Etat est competent. La Commission est saisie.’ "
            "Le texte s’applique.")
    depth = _depth_per_character(text)
    inside = text.index("competent.")
    outside = text.index("Le texte")
    assert depth[inside] == 1
    assert depth[outside] == 0
    assert _events(text) == {("SPAN_START", 0)}


def test_a_possessive_does_not_close_an_amending_instruments_quotation():
    """EU drafting quotes the enacted text in U+2018 / U+2019.

    The possessive in "the operator's records" is not the end of the provision.
    """
    text = ("Article 3 is replaced by the following: ‘The competent authority "
            "shall carry out the checks. Where the operator’s records are "
            "incomplete, the authority may require further evidence. The "
            "operator shall be informed.’ This paragraph applies from 2027.")
    depth = _depth_per_character(text)
    assert depth[text.index("further evidence")] == 1
    assert depth[text.index("This paragraph")] == 0


# --- what must NOT have changed --------------------------------------------

def test_genuine_single_quotation_is_still_an_enclosure():
    """The marks still delimit when they stand between words rather than in one."""
    for character in sorted(CI._APOSTROPHE_ALSO | {'"'}):
        text = (f"The report {character}The Council is competent{character} "
                "was adopted yesterday.")
        assert len(CI.build_clause_context(text).clauses) == 1, \
            hex(ord(character))


def test_a_quoted_full_stop_between_two_words_still_suppresses_a_boundary():
    """The quotation still hides its own full stop; the coordinator outside it
    still opens a clause, which is the behaviour on both sides of this change."""
    text = ('The notice said "The session is closed. The record is sealed." '
            "and was published.")
    events = _events(text)
    assert not any(kind == "TERMINAL_PUNCTUATION" for kind, _index in events)
    assert ("COORDINATOR_WITH_OWN_FINITE_HEAD", 11) in events
    assert _depth_per_character(text)[text.index("The record")] == 1


def test_brackets_and_guillemets_are_untouched_by_the_apostrophe_class():
    """Derived from `_ENCLOSURE_PAIRS` so a new pair cannot slip past."""
    for opener, closer in sorted(CI._ENCLOSURE_PAIRS.items()):
        text = (f"Le texte {opener}Le Conseil est competent. La Commission "
                f"est saisie.{closer} a ete adopte.")
        depth = _depth_per_character(text)
        assert depth[text.index("La Commission")] == 1, hex(ord(opener))
        assert depth[text.index("a ete adopte")] == 0, hex(ord(opener))


def test_an_abandoned_opener_still_suppresses_nothing():
    """PASS 1's own hardening must survive the apostrophe skip."""
    text = ("Il prete le serment suivant: «Je jure que l'exercice de mes "
            "fonctions. Le Conseil est competent.")
    assert max(_depth_per_character(text)) == 0


def test_the_word_final_apostrophe_is_a_declared_residue_not_a_silent_one():
    """The English plural possessive is NOT decided, and that is on purpose.

    It is locally indistinguishable from a closing straight quote.  This test
    exists so the residue is a pinned, visible fact rather than something a
    reader has to rediscover: if a successor ever repairs it, this test fails
    and forces the limitation register to be updated with it.
    """
    text = "the Members' rights"
    assert CI._apostrophe_letter_offsets(text) == frozenset()
