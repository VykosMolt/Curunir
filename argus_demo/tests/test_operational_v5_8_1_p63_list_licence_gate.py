"""P6.3 workstream B2 — what the L1 list-licence gate actually admits.

WHY THIS FILE EXISTS
--------------------
`roles_v2`'s comment at the governed-list gate used to say "a typing FAILURE is
not a licence refusal".  The gate is set MEMBERSHIP --
`content_region_type in _LIST_LICENSE_REGION_TYPES` -- and that frozenset holds
exactly two names.  So the claim held for ONE spelling of a typing failure, the
declared vocabulary's own `UNKNOWN_STRUCTURAL_REGION`, and was false for every
other way a caller can fail to type a region.  An independent seat measured the
consequence and found that the acceptance record's NARROW-1 triage rested on
that false ground.

The comment has been corrected to say what the gate does, and it now carries
MEASURED FIGURES.  A figure stated in a comment and checked by nothing is the
same staleness class the fullpop prose defect is about, so the figures are
pinned here.

WHAT IS AND IS NOT CLAIMED
--------------------------
The gate is NOT repaired.  It is unreachable from production -- `v5_4/
invariants.py` never passes `content_region_type`, so both bind call sites take
`build_lattice`'s own default, which IS in the licence set -- and widening it
would move published role states and needs its own authorisation.  This file
pins the CURRENT behaviour and the CURRENT reachability ground, so that
changing either is a visible, reviewed change.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from curunir_operational.v5_8_1 import roles_v2 as V2
from curunir_operational.v5_8_1 import structure as ST

pytestmark = pytest.mark.no_db


#: LITERAL.  The two names the gate admits.
_EXPECTED_LICENCE_SET = frozenset({
    "PRIMARY_PROPOSITION_CONTENT",
    "UNKNOWN_STRUCTURAL_REGION",
})

#: LITERAL.  Every one of these is a typing FAILURE in the ordinary sense and
#: none is in the licence set.  If the gate ever became a predicate over "was
#: the typing successful" rather than an enumeration, these would be admitted
#: and the divergence arm below would fail -- loudly, which is the point.
_OUT_OF_VOCABULARY = (
    "SIDEBAR_PROMOTION", "unknown_structural_region", "UNKNOWN", "",
    " ", "UNKNOWN_STRUCTURAL_REGION ", " UNKNOWN_STRUCTURAL_REGION",
    "UNTYPED", "TYPING_FAILED", "None", "null",
)

#: Bodies the governed-list generator can actually realise.  Ordinary Arabic
#: prose never reaches `_arabic_governed_list_action`, so it cannot show the
#: gate at all -- a first measurement over such prose reported zero divergence
#: and was wrong for that reason.
_GOVERNED_LIST_BODIES = {
    "istifal": "استعراض التقارير الواردة من الدول الأعضاء في هذا الشأن.",
    "istifal_waw": "واستعراض التقارير الواردة من الدول الأعضاء.",
    "masdar_waw": "وشرب كمياتٍ كافية من الماء النظيف يومياً.",
}

_FIELDS = tuple(field.name for field in
                dataclasses.fields(V2.RoleBindingV2Record)
                if field.name != "recorded_time")


def _list_context():
    base = ST.empty_context(document_id="p63b2-doc", region_id="p63b2-region",
                            reading_order_state="READING_ORDER_ESTABLISHED")
    return dataclasses.replace(
        base, list_lead_in_ids=("p63b2-lead",),
        governing_clause_candidates=("p63b2-gov",),
        governing_clause_relation_types=("LIST_ITEM_OF",),
        governing_clause_confidences=(0.9,))


def _published(body: str, region: str) -> dict:
    record = V2.bind(candidate_id="p63b2", text=body, language="ar",
                     content_region_type=region,
                     structural_context=_list_context())
    return {name: getattr(record, name) for name in _FIELDS}


def test_the_licence_set_is_an_enumeration_of_exactly_two_names():
    """The gate's shape, pinned.  Growing or shrinking this set changes which
    callers can license governed-list generation and must be reviewed."""
    assert set(V2._LIST_LICENSE_REGION_TYPES) == set(_EXPECTED_LICENCE_SET)
    assert len(V2._LIST_LICENSE_REGION_TYPES) == 2


@pytest.mark.parametrize("body_name", sorted(_GOVERNED_LIST_BODIES))
def test_in_licence_region_types_agree_with_each_other(body_name):
    """The two admitted names publish the SAME record, so the divergence
    measured below is attributable to the gate and not to the region type
    having some other effect."""
    body = _GOVERNED_LIST_BODIES[body_name]
    reference = _published(body, "UNKNOWN_STRUCTURAL_REGION")
    observed = _published(body, "PRIMARY_PROPOSITION_CONTENT")
    assert observed == reference, {
        k: (reference[k], observed[k]) for k in reference
        if reference[k] != observed[k]}


@pytest.mark.parametrize("body_name", sorted(_GOVERNED_LIST_BODIES))
def test_an_out_of_vocabulary_typing_failure_is_refused_like_furniture(
        body_name):
    """The corrected comment's central claim, checked.

    Every string here denotes a region the caller could not type, and every
    one of them is REFUSED the licence -- which is why the old comment's "a
    typing FAILURE is not a licence refusal" was false as a general claim.
    """
    body = _GOVERNED_LIST_BODIES[body_name]
    reference = _published(body, "UNKNOWN_STRUCTURAL_REGION")
    for region in _OUT_OF_VOCABULARY:
        assert region not in V2._LIST_LICENSE_REGION_TYPES
        observed = _published(body, region)
        differing = [k for k in reference if reference[k] != observed[k]]
        assert differing, (
            body_name, region,
            "an out-of-vocabulary region type was ADMITTED; the gate is no "
            "longer an enumeration and the comment at "
            "_LIST_LICENSE_REGION_TYPES is now wrong")
        # the movement is real and reaches the published role states, which is
        # what makes the corrected comment's severity claim true.
        assert "internal_state" in differing, (body_name, region, differing)


def test_the_divergence_reaches_the_published_role_states():
    """The comment states that up to 21 published fields move.  This pins the
    ORDER of that claim rather than one exact number, because the exact count
    is a property of the probe bodies and would make the comment fragile."""
    worst = 0
    for body in _GOVERNED_LIST_BODIES.values():
        reference = _published(body, "UNKNOWN_STRUCTURAL_REGION")
        for region in _OUT_OF_VOCABULARY:
            observed = _published(body, region)
            worst = max(worst, sum(1 for k in reference
                                   if reference[k] != observed[k]))
    assert worst >= 15, worst
    assert worst <= len(_FIELDS)


def test_production_still_never_supplies_the_parameter():
    """The REACHABILITY ground the register-bounded triage now rests on.

    NARROW-1's recorded ground was that the capability is correct on
    out-of-vocabulary input; the arms above show it is not.  The ground that
    does hold is that production never supplies the parameter at all.  If a
    caller ever starts supplying it, this fails and the triage must be redone.
    """
    invariants = (pathlib.Path(V2.__file__).resolve().parents[1]
                  / "v5_4" / "invariants.py").read_text(encoding="utf-8")
    assert "_roles_v2.bind(" in invariants, "the call sites moved; re-check"
    assert "content_region_type" not in invariants, (
        "v5_4/invariants.py now mentions content_region_type: the L1 gate may "
        "have become reachable from production and its register-bounded "
        "triage must be redone")
