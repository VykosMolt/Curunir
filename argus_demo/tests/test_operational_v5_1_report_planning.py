from __future__ import annotations

import pytest

from curunir_operational.v5_1.report_planning import (
    CERTAINTY_LEVELS, OMISSION_REASON_TO_DISPOSITION, aggregate_dependence,
    atomic_proposition, certainty_for_evidence_state, decompose_sentence,
    independence_language_guard, material_omission_audit, process_assertion_guard,
    proposition_evidence_package, qualification_preservation_check,
    structured_prose_consistency, validate_executive_summary,
)

pytestmark = pytest.mark.no_db


def make_prop(**overrides):
    values = dict(
        text="The northern harbour expansion is planned for 2027.",
        kind="FACTUAL", modality="PLANNED", certainty="REPORTED_ONLY",
        evidence_state="REPORTED", temporal_scope=("2027-01-01", "2027-12-31"),
        geographic_scope=("EE",), qualifications=("PLANNED",),
        supporting_claim_ids=("claim-1",), supporting_span_ids=("cand-1",),
        supporting_basis_ids=("basis-1",))
    values.update(overrides)
    return atomic_proposition(**values)


def claim_fixture(claim_id="claim-a", statement="harbour dredging project planned for 2027",
                  modality="PLANNED", epistemic_state="REPORTED"):
    return {"claim_id": claim_id, "normalized_statement": statement, "modality": modality,
            "epistemic_state": epistemic_state, "temporal_scope": ("2027-01-01", None),
            "geographic_scope": ("EE",), "candidate_ids": (f"cand-{claim_id}",),
            "evidence_basis_ids": (f"basis-{claim_id}",)}


# --- AtomicProposition contract guards ---

def test_proposition_rejects_unknown_vocabulary():
    with pytest.raises(ValueError, match="modality"):
        make_prop(modality="GUESSED")
    with pytest.raises(ValueError, match="qualification"):
        make_prop(qualifications=("SORT_OF_PLANNED",))
    with pytest.raises(ValueError, match="dependence"):
        make_prop(dependence_summary="PROBABLY_FINE")


def test_factual_proposition_requires_claim_and_span_support():
    with pytest.raises(ValueError, match="factual proposition requires"):
        make_prop(supporting_claim_ids=(), supporting_span_ids=())


def test_corroborated_certainty_requires_affirmative_independence():
    with pytest.raises(ValueError, match="INDEPENDENCE_SUPPORTED"):
        make_prop(certainty="CORROBORATED", dependence_summary="NO_DEPENDENCE_FOUND")
    value = make_prop(certainty="CORROBORATED", dependence_summary="INDEPENDENCE_SUPPORTED")
    assert value.certainty == "CORROBORATED"


def test_defeated_evidence_forces_unresolved_certainty():
    with pytest.raises(ValueError, match="UNRESOLVED"):
        make_prop(evidence_state="CONTRADICTED", certainty="SUPPORTED")
    value = make_prop(evidence_state="RETRACTED", certainty="UNRESOLVED",
                      contradiction_state="RETRACTION")
    assert value.to_record()["evidence_state"] == "RETRACTED"


def test_certainty_ladder_and_dependence_aggregation():
    assert CERTAINTY_LEVELS.index("CORROBORATED") > CERTAINTY_LEVELS.index("SUPPORTED")
    assert certainty_for_evidence_state("REPORTED") == "REPORTED_ONLY"
    with pytest.raises(ValueError):
        certainty_for_evidence_state("VIBES")
    assert aggregate_dependence(()) == "INDEPENDENCE_UNKNOWN"
    assert aggregate_dependence(("INDEPENDENCE_SUPPORTED", "SYNDICATION_DERIVATIVE")) == \
        "SYNDICATION_DERIVATIVE"
    with pytest.raises(ValueError):
        aggregate_dependence(("KIND_OF_DEPENDENT",))


# --- Section 17.1 decomposition ---

def test_single_clause_sentence_decomposes_to_one_proposition():
    result = decompose_sentence("The harbour dredging project is planned for 2027.",
                                (claim_fixture(),))
    assert result.status == "DECOMPOSED" and len(result.propositions) == 1
    value = result.propositions[0]
    assert value.supporting_claim_ids == ("claim-a",)
    assert value.supporting_span_ids == ("cand-claim-a",)
    assert value.qualifications == ("PLANNED",)


def test_bundled_sentence_with_full_support_decomposes_per_clause():
    sentence = ("The harbour dredging project is planned for 2027, and the ferry operator "
                "reported record passenger numbers.")
    claims = (claim_fixture(),
              claim_fixture("claim-b", "ferry operator reported record passenger numbers",
                            modality="REPORTED"))
    result = decompose_sentence(sentence, claims)
    assert result.status == "DECOMPOSED" and len(result.propositions) == 2
    assert result.propositions[0].supporting_claim_ids == ("claim-a",)
    assert result.propositions[1].supporting_claim_ids == ("claim-b",)


def test_bundled_sentence_without_per_proposition_support_requires_split():
    sentence = ("The harbour dredging project is planned for 2027, and the ferry operator "
                "reported record passenger numbers.")
    result = decompose_sentence(sentence, (claim_fixture(),))
    assert result.status == "REQUIRES_SPLIT"
    assert result.propositions == ()
    assert any("ferry operator" in segment for segment in result.unsupported_segments)


def test_decomposition_extracts_attribution():
    result = decompose_sentence(
        "According to the port authority, the harbour dredging project is planned for 2027.",
        (claim_fixture(),))
    assert result.propositions[0].attribution == "the port authority"


def test_decomposition_requires_mapped_claims():
    with pytest.raises(ValueError, match="mapped claim"):
        decompose_sentence("The harbour dredging project is planned for 2027.", ())


# --- Section 17.3 independence language guard ---

def test_independence_phrase_blocked_without_affirmative_support():
    violations = independence_language_guard(
        "The figures were independently confirmed by two outlets.",
        ("NO_DEPENDENCE_FOUND",))
    assert len(violations) == 1
    assert violations[0].code == "INDEPENDENCE_LANGUAGE_WITHOUT_SUPPORT"
    assert violations[0].suggested_substitutes == \
        ("no dependence was identified among the reviewed sources",)


def test_independence_phrase_allowed_with_affirmative_support():
    assert independence_language_guard(
        "The figures were independently confirmed by two outlets.",
        ("INDEPENDENCE_SUPPORTED",)) == ()


def test_independence_phrase_blocked_when_aggregate_not_affirmative():
    # One independent pair among derivative or absence-only pairs never
    # licenses global independence wording (conservative aggregate rule).
    for mixed in (("INDEPENDENCE_SUPPORTED", "DERIVATIVE_CONFIRMED"),
                  ("INDEPENDENCE_SUPPORTED", "NO_DEPENDENCE_FOUND"),
                  ("INDEPENDENCE_SUPPORTED", "INDEPENDENCE_UNKNOWN")):
        violations = independence_language_guard(
            "The figures were independently confirmed by two outlets.", mixed)
        assert violations, mixed
        assert violations[0].code == "INDEPENDENCE_LANGUAGE_WITHOUT_SUPPORT"


def test_independence_substitutes_are_deterministic_per_state():
    unresolved = independence_language_guard(
        "Multiple independent sources described the tender.", ("INDEPENDENCE_UNKNOWN",))
    assert unresolved[0].suggested_substitutes == ("source independence remains unresolved",)
    derivative = independence_language_guard(
        "Multiple independent sources described the tender.", ("SYNDICATION_DERIVATIVE",))
    assert derivative[0].suggested_substitutes == ("reported in multiple publications",)


def test_independence_guard_rejects_unknown_state():
    with pytest.raises(ValueError, match="unknown dependence states"):
        independence_language_guard("Anything at all.", ("MOSTLY_INDEPENDENT",))


# --- Section 17.2 qualification preservation (multilingual) ---

def test_dropped_qualification_detected_in_german_rendering():
    value = make_prop()
    kept = qualification_preservation_check(value, "Die Hafenerweiterung ist für 2027 geplant.")
    assert kept == ()
    dropped = qualification_preservation_check(value, "Die Hafenerweiterung erfolgt 2027.")
    assert len(dropped) == 1 and dropped[0].code == "MATERIAL_QUALIFICATION_OMITTED"
    assert dropped[0].matched_text == "PLANNED"


def test_qualification_realized_in_french_and_dropped_in_spanish():
    value = make_prop()
    assert qualification_preservation_check(
        value, "L'agrandissement du port est prévu pour 2027.") == ()
    dropped = qualification_preservation_check(
        value, "La ampliación del puerto se completará en 2027.")
    assert [item.code for item in dropped] == ["MATERIAL_QUALIFICATION_OMITTED"]


def test_multiple_dropped_markers_each_reported():
    value = make_prop(qualifications=("PLANNED", "DISPUTED"), modality="PLANNED")
    dropped = qualification_preservation_check(value, "The expansion will proceed in 2027.")
    assert {item.matched_text for item in dropped} == {"PLANNED", "DISPUTED"}


# --- process assertion guard ---

def test_process_assertion_without_proof_is_violation():
    violations = process_assertion_guard(
        "The run was replayed with byte-identical outputs.", {})
    assert len(violations) == 1
    assert violations[0].code == "PROCESS_ASSERTION_WITHOUT_OPERATIONAL_PROOF"
    assert "REPLAY_REPRODUCIBILITY" in violations[0].detail


def test_process_assertion_with_proof_reference_passes():
    assert process_assertion_guard(
        "The run was replayed with byte-identical outputs.",
        {"REPLAY_REPRODUCIBILITY": ("replay-ledger-3",)}) == ()


def test_each_detected_assertion_family_needs_its_own_proof():
    violations = process_assertion_guard(
        "Every packet was verified during the zero-network replay.",
        {"REPLAY_REPRODUCIBILITY": ("replay-ledger-3",)})
    assert {item.code for item in violations} == {"PROCESS_ASSERTION_WITHOUT_OPERATIONAL_PROOF"}
    assert {"NETWORK_ISOLATION", "COMPLETE_COVERAGE"} == \
        {item.detail.split("type ")[1].split(" ")[0] for item in violations}


def test_process_assertion_guard_rejects_unknown_assertion_type():
    with pytest.raises(ValueError, match="unknown process assertion types"):
        process_assertion_guard("Anything.", {"COFFEE_QUALITY": ("x",)})


# --- structured vs prose consistency ---

def test_prose_matching_structured_supersession_passes():
    value = make_prop(
        text="The initial annex was superseded by the revised annex.",
        modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED",
        contradiction_state="SUPERSESSION", qualifications=("SUPERSEDED",))
    assert structured_prose_consistency(value, {"relation_state": "SUPERSESSION"}) == ()


def test_prose_structure_supersession_divergence_detected():
    silent = make_prop(text="The annex remains in force without changes.",
                       modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED")
    violations = structured_prose_consistency(silent, {"relation_state": "SUPERSESSION"})
    codes = [item.code for item in violations]
    assert codes and set(codes) == {"STRUCTURED_PROSE_DIVERGENCE"}
    narrating = make_prop(text="The initial annex was superseded by the revised annex.",
                          modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED")
    violations = structured_prose_consistency(narrating, {"relation_state": "NO_CONFLICT"})
    assert any("SUPERSESSION" in item.detail for item in violations)


def test_identity_qualifier_must_survive_into_prose():
    value = make_prop(text="The self-described representative announced the tender schedule.",
                      modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED",
                      qualifications=())
    states = {"relation_state": "NO_CONFLICT", "identity_qualifiers": ("self-described",)}
    assert structured_prose_consistency(value, states) == ()
    bare = make_prop(text="The representative announced the tender schedule.",
                     modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED",
                     qualifications=())
    violations = structured_prose_consistency(bare, states)
    assert [item.code for item in violations] == ["IDENTITY_QUALIFIER_OMITTED"]


def test_structured_prose_requires_valid_relation_state():
    value = make_prop()
    with pytest.raises(ValueError, match="relation state"):
        structured_prose_consistency(value, {})
    with pytest.raises(ValueError, match="relation state"):
        structured_prose_consistency(value, {"relation_state": "SOMETHING_ELSE"})


# --- Section 17.4 executive summary ---

def body_fixture():
    b1 = make_prop()
    b2 = make_prop(text="The initial passenger figure was corrected in a later filing.",
                   modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED",
                   contradiction_state="CORRECTION", qualifications=("CORRECTED",),
                   supporting_claim_ids=("claim-2",), supporting_span_ids=("cand-2",))
    return (b1, b2)


def exec_fixture():
    e1 = make_prop(text="The harbour expansion is planned for 2027.")
    e_corr = make_prop(text="An initial figure was corrected in a later filing.",
                       modality="ASSERTED", certainty="SUPPORTED", evidence_state="SUPPORTED",
                       contradiction_state="CORRECTION", qualifications=("CORRECTED",),
                       supporting_claim_ids=("claim-2",), supporting_span_ids=("cand-2",))
    e_lim = make_prop(text="The analysis refuses to conclude whether the second phase is funded.",
                      kind="LIMITATION", modality="ASSERTED", certainty="UNRESOLVED",
                      evidence_state="UNKNOWN", qualifications=(),
                      supporting_claim_ids=(), supporting_span_ids=())
    return (e1, e_corr, e_lim)


def test_conforming_executive_summary_passes():
    assert validate_executive_summary(exec_fixture(), body_fixture()) == ()


def test_exec_certainty_above_body_is_violation():
    e1, e_corr, e_lim = exec_fixture()
    stronger = make_prop(text="The harbour expansion is planned for 2027.",
                         certainty="SUPPORTED", evidence_state="SUPPORTED")
    violations = validate_executive_summary((stronger, e_corr, e_lim), body_fixture())
    assert "EXEC_CERTAINTY_ABOVE_BODY" in {item.code for item in violations}


def test_exec_dropping_body_qualification_is_overcompression():
    e1, e_corr, e_lim = exec_fixture()
    flattened = make_prop(text="The harbour expansion happens in 2027.", qualifications=())
    violations = validate_executive_summary((flattened, e_corr, e_lim), body_fixture())
    codes = {item.code for item in violations}
    assert "EXEC_OVERCOMPRESSION" in codes


def test_exec_omitting_decisive_counterevidence_is_violation():
    e1, _, e_lim = exec_fixture()
    violations = validate_executive_summary((e1, e_lim), body_fixture())
    assert "EXEC_OMITS_DECISIVE_COUNTEREVIDENCE" in {item.code for item in violations}


def test_exec_requires_refusal_and_dependence_limitation():
    e1, e_corr, _ = exec_fixture()
    violations = validate_executive_summary((e1, e_corr), body_fixture())
    codes = {item.code for item in violations}
    assert "EXEC_MISSING_REFUSAL_STATEMENT" in codes
    assert "EXEC_MISSING_DEPENDENCE_LIMITATION" in codes


def test_exec_requires_temporal_boundary_and_body_support():
    e1, e_corr, e_lim = exec_fixture()
    unanchored = make_prop(text="A tender for the harbour is planned.",
                           temporal_scope=(None, None), supporting_claim_ids=("claim-9",))
    e_corr_unanchored = make_prop(
        text=e_corr.text, modality="ASSERTED", certainty="SUPPORTED",
        evidence_state="SUPPORTED", contradiction_state="CORRECTION",
        qualifications=("CORRECTED",), temporal_scope=(None, None),
        supporting_claim_ids=("claim-2",), supporting_span_ids=("cand-2",))
    e_lim_unanchored = make_prop(
        text=e_lim.text, kind="LIMITATION", modality="ASSERTED", certainty="UNRESOLVED",
        evidence_state="UNKNOWN", qualifications=(), temporal_scope=(None, None),
        supporting_claim_ids=(), supporting_span_ids=())
    violations = validate_executive_summary(
        (unanchored, e_corr_unanchored, e_lim_unanchored), body_fixture())
    codes = {item.code for item in violations}
    assert "EXEC_MISSING_TEMPORAL_BOUNDARY" in codes
    assert "EXEC_PROPOSITION_WITHOUT_BODY_SUPPORT" in codes


def test_exec_unresolved_hypotheses_must_surface():
    body = body_fixture() + (make_prop(
        text="Whether the second operator participates remains unresolved.",
        modality="ASSERTED", certainty="UNRESOLVED", evidence_state="UNKNOWN",
        contradiction_state="UNRESOLVED", qualifications=("UNRESOLVED",),
        supporting_claim_ids=("claim-3",), supporting_span_ids=("cand-3",)),)
    e1, e_corr, e_lim = exec_fixture()
    resolved_only = make_prop(
        text="The analysis does not conclude on the second operator.", kind="LIMITATION",
        modality="ASSERTED", certainty="QUALIFIED", evidence_state="QUALIFIED",
        qualifications=(), supporting_claim_ids=(), supporting_span_ids=())
    violations = validate_executive_summary((e1, e_corr, resolved_only), body)
    assert "EXEC_MISSING_UNRESOLVED_HYPOTHESES" in {item.code for item in violations}
    assert validate_executive_summary((e1, e_corr, e_lim), body) == ()


def test_exec_validation_requires_body():
    with pytest.raises(ValueError, match="body"):
        validate_executive_summary(exec_fixture(), ())


# --- Section 17.6 proposition evidence package ---

def test_published_package_requires_final_sentence_and_qualified_disposition():
    value = make_prop()
    package = proposition_evidence_package(
        proposition=value, disposition="PUBLISHED_WITH_QUALIFICATION",
        final_sentence="Die Hafenerweiterung ist für 2027 geplant.",
        allowed_wording=("planned for 2027",),
        disallowed_stronger_wording=("confirmed for 2027",))
    assert package.qualifications == ("PLANNED",)
    with pytest.raises(ValueError, match="final sentence"):
        proposition_evidence_package(proposition=value, disposition="PUBLISHED_WITH_QUALIFICATION")
    with pytest.raises(ValueError, match="PUBLISHED_WITH_QUALIFICATION"):
        proposition_evidence_package(proposition=value, disposition="PUBLISHED_FAITHFULLY",
                                     final_sentence="The expansion is planned for 2027.")


def test_omitted_package_requires_reason_and_failure_requires_class():
    value = make_prop()
    with pytest.raises(ValueError, match="omission reason"):
        proposition_evidence_package(proposition=value, disposition="OMITTED_NOT_MATERIAL")
    with pytest.raises(ValueError, match="defect class"):
        proposition_evidence_package(proposition=value, disposition="SYSTEM_CAPABILITY_FAILURE",
                                     omission_reason="RENDER_FAILURE")
    package = proposition_evidence_package(
        proposition=value, disposition="SYSTEM_CAPABILITY_FAILURE",
        omission_reason="RENDER_FAILURE",
        capability_failure_class="PROPOSITION_RENDER_FAILURE")
    assert package.to_record()["capability_failure_class"] == "PROPOSITION_RENDER_FAILURE"


def test_package_allowed_wording_cannot_leak_independence_language():
    value = make_prop()
    with pytest.raises(ValueError, match="independence language"):
        proposition_evidence_package(
            proposition=value, disposition="PUBLISHED_WITH_QUALIFICATION",
            final_sentence="The expansion is planned for 2027.",
            allowed_wording=("independently confirmed by several sources",))
    supported = make_prop(dependence_summary="INDEPENDENCE_SUPPORTED")
    package = proposition_evidence_package(
        proposition=supported, disposition="PUBLISHED_WITH_QUALIFICATION",
        final_sentence="The expansion is planned for 2027.",
        allowed_wording=("independently confirmed by several sources",))
    assert package.dependence_summary == "INDEPENDENCE_SUPPORTED"


# --- Section 17.5 material omission audit ---

def omission_props():
    p1 = make_prop()
    p2 = make_prop(text="The ferry operator reported record passenger numbers.",
                   modality="REPORTED", qualifications=("REPORTED",),
                   supporting_claim_ids=("claim-2",), supporting_span_ids=("cand-2",))
    p3 = make_prop(text="A second terminal phase was proposed by the regional council.",
                   modality="PROPOSED", qualifications=("PROPOSED",),
                   supporting_claim_ids=("claim-3",), supporting_span_ids=("cand-3",))
    return p1, p2, p3


def test_omission_audit_assigns_disposition_to_every_proposition():
    p1, p2, p3 = omission_props()
    audit = material_omission_audit(
        (p1, p2, p3), (p1.proposition_id,),
        {p2.proposition_id: "NOT_MATERIAL", p3.proposition_id: "EVIDENCE_INSUFFICIENT"})
    assert set(audit.dispositions) == {p1.proposition_id, p2.proposition_id, p3.proposition_id}
    assert audit.dispositions[p1.proposition_id] == "PUBLISHED_WITH_QUALIFICATION"
    assert audit.dispositions[p2.proposition_id] == "OMITTED_NOT_MATERIAL"
    assert audit.dispositions[p3.proposition_id] == "OMITTED_EVIDENCE_INSUFFICIENT"
    assert audit.capability_failures == () and audit.published_count == 1 and audit.omitted_count == 2


def test_render_failure_omission_is_capability_failure():
    p1, p2, _ = omission_props()
    audit = material_omission_audit((p1, p2), (p1.proposition_id,),
                                    {p2.proposition_id: "RENDER_FAILURE"})
    assert audit.dispositions[p2.proposition_id] == "SYSTEM_CAPABILITY_FAILURE"
    outcome = audit.capability_failures[0]
    assert outcome.outcome == "SYSTEM_CAPABILITY_FAILURE"
    assert outcome.capability_failure_class == "PROPOSITION_RENDER_FAILURE"
    assert outcome.subject_kind == "REPORT_PROPOSITION"


def test_silent_omission_is_capability_failure_not_quiet_gap():
    p1, p2, _ = omission_props()
    audit = material_omission_audit((p1, p2), (p1.proposition_id,), {})
    assert audit.dispositions[p2.proposition_id] == "SYSTEM_CAPABILITY_FAILURE"
    assert audit.capability_failures[0].capability_failure_class == "PROPOSITION_RENDER_FAILURE"


def test_omission_audit_rejects_invalid_reasons_and_inconsistent_ledger():
    p1, p2, _ = omission_props()
    with pytest.raises(ValueError, match="invalid omission reasons"):
        material_omission_audit((p1, p2), (p1.proposition_id,),
                                {p2.proposition_id: "DID_NOT_FEEL_LIKE_IT"})
    with pytest.raises(ValueError, match="published proposition cannot"):
        material_omission_audit((p1, p2), (p1.proposition_id,),
                                {p1.proposition_id: "NOT_MATERIAL",
                                 p2.proposition_id: "NOT_MATERIAL"})
    with pytest.raises(ValueError, match="unknown propositions"):
        material_omission_audit((p1,), (p1.proposition_id,), {"prop-ghost": "NOT_MATERIAL"})


def test_omission_reason_categories_cover_only_contracted_dispositions():
    assert set(OMISSION_REASON_TO_DISPOSITION.values()) <= {
        "OMITTED_EVIDENCE_INSUFFICIENT", "OMITTED_NOT_MATERIAL", "OMITTED_ACCESS_RESTRICTED",
        "REFUSED_UNSUPPORTED", "SYSTEM_CAPABILITY_FAILURE"}
