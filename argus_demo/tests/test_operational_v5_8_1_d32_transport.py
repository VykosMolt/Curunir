"""D32 — typed structural context, proved through the real production path.

The measurement that motivated this: 35 of 120 diagnostic units are
governing-clause recitals splitting 20 PARTIAL / 15 RECOVERABLE, with identical
left-context length either side.  No text window separates them.  The
distinguishing fact is structural, `regions.py` computes it, and until now
`InvariantContext` discarded it.

These tests do not construct `StructuralClauseContext` by hand and hand it to
the binder — that would prove nothing, and section 9 forbids it.  They start
from a source document, segment it, derive the relations, build the production
context, and call the production entry point.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import regions as RG
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


GOVERNED = """<html><body>
<h2>Resolution WHA76.4</h2>
<p>The Health Assembly, having considered the report of the Director-General,</p>
<p>DECIDES to adopt the global strategy set out in the annex to this document.</p>
<p>Recalling resolution WHA65.4 on the prevention of noncommunicable disease,</p>
</body></html>"""

UNGOVERNED = """<html><body>
<h2>Background note</h2>
<p>The following observations were collected during the review period.</p>
<p>Recalling resolution WHA65.4 on the prevention of noncommunicable disease,</p>
</body></html>"""


def _segment(markup: str, source_id: str):
    return RG.segment(markup, source_id=source_id, source_family_id="test.example")


def _recital_region(regions):
    return next(r for r in regions if ST.is_recital(r.text))


def _context(markup: str, source_id: str) -> ST.StructuralClauseContext:
    regions = _segment(markup, source_id)
    recital = _recital_region(regions)
    return ST.build_structural_context(
        regions=regions, region_id=recital.region_id,
        document_id=source_id, manifestation_id=f"{source_id}-m")


# --- the paired case -------------------------------------------------------

def test_same_recital_with_a_governing_enactment_is_recoverable():
    regions = _segment(GOVERNED, "doc-governed")
    recital = _recital_region(regions)
    context = _context(GOVERNED, "doc-governed")
    assert context.governing_clause_candidates
    assert context.governing_resolution()[0] == "GOVERNING_CLAUSE_UNIQUE"

    result = V2.bind(candidate_id="u1", text=recital.text, language="en",
                     structural_context=context)
    assert result.subject_state == "GOVERNING_CLAUSE_SUBJECT"
    assert result.role_binding_state == "ROLE_BINDING_RECOVERABLE"


def test_the_same_recital_text_without_an_enactment_is_partial():
    """Identical wording, different structure, different terminal state."""
    regions = _segment(UNGOVERNED, "doc-ungoverned")
    recital = _recital_region(regions)
    context = _context(UNGOVERNED, "doc-ungoverned")
    assert context.governing_resolution()[0] == "GOVERNING_CLAUSE_ABSENT"

    result = V2.bind(candidate_id="u2", text=recital.text, language="en",
                     structural_context=context)
    assert result.subject_state == "GOVERNING_CLAUSE_SUBJECT"
    assert result.role_binding_state == "ROLE_BINDING_PARTIAL"


def test_the_two_recitals_are_the_same_words():
    """The distinction cannot be coming from the span; prove it explicitly."""
    left = _recital_region(_segment(GOVERNED, "a")).text
    right = _recital_region(_segment(UNGOVERNED, "b")).text
    assert left == right


# --- absence is distinguished from never-supplied -------------------------

def test_an_unwired_caller_is_not_mistaken_for_an_absent_enactment():
    regions = _segment(GOVERNED, "doc-governed")
    recital = _recital_region(regions)
    unwired = V2.bind(candidate_id="u3", text=recital.text, language="en")
    assert unwired.role_binding_state == "ROLE_BINDING_PARTIAL"
    assert "did not supply" in unwired.binding_reason


# --- bounded search --------------------------------------------------------

def test_the_search_does_not_cross_a_section_heading():
    """An enactment in a different section does not govern this recital."""
    markup = """<html><body>
<h2>Part I</h2>
<p>The Health Assembly,</p>
<h2>Part II</h2>
<p>Recalling resolution WHA65.4 on noncommunicable disease,</p>
</body></html>"""
    context = _context(markup, "doc-sectioned")
    assert context.governing_resolution()[0] == "GOVERNING_CLAUSE_ABSENT"


def test_several_plausible_enactments_are_ambiguous_not_nearest_wins():
    markup = """<html><body>
<h2>Resolution</h2>
<p>The Health Assembly,</p>
<p>The Executive Board,</p>
<p>Recalling resolution WHA65.4 on noncommunicable disease,</p>
</body></html>"""
    context = _context(markup, "doc-ambiguous")
    state, chosen, _ = context.governing_resolution()
    assert state == "GOVERNING_CLAUSE_AMBIGUOUS"
    assert chosen is None


def test_lookback_is_bounded_by_the_declared_window():
    filler = "".join(
        f"<p>Paragraph {n} records an administrative detail of the session.</p>"
        for n in range(ST.MAX_STRUCTURAL_LOOKBACK + 4))
    markup = ("<html><body><h2>Resolution</h2>"
              "<p>The Health Assembly,</p>"
              + filler +
              "<p>Recalling resolution WHA65.4 on noncommunicable disease,</p>"
              "</body></html>")
    context = _context(markup, "doc-far")
    assert context.governing_resolution()[0] == "GOVERNING_CLAUSE_ABSENT"


# --- lineage ---------------------------------------------------------------

def test_inherited_roles_are_named_by_region_never_pasted_as_text():
    context = _context(GOVERNED, "doc-governed")
    for relation in context.recital_to_enactment_relations:
        assert relation.target_region_id.startswith("v5-8-1-region")
        assert relation.relation_type == "RECITAL_GOVERNED_BY_ENACTMENT"
    regions = _segment(GOVERNED, "doc-governed")
    recital = _recital_region(regions)
    result = V2.bind(candidate_id="u4", text=recital.text, language="en",
                     structural_context=context)
    # the enacting clause's words must not appear in the local span
    assert "Health Assembly" not in (result.predicate_head
                                     + result.predicate_complement)


def test_context_carries_a_source_hash_and_required_context_ids():
    context = _context(GOVERNED, "doc-governed")
    assert context.context_source_hash
    assert "LEFT_CONTEXT" in context.required_context_ids


# --- transport-boundary mutations (section 11.1) ---------------------------

def test_mutation_relation_computed_then_dropped_changes_the_verdict():
    """If the transport is removed, the recoverable case must stop being one."""
    regions = _segment(GOVERNED, "doc-governed")
    recital = _recital_region(regions)
    context = _context(GOVERNED, "doc-governed")
    with_transport = V2.bind(candidate_id="u5", text=recital.text,
                             language="en", structural_context=context)
    without_transport = V2.bind(candidate_id="u5", text=recital.text,
                                language="en", structural_context=None)
    assert with_transport.role_binding_state == "ROLE_BINDING_RECOVERABLE"
    assert without_transport.role_binding_state == "ROLE_BINDING_PARTIAL"


def test_mutation_empty_context_is_absent_not_unique():
    empty = ST.empty_context(document_id="d", region_id="r")
    assert empty.governing_resolution()[0] == "GOVERNING_CLAUSE_ABSENT"


def test_mutation_confidence_and_candidate_lengths_must_agree():
    with pytest.raises(ValueError):
        ST.StructuralClauseContext(
            context_id="c", document_id="d", manifestation_id="m",
            current_region_id="r", current_proposition_id="p",
            governing_clause_candidates=("a", "b"),
            governing_clause_confidences=(0.9,))
