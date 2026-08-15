"""D37 — a precision estimate must carry the evidence for believing it.

Four sessions of this programme reported accepted precision of 0.310, 0.667 and
0.500 over accepted populations of three, three and two units of 120, and
compared them as though the movement were a result.  The derived minimum is 82.
These tests make that class of claim impossible to make again.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_8_1 import sampling as SP

pytestmark = pytest.mark.no_db


def test_the_minimum_is_derived_not_chosen():
    """It comes from the same half-width rule the surface plans use."""
    assert SP.minimum_adjudicable_accepted() == SP.units_required_for_precision(
        expected_rate=0.95, target_half_width=SP.MAX_INTERVAL_HALF_WIDTH)
    assert SP.minimum_adjudicable_accepted() > 50


@pytest.mark.parametrize("accepted,correct", [(2, 1), (3, 2), (3, 1), (10, 9)])
def test_small_accepted_populations_are_not_adjudicable(accepted, correct):
    result = SP.precision_adjudicability(accepted_n=accepted,
                                         correct_accepted_n=correct,
                                         effective_families=3)
    assert result["adjudicability"] == "NOT_ADJUDICABLE_INSUFFICIENT_ACCEPTED_N"
    assert result["may_contribute_to_pass"] is False


def test_the_historical_figures_could_never_have_passed_or_failed():
    """0.667 on 2/3 and 0.500 on 1/2 are arithmetic, not measurements."""
    for accepted, correct in ((3, 2), (2, 1)):
        result = SP.precision_adjudicability(accepted_n=accepted,
                                             correct_accepted_n=correct,
                                             effective_families=1)
        assert not result["may_contribute_to_pass"]
        assert result["interval_half_width"] > 0.3


def test_an_adequately_powered_estimate_is_adjudicable():
    """Neighbouring positive: the rule must not refuse everything."""
    result = SP.precision_adjudicability(accepted_n=200, correct_accepted_n=195,
                                         effective_families=8)
    assert result["adjudicability"] == "ADJUDICABLE"
    assert result["may_contribute_to_pass"] is True


def test_one_source_family_is_not_effective_evidence():
    result = SP.precision_adjudicability(accepted_n=200, correct_accepted_n=195,
                                         effective_families=1)
    assert result["adjudicability"] == "NOT_ADJUDICABLE_INSUFFICIENT_EFFECTIVE_N"


def test_a_wide_interval_is_refused_even_at_sufficient_n():
    """A 50/50 split at the minimum n still cannot decide a 0.95 gate."""
    minimum = SP.minimum_adjudicable_accepted()
    result = SP.precision_adjudicability(accepted_n=minimum,
                                         correct_accepted_n=minimum // 2,
                                         effective_families=5)
    assert result["adjudicability"] == "NOT_ADJUDICABLE_CONFIDENCE_TOO_WIDE"


def test_every_report_carries_its_denominator_and_interval():
    result = SP.precision_adjudicability(accepted_n=5, correct_accepted_n=5)
    for key in ("accepted_n", "correct_accepted_n", "confidence_interval",
                "interval_half_width", "confidence_method",
                "minimum_adjudicable_accepted_n", "adjudicability"):
        assert key in result


def test_perfect_precision_on_two_units_is_still_not_a_pass():
    """The mutation section 15 names first: 2/2 reported as PASS."""
    result = SP.precision_adjudicability(accepted_n=2, correct_accepted_n=2,
                                         effective_families=5)
    assert result["point_estimate"] == 1.0
    assert result["may_contribute_to_pass"] is False
