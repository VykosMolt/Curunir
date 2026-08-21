from __future__ import annotations

import itertools
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
from curunir_operational.security import (
    MaterialReference,
    material_references,
    reference_field_is_classified,
    resolve_reference_records,
)

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


def test_inherited_marking_no_write_down_property_across_every_axis():
    markings = [
        Marking("ORG", releasability=("PUBLIC",)),
        Marking("ORG", compartments=("SPECIAL",), releasability=("PUBLIC",)),
        Marking("ORG", releasability=("REL_A",)),
        Marking("ORG", releasability=("REL_B",)),
        Marking("ORG", releasability=()),
        Marking("ORG", releasability=("PUBLIC",), min_role="SUPERVISOR"),
        Marking("PARTNER", releasability=("REL_A",)),
        Marking("PARTNER", releasability=()),
    ]
    contexts = [
        AccessContext("observer", "u", "HUMAN", ("OBSERVER",),
                      organisation="ORG"),
        AccessContext("public", "u", "HUMAN", ("ANALYST",),
                      releasability=("PUBLIC",), organisation="ORG"),
        AccessContext("special", "u", "HUMAN", ("SUPERVISOR",),
                      compartments=("SPECIAL",),
                      releasability=("PUBLIC", "REL_A"), organisation="ORG"),
        AccessContext("coalition", "u", "HUMAN", ("ANALYST",),
                      releasability=("REL_A", "REL_B"), organisation="PARTNER"),
    ]
    for base, reference in itertools.product(markings, repeat=2):
        derived = inherited_marking(base, [reference])
        for context in contexts:
            if can_view(derived, context):
                assert can_view(base, context)
                assert can_view(reference, context)


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


def test_material_reference_registry_covers_dynamic_pairs_and_plain_basis_refs():
    record = {
        "record_type": "workbench_report",
        "report_id": "report-1",
        "sections": [{
            "sentences": [{"basis_refs": ["claim-1", "forecast-1"]}],
        }],
        "nested_transition": {
            "subject_kind": "hypothesis",
            "subject_id": "hyp-1",
        },
        "edges": [{
            "from_kind": "object",
            "from_id": "object-1",
            "to_kind": "claim",
            "to_id": "claim-2",
        }],
    }
    refs = set(material_references(record))
    assert MaterialReference("*", "claim-1") in refs
    assert MaterialReference("*", "forecast-1") in refs
    assert MaterialReference("hypothesis", "hyp-1") in refs
    assert MaterialReference("object_version", "object-1") in refs
    assert MaterialReference("semantic_claim", "claim-2") in refs


def test_material_reference_registry_covers_analytic_evidence_and_sources():
    refs = set(material_references({
        "record_type": "forecast_indicator",
        "indicator_id": "indicator-1",
        "fired_evidence_refs": ["observation-1"],
    }))
    assert MaterialReference("*", "observation-1") in refs

    refs = set(material_references({
        "record_type": "analytic_forecast",
        "forecast_id": "forecast-1",
        "resolution_evidence_refs": ["claim-1"],
    }))
    assert MaterialReference("*", "claim-1") in refs

    refs = set(material_references({
        "record_type": "discriminator",
        "discriminator_id": "disc-1",
        "source_refs": [["analytic_theme", "theme-1"]],
    }))
    assert MaterialReference("analytic_theme", "theme-1") in refs


def test_every_contract_reference_shaped_field_has_an_explicit_policy():
    """A new reference carrier cannot silently bypass the marking floor."""
    import dataclasses
    import importlib
    import inspect

    modules = [
        importlib.import_module(name)
        for name in (
            "curunir_analytic.contracts",
            "curunir_fabric.contracts",
            "curunir_identity.contracts",
            "curunir_operational.contracts",
            "curunir_semantic.contracts",
            "curunir_workbench.contracts",
        )
    ]
    gaps = []
    for module in modules:
        for _, contract in inspect.getmembers(module, inspect.isclass):
            record_type = getattr(contract, "RECORD_TYPE", "")
            if contract.__module__ != module.__name__ \
                    or not record_type or not dataclasses.is_dataclass(contract):
                continue
            for field in dataclasses.fields(contract):
                if not reference_field_is_classified(record_type, field.name):
                    gaps.append(f"{module.__name__}.{contract.__name__}.{field.name}")
    assert gaps == [], "unclassified identifier/reference fields: " + ", ".join(gaps)


def test_reference_resolution_handles_pinned_versions_and_report_parts():
    records = {
        "object_version": [
            {"record_type": "object_version", "object_id": "obj", "version": 1},
            {"record_type": "object_version", "object_id": "obj", "version": 2},
        ],
        "workbench_report": [{
            "record_type": "workbench_report",
            "report_id": "report",
            "version": 3,
            "sections": [{
                "section_id": "section",
                "sentences": [{"sentence_id": "sentence"}],
            }],
        }],
    }

    class FakeStore:
        def records_of(self, record_type):
            return records.get(record_type, [])

    resolved = resolve_reference_records(FakeStore(), [
        MaterialReference("object_version", "obj@v1"),
        MaterialReference("workbench_report", "sentence"),
    ])
    assert resolved[0]["version"] == 1
    assert resolved[1]["report_id"] == "report"
