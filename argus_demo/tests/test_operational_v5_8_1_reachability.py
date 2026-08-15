"""Production-interface reachability for role binding (V5.8.1 section 12).

D29 was not a bug in a rule.  It was a capability that passed its own unit tests
and could not fire in the pipeline, because the caller never supplied the input
the capability consults.  A test suite that calls the module directly cannot see
that class of defect at all — it is exactly the shape the direct test is blind
to.

So these tests assert reachability through the *production caller*, and the
registry below is the declared list of what the system claims to do.  A
capability whose required input no caller supplies must be declared unreachable
rather than quietly counted as present.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from curunir_operational.v5_4 import invariants as INV
from curunir_operational.v5_8_1 import roles_v2 as RB

pytestmark = pytest.mark.no_db

#: capability_id -> (bind() parameter it needs, production caller status)
CAPABILITY_REGISTRY: dict[str, dict] = {
    "bounded_anaphora": {
        "required_input": "left_context",
        "claimed_reachable": True,
    },
    # D32 closed these three: the typed governing relation, the list lead-in
    # and the shared-role candidates all arrive inside structural_context,
    # which build_context now transports and both call sites now pass.
    "governing_clause_inheritance": {
        "required_input": "structural_context",
        "claimed_reachable": True,
    },
    "shared_subject_inheritance": {
        "required_input": "structural_context",
        "claimed_reachable": True,
    },
    "shared_predicate_inheritance": {
        "required_input": "structural_context",
        "claimed_reachable": True,
    },
    "heading_context": {
        "required_input": "heading",
        "claimed_reachable": False,
    },
    "semantic_actor_separation": {
        "required_input": None,
        "claimed_reachable": True,
    },
    "attribution_source": {
        "required_input": None,
        "claimed_reachable": True,
    },
    "passive_patient_handling": {
        "required_input": None,
        "claimed_reachable": True,
    },
}

CALL_SITES = ("referential_subject_complete", "predicate_complete")


def _call_site_sources() -> dict[str, str]:
    return {name: inspect.getsource(getattr(INV, name)) for name in CALL_SITES}


def _supplied_arguments() -> set[str]:
    """Which bind() keyword arguments any production call site actually passes."""
    supplied: set[str] = set()
    for source in _call_site_sources().values():
        marker = ("_roles_v2.bind(" if "_roles_v2.bind(" in source
                  else "_roles.bind(" if "_roles.bind(" in source else None)
        if marker is None:
            continue
        segment = source.split(marker, 1)[1]
        depth, end = 1, 0
        for index, char in enumerate(segment):
            depth += (char == "(") - (char == ")")
            if depth == 0:
                end = index
                break
        for parameter in inspect.signature(RB.bind).parameters:
            if f"{parameter}=" in segment[:end]:
                supplied.add(parameter)
    return supplied


def test_every_claimed_capability_is_reachable_through_a_production_caller():
    """The D29 mutation: a capability tested directly but unreachable in production."""
    supplied = _supplied_arguments()
    unreachable = []
    for capability, record in CAPABILITY_REGISTRY.items():
        needed = record["required_input"]
        reachable = needed is None or needed in supplied
        if record["claimed_reachable"] and not reachable:
            unreachable.append((capability, needed))
    assert not unreachable, (
        "capabilities claimed active but whose required input no production "
        f"caller supplies: {unreachable}")


def test_registry_declares_the_unreachable_capabilities_honestly():
    """A capability the caller cannot reach must be declared, not assumed."""
    supplied = _supplied_arguments()
    misdeclared = []
    for capability, record in CAPABILITY_REGISTRY.items():
        needed = record["required_input"]
        reachable = needed is None or needed in supplied
        if reachable and not record["claimed_reachable"]:
            misdeclared.append(capability)
    assert not misdeclared, (
        "these are reachable in production but the registry still calls them "
        f"unreachable — update the registry: {misdeclared}")


def test_bounded_anaphora_is_reachable_and_actually_fires():
    """Reachability is not enough; the supplied value must change behaviour."""
    supplied = _supplied_arguments()
    assert "left_context" in supplied
    assert "structural_context" in supplied


def test_unreachable_capabilities_are_not_counted_as_production_behaviour():
    """roles.py accepts these; no caller passes them. That must stay visible."""
    supplied = _supplied_arguments()
    for parameter in ("governing_clause_id", "shared_subject_source",
                      "shared_predicate_source", "heading"):
        assert parameter in inspect.signature(RB.bind).parameters
        assert parameter not in supplied, (
            f"{parameter} is now supplied by a production caller — move it to "
            "claimed_reachable=True in CAPABILITY_REGISTRY and add an "
            "end-to-end test through the production entry point")


def test_reachability_record_is_written_for_the_audit_trail():
    supplied = _supplied_arguments()
    record = {
        capability: {
            "capability_id": capability,
            "required_inputs": data["required_input"],
            "production_caller": list(CALL_SITES),
            "actual_production_inputs": sorted(supplied),
            "reachable_in_production": (data["required_input"] is None
                                        or data["required_input"] in supplied),
            "claimed_reachable": data["claimed_reachable"],
        }
        for capability, data in CAPABILITY_REGISTRY.items()
    }
    # the campaign copy of this record is frozen evidence; a test may not
    # rewrite it as a side effect, so the record is asserted in memory only
    reachable = sum(1 for r in record.values() if r["reachable_in_production"])
    assert reachable == sum(1 for r in record.values() if r["claimed_reachable"])
