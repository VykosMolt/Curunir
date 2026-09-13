"""Reference policy is read from the record classes, not from a table.

These tests pin the mechanism: labels are skipped, dynamic kinds resolve, blobs
and undeclared keys are scanned by name, untyped families are scanned in full,
and a marked record of an unknown type is refused rather than passed through.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from curunir_operational.contracts import RECORD_CLASSES, Record, unregister_record_class
from curunir_operational.references import DynamicRef, Label, Ref, RefInPairs, Refs, field_specs
from curunir_operational.security import (MaterialReference, UNTYPED_RECORD_TYPES, material_references,
                                          record_class, reference_field_is_classified)
from curunir_workbench.store import WorkbenchStore

pytestmark = pytest.mark.no_db


def refs(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {(r.kind, r.record_id) for r in material_references(record)}


def test_every_stored_record_type_has_a_class_or_is_declared_untyped():
    missing = {rt for rt in WorkbenchStore.EVENT_TYPES.values()
               if record_class(rt) is None and rt not in UNTYPED_RECORD_TYPES}
    assert not missing, sorted(missing)
    assert not UNTYPED_RECORD_TYPES & set(RECORD_CLASSES)


def test_a_marked_record_of_an_unknown_type_is_refused():
    with pytest.raises(ValueError, match="no record class"):
        material_references({"record_type": "made_up", "marking": {}, "object_id": "x"})


def test_annotations_drive_the_walk():
    @dataclass(frozen=True)
    class Probe(Record):
        RECORD_TYPE = "probe_record_for_test"
        ID_FIELD = "probe_id"
        probe_id: str
        claim_id: Ref("semantic_claim")
        subject_kind: str
        subject_id: DynamicRef("subject_kind")
        tags: Label(tuple[str, ...])
        evidence_ids: Refs("evidence")
        payload: dict[str, Any]
        plain_note: str

    try:
        specs = field_specs(Probe)
        assert specs["claim_id"].ref.kind == "semantic_claim"
        assert specs["subject_id"].ref.kind_from == "subject_kind"
        assert specs["tags"].label and specs["payload"].ref is None
        record = {"record_type": Probe.RECORD_TYPE, "marking": {}, "probe_id": "p1", "claim_id": "c1",
                  "subject_kind": "theme", "subject_id": "t1", "tags": ("obs-1",), "evidence_ids": ("e1", "e2"),
                  "payload": {"note": "x", "claim_id": "c2", "nested": [{"object_id": "o1"}]},
                  "plain_note": "obs-2", "undeclared": {"forecast_id": "f1"}}
        assert refs(record) == {("semantic_claim", "c1"), ("analytic_theme", "t1"), ("evidence", "e1"),
                                ("evidence", "e2"), ("semantic_claim", "c2"), ("object_version", "o1"),
                                ("analytic_forecast", "f1")}
        assert reference_field_is_classified(Probe.RECORD_TYPE, "plain_note")  # not id-shaped
        assert reference_field_is_classified(Probe.RECORD_TYPE, "tags")
        assert reference_field_is_classified(Probe.RECORD_TYPE, "probe_id")
    finally:
        unregister_record_class(Probe.RECORD_TYPE)


def test_an_unannotated_id_field_is_unclassified_and_still_scanned_by_name():
    @dataclass(frozen=True)
    class Sloppy(Record):
        RECORD_TYPE = "sloppy_record_for_test"
        ID_FIELD = "sloppy_id"
        sloppy_id: str
        claim_id: str

    try:
        assert not reference_field_is_classified(Sloppy.RECORD_TYPE, "claim_id")
        assert refs({"record_type": Sloppy.RECORD_TYPE, "marking": {}, "sloppy_id": "s", "claim_id": "c9"}) \
            == {("semantic_claim", "c9")}
    finally:
        unregister_record_class(Sloppy.RECORD_TYPE)


def test_untyped_families_are_scanned_in_full():
    # Definitions have no record class. Their own ids are labels; anything else
    # they name by a known field name is a reference.
    record = {"record_type": "workshop_definition", "marking": {}, "workshop_id": "ws-1",
              "layers": [{"schema_id": "sch-1", "filters": [{"object_id": "infra-1"}]}]}
    assert refs(record) == {("object_version", "infra-1")}


def test_material_reference_is_a_plain_value():
    assert MaterialReference("semantic_claim", "c1") == MaterialReference("semantic_claim", "c1")


def test_pairs_carry_ids_at_a_fixed_position_and_unannotated_pairs_are_unclassified():
    @dataclass(frozen=True)
    class Paired(Record):
        RECORD_TYPE = "paired_record_for_test"
        ID_FIELD = "paired_id"
        paired_id: str
        participants: RefInPairs("object_version", 0)
        lineage: RefInPairs("analytic_theme", 1)
        scheme_values: Label(tuple[tuple[str, str], ...])
        bare_pairs: tuple[tuple[str, str], ...]

    try:
        record = {"record_type": Paired.RECORD_TYPE, "marking": {}, "paired_id": "p",
                  "participants": (("obj-1", "OBSERVER"),), "lineage": (("MERGED_FROM", "theme-1"),),
                  "scheme_values": (("lei", "ABC"),), "bare_pairs": (("x", "y"),)}
        assert refs(record) == {("object_version", "obj-1"), ("analytic_theme", "theme-1")}
        assert reference_field_is_classified(Paired.RECORD_TYPE, "participants")
        assert reference_field_is_classified(Paired.RECORD_TYPE, "scheme_values")
        assert not reference_field_is_classified(Paired.RECORD_TYPE, "bare_pairs")
    finally:
        unregister_record_class(Paired.RECORD_TYPE)


def test_a_dynamic_kind_must_name_a_real_field():
    @dataclass(frozen=True)
    class Broken(Record):
        RECORD_TYPE = "broken_record_for_test"
        ID_FIELD = "broken_id"
        broken_id: str
        subject_id: DynamicRef("subject_kind")

    try:
        with pytest.raises(TypeError, match="subject_kind"):
            field_specs(Broken)
    finally:
        unregister_record_class(Broken.RECORD_TYPE)


def test_the_blob_policy_follows_the_registry_not_its_size():
    """Register, forget, register again at the same size: the policy must
    know the new class's names and forget the old one's."""
    def make(name, field):
        namespace = {"__annotations__": {f"{name}_id": str, field: Ref("semantic_claim")},
                     "RECORD_TYPE": f"{name}_record_for_test", "ID_FIELD": f"{name}_id"}
        return dataclass(frozen=True)(type(name.title(), (Record,), namespace))

    carrier = {"record_type": "workshop_definition", "marking": {}, "payload": {"aaa_ref": "claim-a", "bbb_ref": "claim-b"}}
    first = make("aaa", "aaa_ref")
    try:
        assert ("semantic_claim", "claim-a") in refs(carrier)
    finally:
        unregister_record_class(first.RECORD_TYPE)
    second = make("bbb", "bbb_ref")
    try:
        found = refs(carrier)
        assert ("semantic_claim", "claim-b") in found and ("semantic_claim", "claim-a") not in found
    finally:
        unregister_record_class(second.RECORD_TYPE)


def test_a_label_on_one_class_does_not_hide_a_reference_name_inside_payloads():
    # `labels` is a Label on object versions; a payload key `claim_ids` still resolves.
    record = {"record_type": "workshop_definition", "marking": {}, "layers": [{"claim_ids": ["c-1"], "labels": ["c-2"]}]}
    assert refs(record) == {("semantic_claim", "c-1")}


def test_a_duplicate_record_type_is_refused():
    with pytest.raises(TypeError, match="object_version"):
        @dataclass(frozen=True)
        class Twin(Record):
            RECORD_TYPE = "object_version"
            ID_FIELD = "object_id"
            object_id: str
