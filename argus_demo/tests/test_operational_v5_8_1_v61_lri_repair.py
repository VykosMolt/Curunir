"""V6.1 LRI repair — LRI-R1 (provenance-aware symmetric absence) and LRI-D1.

Every test here FAILS against a byte-exact revert to the accepted substrate
2afab0d5, and that is executed, not asserted: see
artifacts/curunir_v6_readiness/v6_1_lri_repair/scripts/lri_repair_revert_proof.py,
which installs the frozen 2afab0d5 snapshot and requires this file to go red.

The controls in this module are built to be OBSERVED FIRING IN BOTH DIRECTIONS.
The campaign's recurring defect is a check that reports green because its probe
reads a constant or matches nothing, so the LRI-R1 tests are arranged as a 2x2
over the two things the repair claims to separate:

                          | governing verdict ABSENT | verdict NOT_SUPPLIED
    -------------------------------------------------------------------------
    M8 evidence-of-absence| ABSENT_BY_CONSTRUCTION   | UNRESOLVED
    M14 span-closure route| UNRESOLVED               | UNRESOLVED

Three of the four cells differ from the fourth, so no constant can satisfy the
set; and the top-left/bottom-left pair share a governing verdict while the
top-left/top-right pair share a provenance, so neither variable alone explains
the table.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db

#: `required_context_ids` vocabulary, quoted from the frozen reference schema
#: 55_d25_role_binding_reference/freeze/schema.py: CONTEXT_IDS.  Written out
#: rather than imported so that a change to the frozen file cannot silently
#: widen what this test accepts.
FROZEN_CONTEXT_IDS = ("LEAD_IN", "LEFT_CONTEXT", "HEADING", "SOURCE_PUBLISHER")

#: A structural context carrying no governing candidates.  `governing_resolution`
#: returns the POSITIVE verdict GOVERNING_CLAUSE_ABSENT for it -- "no typed
#: governing relation reaches this span" -- which is the evidence M8 requires.
def _absent_governor_context():
    return ST.empty_context(document_id="d-lri", region_id="r-lri")


#: A body with no verbal or predicative evidence: the hard-negative route.
_STRUCTURAL_NEGATIVE_BODY = "COMMITTEE SESSION 2024"
#: The P4 publication witness.  Its subject constituent is a bare preposition,
#: so NO_SEMANTIC_SUBJECT arrives from M14's span closure, never from M8.
_M14_WITNESS_BODY = "in shall the policy"

_BASE = dict(language="en", left_context="", heading="",
             content_region_type="PRIMARY_PROPOSITION_CONTENT")


def _bind(candidate_id, text, context=None):
    kwargs = dict(candidate_id=candidate_id, text=text, **_BASE)
    if context is not None:
        kwargs["structural_context"] = context
    return V2.bind(**kwargs)


def test_the_governing_verdict_this_repair_keys_on_is_the_positive_one():
    """Precondition, not decoration.

    Every LRI-R1 test below depends on `empty_context` producing the POSITIVE
    verdict GOVERNING_CLAUSE_ABSENT rather than the ignorance verdict.  If that
    ever changed, the 2x2 would collapse to a single column and the tests would
    pass while measuring nothing.  This asserts the precondition directly.
    """
    state, _identifier, reason = _absent_governor_context().governing_resolution()
    assert state == "GOVERNING_CLAUSE_ABSENT"
    assert "no typed governing relation reaches this span" in reason


# --- LRI-R1: the 2x2 ------------------------------------------------------

def test_m8_evidence_of_absence_reads_absent_on_BOTH_axes():
    """Top-left cell.  No local predication AND a positive verdict of no
    governing structure: there is no frame anywhere, so there is nothing for
    either role to be unresolved about.  Both axes must say so, and must say the
    SAME thing -- the mixed pair is the defect LRI-R1 removes."""
    record = _bind("lri-r1-evidential", _STRUCTURAL_NEGATIVE_BODY,
                   _absent_governor_context())
    assert record.subject_state == "NO_SEMANTIC_SUBJECT"
    assert record.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert record.subject_completeness == "ABSENT_BY_CONSTRUCTION"
    assert record.predicate_completeness == "ABSENT_BY_CONSTRUCTION"
    assert record.subject_completeness == record.predicate_completeness


def test_an_unsupplied_governor_is_ignorance_and_never_evidence_of_absence():
    """Top-right cell.  The SAME body, with no structural context at all.
    GOVERNING_CLAUSE_NOT_SUPPLIED means the caller said nothing, which is not
    evidence that nothing is there.  Both axes fall to UNRESOLVED."""
    record = _bind("lri-r1-unwired", _STRUCTURAL_NEGATIVE_BODY)
    assert record.subject_state == "NO_SEMANTIC_SUBJECT"
    assert record.predicate_state == "NO_SEMANTIC_PREDICATE"
    assert record.subject_completeness == "UNRESOLVED"
    assert record.predicate_completeness == "UNRESOLVED"


def test_the_M14_route_to_NO_SEMANTIC_SUBJECT_is_not_evidence_of_absence():
    """Bottom-left cell, and the control that isolates PROVENANCE from WIRING.

    This body is bound WITH a structural context whose verdict is the positive
    GOVERNING_CLAUSE_ABSENT -- the same verdict that licenses absence in the
    top-left cell.  It still reads UNRESOLVED, because its NO_SEMANTIC_SUBJECT
    comes from M14 removing a bare-preposition constituent, which says only that
    the constituent it was handed is not a subject.  Without this cell the flag
    could be a proxy for "the caller supplied no context", and the repair would
    be measuring the harness rather than the evidence.
    """
    record = _bind("lri-r1-m14-wired", _M14_WITNESS_BODY,
                   _absent_governor_context())
    assert record.subject_state == "NO_SEMANTIC_SUBJECT"
    assert record.subject_completeness == "UNRESOLVED"


def test_the_P4_publication_witness_stays_quarantined():
    """Bottom-right cell, and the entanglement guarantee.

    "in shall the policy" published as ROLE_BINDING_ESTABLISHED / EVIDENCE_BOUND
    on both B410 and pre-P4.  P4 closed that, and LRI-R1 must not reopen it.
    Checked with and without structural context, because the repair introduced a
    provenance flag and a flag that leaked would show up here first.
    """
    for label, context in (("unwired", None),
                           ("wired", _absent_governor_context())):
        for body in (_M14_WITNESS_BODY, _M14_WITNESS_BODY + "."):
            record = _bind(f"lri-r1-witness-{label}", body, context)
            assert record.subject_completeness == "UNRESOLVED", (label, body)
            assert record.role_binding_state != "ROLE_BINDING_ESTABLISHED"
            assert record.final_extraction_disposition == "QUARANTINED"


def test_the_expletive_positive_control_still_publishes():
    """The repair must not over-block.  A genuine subjectless construction is a
    positive typing, not an absence, and EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION
    keeps its unconditional ABSENT_BY_CONSTRUCTION mapping."""
    for label, context in (("unwired", None),
                           ("wired", _absent_governor_context())):
        record = _bind(f"lri-r1-expletive-{label}",
                       "It is necessary to review the policy.", context)
        assert record.subject_state == "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION"
        assert record.subject_completeness == "ABSENT_BY_CONSTRUCTION"
        assert record.role_binding_state == "ROLE_BINDING_ESTABLISHED"
        assert record.final_extraction_disposition == "EVIDENCE_BOUND"


def test_the_non_proposition_control_stays_rejected_either_way():
    """Whichever way the absence axis reads, furniture is still refused."""
    for label, context in (("unwired", None),
                           ("wired", _absent_governor_context())):
        record = _bind(f"lri-r1-nonprop-{label}", _STRUCTURAL_NEGATIVE_BODY,
                       context)
        assert record.internal_state == "INVALID_BINDING_CANDIDATE"
        assert record.role_binding_state == "ROLE_BINDING_INVALID"
        assert record.final_extraction_disposition == "REJECTED"


@pytest.mark.parametrize("evidential", [True, False])
def test_the_adapter_answers_the_two_axes_identically_from_one_flag(evidential):
    """The symmetry, checked at the adapter with everything else held equal.

    Before LRI-R1 this call returned UNRESOLVED for the subject and
    ABSENT_BY_CONSTRUCTION for the predicate from one set of inputs.  Both
    parametrisations run, so neither answer is a constant.
    """
    facts = V2._terminal_facts(
        internal_state="INVALID_BINDING_CANDIDATE",
        subject_state="NO_SEMANTIC_SUBJECT", subject_span=None,
        predicate_state="NO_SEMANTIC_PREDICATE", predicate_span=None,
        antecedent_state="NO_ANAPHOR_PRESENT",
        required_context_ids=(), repair_requirement=None,
        structural_context_supplied=evidential,
        governing_state=("GOVERNING_CLAUSE_ABSENT" if evidential
                         else "GOVERNING_CLAUSE_NOT_SUPPLIED"),
        subject_absence_is_evidential=evidential,
        predicate_absence_is_evidential=evidential,
    )
    expected = "ABSENT_BY_CONSTRUCTION" if evidential else "UNRESOLVED"
    assert facts.subject_completeness == expected
    assert facts.predicate_completeness == expected
    assert facts.subject_completeness == facts.predicate_completeness


def test_the_flags_default_closed():
    """A caller that does not report a provenance gets the weaker claim.

    ABSENT_BY_CONSTRUCTION asserts something positive about the document; it may
    never be reachable by omission.
    """
    facts = V2._terminal_facts(
        internal_state="INVALID_BINDING_CANDIDATE",
        subject_state="NO_SEMANTIC_SUBJECT", subject_span=None,
        predicate_state="NO_SEMANTIC_PREDICATE", predicate_span=None,
        antecedent_state="NO_ANAPHOR_PRESENT",
        required_context_ids=(), repair_requirement=None,
        structural_context_supplied=True,
        governing_state="GOVERNING_CLAUSE_ABSENT",
    )
    assert facts.subject_completeness == "UNRESOLVED"
    assert facts.predicate_completeness == "UNRESOLVED"


# --- LRI-D1: the invented context id --------------------------------------

_COORDINATE_ROUTES = (
    ("INHERITED_COORDINATE_SUBJECT", "EXPLICIT_FINITE_PREDICATE"),
    ("EXPLICIT_SUBJECT", "SHARED_COORDINATE_PREDICATE"),
)


@pytest.mark.parametrize(("subject_state", "predicate_state"),
                         _COORDINATE_ROUTES)
def test_the_coordinate_routes_emit_a_declared_context_id(subject_state,
                                                          predicate_state):
    """LRI-D1.  Production never supplies a shared source, so this fallback is
    the only value these routes ever emitted, and it was COORDINATE_CONTEXT --
    a token in no declared vocabulary.  It is the sole cause of the GATE-5
    full-cohort safety audit's unknown_disposition_names_consumed = 1.
    """
    required, repair = V2.derive_obligations(
        subject_state=subject_state, predicate_state=predicate_state,
        shared_subject_source=None, shared_predicate_source=None,
    )
    assert required, "zero-match precondition: the route emitted no obligation"
    assert "COORDINATE_CONTEXT" not in required
    assert required == ("LEFT_CONTEXT",)
    assert repair is not None


def test_no_module_supplied_context_id_is_outside_the_frozen_vocabulary():
    """Exhaustive over the states this module can hand the adapter, with the
    caller supplying nothing -- which is exactly what production does.

    The zero-match precondition is explicit: a run that produced no context ids
    at all would satisfy a membership test vacuously, so the count is asserted.
    """
    subject_states = (
        "EXPLICIT_SUBJECT", "ZERO_COPULA_NOMINAL_SUBJECT", "POSTVERBAL_SUBJECT",
        "IMPLICIT_CONTEXT_BOUND_SUBJECT", "ANAPHORIC_SUBJECT_RECOVERABLE",
        "INHERITED_COORDINATE_SUBJECT", "GOVERNING_CLAUSE_SUBJECT",
        "PASSIVE_PATIENT_SUBJECT", "INSTITUTIONAL_ISSUER_SUBJECT",
        "EXPLETIVE_OR_IMPERSONAL_CONSTRUCTION", "SUBJECT_UNRESOLVED",
        "NO_SEMANTIC_SUBJECT",
    )
    predicate_states = (
        "EXPLICIT_FINITE_PREDICATE", "NOMINAL_PREDICATE",
        "PARTICIPIAL_PREDICATE", "DEONTIC_OPERATOR_WITH_COMPLEMENT",
        "PASSIVE_OR_IMPERSONAL_PREDICATE", "SHARED_COORDINATE_PREDICATE",
        "GOVERNING_CLAUSE_PREDICATE", "RECITAL_RELATION_PREDICATE",
        "PREDICATE_RECOVERABLE", "PREDICATE_UNRESOLVED",
        "NO_SEMANTIC_PREDICATE",
    )
    emitted = set()
    for subject_state in subject_states:
        for predicate_state in predicate_states:
            for inherited in (False, True):
                required, _repair = V2.derive_obligations(
                    subject_state=subject_state,
                    predicate_state=predicate_state,
                    predicate_frame_inherited=inherited,
                )
                emitted.update(required)
    assert emitted, "zero-match precondition: no obligations were produced"
    assert emitted <= set(FROZEN_CONTEXT_IDS), sorted(
        emitted - set(FROZEN_CONTEXT_IDS))


def test_a_caller_supplied_shared_source_is_still_carried():
    """Negative control for LRI-D1: the repair changed the FALLBACK, and did not
    hard-code the answer.  A named lineage still reaches the obligation."""
    required, _repair = V2.derive_obligations(
        subject_state="INHERITED_COORDINATE_SUBJECT",
        predicate_state="EXPLICIT_FINITE_PREDICATE",
        shared_subject_source="HEADING",
    )
    assert required == ("HEADING",)
