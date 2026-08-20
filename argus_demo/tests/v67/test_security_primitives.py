from __future__ import annotations

import math

import pytest

from curunir_operational.access import (
    AccessContext,
    Marking,
    UNJOINABLE_SEAL_COMPARTMENT,
    can_view,
    inherited_marking,
    marking_from_record,
)
from curunir_operational.canonical import canonical_line, parse_json_strict
from curunir_operational.security import MaterialReference, material_references

pytestmark = pytest.mark.no_db


PUBLIC = Marking("ORG", releasability=("PUBLIC",))
SPECIAL = Marking("ORG", compartments=("SPECIAL",), releasability=("PUBLIC",))
PUBLIC_CONTEXT = AccessContext(
    "ctx-public", "reader", "HUMAN", ("ANALYST",),
    releasability=("PUBLIC",), organisation="ORG",
)


def test_canonical_boundary_refuses_poison_values_and_duplicate_keys():
    for value in (math.nan, math.inf, -math.inf, "\ud800", {1: "not-json"},
                  {"bad": b"bytes"}, {"bad": {1, 2}}):
        with pytest.raises(ValueError):
            canonical_line(value)
    with pytest.raises(ValueError, match="duplicate"):
        parse_json_strict('{"score": 1, "score": 2}')
    with pytest.raises(ValueError, match="non-finite"):
        parse_json_strict('{"score": NaN}')


def test_inherited_marking_never_writes_down_and_unjoinable_is_deny_all():
    raised = inherited_marking(PUBLIC, [SPECIAL])
    assert "SPECIAL" in raised.compartments
    assert not can_view(raised, PUBLIC_CONTEXT)

    partner_only = Marking("PARTNER", releasability=("REL_PARTNER",))
    sealed = inherited_marking(PUBLIC, [partner_only])
    assert UNJOINABLE_SEAL_COMPARTMENT in sealed.compartments
    assert not can_view(sealed, PUBLIC_CONTEXT)
    with pytest.raises(ValueError, match="reserved deny-all"):
        AccessContext("forged", "reader", "HUMAN", ("SUPERVISOR",),
                      compartments=(UNJOINABLE_SEAL_COMPARTMENT,),
                      organisation="ORG")


def test_missing_marking_role_reconstructs_to_least_privilege():
    reconstructed = marking_from_record({"owning_authority": "ORG"})
    assert reconstructed.min_role == "SUPERVISOR"
    assert not can_view(reconstructed, PUBLIC_CONTEXT)


def test_material_reference_registry_walks_nested_and_typed_fields():
    record = {
        "record_type": "analytic_forecast",
        "forecast_id": "fc-1",
        "proposition_refs": [["claim", "cl-1"], ["hypothesis", "hyp-1"]],
        "assumption_ids": ["asm-1"],
        "indicator_ids": ["ind-association-only"],
        "nested": {"positions": [{"relationship_ids": ["rel-1"]}]},
    }
    refs = set(material_references(record))
    assert MaterialReference("semantic_claim", "cl-1") in refs
    assert MaterialReference("hypothesis", "hyp-1") in refs
    assert MaterialReference("analytic_assumption", "asm-1") in refs
    assert MaterialReference("relationship_version", "rel-1") in refs
    assert MaterialReference("forecast_indicator", "ind-association-only") not in refs
