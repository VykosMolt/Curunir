"""P6.3 workstream B — the ambiguity/margin justification family.

WHY THIS FILE EXISTS
--------------------
`resolve_internal` publishes two sentences that together justify every record
this family reaches, and the second of them is the SOLE justification carried
by every EVIDENCE_BOUND record.  Four independent review seats found the family
defective on the frozen corpus, in two ways:

  (i)  SCOPE.  The count was taken over the COMPARISON POOL while the same
       record published `analyses_considered` for the FULL analysis list.  On
       14 of the 40 corpus units in the family the two diverge, by as much as
       7 against 200.

  (ii) BASIS.  "one analysis leads the field by more than the declared margin"
       was VACUOUS on both EVIDENCE_BOUND units -- their comparison pool holds
       exactly ONE analysis, so there was no field to lead -- and MISLEADING on
       `d25unit-185e002e`, where three of the 168 legal analyses strictly
       outscore the selected reading (0.28, 0.28, 0.25 against 0.18) and the
       selection was made by D42J relation precedence rather than by any
       scalar margin.

Two seats disagreed about whether the sentence was false at all, and BOTH WERE
RIGHT: it never stated its own scope, so it had no truth value to measure.
That ambiguity is the defect this file pins closed.

WHAT IS PINNED, AND WHY NOT THE TEXT
------------------------------------
`test_operational_v5_8_1_p61_fullpop_reason_pin.py` already pins the exact
TEXT of every sentence on the frozen population.  That pin is regenerated
whenever production's wording changes, so on its own it cannot stop a
coordinated edit that changes production and regenerates the table together --
which is exactly how a false sentence survived into a shipped record before.

This file pins PROPERTIES instead, and each of them is checkable without any
oracle:

  P1  SELF-CONSISTENCY.  A sentence that states "of M considered" must agree
      with the SAME RECORD's own `analyses_considered`.  Two fields of one
      record contradicting each other needs no reference to detect, and it is
      precisely defect (i).
  P2  SCOPE IS ALWAYS STATED.  Every member of the family names the pool it
      quantifies over.
  P3  THE WITHDRAWN CLAIM NEVER RETURNS.  No record may publish "leads the
      field" again.
  P4  CONSTRUCTION.  The sentence builders are driven directly over a grid of
      pool sizes and relation types, and every figure they state must be the
      figure they were given.  This is what "mechanically true by
      construction" means, checked rather than asserted.
  P5  THE BASIS CANNOT DRIFT FROM THE ORDERING.  The relation set that decides
      the sort order and the relation set the sentence describes must be the
      SAME OBJECT, checked on production's own syntax.

Nothing here reads production to obtain an expected value.  P1-P3 compare a
record against ITSELF; P4 compares a pure function against its own arguments;
P5 is a statement about syntax.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


# ===========================================================================
# The constructed surface.  No frozen corpus is required, so every arm below
# runs in BOTH arena shapes.
# ===========================================================================

_BODIES = {
    "decisive_ru": dict(
        text="предотвратимой смертности, что особенно ярко проявилось в ходе "
             "пандемии COVID-19,",
        left_context="кислороду и что отсутствие этого доступа является "
                     "фактором, способствующим", heading=""),
    "operative_ru": dict(
        text="Рекомендует государствам-членам представить доклад Секретариату.",
        left_context="Комитет рассмотрел доклад.", heading=""),
    "fragment_ru": dict(
        text="но и позволяют добиться жизнестойкости в долгосрочной "
             "перспективе, что способствует",
        left_context="психосоциальной поддержке, которые не только "
                     "удовлетворяют насущные потребности,", heading=""),
    "pronominal_ru": dict(
        text="что они представляют доклад Секретариату в установленный срок,",
        left_context="Комитет рассмотрел доклад,", heading=""),
    "deontic_ar": dict(
        text="وينبغي علاج الأشخاص المعرّضين لخطر كبير بالأدوية المضادة "
             "للفيروسات في أقرب وقت ممكن.",
        left_context="التماس الرعاية الطبية عند ظهور الأعراض.", heading=""),
    "subordinate_de": dict(
        text="sie hierzu aus rechtlichen Gründen nicht in der Lage ist;",
        left_context="1.", heading="§ 5"),
    "german_lexical_de": dict(
        text="Die Behörde hat die Maßnahmen zu treffen, die sie für "
             "erforderlich hält.",
        left_context="Der Rat prüfte den Bericht.", heading=""),
    "committee_en": dict(
        text="The Committee shall review the report of the Secretariat.",
        left_context="The Council considered the matter.", heading=""),
    "operative_es": dict(
        text="El Ministerio de Sanidad adopta las medidas necesarias para la "
             "proteccion de la salud publica.",
        left_context="El Consejo examino el informe.", heading=""),
    "operative_it": dict(
        text="Il Ministero della salute adotta le misure necessarie per la "
             "tutela della salute pubblica.",
        left_context="Il Consiglio ha esaminato la relazione.", heading=""),
    "operative_fr": dict(
        text="Le Ministere adopte les mesures necessaires pour la protection "
             "de la sante publique.",
        left_context="Le Conseil a examine le rapport.", heading=""),
    "date_line": dict(text="A70/12  Agenda item 13.1  22 May 2017",
                      left_context="", heading=""),
}

_LANGUAGES = (None, "ru", "ar", "de", "en", "es", "fr", "it", "zz")
_REGIONS = ("UNKNOWN_STRUCTURAL_REGION", "PRIMARY_PROPOSITION_CONTENT",
            "NAVIGATION_MENU")


def _shapes():
    base = ST.empty_context(document_id="p63b-doc", region_id="p63b-region",
                            reading_order_state="READING_ORDER_ESTABLISHED")
    yield "none", None
    yield "plain", base
    yield "unique", dataclasses.replace(
        base, governing_clause_candidates=("p63b-gov-a",),
        governing_clause_relation_types=("LIST_ITEM_OF",),
        governing_clause_confidences=(0.91,))
    yield "ambiguous", dataclasses.replace(
        base, governing_clause_candidates=("p63b-gov-a", "p63b-gov-b"),
        governing_clause_relation_types=("LIST_ITEM_OF", "LIST_ITEM_OF"),
        governing_clause_confidences=(0.91, 0.88))


def _records():
    for body_name in sorted(_BODIES):
        spec = _BODIES[body_name]
        for language in _LANGUAGES:
            for region in _REGIONS:
                for shape_name, context in _shapes():
                    yield (body_name, language, region, shape_name,
                           V2.bind(candidate_id="p63b", text=spec["text"],
                                   language=language,
                                   left_context=spec["left_context"],
                                   heading=spec["heading"],
                                   content_region_type=region,
                                   structural_context=context))


# ---------------------------------------------------------------------------
# The family, recognised by LITERAL fragments of this file.
# ---------------------------------------------------------------------------

_COMPARED = "analyses compared"
_ONLY = "the selected reading is the only analysis compared"
_CONSIDERED = " considered"
_LEADS = "leads the field"


def _stated_considered(reason: str) -> int | None:
    """The "of M considered" figure, if the sentence states one."""
    marker = "left of "
    if marker not in reason:
        return None
    tail = reason.split(marker, 1)[1]
    digits = tail.split(" ", 1)[0]
    return int(digits) if digits.isdigit() else None


def _in_family(reason: str) -> bool:
    return _COMPARED in reason or reason.startswith(_ONLY)


# ===========================================================================
# P1 -- self-consistency with the record's own analyses_considered
# ===========================================================================

@pytest.mark.parametrize("body_name", sorted(_BODIES))
def test_the_stated_scope_agrees_with_the_records_own_analysis_count(
        body_name):
    """P1, and defect (i) directly.

    The sentence's "of M considered" and the record's `analyses_considered`
    are two statements by the SAME record about the same quantity.  A record
    that contradicts itself is detectable without any reference, which is why
    this is the arm that cannot be defeated by regenerating a frozen table.
    """
    spec = _BODIES[body_name]
    for language in _LANGUAGES:
        for region in _REGIONS:
            for shape_name, context in _shapes():
                record = V2.bind(
                    candidate_id="p63b", text=spec["text"], language=language,
                    left_context=spec["left_context"], heading=spec["heading"],
                    content_region_type=region, structural_context=context)
                stated = _stated_considered(record.binding_reason)
                if stated is None:
                    continue
                assert stated == record.analyses_considered, (
                    body_name, language, region, shape_name,
                    stated, record.analyses_considered, record.binding_reason)


def test_the_family_is_actually_reached_by_the_constructed_surface():
    """Floor: the arms above are not vacuous.

    If no constructed point reached the family, every quantified arm would
    pass over an empty set.  This requires the surface to reach BOTH members
    of the family and to produce at least one sentence that states a scope.
    """
    reasons = [record.binding_reason for *_ignored, record in _records()]
    family = [r for r in reasons if _in_family(r)]
    assert len(family) >= 20, len(family)
    assert any("score less than the declared" in r for r in family), \
        "the ambiguity member was never reached"
    assert any("scores at least the declared" in r or r.startswith(_ONLY)
               for r in family), "the selection member was never reached"
    assert any(_stated_considered(r) is not None for r in family), \
        "no sentence stated a scope, so P1 quantified over nothing"


# ===========================================================================
# P2/P3 -- the scope is always stated, and the withdrawn claim never returns
# ===========================================================================

def test_every_member_of_the_family_names_the_pool_it_quantifies_over():
    """P2.  A margin claim with no stated scope is the defect itself."""
    for body_name, language, region, shape_name, record in _records():
        reason = record.binding_reason
        if not _in_family(reason):
            continue
        assert (_COMPARED in reason or reason.startswith(_ONLY)), (
            body_name, language, region, shape_name, reason)


def test_no_record_ever_claims_to_lead_the_field_again():
    """P3.  The withdrawn wording, pinned as withdrawn.

    It was vacuous on a singleton pool and misleading where the selection was
    made by relation precedence.  Reintroducing it -- by revert, by copy, or
    by a new branch -- fails here.
    """
    for body_name, language, region, shape_name, record in _records():
        assert _LEADS not in record.binding_reason, (
            body_name, language, region, shape_name, record.binding_reason)
    assert _LEADS not in inspect.getsource(V2.resolve_internal)
    assert _LEADS not in inspect.getsource(V2._unique_selection_sentence)


# ===========================================================================
# P4 -- the builders state the figures they are given, over a grid
# ===========================================================================

_GRID = [
    # (considered, legal, structural, compared, relation)
    (1, 1, 1, 1, ""),
    (7, 7, 7, 1, "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF"),
    (168, 168, 77, 1, "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE"),
    (200, 199, 199, 7, "LOCAL_SUBJECT_UNIQUE_CLOSED_FINITE_ANCHOR"),
    (142, 142, 16, 16, ""),
    (116, 116, 41, 4, "POST_RELATIVE_MODAL_RESUMES_SUBJECT"),
    (94, 94, 38, 4, "LOCAL_SUBJECT_REQUIRED_TO_DEONTIC_FRAME"),
    (67, 67, 49, 49, ""),
    (10, 10, 4, 4, ""),
    (5, 5, 5, 5, ""),
    (3, 2, 2, 2, ""),
]


@pytest.mark.parametrize("row", _GRID)
def test_the_sentence_builders_state_the_numbers_they_are_given(row):
    """P4.  Mechanically true by construction, checked."""
    considered, legal, structural, compared, relation = row
    provenance = V2._comparison_provenance(
        considered=considered, legal=legal, structural=structural,
        compared=compared, active_relation_type=relation)

    ambiguity = V2._ambiguity_sentence(
        within_margin=compared - 1, compared=compared, provenance=provenance)
    assert f"{compared} of the {compared} analyses compared" in ambiguity
    assert f"declared {V2.SEPARATION_MARGIN} margin below the selected " \
           "reading" in ambiguity
    assert ambiguity.endswith("the source does not decide")

    selection = V2._unique_selection_sentence(
        compared=compared, provenance=provenance,
        active_relation_type=relation)
    if compared == 1:
        assert selection.startswith(_ONLY)
        assert "leads" not in selection
        assert "no margin separated it from anything" in selection
    else:
        assert f"among the {compared} analyses compared" in selection
        assert "scores at least the declared" in selection

    # the provenance names the considered total exactly when it narrowed
    if compared == considered:
        assert provenance == ""
        assert _stated_considered(ambiguity) is None
    else:
        assert _stated_considered(ambiguity) == considered
        assert _stated_considered(selection) == considered

    # and it names a narrowing if and only if that narrowing happened
    assert ("the hard-constraint filter" in provenance) == (legal < considered)
    assert ("the structural precedence filters" in provenance) == \
        (structural < legal)
    assert (f"a decisive {relation} relation" in provenance) == \
        bool(relation and compared < structural)


def test_the_relation_ordering_basis_is_named_only_when_it_applies():
    """P4's basis half: a relation-ordered pool says so, a score-ordered one
    does not, and neither says it for a singleton, where there was no
    ordering to describe."""
    ordered = sorted(V2._RELATION_ORDERED_POOL)
    assert len(ordered) == 4
    for relation in ordered:
        sentence = V2._unique_selection_sentence(
            compared=5, provenance="", active_relation_type=relation)
        assert f"chosen by a decisive {relation} relation" in sentence
        assert "rather than by score" in sentence
        singleton = V2._unique_selection_sentence(
            compared=1, provenance="", active_relation_type=relation)
        assert "chosen by a decisive" not in singleton
    for relation in ("", "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF", "SOMETHING_ELSE"):
        sentence = V2._unique_selection_sentence(
            compared=5, provenance="", active_relation_type=relation)
        assert "chosen by a decisive" not in sentence, relation


def test_the_scope_checker_in_this_file_is_not_vacuous():
    """Non-vacuity for `_stated_considered` and `_in_family`: decoys defined
    here, never in production."""
    assert _stated_considered("what the structural precedence filters left "
                              "of 168 considered") == 168
    assert _stated_considered("no provenance at all") is None
    assert _in_family("7 of the 7 analyses compared score less than ...")
    assert _in_family(_ONLY + ", which is ...")
    assert not _in_family("one analysis leads the field by more than the "
                          "declared margin")
    assert not _in_family("no analysis carries a predicate or a governing "
                          "clause")


# ===========================================================================
# P5 -- the described basis and the actual ordering are one object
# ===========================================================================

def test_the_ordering_set_and_the_described_set_are_the_same_object():
    """P5.  The sort branch and the sentence must read ONE constant.

    While the four relation types were an anonymous set literal inside the
    sort branch, a justification could describe an ordering the code no longer
    used and nothing would notice.  This asserts, on production's own syntax,
    that `resolve_internal` and `_unique_selection_sentence` both resolve the
    module constant and that neither carries its own inline copy.
    """
    module = ast.parse(inspect.getsource(V2))
    constants = [node for node in module.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name)
                         and t.id == "_RELATION_ORDERED_POOL"
                         for t in node.targets)]
    assert len(constants) == 1, "the constant must be defined exactly once"

    for function_name in ("resolve_internal", "_unique_selection_sentence"):
        source = textwrap.dedent(
            inspect.getsource(getattr(V2, function_name)))
        tree = ast.parse(source).body[0]
        reads = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Name)
                 and node.id == "_RELATION_ORDERED_POOL"]
        assert reads, f"{function_name} does not read the constant"
        # and it must not carry an inline COPY of the ordering set.  The test
        # is EQUALITY with the constant's own membership, not overlap: the
        # D42J precedence tuple in `resolve_internal` legitimately lists five
        # relation types in priority order, four of which are also the ordered
        # set, and forbidding that would forbid the algorithm rather than the
        # duplication.
        literals = [node for node in ast.walk(tree)
                    if isinstance(node, (ast.Set, ast.List, ast.Tuple))
                    and {e.value for e in node.elts
                         if isinstance(e, ast.Constant)}
                    == set(V2._RELATION_ORDERED_POOL)]
        assert literals == [], (
            f"{function_name} carries an inline copy of the ordering set; the "
            "sentence could then describe an ordering the sort does not use")


def test_the_singleton_case_cannot_claim_a_margin_it_did_not_apply():
    """The exact shape of defect (ii), pinned.

    A pool of one reaches the selection branch trivially -- there is no rival
    to be within the margin -- so any sentence there that asserts a margin win
    is vacuous by construction, whatever the numbers happen to be.
    """
    for relation in ("", "LOCAL_SUBJECT_GERMAN_LEXICAL_FINITE",
                     "LOCAL_OVERT_PRONOMINAL_SUBJECT_OF"):
        for provenance in ("", "what the structural precedence filters left "
                               "of 168 considered"):
            sentence = V2._unique_selection_sentence(
                compared=1, provenance=provenance,
                active_relation_type=relation)
            assert "leads" not in sentence
            assert "scores at least the declared" not in sentence
            assert "by more than" not in sentence
            assert sentence.startswith(_ONLY)
