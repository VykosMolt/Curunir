"""The access decision itself: can_view and visible, called directly.

Every reason to refuse gets a refusing case and a matching allowing one, so
nothing here passes by refusing everything, and visible() must return exactly
the viewable records rather than all or none.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from curunir_operational.access import (
    AccessContext,
    Marking,
    can_view,
    context_from_record,
    marking_from_record,
    visible,
)


def _context(**overrides):
    base = dict(
        context_id="ctx-1",
        actor_id="actor-1",
        actor_kind="HUMAN",
        roles=("ANALYST",),
        compartments=("ALPHA",),
        releasability=("EU",),
        organisation="AUTH-A",
    )
    base.update(overrides)
    return AccessContext(**base)


def _marking(**overrides):
    base = dict(
        owning_authority="AUTH-A",
        compartments=("ALPHA",),
        releasability=("EU",),
        min_role="ANALYST",
    )
    base.update(overrides)
    return Marking(**base)


def test_a_satisfied_context_can_view():
    assert can_view(_marking(), _context()) is True


def test_role_rank_below_the_marking_minimum_denies():
    assert can_view(_marking(min_role="SUPERVISOR"), _context(roles=("ANALYST",))) is False
    assert can_view(_marking(min_role="SUPERVISOR"), _context(roles=("SUPERVISOR",))) is True


def test_every_marking_compartment_must_be_held():
    assert can_view(_marking(compartments=("ALPHA", "BRAVO")), _context(compartments=("ALPHA",))) is False
    assert can_view(_marking(compartments=("ALPHA", "BRAVO")), _context(compartments=("ALPHA", "BRAVO"))) is True


def test_releasability_requires_a_non_empty_intersection():
    assert can_view(_marking(releasability=("NATO",)), _context(releasability=("EU",))) is False
    assert can_view(_marking(releasability=("NATO", "EU")), _context(releasability=("EU",))) is True


def test_without_releasability_only_the_owning_authority_may_view():
    unreleasable = _marking(releasability=())
    assert can_view(unreleasable, _context(organisation="AUTH-A")) is True
    assert can_view(unreleasable, _context(organisation="AUTH-B")) is False


def test_absent_marking_denies():
    assert can_view(None, _context()) is False


def test_unresolvable_minimum_role_denies_rather_than_defaulting():
    # A raw record can carry a role the model does not know; it must deny.
    assert can_view({"owning_authority": "AUTH-A", "min_role": "ARCHONT"}, _context()) is False


def test_a_context_with_no_known_role_can_view_nothing():
    observer_only = _marking(min_role="OBSERVER")
    assert can_view(observer_only, _context(roles=())) is False


def test_visible_filters_exactly_the_viewable_records():
    records = [
        {"id": "r1", "marking": _marking()},
        {"id": "r2", "marking": _marking(min_role="SUPERVISOR")},
        {"id": "r3", "marking": _marking(compartments=("ALPHA", "BRAVO"))},
        {"id": "r4", "marking": None},
        {"id": "r5", "marking": _marking(releasability=(), owning_authority="AUTH-A")},
    ]
    shown = [r["id"] for r in visible(records, _context())]
    assert shown == ["r1", "r5"]


def test_record_round_trip_preserves_the_access_decision():
    marking = _marking(compartments=("BRAVO", "ALPHA"), releasability=("EU", "NATO"))
    context = _context(compartments=("ALPHA", "BRAVO"), releasability=("NATO",))
    assert can_view(marking, context) is True
    assert can_view(marking_from_record(marking.to_record()), context_from_record(context.to_record())) is True


def test_a_marking_without_an_owning_authority_is_rejected_at_construction():
    with pytest.raises(ValueError):
        Marking(owning_authority="")
