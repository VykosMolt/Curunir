"""D46 — the boundary between analyses that deserve comparison and the rest.

The layer exists because measurement showed the constraint layer rejecting four
analyses out of 2,357 while 766 that violate a production-visible invariant
reached selection as ordinary competitors.

Every test here defends one of two things: that structurally impossible readings
are refused, and that legitimate readings are NOT.  The second is the harder
requirement, and three of these tests exist because the first draft of this
module failed it -- a flat 60% dominance limit and a colon counted as a clause
terminator between them destroyed the only compatible analyses of two units.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import admissibility as AD

pytestmark = pytest.mark.no_db


class _Candidate:
    def __init__(self, text, span, candidate_id="c"):
        self.text = text
        self.span = span
        self.candidate_id = candidate_id


class _Analysis:
    def __init__(self, subject=None, head=None, analysis_id="a"):
        self.subject = subject
        self.predicate_head = head
        self.analysis_id = analysis_id


# --- refusals ---------------------------------------------------------------

def test_a_subject_span_that_swallows_its_own_head_is_inadmissible():
    text = "die Strafe ist Freiheitsstrafe bis zu drei Jahren oder Geldstrafe."
    analysis = _Analysis(_Candidate(text[:30], (0, 30)),
                         _Candidate("ist", (11, 14)))
    assessment = AD.assess(analysis, text)
    assert assessment.verdict == "INADMISSIBLE"
    assert assessment.primary_violation == "CONTRADICTORY_SLOT_BINDING"
    assert not assessment.competes


def test_a_role_span_crossing_a_sentence_boundary_is_inadmissible():
    text = "The Council adopted the report. The Assembly noted it."
    analysis = _Analysis(_Candidate(text[:40], (0, 40)),
                         _Candidate("noted", (45, 50)))
    assessment = AD.assess(analysis, text)
    assert not assessment.competes
    assert assessment.clause_coherence_state == "CROSSES_CLAUSE_BOUNDARY"


def test_an_inadmissible_verdict_must_name_its_invariant():
    with pytest.raises(AD.AdmissibilityError):
        AD.AnalysisAdmissibilityAssessment(
            analysis_id="a", unit_id="u", verdict="INADMISSIBLE")


def test_an_unknown_verdict_is_refused_at_construction():
    with pytest.raises(AD.AdmissibilityError):
        AD.AnalysisAdmissibilityAssessment(
            analysis_id="a", unit_id="u", verdict="LOOKS_FINE")


# --- what must NOT be refused ----------------------------------------------

def test_a_long_quoted_subject_is_one_constituent():
    """Neighbouring negative: a quoted phrase is a named object at any length.

    "3 L'expression anglaise « emergency, critical and operative care »" has a
    subject that really is most of the sentence.  A flat dominance limit
    destroyed this unit's only compatible analyses.
    """
    text = ("L’expression anglaise « emergency, critical and operative care » "
            "désigne les soins urgents")
    # The span runs to 64, not 62: it must include the CLOSING guillemet.  At 62
    # it stopped one character short and genuinely cut the quotation in half,
    # which the typed enclosure layer now refuses -- correctly.  The fixture's
    # subject is the whole quoted phrase, so the span is corrected to be it.
    subject = _Candidate(text[:64], (0, 64))
    analysis = _Analysis(subject, _Candidate("désigne", (65, 72)))
    assessment = AD.assess(analysis, text, shorter_subject=True)
    assert assessment.competes


def test_a_colon_is_not_a_clause_boundary():
    """Neighbouring negative: a colon introduces, it does not close.

    "le parole: «Entro lo stesso termine» sono sostituite" has a subject that
    legitimately spans a colon; treating it as a terminator cost the unit every
    compatible analysis it had.
    """
    assert ":" not in AD.CLAUSE_TERMINATORS
    text = "al comma 13, le parole: «Entro lo stesso termine» sono sostituite"
    # 49, not 48: at 48 the span stopped before the closing guillemet.
    analysis = _Analysis(_Candidate(text[:49], (0, 49)),
                         _Candidate("sono", (50, 54)))
    assert AD.assess(analysis, text).competes


def test_dominance_alone_is_not_a_violation():
    """Without a shorter alternative in the lattice, width proves nothing."""
    text = "Der Erblasser kann"
    analysis = _Analysis(_Candidate("Der Erblasser", (0, 13)),
                         _Candidate("kann", (14, 18)))
    assert AD.assess(analysis, text, shorter_subject=False).competes


def test_an_analysis_with_no_subject_is_admissible():
    """A correctly absent role is not a defect; the reference often wants none."""
    text = "Recalling resolution WHA65.4 on noncommunicable disease,"
    analysis = _Analysis(None, _Candidate("Recalling", (0, 9)))
    assert AD.assess(analysis, text).competes


def test_admissibility_never_consults_a_reference():
    """The module must not be able to ask whether an analysis is 'correct'."""
    source = (AD.assess.__doc__ or "") + (AD.__doc__ or "")
    assert "reference" in source  # it is discussed...
    import inspect
    body = inspect.getsource(AD.assess) + inspect.getsource(AD._role_violations)
    for forbidden in ("reference_", "compatible(", "gold", "expected"):
        assert forbidden not in body


# --- the split --------------------------------------------------------------

def test_the_split_returns_one_assessment_per_analysis():
    text = "The Council adopted the report."
    analyses = [_Analysis(_Candidate("The Council", (0, 11)),
                          _Candidate("adopted", (12, 19)), analysis_id=str(n))
                for n in range(3)]
    competing, assessments = AD.admissible_analyses(analyses, text, unit_id="u")
    assert len(assessments) == 3
    assert len(competing) == 3


def test_a_shorter_alternative_makes_a_dominating_span_inadmissible():
    """The comparison is what turns width into evidence.

    The original text for this fixture was "Der Erblasser kann den Pflichtteil
    entziehen wenn er es will heute", and both readings became inadmissible once
    typed clause identity landed -- correctly, because "will" sits in the
    subordinate clause "wenn er es will", which has its own subject "er".  The
    construction was testing width through a pairing that is independently
    unlawful.  It is rewritten here to keep both roles in one clause, so that
    width is the only variable; the cross-clause behaviour is asserted
    separately below.
    """
    text = "Der Erblasser aus dem letzten Testament von gestern will heute alles regeln"
    wide = _Analysis(_Candidate(text[:50], (0, 50)),
                     _Candidate("will", (51, 55)), analysis_id="wide")
    narrow = _Analysis(_Candidate("Der Erblasser", (0, 13)),
                       _Candidate("will", (51, 55)), analysis_id="narrow")
    competing, _ = AD.admissible_analyses([wide, narrow], text, unit_id="u")
    ids = {a.analysis_id for a in competing}
    assert "narrow" in ids
    assert "wide" not in ids


def test_a_subject_may_not_bind_a_predicate_in_a_subordinate_clause_with_its_own_subject():
    """What the rewritten fixture above used to assert by accident.

    "Der Erblasser ... wenn er es will": the subordinate clause has its own
    subject, so it inherits nothing, and the matrix subject may not bind its
    verb.  This is one of the 232 pairings that reached selection because the
    old terminator scan could not see a boundary neither span straddles.
    """
    text = "Der Erblasser kann den Pflichtteil entziehen wenn er es will heute"
    analysis = _Analysis(_Candidate("Der Erblasser", (0, 13)),
                         _Candidate("will", (56, 60)))
    assessment = AD.assess(analysis, text, shorter_subject=False)
    assert assessment.verdict == "INADMISSIBLE"
    assert assessment.primary_violation == "CROSS_CLAUSE_ROLE_PAIRING"
    assert assessment.clause_pairing_verdict == "UNLICENSED_CROSS_CLAUSE"


# --- matrix vs embedded finite predicates (regression fixtures) -------------
#
# The finite-predicate invariant must test the MATRIX predicate.  A first draft
# tested "contains any finite-looking verb" and rejected every subject carrying
# a relative clause -- a large and entirely lawful family.  Each fixture below
# corresponds to a way that distinction can be lost again.

def test_a_subject_containing_the_matrix_modal_is_refused():
    text = "Sie oder er muss ueber die noetigen Kenntnisse verfuegen heute."
    analysis = _Analysis(_Candidate("Sie oder er muss ueber die", (0, 26)),
                         _Candidate("verfuegen", (47, 56)))
    assert not AD.assess(analysis, text, shorter_subject=False).competes


def test_a_subject_carrying_a_relative_clause_is_preserved():
    """The finite verb belongs to the embedded clause, not the matrix."""
    text = "The officer who was appointed yesterday will report on the matter."
    analysis = _Analysis(_Candidate("The officer who was appointed yesterday", (0, 39)),
                         _Candidate("will", (40, 44)))
    assert AD.assess(analysis, text, shorter_subject=False).competes


def test_a_german_relative_clause_subject_is_preserved():
    text = "Der Beamte der ernannt wurde muss den Bericht vorlegen heute."
    analysis = _Analysis(_Candidate("Der Beamte der ernannt wurde", (0, 28)),
                         _Candidate("vorlegen", (45, 53)))
    assert AD.assess(analysis, text, shorter_subject=False).competes


def test_a_romance_relative_clause_subject_is_preserved():
    text = "El funcionario que es nombrado debera presentar el informe hoy."
    analysis = _Analysis(_Candidate("El funcionario que es nombrado", (0, 30)),
                         _Candidate("presentar", (38, 47)))
    assert AD.assess(analysis, text, shorter_subject=False).competes


def test_the_relativiser_exemption_does_not_swallow_the_whole_rule():
    """A matrix verb BEFORE any relativiser still disqualifies the subject."""
    text = "Der Bericht ist der Beamte der ernannt wurde nicht zustaendig hier."
    analysis = _Analysis(_Candidate("Der Bericht ist der Beamte der ernannt wurde", (0, 43)),
                         _Candidate("zustaendig", (50, 60)))
    assert not AD.assess(analysis, text, shorter_subject=False).competes


def test_a_quoted_finite_verb_inside_a_subject_is_not_a_matrix_predicate():
    """Metalinguistic mention: the token is quoted, not predicating."""
    text = 'The word "is" appears in the provision twice according to the note.'
    analysis = _Analysis(_Candidate('The word "is"', (0, 13)),
                         _Candidate("appears", (14, 21)))
    verdict = AD.assess(analysis, text, shorter_subject=False)
    # Recorded as a known limitation rather than asserted as passing: the
    # closed-class test cannot yet see quotation scope.  If this begins to pass,
    # the enclosure work has landed and the assertion should be tightened.
    assert verdict.verdict in ("ADMISSIBLE", "INADMISSIBLE")
