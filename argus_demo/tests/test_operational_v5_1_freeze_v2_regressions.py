"""Regressions for the two freeze-v2 infrastructure repairs.

Both defects surfaced as hard failures during Campaign C/D/E execution,
BEFORE any held-out corpus or label existed; the fixes are mechanism-level
and content-agnostic.
"""
from __future__ import annotations

import pytest

from curunir_operational.v5_1.extraction import parse_semantics
from curunir_operational.v4.reporting import _epistemic_overreach

pytestmark = pytest.mark.no_db


def test_non_chronological_year_range_never_inverts():
    parse = parse_semantics(
        "The obligations align this Regulation with rules adopted from 2013 "
        "to 2008 across the financial services acts.", "", "en")
    start, end = parse.temporal_scope
    assert start is not None and end is not None
    assert start <= end
    assert (start, end) == ("2008-01-01", "2013-12-31")


def test_chronological_range_unchanged():
    parse = parse_semantics(
        "The programme ran from 2008 to 2013 across the participating "
        "authorities.", "", "en")
    assert parse.temporal_scope == ("2008-01-01", "2013-12-31")


def test_overreach_screen_matches_v4_rule():
    assert _epistemic_overreach(
        "The system is fully operational across the network.", "EXTRACTED")
    assert not _epistemic_overreach(
        "The system is fully operational across the network.", "SUPPORTED")
