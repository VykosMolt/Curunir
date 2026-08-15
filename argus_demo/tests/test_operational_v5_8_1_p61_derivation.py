"""P6.1 R1 — UNDECIDED_ROLE is DERIVED, not merely declared.

Installed verbatim from the campaign-413 adjudicator scratch test
(``07_P6_REFUSAL_SITE_VALIDATION/p6_adjudicator_DERIV2/
test_deriv2_q9_derivation.py``), with exactly two adaptations:

* ``pytestmark = pytest.mark.no_db`` is added.  Without it the autouse
  ``clean_db`` fixture skips this file whenever Postgres is unreachable, so the
  protection would look installed and provide nothing.  ``no_db`` is the
  convention every other ``test_operational_v5_8_1_*`` file already follows.
* this module docstring records the provenance.

No assertion, literal, tolerance or import was changed.

Derives the UNDECIDED_ROLE membership from production semantics alone:

  residue      = (SUBJECT_STATES u PREDICATE_STATES)
                 - (SUBJECT_BOUND u SUBJECT_RECOVERABLE
                    u PREDICATE_BOUND u PREDICATE_RECOVERABLE_STATES)
  never_pub    = role states that cannot appear in any EVIDENCE_BOUND record
                 over the full _terminal_facts input space, end-to-end through
                 terminal.derive_terminal and terminal.derive_disposition
  derived      = residue INTERSECT never_pub

and asserts derived == roles_v2.UNDECIDED_ROLE. Reads no test literal.
"""
import itertools

import pytest

from curunir_operational.v5_8_1 import roles as R
from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import terminal as T

pytestmark = pytest.mark.no_db

_ANTECEDENTS = ("NO_ANAPHOR_PRESENT", "ANTECEDENT_ABSENT",
                "ANTECEDENT_RECOVERABLE")
_SPANS = (None, (0, 5))
_CONTEXT_IDS = ((), ("CTX_A",), ("SOURCE_PUBLISHER",),
                ("CTX_A", "SOURCE_PUBLISHER"))
_GOVERNING = ("GOVERNING_CLAUSE_NOT_SUPPLIED", "GOVERNING_CLAUSE_UNIQUE",
              "GOVERNING_CLAUSE_AMBIGUOUS", "GOVERNING_CLAUSE_ABSENT")
_REPAIRS = (None, "NAMED_REPAIR")
_BOOLS = (False, True)


def _publishable_states():
    subj_pub = {s: False for s in R.SUBJECT_STATES}
    pred_pub = {p: False for p in R.PREDICATE_STATES}
    for (subject_state, predicate_state, internal_state, antecedent_state,
         s_span, p_span, ctx_ids, governing_state, structural, repair,
         frame_inh) in itertools.product(
            R.SUBJECT_STATES, R.PREDICATE_STATES, V2.INTERNAL_STATES,
            _ANTECEDENTS, _SPANS, _SPANS, _CONTEXT_IDS, _GOVERNING, _BOOLS,
            _REPAIRS, _BOOLS):
        facts = V2._terminal_facts(
            internal_state=internal_state,
            subject_state=subject_state,
            subject_span=s_span,
            predicate_state=predicate_state,
            predicate_span=p_span,
            antecedent_state=antecedent_state,
            required_context_ids=ctx_ids,
            repair_requirement=repair,
            structural_context_supplied=structural,
            governing_state=governing_state,
            predicate_frame_inherited=frame_inh,
        )
        terminal = T.derive_terminal(facts)
        disposition = T.derive_disposition(facts, terminal.value)
        if disposition.value == "EVIDENCE_BOUND":
            subj_pub[subject_state] = True
            pred_pub[predicate_state] = True
    return subj_pub, pred_pub


def test_undecided_role_membership_is_derivable_from_terminal_semantics():
    subj_pub, pred_pub = _publishable_states()
    never_pub = ({s for s, ok in subj_pub.items() if not ok}
                 | {p for p, ok in pred_pub.items() if not ok})
    residue = (set(R.SUBJECT_STATES) | set(R.PREDICATE_STATES)) - (
        R.SUBJECT_BOUND | R.SUBJECT_RECOVERABLE
        | R.PREDICATE_BOUND | R.PREDICATE_RECOVERABLE_STATES)
    derived = residue & never_pub
    assert derived == V2.UNDECIDED_ROLE, (
        "derived from terminal semantics: %s != shipped: %s"
        % (sorted(derived), sorted(V2.UNDECIDED_ROLE)))
