"""M6, M7, M9 and M10: structural precedence in the selector.

Each of these defers a class of readings in comparison.  None of them removes a
candidate, changes an admissibility verdict, or moves a constant, and each is
silent where its structural precondition is absent.  Both halves are tested.
"""

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2

pytestmark = pytest.mark.no_db


def _analyses(text: str, language: str):
    return V2.build_lattice(text=text, language=language)[1]


def _states(analyses, attribute: str):
    return {getattr(analysis, attribute) for analysis in analyses}


# --- M6: an argument noun does not head a predication ----------------------

def test_m6_argument_noun_head_is_deferred_in_comparison():
    analyses = _analyses("IMPUGNAZIONI Titolo I Impugnazioni in generale", "it")
    argument_noun_heads = [
        analysis for analysis in analyses
        if analysis.predicate_head is not None
        and analysis.predicate_head.predicate_type == "ARGUMENT_NOUN"
    ]
    assert argument_noun_heads, "the probe text must produce such a head"
    _state, chosen, _reason = V2.resolve_internal(analyses)
    assert chosen is not None
    assert (chosen.predicate_head is None
            or chosen.predicate_head.predicate_type != "ARGUMENT_NOUN")


def test_m6_removes_no_analysis():
    """Deferral is not rejection: every reading is still in the lattice."""
    analyses = _analyses("IMPUGNAZIONI Titolo I Impugnazioni in generale", "it")
    assert any(
        analysis.predicate_head is not None
        and analysis.predicate_head.predicate_type == "ARGUMENT_NOUN"
        for analysis in analyses
    )


# --- M7: a head outside the finite matrix clause ---------------------------

def test_m7_records_head_clause_state():
    analyses = _analyses(
        "(1) Hat sich ein Abkömmling verschuldet, so kann der Erblasser "
        "das Recht beschränken.", "de")
    states = _states(analyses, "head_clause_state")
    assert states <= {
        "NO_FINITE_MATRIX_CLAUSE", "IN_FINITE_MATRIX_CLAUSE",
        "OUTSIDE_FINITE_MATRIX_CLAUSE",
    }


def test_m7_is_silent_without_a_finite_matrix_clause():
    analyses = _analyses("les personnes atteintes de maladies chroniques ;",
                         "fr")
    assert _states(analyses, "head_clause_state") == {
        "NO_FINITE_MATRIX_CLAUSE"}


# --- M9: the correlative apodosis carries the predication ------------------

def test_m9_selects_the_apodosis_predication():
    analyses = _analyses(
        "(1) Hat sich ein Abkömmling verschuldet, so kann der Erblasser "
        "das Recht beschränken.", "de")
    assert "IN_APODOSIS" in _states(analyses, "head_correlative_state")
    _state, chosen, _reason = V2.resolve_internal(analyses)
    assert chosen is not None
    assert chosen.head_correlative_state != "OUTSIDE_APODOSIS"


def test_m9_is_silent_where_no_apodosis_is_opened():
    analyses = _analyses("Die Rücklagen müssen ausgewiesen sein.", "de")
    assert _states(analyses, "head_correlative_state") == {"NO_APODOSIS"}


# --- M10: a comma-bounded dependent region ---------------------------------

def test_m10_defers_a_head_inside_a_bounded_dependent_region():
    analyses = _analyses(
        "Die Rücklagen, die umgewandelt werden sollen, müssen ausgewiesen "
        "sein.", "de")
    assert "IN_DEPENDENT_REGION" in _states(analyses, "head_region_state")
    _state, chosen, _reason = V2.resolve_internal(analyses)
    assert chosen is not None
    assert chosen.head_region_state != "IN_DEPENDENT_REGION"


def test_m10_opens_no_region_without_a_closing_comma():
    """An unclosed relativiser is not a bounded interruption."""
    analyses = _analyses(
        "Die Rücklagen, die in Grundkapital umgewandelt werden sollen", "de")
    assert _states(analyses, "head_region_state") <= {
        "NO_DEPENDENT_REGION", "OUTSIDE_DEPENDENT_REGION"}


def test_m10_region_helper_bounds_on_the_first_closer():
    from curunir_operational.v5_8_1 import clause_identity as CI
    text = ("Die Rücklagen, die umgewandelt werden sollen, müssen "
            "ausgewiesen sein.")
    regions = V2._bounded_dependent_regions(text, CI.build_clause_context(text))
    assert regions
    for start, stop in regions:
        assert 0 <= start < stop <= len(text)
        assert text[stop - 1] in ",،;؛"


def test_m10_unclosed_region_is_not_opened():
    from curunir_operational.v5_8_1 import clause_identity as CI
    text = "Die Rücklagen die umgewandelt werden sollen"
    assert V2._bounded_dependent_regions(
        text, CI.build_clause_context(text)) == []


# --- shared invariants ------------------------------------------------------

def test_structural_precedence_changes_no_frozen_constant():
    assert V2.SEPARATION_MARGIN == 0.12
    assert V2.REQUIRED_SLOT_MISSING_PENALTY == 0.35
    assert V2.UNSUPPORTED_SLOT_PENALTY == 0.15
    assert V2.SUPPORTED_OPTIONAL_SLOT_CREDIT == 0.03
    assert V2.MAX_OPTIONAL_CREDIT == 0.06


def test_recorded_states_are_always_populated():
    for text, language in (
        ("Die Rücklagen müssen ausgewiesen sein.", "de"),
        ("The appellant is required to make a statement.", "en"),
        ("(6) оказывать поддержку государствам-членам", "ru"),
        ("واستعمال الوشائع وأجهزة التبخير.", "ar"),
    ):
        for analysis in _analyses(text, language):
            assert analysis.head_clause_state != "NOT_ASSESSED"
            assert analysis.head_correlative_state != "NOT_ASSESSED"
            assert analysis.head_region_state != "NOT_ASSESSED"
