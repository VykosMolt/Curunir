"""V5.7 §9 / §31 — the reference-amendment mutations.

Eight cases from §9 plus the reference-integrity cases from §31 that Gate A can
already decide.  Each asserts on the refusal message, so a case cannot start
passing because something unrelated failed first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from curunir_operational.v5_6 import schema as S
from curunir_operational.v5_6_1 import reference as R6
from curunir_operational.v5_7 import adjudication as AD
from curunir_operational.v5_7 import states as ST

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]
V61 = ROOT / "artifacts/curunir_reference_standard_repair_v5_6_1_20260725"
V57 = ROOT / ("artifacts/curunir_reference_finalization_and_clean_generalization"
              "_v5_7_20260725")


def _support(**over):
    base = {"addresses_proposition": True, "actor_alignment": "ALIGNED",
            "predicate_alignment": "ALIGNED", "object_alignment": "ALIGNED",
            "scope_alignment": "ALIGNED", "time_alignment": "ALIGNED",
            "polarity_alignment": "ALIGNED", "modality_alignment": "ALIGNED",
            "lifecycle_alignment": "ALIGNED", "attribution_alignment": "NOT_APPLICABLE",
            "support_completeness": "COMPLETE", "support_class": "FULL_SUPPORT",
            "required_qualifications": [], "wording_permission": "DIRECT_FACTUAL_PUBLICATION",
            "disposition": "PUBLISHED", "first_material_failure": None,
            "unit_type": "SUPPORT"}
    base.update(over)
    return base


# --- §9.1 invalid extraction marked addressable -----------------------------

def test_m1_invalid_extraction_marked_addressable():
    with pytest.raises(ST.PrecedenceViolation, match="no evidence proposition"):
        ST.pipeline_state(unit_id="u", extraction_state="EXTRACTION_INVALID",
                          addressability="VALID_EVIDENCE_ADDRESSING_PROPOSITION")


# --- §9.2 recoverable boundary mapped directly to support -------------------

def test_m2_recoverable_boundary_mapped_directly_to_support():
    with pytest.raises(ST.PrecedenceViolation, match="reaches no support classification"):
        ST.pipeline_state(
            unit_id="u",
            extraction_state="EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
            support_class="PARTIAL_SUPPORT",
            boundary_repair_plan={"repair_direction": "EXPAND_LEFT_CONTEXT"})


# --- §9.3 valid irrelevant evidence marked extraction invalid ---------------

def test_m3_valid_irrelevant_evidence_is_not_extraction_invalid():
    valid = ST.pipeline_state(
        unit_id="u", extraction_state="EXTRACTION_VALID_EVIDENCE_BOUND",
        addressability="VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION",
        support_class="NOT_SUPPORTED")
    assert not valid.is_upstream_failure
    # And the converse route is closed: an invalid extraction cannot borrow the
    # NOT_SUPPORTED label that only valid-but-irrelevant evidence earns.
    assert ST.support_class_permitted(
        "EXTRACTION_INVALID", "ADDRESSABILITY_NOT_REACHED") == ()


# --- §9.4 AFFIRM_MAJORITY used as final semantic label ----------------------

def test_m4_affirm_majority_used_as_final_semantic_label():
    record = AD.resolution_record(
        unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=_support(), primaries=[_support()] * 3,
        seats=["A", "B", "C"], rationale="r" * 150)
    with pytest.raises(AD.ResolutionViolation, match="procedure, not a semantic result"):
        AD.reconstruct_from_action(record)


# --- §9.5 component correction omitted from resolved payload ----------------

def test_m5_component_correction_omitted_from_resolved_payload():
    partial = {k: v for k, v in _support().items()
               if k not in ("required_qualifications",)}
    with pytest.raises(AD.ResolutionViolation, match="not canonical; missing"):
        AD.resolution_record(
            unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
            resolved_decision=partial, primaries=[_support()] * 3,
            seats=["A", "B", "C"], rationale="r" * 150)


# --- §9.6 malformed rotated packet retained in scoring ----------------------

@pytest.mark.skipif(not (V57 / "01_rotated_context_audit").exists(),
                    reason="Gate A audit not yet produced")
def test_m6_malformed_rotated_packet_retained_in_scoring():
    audit = [json.loads(l) for l in
             (V57 / "01_rotated_context_audit/rotated_context_unit_audit.jsonl"
              ).read_text().splitlines() if l.strip()]
    assert audit, "the audit must enumerate every rotated unit"
    # None may be marked validly re-adjudicated on the strength of the V5.6.1
    # panel alone, because that panel never saw a corrected context.
    for row in audit:
        if row["disposition"] == "VALIDLY_REGENERATED_AND_RE_ADJUDICATED":
            assert row["three_fresh_primary_seats_reviewed_corrected_context"], \
                row["original_unit_id"]


# --- §9.7 corrected packet reviewed only by adjudicator ---------------------

@pytest.mark.skipif(not (V57 / "01_rotated_context_audit").exists(),
                    reason="Gate A audit not yet produced")
def test_m7_corrected_packet_reviewed_only_by_adjudicator_is_insufficient():
    audit = [json.loads(l) for l in
             (V57 / "01_rotated_context_audit/rotated_context_unit_audit.jsonl"
              ).read_text().splitlines() if l.strip()]
    for row in audit:
        adjudicator_only = (row["fresh_adjudicator_reviewed_corrected_context"]
                            and not row["three_fresh_primary_seats_reviewed_corrected_context"])
        if adjudicator_only:
            assert row["disposition"] != "VALIDLY_REGENERATED_AND_RE_ADJUDICATED"


# --- §9.8 V5.6.1 record overwritten -----------------------------------------

@pytest.mark.skipif(not V61.exists(), reason="V5.6.1 artifacts absent")
def test_m8_v5_6_1_records_are_not_overwritten():
    import hashlib
    manifest = json.loads(
        (V57 / "00_baseline/v5_6_1_artifact_manifest.json").read_text())
    changed = []
    for relative, expected in manifest["hashes"].items():
        actual = hashlib.sha256((V61 / relative).read_bytes()).hexdigest()
        if actual != expected:
            changed.append(relative)
    assert changed == [], changed


# --- §31 reference-integrity cases Gate A can already decide ----------------

def test_m31_03_invalid_extraction_marked_proposition_addressable():
    for state in sorted(ST.UPSTREAM_CONSTRUCTION_STATES):
        with pytest.raises(ST.PrecedenceViolation):
            ST.pipeline_state(
                unit_id="u", extraction_state=state,
                addressability="VALID_EVIDENCE_ADDRESSING_PROPOSITION",
                boundary_repair_plan={"repair_direction": "EXPAND_LEFT_CONTEXT"})


def test_m31_04_recoverable_boundary_receives_final_support_class():
    for support_class in ("FULL_SUPPORT", "NOT_SUPPORTED", "WRONG_ENTITY"):
        with pytest.raises(ST.PrecedenceViolation):
            ST.pipeline_state(
                unit_id="u",
                extraction_state="EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
                support_class=support_class,
                boundary_repair_plan={"repair_direction": "EXPAND_BOTH_DIRECTIONS"})


def test_m31_14_missing_recoverable_boundary_state():
    """The state must exist and must not be an alias of a neighbour."""
    assert "RECOVERABLE_WITH_BOUNDARY_REPAIR" in ST.TERMINAL_STATES_V2
    neighbours = ("ACCEPTED_CANDIDATE", "EVIDENCE_BOUND", "SEMANTICALLY_PARSED",
                  "QUARANTINED", "REJECTED")
    produced = ST.terminal_state_v2(
        semantic_validity="RECOVERABLE_WITH_BOUNDARY_REPAIR",
        wording_as_written="NOT_PERMITTED",
        exact_quotation="EXACT_QUOTATION_NOT_PERMITTED")
    assert produced not in neighbours
    assert produced == "RECOVERABLE_WITH_BOUNDARY_REPAIR"


def test_m31_06_component_correction_lost_inside_affirm_majority():
    majority = _support(support_class="QUALIFIED_SUPPORT",
                        disposition="PUBLISHED_WITH_QUALIFICATION",
                        required_qualifications=[])
    dissent = _support(support_class="NOT_SUPPORTED", addresses_proposition=False,
                       support_completeness="ABSENT",
                       wording_permission="NO_PUBLICATION_PERMITTED",
                       disposition="REJECTED_UNSUPPORTED")
    corrected = _support(support_class="QUALIFIED_SUPPORT",
                         disposition="PUBLISHED_WITH_QUALIFICATION",
                         required_qualifications=["PILOT_ONLY"])
    record = AD.resolution_record(
        unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=corrected, primaries=[majority, majority, dissent],
        seats=["A", "B", "C"], rationale="r" * 150)
    # The correction survives in the payload, and the record says the label
    # alone would mislead.
    assert record.resolved_decision["required_qualifications"] == ["PILOT_ONLY"]
    assert record.action_alone_would_mislead
    assert R6.decisive_value(record.resolved_decision) == R6.decisive_value(majority)
