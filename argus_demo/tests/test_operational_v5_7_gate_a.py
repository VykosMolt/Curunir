"""V5.7 Gate A — precedence and resolved payload.

Two defects, one shape: a record that asserts more than the pipeline established.

**P1** let an upstream construction failure be written down as
``NOT_SUPPORTED`` — which claims that valid evidence exists and fails to address
the claim.  No such evidence exists; it was never built.

**P2** let an adjudication *action* stand where its *result* belongs.  Thirteen
of V5.6.1's seventy-three adjudications corrected a component inside an affirmed
action, so anyone rebuilding a decision from the label would have rebuilt the
majority's answer instead of the adjudicator's.

Both are now refused by constructors rather than described in prose.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_6 import schema as S
from curunir_operational.v5_7 import adjudication as AD
from curunir_operational.v5_7 import states as ST

pytestmark = pytest.mark.no_db


# ===========================================================================
# §7 — precedence
# ===========================================================================

def test_only_evidence_bound_extraction_reaches_addressability():
    for state in ST.EXTRACTION_STATES:
        expected = state == "EXTRACTION_VALID_EVIDENCE_BOUND"
        assert ST.may_evaluate_addressability(state) is expected, state


@pytest.mark.parametrize("state", sorted(ST.UPSTREAM_CONSTRUCTION_STATES))
def test_an_upstream_failure_reaches_no_support_class(state):
    assert ST.support_class_permitted(state, "ADDRESSABILITY_NOT_REACHED") == ()
    with pytest.raises(ST.PrecedenceViolation, match="reaches no support classification"):
        ST.pipeline_state(unit_id="u", extraction_state=state,
                          support_class="NOT_SUPPORTED",
                          boundary_repair_plan={"repair_direction": "EXPAND_LEFT_CONTEXT"})


def test_invalid_extraction_may_not_be_addressable():
    with pytest.raises(ST.PrecedenceViolation, match="no evidence proposition"):
        ST.pipeline_state(unit_id="u", extraction_state="EXTRACTION_INVALID",
                          addressability="VALID_EVIDENCE_ADDRESSING_PROPOSITION")


def test_recoverable_boundary_may_not_carry_full_support():
    with pytest.raises(ST.PrecedenceViolation):
        ST.pipeline_state(
            unit_id="u",
            extraction_state="EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR",
            support_class="FULL_SUPPORT",
            boundary_repair_plan={"repair_direction": "EXPAND_LEFT_CONTEXT"})


def test_valid_not_evidence_bound_may_not_carry_a_specific_mismatch():
    with pytest.raises(ST.PrecedenceViolation):
        ST.pipeline_state(unit_id="u",
                          extraction_state="EXTRACTION_VALID_NOT_EVIDENCE_BOUND",
                          support_class="WRONG_ENTITY")


def test_valid_irrelevant_evidence_is_not_supported_and_nothing_else():
    permitted = ST.support_class_permitted(
        "EXTRACTION_VALID_EVIDENCE_BOUND",
        "VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION")
    assert permitted == ("NOT_SUPPORTED",)
    for mismatch in sorted(S.SPECIFIC_MISMATCH):
        assert mismatch not in permitted
    with pytest.raises(ST.PrecedenceViolation):
        ST.pipeline_state(
            unit_id="u", extraction_state="EXTRACTION_VALID_EVIDENCE_BOUND",
            addressability="VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION",
            support_class="WRONG_MODALITY")


def test_valid_irrelevant_evidence_is_not_an_extraction_failure():
    """Irrelevance is downstream of validity; it does not travel back upstream."""
    state = ST.pipeline_state(
        unit_id="u", extraction_state="EXTRACTION_VALID_EVIDENCE_BOUND",
        addressability="VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION",
        support_class="NOT_SUPPORTED")
    assert not state.is_upstream_failure
    assert state.reached_support


def test_a_recoverable_boundary_must_name_its_repair():
    with pytest.raises(ST.PrecedenceViolation, match="rejection wearing a softer word"):
        ST.pipeline_state(
            unit_id="u",
            extraction_state="EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR")


def test_the_recoverable_state_maps_to_itself():
    """§11: it may not be folded into a neighbouring state for scoring."""
    assert "RECOVERABLE_WITH_BOUNDARY_REPAIR" in ST.TERMINAL_STATES_V2
    assert ST.terminal_state_v2(
        semantic_validity="RECOVERABLE_WITH_BOUNDARY_REPAIR",
        wording_as_written="PERMITTED_ONLY_AFTER_BOUNDARY_REPAIR",
        exact_quotation="EXACT_QUOTATION_NOT_PERMITTED"
    ) == "RECOVERABLE_WITH_BOUNDARY_REPAIR"
    assert ST.extraction_state_from_terminal("RECOVERABLE_WITH_BOUNDARY_REPAIR") \
        == "EXTRACTION_RECOVERABLE_WITH_BOUNDARY_REPAIR"


def test_the_terminal_vocabulary_is_versioned_not_widened_in_place():
    assert ST.TERMINAL_INTERFACE_VERSION.endswith("_2")
    assert len(ST.TERMINAL_STATES_V2) == 8


def test_the_precedence_audit_finds_an_upstream_failure_recorded_as_support():
    report = ST.audit_precedence([
        {"unit_id": "a", "extraction_state": "EXTRACTION_VALID_EVIDENCE_BOUND",
         "addressability": "VALID_EVIDENCE_NOT_ADDRESSING_PROPOSITION",
         "support_class": "NOT_SUPPORTED"},
        {"unit_id": "b", "extraction_state": "EXTRACTION_INVALID",
         "support_class": "NOT_SUPPORTED"},
    ])
    assert report["precedence_violations"] == 1
    assert report["examples"][0]["unit"] == "b"


# ===========================================================================
# §8 — resolved payload
# ===========================================================================

def _extraction(**over):
    base = {"candidate_semantic_validity": "VALID",
            "boundary_repair_requirement": "NO_REPAIR_REQUIRED",
            "exact_quotation_permission": "NO_EXACT_QUOTATION_REQUESTED",
            "paraphrase_permission": "PARAPHRASE_PERMITTED",
            "wording_as_written_permission": "PERMITTED_AS_WRITTEN",
            "mapping_satisfied": ["TEXT_LOCATABLE"],
            "first_material_failure": None, "unit_type": "EXTRACTION"}
    base.update(over)
    return base


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


def test_an_action_alone_is_not_a_result():
    record = AD.resolution_record(
        unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=_support(), primaries=[_support()] * 3,
        seats=["A", "B", "C"], rationale="r" * 150)
    with pytest.raises(AD.ResolutionViolation, match="procedure, not a semantic result"):
        AD.reconstruct_from_action(record)


def test_a_component_correction_inside_an_affirmation_is_recorded():
    # The correction must sit INSIDE the affirmed decision: same support class,
    # same disposition — the pair the outcome label is keyed on — but a
    # qualification the majority did not record.  That is precisely the shape
    # the label cannot express, and the shape defect P2 was about.
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
    assert record.action_alone_would_mislead
    assert "required_qualifications" in record.corrected_components
    # And the reverse: an affirmation that changes nothing says so.
    clean = AD.resolution_record(
        unit_id="v", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=majority, primaries=[majority, majority, dissent],
        seats=["A", "B", "C"], rationale="r" * 150)
    assert not clean.action_alone_would_mislead


def test_a_resolution_without_a_canonical_payload_is_refused():
    incomplete = {k: v for k, v in _support().items() if k != "support_completeness"}
    with pytest.raises(AD.ResolutionViolation, match="not canonical; missing"):
        AD.resolution_record(
            unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
            resolved_decision=incomplete, primaries=[_support()] * 3,
            seats=["A", "B", "C"], rationale="r" * 150)


def test_an_affirmative_class_need_not_carry_a_material_failure():
    """The schema forbids one; absent-and-forbidden is not missing."""
    record = AD.resolution_record(
        unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision={k: v for k, v in _support().items()
                           if k != "first_material_failure"},
        primaries=[_support()] * 3, seats=["A", "B", "C"], rationale="r" * 150)
    assert record.resolved_decision["support_class"] == "FULL_SUPPORT"


def test_a_defect_action_carries_no_payload():
    with pytest.raises(AD.ResolutionViolation, match="may not carry a semantic payload"):
        AD.resolution_record(
            unit_id="u", unit_type="SUPPORT",
            resolution_action="PACKET_CONSTRUCTION_DEFECT",
            resolved_decision=_support(), primaries=[_support()] * 3,
            seats=["A", "B", "C"], rationale="r" * 150)


def test_a_resolution_preserves_all_three_primary_decisions():
    with pytest.raises(AD.ResolutionViolation, match="preserves all three"):
        AD.resolution_record(
            unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
            resolved_decision=_support(), primaries=[_support()] * 2,
            seats=["A", "B"], rationale="r" * 150)


def test_the_resolved_payload_may_not_contradict_itself():
    with pytest.raises(AD.ResolutionViolation, match="unpublishable support class"):
        AD.resolution_record(
            unit_id="u", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
            resolved_decision=_support(support_class="NOT_SUPPORTED",
                                       addresses_proposition=False,
                                       support_completeness="ABSENT"),
            primaries=[_support()] * 3, seats=["A", "B", "C"], rationale="r" * 150)


def test_extraction_payloads_carry_a_terminal_state():
    record = AD.resolution_record(
        unit_id="u", unit_type="EXTRACTION", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=_extraction(), primaries=[_extraction()] * 3,
        seats=["A", "B", "C"], rationale="r" * 150)
    assert record.resolved_decision["terminal_state"] in ST.TERMINAL_STATES_V2


def test_the_payload_audit_reports_zero_lost_by_construction():
    records = [AD.resolution_record(
        unit_id=f"u{i}", unit_type="SUPPORT", resolution_action="AFFIRM_MAJORITY",
        resolved_decision=_support(), primaries=[_support()] * 3,
        seats=["A", "B", "C"], rationale="r" * 150) for i in range(3)]
    report = AD.audit_resolved_payloads(records)
    assert report["verdict"] == "PASS"
    assert report["adjudication_records_without_resolved_payload"] == 0
    assert report["lost_component_corrections"] == 0
    assert "by construction" in report["how_lost_is_measured"]
