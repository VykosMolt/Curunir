"""V6.7 information-flow property lock (adversarial-review NEW-1; brief §124).

The single invariant every derived marking must satisfy, across ALL axes
(compartment, role, releasability, organisation) — not just the compartment
axis the fixtures exercise:

    for every context C:  can_view(inherited_marking(base, refs), C)
                          =>  can_view(base, C) AND can_view(r, C) for each r

i.e. anyone who can view a derived record can view every input it was derived
from. A satisfiable seal on un-joinable inputs violated this on the
releasability/authority axis (a base-org supervisor without the partner release
could view a record derived from partner-releasable state); the fix seals such
inputs to a deny-all marking. This property test is deliberately broad over
coalition / multi-authority markings, the case the shipped fixtures do not
cover.
"""
from __future__ import annotations

import itertools

import pytest

from curunir_operational.access import (AccessContext, Marking, can_view,
                                         inherited_marking, most_restrictive)

pytestmark = pytest.mark.no_db

# a spread of markings across every axis, incl. cross-authority and orthogonal
# releasability channels (the un-joinable cases)
MARKINGS = [
    Marking("MISSION", releasability=("PUBLIC",)),
    Marking("MISSION", compartments=("SPECIAL",), releasability=("PUBLIC",)),
    Marking("MISSION", compartments=("SPECIAL", "GAMMA"), releasability=("PUBLIC",)),
    Marking("MISSION", releasability=("REL_A",)),
    Marking("MISSION", releasability=("REL_B",)),
    Marking("MISSION", releasability=("REL_A", "REL_B")),
    Marking("MISSION", releasability=()),                       # org-locked
    Marking("MISSION", releasability=("PUBLIC",), min_role="SUPERVISOR"),
    Marking("PARTNER", releasability=("REL_A",)),               # other authority
    Marking("PARTNER", releasability=()),                       # other org-locked
]

# a spread of contexts across every axis (orgs, roles, compartments, releases)
CONTEXTS = [
    AccessContext("c1", "u", "HUMAN", ("OBSERVER",), organisation="MISSION"),
    AccessContext("c2", "u", "HUMAN", ("ANALYST",), organisation="MISSION"),
    AccessContext("c3", "u", "HUMAN", ("SUPERVISOR",), organisation="MISSION"),
    AccessContext("c4", "u", "HUMAN", ("SUPERVISOR",), compartments=("SPECIAL", "GAMMA"),
                  organisation="MISSION"),
    AccessContext("c5", "u", "HUMAN", ("ANALYST",), releasability=("PUBLIC",),
                  organisation="MISSION"),
    AccessContext("c6", "u", "HUMAN", ("ANALYST",), releasability=("REL_A",),
                  organisation="OTHER"),
    AccessContext("c7", "u", "HUMAN", ("ANALYST",), releasability=("REL_A", "REL_B"),
                  organisation="MISSION"),
    AccessContext("c8", "u", "HUMAN", ("SUPERVISOR",), compartments=("SPECIAL",),
                  releasability=("PUBLIC", "REL_A"), organisation="MISSION"),
    AccessContext("c9", "u", "HUMAN", ("SUPERVISOR",), releasability=("PUBLIC",),
                  organisation="PARTNER"),
]


def _check_no_writedown(base: Marking, refs: list[Marking]) -> None:
    derived = inherited_marking(base, refs)
    for ctx in CONTEXTS:
        if can_view(derived, ctx):
            assert can_view(base, ctx), (
                f"write-down: {ctx.context_id} can view the derived record but "
                f"not its base {base.to_record()}")
            for ref in refs:
                assert can_view(ref, ctx), (
                    f"write-down: {ctx.context_id} can view the derived record "
                    f"but not its input {ref.to_record()}")


@pytest.mark.parametrize("base,ref", list(itertools.product(MARKINGS, MARKINGS)))
def test_pairwise_derivation_never_writes_down(base, ref):
    _check_no_writedown(base, [ref])


@pytest.mark.parametrize("base", MARKINGS)
def test_triple_derivation_never_writes_down(base):
    # a derivation from several inputs at once (a forecast basis, a theme's
    # member claims) must still never write down
    _check_no_writedown(base, [MARKINGS[3], MARKINGS[8], MARKINGS[1]])


def test_joinable_same_axis_is_exact_not_sealed():
    # same authority, compatible axes must join exactly (never spuriously seal)
    a = Marking("MISSION", compartments=("SPECIAL",), releasability=("PUBLIC",))
    b = Marking("MISSION", compartments=("GAMMA",), releasability=("PUBLIC",))
    joined = inherited_marking(a, [b])
    assert joined.to_record() == most_restrictive([a, b]).to_record()
    assert set(joined.compartments) == {"SPECIAL", "GAMMA"}
    assert "curunir:unjoinable-seal" not in joined.compartments


def test_unjoinable_seals_to_deny_all():
    # orthogonal releasability / cross-authority cannot be expressed → deny-all
    base = Marking("MISSION", releasability=("PUBLIC",))
    ref = Marking("PARTNER", releasability=("REL_A",))
    sealed = inherited_marking(base, [ref])
    assert "curunir:unjoinable-seal" in sealed.compartments
    assert not any(can_view(sealed, ctx) for ctx in CONTEXTS), \
        "a deny-all seal is viewable by no one"
