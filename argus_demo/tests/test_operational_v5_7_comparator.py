"""V5.7 §2 — the full-payload comparator.

Every case here shares a headline and differs underneath. Under the old keying
each compared as identical, which is how a required qualification could be
dropped, or exact quotation withdrawn, without anything being routed anywhere.

Three independent agents reported the limitation before it was fixed; these
tests are the fixtures they described.
"""

from __future__ import annotations

import pytest

from curunir_operational.v5_7 import comparator as C

pytestmark = pytest.mark.no_db


def support(**over):
    base = {"addresses_proposition": True, "entity_alignment": "ALIGNED",
            "predicate_alignment": "ALIGNED", "object_alignment": "ALIGNED",
            "scope_alignment": "ALIGNED", "time_alignment": "ALIGNED",
            "polarity_alignment": "ALIGNED", "modality_alignment": "ALIGNED",
            "lifecycle_alignment": "ALIGNED", "attribution_alignment": "ALIGNED",
            "dependence_limitations": [], "counterevidence_state": "NONE",
            "support_completeness": "QUALIFIED", "first_material_failure": None,
            "support_class": "QUALIFIED_SUPPORT",
            "required_qualifications": ["PARTIAL_SCOPE"],
            "permitted_wording": "DIRECT_FACTUAL_PUBLICATION",
            "publication_disposition": "PUBLISHED_WITH_QUALIFICATION"}
    base.update(over)
    return base


def extraction(**over):
    base = {"candidate_semantic_validity": "VALID",
            "boundary_repair_requirement": "NO_REPAIR_REQUIRED",
            "evidence_binding_state": "EVIDENCE_BOUND",
            "exact_quotation_permission": "EXACT_QUOTATION_PERMITTED",
            "paraphrase_permission": "PARAPHRASE_PERMITTED",
            "wording_as_written_permission": "PERMITTED_AS_WRITTEN",
            "terminal_state": "ACCEPTED_CANDIDATE",
            "semantic_actor": "entity-1", "attribution_source": None,
            "quoted_speaker": None, "polarity": "POSITIVE",
            "modality": "ASSERTED", "lifecycle_state": "OPERATIONAL",
            "temporal_scope": [None, None],
            "mapping_satisfied": ["TEXT_LOCATABLE", "EXACT_VALUE_QUOTABLE"],
            "first_material_failure": None}
    base.update(over)
    return base


def cmp_support(a, b):
    return C.compare(a, b, unit_type="SUPPORT", unit_id="u")


def cmp_extraction(a, b):
    return C.compare(a, b, unit_type="EXTRACTION", unit_id="u")


# ===========================================================================
# §2.3 — the qualification-sensitive fixtures, all headline-identical
# ===========================================================================

@pytest.mark.parametrize("qualification", [
    "ATTRIBUTION_REQUIRED", "VENDOR_REPORTED", "PRELIMINARY", "PILOT_ONLY",
    "GEOGRAPHICALLY_BOUNDED", "DEPENDENCE_UNRESOLVED", "COUNTEREVIDENCE_PRESENT",
    "UNCERTAINTY_REQUIRED",
])
def test_a_dropped_qualification_is_material_though_the_headline_is_identical(
        qualification):
    with_it = support(required_qualifications=["PARTIAL_SCOPE", qualification])
    without = support(required_qualifications=["PARTIAL_SCOPE"])
    result = cmp_support(with_it, without)
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert result.top_level_identical, "the fixture must share its headline"
    assert result.invisible_to_the_old_comparator
    assert "required_qualifications" in result.differing_components
    assert qualification in result.component_detail[
        "required_qualifications"]["truth_bearing"]


def test_qualification_order_is_not_a_difference():
    left = support(required_qualifications=["PARTIAL_SCOPE", "PILOT_ONLY"])
    right = support(required_qualifications=["PILOT_ONLY", "PARTIAL_SCOPE"])
    assert cmp_support(left, right).result == "IDENTICAL"


def test_exact_quotation_withdrawn_is_material_though_the_headline_is_identical():
    result = cmp_extraction(
        extraction(),
        extraction(exact_quotation_permission="EXACT_QUOTATION_REQUIRES_STRONGER_MAPPING"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert result.top_level_identical
    assert result.invisible_to_the_old_comparator


def test_paraphrase_permission_change_is_material():
    result = cmp_extraction(
        extraction(), extraction(paraphrase_permission="PARAPHRASE_REQUIRES_QUALIFICATION"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert "paraphrase_permission" in result.differing_components


def test_a_different_first_material_failure_is_material():
    left = support(support_class="NOT_SUPPORTED", addresses_proposition=False,
                   publication_disposition="REJECTED_UNSUPPORTED",
                   permitted_wording="NO_PUBLICATION_PERMITTED",
                   first_material_failure="addresses_proposition")
    right = support(support_class="NOT_SUPPORTED", addresses_proposition=False,
                    publication_disposition="REJECTED_UNSUPPORTED",
                    permitted_wording="NO_PUBLICATION_PERMITTED",
                    first_material_failure="object_alignment")
    result = cmp_support(left, right)
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert result.top_level_identical
    assert "first_material_failure" in result.differing_components


def test_a_different_lifecycle_reading_is_material():
    result = cmp_extraction(
        extraction(), extraction(lifecycle_state="PILOT"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert "lifecycle_state" in result.differing_components


def test_an_actor_substitution_is_material():
    result = cmp_extraction(extraction(), extraction(semantic_actor="entity-2"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert "semantic_actor" in result.differing_components


def test_unresolved_dependence_versus_independent_corroboration_is_material():
    result = cmp_support(
        support(dependence_limitations=["DEPENDENCE_UNRESOLVED"]),
        support(dependence_limitations=[]))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert "dependence_limitations" in result.differing_components


def test_permitted_wording_change_is_material_though_disposition_holds():
    result = cmp_support(
        support(), support(permitted_wording="ATTRIBUTED_METACLAIM_ONLY"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"
    assert result.top_level_identical
    assert "permitted_wording" in result.differing_components


# ===========================================================================
# Behaviour of the other results
# ===========================================================================

def test_identical_payloads_compare_identical():
    assert cmp_support(support(), support()).result == "IDENTICAL"
    assert cmp_extraction(extraction(), extraction()).result == "IDENTICAL"


def test_prose_is_never_a_semantic_difference():
    left = {**support(), "reasoning": "one wording"}
    right = {**support(), "reasoning": "an entirely different wording"}
    assert cmp_support(left, right).result == "IDENTICAL"


def test_declining_to_read_is_normalised_and_recorded():
    result = cmp_support(support(time_alignment="UNRESOLVED"),
                         support(time_alignment="NOT_APPLICABLE"))
    assert result.result == "NONMATERIAL_WORDING_DIFFERENCE"
    assert "time_alignment" in result.normalised_away


def test_aligned_versus_no_reading_is_never_normalised():
    result = cmp_support(support(), support(time_alignment="UNRESOLVED"))
    assert result.result == "MATERIAL_COMPONENT_DIFFERENCE"


def test_a_headline_move_is_a_top_level_difference():
    result = cmp_support(support(), support(
        support_class="PARTIAL_SUPPORT",
        publication_disposition="MOVED_TO_UNCERTAINTY_SECTION"))
    assert result.result == "TOP_LEVEL_CLASS_DIFFERENCE"
    assert not result.top_level_identical
    assert result.would_old_comparator_see_it


def test_an_empty_payload_is_incomparable_not_identical():
    result = cmp_support(support(), {})
    assert result.result == "PROTOCOL_INCOMPARABLE"


def test_aliases_are_resolved_not_treated_as_differences():
    old_names = {**support()}
    old_names["actor_alignment"] = old_names.pop("entity_alignment")
    old_names["wording_permission"] = old_names.pop("permitted_wording")
    old_names["disposition"] = old_names.pop("publication_disposition")
    assert cmp_support(support(), old_names).result == "IDENTICAL"


def test_material_comparisons_are_what_gets_routed():
    comparisons = [
        cmp_support(support(), support()),
        cmp_support(support(), support(required_qualifications=[])),
        cmp_support(support(), support(support_class="PARTIAL_SUPPORT",
                                       publication_disposition="MOVED_TO_UNCERTAINTY_SECTION")),
    ]
    routed = C.route_material(comparisons)
    assert len(routed) == 2


def test_the_audit_counts_what_the_old_keying_would_have_missed():
    comparisons = [
        cmp_support(support(), support(required_qualifications=[])),
        cmp_support(support(), support(permitted_wording="ATTRIBUTED_METACLAIM_ONLY")),
        cmp_support(support(), support(support_class="PARTIAL_SUPPORT",
                                       publication_disposition="MOVED_TO_UNCERTAINTY_SECTION")),
    ]
    report = C.audit_comparator(comparisons)
    assert report["material_component_differences"] == 2
    assert report["component_differences_previously_invisible"] == 2
    assert report["qualification_only_differences"] == 2


def test_a_material_difference_must_name_its_components():
    with pytest.raises(C.ComparisonViolation, match="must name the components"):
        C.PayloadComparison(
            "c", "u", "SUPPORT", "MATERIAL_COMPONENT_DIFFERENCE", True, (), {},
            (), False, C.PAYLOAD_VERSION, "2026-07-25T00:00:00+00:00")
