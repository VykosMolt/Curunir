"""V6.1 residue repair — the two repairs the isolation phase judged REPAIRABLE.

WHY THIS FILE EXISTS
--------------------
The V6.1 isolation seat examined three targets and designed exactly two
repairs.  Both are pinned here.  Nothing else it examined is implemented: three
of the five Target-1 residue units are NOT repairable on the campaign's own
frozen adjudication, one is NOT_ESTABLISHED, and Target 2 is NOT_ESTABLISHED
and blocked on a truth-artifact question that is not an engineering decision.

T3-R1 — WHAT ``span_initial`` MEANS
-----------------------------------
``predicate_types.classify`` guards one rule with ``span_initial``, and that
rule is about SENTENCE-INITIAL POSITION: "a capitalised NON-INITIAL token is a
noun."  Both call sites computed it as "the span starts at literal character
offset 0".  ``bind`` takes ``body = (text or '')[:MAX_SCAN_CHARS]`` with no
strip, so ONE LEADING SPACE moved a recital head's span from (0, 7) to (1, 8),
``span_initial`` flipped True -> False, ``_GERMAN_NOUN_SHAPE`` typed the head
ARGUMENT_NOUN, the V581-D42-M6 structural filter dropped the recital analysis,
and the published record fell from

    PARTIAL / RECITAL_RELATION_PREDICATE / GOVERNING_CLAUSE_SUBJECT
to
    UNRESOLVED / PREDICATE_UNRESOLVED / SUBJECT_UNRESOLVED

with a null predicate span.  M6's own "skip the filter when it would empty the
pool" valve CANNOT fire here, because the NULL analysis has
``predicate_head is None``, survives the filter, and keeps the pool non-empty.

Two corrections the isolation measured, and this file pins BOTH, because the
registered form of the finding was wrong in two ways:

  * IT IS THE REGEX, NOT THE LEXICON.  The affected set is exactly the LATIN
    heads matching ``^[A-ZÄÖÜ][a-zäöüß]{2,}$``.  French ``Notant`` /
    ``Rappelant`` / ``Reconnaissant`` ARE affected; English ``whereas`` and
    ``WHEREAS`` are NOT.  A four-lexicon reading scores 13/18 on the frozen
    token table; the shape reading scores 18/18.
  * IT IS NOT HEAD-FINAL SPECIFIC.  ``' Gestützt auf Artikel 5,'`` — a German
    recital WITH a body — is affected too.

T1-R1 — WHAT ``predicate_completeness`` IS DECIDED FROM
-------------------------------------------------------
``_terminal_facts`` decided ``BOUND_LOCAL`` for GOVERNING_CLAUSE_PREDICATE from
THE EXISTENCE OF A SPAN ALONE.  A mid-sentence continuation fragment whose only
predicate-shaped material is an infinitival adjunct — whose frame sits in the
left context — therefore published "bound LOCALLY", which is precisely what a
transported frame is not; the terminal ladder then saw one resolved and one
unresolved role and returned ROLE_BINDING_PARTIAL against BOTH truth artifacts.

This is the same construct LRI-R1 shipped for the ABSENCE axis: the axis is
decided by the EVIDENCE the role-state pipeline actually had, not by which code
path produced the state NAME.  LRI-R1 did not cover the bound-vs-unresolved
decision.  It is covered now, using ``clauses.detect``'s OWN establishment
verdict — used WHOLE, not as a subset of dimensions chosen after seeing which
units it moves.

WHAT THESE TESTS ARE FOR
------------------------
Every test below is written to FAIL against a byte-exact revert to
``ef6e854cefb01279e4486a358c5eb4211854a389...`` — the substrate accepted at
GATE-1 — and the revert proof in
``artifacts/curunir_v6_readiness/v6_1_residue_repair/`` demonstrates that it
does, test by test, rather than asserting it.

NOTHING HERE MAY BE READ AS MOVING A V6 FIGURE.  V6 is closed.  On the frozen
120-unit exposed cohort the T3 repair moves ZERO units — no unit's span_text
begins with whitespace, so the defect is UNREACHABLE there — and the T1 repair
moves exactly two, flipping no gate verdict and publishing nothing new.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import predicate_types as PT
from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


#: The whitespace that survives ``roles_v2._M14_SPAN_TRIM`` — imported from the
#: P6.4 pin rather than re-typed, so it cannot silently narrow here.
from tests.test_operational_v5_8_1_p63_degenerate_span import (  # noqa: E402
    _WHITESPACE_OUTSIDE_THE_SPAN_TRIM,
)

#: Heads that match ``_GERMAN_NOUN_SHAPE`` and are therefore in the repair
#: surface.  French entries are the rows that falsify the registered
#: "four lexicons" framing; ``Überzeugt`` pins the umlaut initial class.
SHAPE_MATCHING_RECITAL_HEADS = [
    ("en", "Whereas"),
    ("de", "Gestützt"),
    ("de", "Eingedenk"),
    ("de", "Überzeugt"),
    ("es", "Considerando"),
    ("es", "Teniendo"),
    ("it", "Visto"),
    ("it", "Riconoscendo"),
    ("en", "Aware"),
    ("fr", "Notant"),
    ("fr", "Rappelant"),
    ("fr", "Reconnaissant"),
]

#: Heads that do NOT match the shape and were never affected.  They are here so
#: the pins below cannot be satisfied by a change that simply disables the rule.
SHAPE_FAILING_HEADS = [
    ("fr", "Considérant"),   # 'é' is outside [a-zäöüß]
    ("fr", "Vu"),            # only one trailing letter; {2,} unsatisfied
    ("en", "whereas"),       # lower-case initial
    ("en", "WHEREAS"),       # upper-case tail
    ("ru", "Учитывая"),      # CYRILLIC never reaches the branch
    ("ar", "وإذ"),           # ARABIC never reaches the branch
]

PUBLISHED_FIELDS = (
    "internal_state", "subject_state", "predicate_state", "predicate_head",
    "predicate_complement", "proposition_status", "subject_completeness",
    "predicate_completeness", "binding_uniqueness", "required_context_status",
    "repairability", "role_binding_state", "terminal_derivation_rule",
    "final_extraction_disposition", "disposition_derivation_rule",
    "repair_requirement", "language_or_script",
)


def _published(text: str, language: str) -> dict:
    record = V2.bind(candidate_id="t", text=text, language=language)
    return {name: getattr(record, name) for name in PUBLISHED_FIELDS}


def _spans(text: str, language: str) -> dict:
    record = V2.bind(candidate_id="t", text=text, language=language)
    return {
        "subject_span": record.subject_span,
        "predicate_span": record.predicate_span,
    }


# --------------------------------------------------------------------------
# T3-R1: the meaning of span_initial
# --------------------------------------------------------------------------

def test_span_initial_asks_whether_a_word_token_precedes_not_whether_offset_is_zero():
    """The predicate itself, exercised in both directions.

    Offset zero must still be sentence-initial; a span preceded only by
    whitespace must ALSO be sentence-initial; and a span preceded by a real
    word token must NOT be.  A predicate that returned a constant would satisfy
    every behavioural pin below by disabling the rule instead of correcting it,
    so all three arms are required here.
    """
    assert V2._span_is_sentence_initial("Whereas", (0, 7)) is True
    assert V2._span_is_sentence_initial(" Whereas", (1, 8)) is True
    assert V2._span_is_sentence_initial("  Whereas", (2, 9)) is True
    assert V2._span_is_sentence_initial("Der Rat Whereas", (8, 15)) is False
    assert V2._span_is_sentence_initial("x", None) is False


@pytest.mark.parametrize("language,head", SHAPE_MATCHING_RECITAL_HEADS)
def test_a_leading_space_does_not_destroy_a_shape_matching_recital_reading(
        language, head):
    """The defect, stated as the behaviour it produced.

    On the accepted substrate these twelve heads published a DIFFERENT record
    with one leading space than without it.  They must now publish the same
    one.
    """
    assert _published(head, language) == _published(" " + head, language)


@pytest.mark.parametrize("language,head", SHAPE_MATCHING_RECITAL_HEADS)
@pytest.mark.parametrize("character", _WHITESPACE_OUTSIDE_THE_SPAN_TRIM)
def test_every_surviving_whitespace_character_leaves_the_record_intact(
        language, head, character):
    """P6.4 pinned 26 characters that ``_M14_SPAN_TRIM`` does not remove.

    For each of them the published record must be unchanged and the two spans
    must SHIFT by exactly the lead's length — not vanish, and not stay put.
    """
    assert _published(head, language) == _published(character + head, language)
    bare = _spans(head, language)
    led = _spans(character + head, language)
    for key, value in bare.items():
        if value is None:
            assert led[key] is None
        else:
            assert led[key] == (value[0] + len(character),
                                value[1] + len(character))


def test_the_repair_surface_is_not_head_final_specific():
    """A German recital WITH a body is affected too.

    The registered finding said "head-final recitals".  It is any Latin recital
    whose head token matches the shape.
    """
    body = "Gestützt auf Artikel 5,"
    assert _published(body, "de") == _published(" " + body, "de")
    assert _published(body, "de")["predicate_state"] != "PREDICATE_UNRESOLVED"


@pytest.mark.parametrize("language,head", SHAPE_FAILING_HEADS)
def test_heads_that_never_matched_the_shape_keep_their_bare_reading(
        language, head):
    """The negative arm.

    These six were never affected.  If a change made them move, the repair
    would have widened past the defect it names.
    """
    assert _published(head, language) == _published(" " + head, language)


def test_the_german_noun_shape_regex_is_neither_widened_nor_narrowed():
    """``_GERMAN_NOUN_SHAPE`` is correct about German orthography.

    The repair changes what ``span_initial`` MEANS and nothing else.  This pins
    the classifier's own behaviour on both sides of the flag, so a future change
    that reached for the regex instead would fail here.
    """
    non_initial = PT.classify("Erwägung", span_initial=False, script="LATIN")
    assert non_initial.lexical_category == "NOUN"
    assert non_initial.predicate_type == "ARGUMENT_NOUN"
    assert non_initial.decisive is True

    initial = PT.classify("Erwägung", span_initial=True, script="LATIN")
    assert initial.predicate_type != "ARGUMENT_NOUN"


def test_a_capitalised_token_after_a_real_word_is_still_typed_a_noun():
    """The genuine typing is PRESERVED — the half of the repair that can be
    lost silently.

    A repair that made ``span_initial`` always True would pass every pin above
    and destroy the rule.  Here a real word token precedes the candidate, so
    the flag must be False and the noun typing must still be reached.
    """
    body = "Der Rat Erwägung"
    offset = body.index("Erwägung")
    assert V2._span_is_sentence_initial(body, (offset, offset + 8)) is False
    typing = PT.classify("Erwägung", span_initial=False, script="LATIN")
    assert typing.predicate_type == "ARGUMENT_NOUN"


# --------------------------------------------------------------------------
# T1-R1: what predicate_completeness is decided from
# --------------------------------------------------------------------------

#: The exact frozen exposed-cohort unit the repair was designed for, quoted by
#: EXACT FULL id.  Its structural context is not available in a unit test, so
#: the span and its left context are pinned as text and the AXIS is asserted,
#: not the terminal — the terminal needs the wired harness, and the campaign's
#: full-corpus differential measures it there.
UNIT_0B44ED5A_SPAN = (
    "internal law, to ensure that the conditions to accede to those "
    "professions whose exercise")
UNIT_0B44ED5A_LEFT = (
    "3 Each Party shall take the necessary legislative or other measures, "
    "in conformity with its")


def test_a_span_with_no_local_predication_is_not_reported_as_bound_locally():
    """The defect, at the axis it is published on.

    The span's only verb-shaped material is an infinitival purpose adjunct
    whose frame is in the left context.  ``clauses.detect`` establishes no
    predication on it at all.  Reporting BOUND_LOCAL asserted a local binding
    the evidence does not carry.
    """
    record = V2.bind(
        candidate_id="t", text=UNIT_0B44ED5A_SPAN, language="en",
        left_context=UNIT_0B44ED5A_LEFT,
        content_region_type="UNKNOWN_STRUCTURAL_REGION")
    assert record.predicate_completeness != "BOUND_LOCAL"


def test_the_completeness_axis_is_answered_by_the_detector_not_by_a_span():
    """The construct, exercised in BOTH directions at the adapter itself.

    Same state, same span, same everything except the evidence flag.  This is
    what makes the repair a rule rather than a special case: with local
    predication established the axis reads BOUND_LOCAL, and without it the axis
    reads UNRESOLVED.
    """
    common = dict(
        internal_state="AMBIGUOUS_BINDING",
        subject_state="GOVERNING_CLAUSE_SUBJECT",
        subject_span=None,
        predicate_state="GOVERNING_CLAUSE_PREDICATE",
        predicate_span=(14, 89),
        antecedent_state="ANTECEDENT_NOT_REQUIRED",
        required_context_ids=(),
        repair_requirement=None,
        structural_context_supplied=True,
        governing_state="GOVERNING_CLAUSE_UNIQUE",
    )
    evidenced = V2._terminal_facts(
        **common, predicate_local_predication_is_evidential=True)
    unevidenced = V2._terminal_facts(
        **common, predicate_local_predication_is_evidential=False)
    assert evidenced.predicate_completeness == "BOUND_LOCAL"
    assert unevidenced.predicate_completeness == "UNRESOLVED"


def test_a_span_that_does_carry_local_predication_still_reads_bound_local():
    """The regression arm, through the live binder.

    A finite English clause establishes predication in ``clauses.detect``, so
    nothing about this reading may move.  Without this pin the repair could
    "pass" by never reporting BOUND_LOCAL again.
    """
    record = V2.bind(
        candidate_id="t",
        text="The Committee considered the report and adopted the resolution.",
        language="en")
    assert record.predicate_completeness == "BOUND_LOCAL"


def test_the_two_normalisation_depths_are_indistinguishable_to_the_detector():
    """The property the T1 repair's placement rests on.

    ``bind`` resolves the clause evidence and hands it to ``build_lattice``,
    which would otherwise have detected it itself AFTER normalising the
    declared tag a SECOND time.  ``_declared_language`` is NOT idempotent -- it
    strips, casefolds, then takes a two-character prefix, so a prefix ending in
    whitespace loses that character only on a second pass -- so the two depths
    are genuinely different values for some tags.

    They are nonetheless indistinguishable to ``clauses.detect``, which reads
    the tag only as ``tag.lower()[:2]`` and tests it against "ar", "ru" and
    LEXICON_COVERED, all of which are two NON-whitespace characters.  A tag at
    which the depths differ has at most one non-whitespace character at either
    depth, so neither can match any of those tests.

    The P6.3 A3 pin requires exactly ONE ``_declared_language`` call per
    ingress, so the repair must NOT normalise again; this test is what makes
    that safe to rely on instead of merely assumed.  Both arms are asserted:
    the depths must genuinely DIFFER somewhere (or the property is vacuous),
    and they must never classify differently ANYWHERE.
    """
    from curunir_operational.v5_8_1 import clauses as CL

    tags = [
        None, "", "de", "DE", " de", "de ", " de ", "\tde\n", "d", "de-DE",
        "ar", "AR", " ar", "ru", "RU", " ru", "fr", "en", "es", "it",
        "a b", "d e", "x　y", "a ", "d ", " ru",
        "e n", "f r", "ẞ", "İ", "ß", 123, True, ["de"], object(),
    ]

    def classification(tag):
        """Exactly the three tests ``clauses.detect`` applies to the tag."""
        declared = (tag or "").lower()[:2]
        return (declared == "ar", declared == "ru",
                declared in CL.LEXICON_COVERED)

    depths_differ_somewhere = False
    for tag in tags:
        once = V2._declared_language(tag)
        twice = V2._declared_language(once)
        if once != twice:
            depths_differ_somewhere = True
        assert classification(once) == classification(twice), (
            repr(tag), repr(once), repr(twice))

    assert depths_differ_somewhere, (
        "no tag in the domain distinguishes the two normalisation depths, so "
        "this test would pass vacuously")


def test_the_evidence_flag_is_scoped_to_governing_clause_predicate():
    """Every other predicate state keeps the span test it always had.

    The measured blast radius of this repair is the GOVERNING_CLAUSE_PREDICATE
    class and nothing else.  If the flag leaked into the other states, units
    outside that class would move and the isolation's Q1 would be false.
    """
    common = dict(
        internal_state="UNIQUE_BINDING_ESTABLISHED",
        subject_state="EXPLICIT_SUBJECT",
        subject_span=(0, 3),
        predicate_state="EXPLICIT_FINITE_PREDICATE",
        predicate_span=(4, 12),
        antecedent_state="ANTECEDENT_NOT_REQUIRED",
        required_context_ids=(),
        repair_requirement=None,
        structural_context_supplied=True,
        governing_state="GOVERNING_CLAUSE_UNIQUE",
    )
    with_flag = V2._terminal_facts(
        **common, predicate_local_predication_is_evidential=True)
    without_flag = V2._terminal_facts(
        **common, predicate_local_predication_is_evidential=False)
    assert with_flag.predicate_completeness == "BOUND_LOCAL"
    assert without_flag.predicate_completeness == "BOUND_LOCAL"
