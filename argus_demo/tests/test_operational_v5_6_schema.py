"""V5.6 — the dependency-consistent schema and the canonical object model.

Two modules are under test, and both exist because of the same V5.3 failure.

``curunir_operational.v5_6.schema`` makes cross-surface contradiction
*unrepresentable*.  The V5.3 panel answered "does the evidence support this
claim?" (Stage B) and "should this proposition be published?" (Stage E) as free
and independent questions.  Of 30 propositions adjudicated on both, 15
contradicted, four of them unanimously on both sides — the same three reviewers
holding 3-0 that the evidence does *not* support a claim and 3-0 that the
proposition built on it should be PUBLISHED.  Here Stage E is derived from
Stages B, C and D: the illegal pairs are not merely rejected on submission, they
are absent from the form and refused by the constructor.

``curunir_operational.v5_6.objects`` fixes the identity layer underneath that.
Every surface refers to one ``proposition_id`` / ``claim_id`` /
``evidence_bundle_id`` rather than being reconnected afterwards by sentence
matching, and "Vendor X states that System Y is operational" is a *separate
proposition* from "System Y is operational" — evidence that the statement was
made fully supports the first and says nothing about the second.

The tests below therefore assert two kinds of thing:

  * the property, over the full cross product, that no reviewer can express a
    contradiction; and
  * the specific historical failures, each named where it is encoded, so that a
    refactor cannot quietly reintroduce one.

Exception *messages* are asserted wherever the modules provide a distinctive
one, so a test cannot start passing for the wrong reason.
"""
from __future__ import annotations

import dataclasses
import itertools

import pytest

from curunir_operational.v5_6 import objects as OBJ
from curunir_operational.v5_6 import schema as SCH

pytestmark = pytest.mark.no_db


TIME = "2026-07-25T00:00:00+00:00"
SEAT = "REVIEWER_A"
PROP = "proposition-1"

#: The Stage-D permission used wherever a stage test is not about permissions.
#: ``DIRECT_FACTUAL_PUBLICATION`` is the neutral choice for Stage E: it trips
#: neither the "Stage D permitted no publication" nor the attributed-metaclaim
#: rule, so a failure in these tests is about the support/disposition pair.
NEUTRAL_PERMISSION = "DIRECT_FACTUAL_PUBLICATION"

NEGATIVE_WORDING_CLASSES = sorted(
    SCH.SPECIFIC_MISMATCH | {"NOT_SUPPORTED", "CONTRADICTED"})
FACTUAL = sorted(SCH.FACTUAL_DISPOSITIONS)


# ---------------------------------------------------------------------------
# Factories.  Each returns a valid record; every test states only its delta.
# ---------------------------------------------------------------------------

def stage_a(**overrides) -> SCH.StageAExtraction:
    kwargs = dict(
        candidate_id="candidate-1", seat_id=SEAT,
        invariants={"proposition_complete": "SATISFIED"},
        mapping_satisfied=("TEXT_LOCATABLE",),
        result="ACCEPTED_CANDIDATE", first_material_failure=None,
        bounded_context_would_repair=False, preferred_candidate_id=None,
        exact_quotation_permitted=False, paraphrase_only=False,
        reasoning="the span carries the whole proposition", recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StageAExtraction(**kwargs)


def stage_b(**overrides) -> SCH.StageBSupport:
    kwargs = dict(
        proposition_id=PROP, seat_id=SEAT, addresses_proposition=True,
        entity_alignment="ALIGNED", predicate_alignment="ALIGNED",
        object_alignment="ALIGNED", scope_alignment="ALIGNED",
        time_alignment="ALIGNED", polarity_alignment="ALIGNED",
        modality_alignment="ALIGNED", lifecycle_alignment="ALIGNED",
        attribution_alignment="ALIGNED", dependence_limitations=(),
        counterevidence_state="NONE_FOUND", support_completeness="COMPLETE",
        first_material_failure=None, support_class="FULL_SUPPORT",
        reasoning="the bundle carries every element of the proposition",
        recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StageBSupport(**kwargs)


def stage_c(**overrides) -> SCH.StageCQualification:
    kwargs = dict(
        proposition_id=PROP, seat_id=SEAT, support_class="FULL_SUPPORT",
        required=("NO_MATERIAL_QUALIFICATION",),
        reasoning="nothing may be removed that changes truth conditions",
        recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StageCQualification(**kwargs)


def stage_d(**overrides) -> SCH.StageDWording:
    kwargs = dict(
        proposition_id=PROP, seat_id=SEAT, support_class="FULL_SUPPORT",
        permission=NEUTRAL_PERMISSION,
        maximally_supported_wording="Agency X operates system Y.",
        minimally_qualified_wording="Agency X operates system Y.",
        prohibited_stronger_wording=(), strongest_permitted_verb="operates",
        permitted_lifecycle_term="OPERATIONAL", required_attribution=None,
        required_uncertainty=None, required_temporal_scope=None,
        required_dependence_qualification=None,
        reasoning="the evidence carries the operative verb", recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StageDWording(**kwargs)


def stage_e(**overrides) -> SCH.StageEPublication:
    kwargs = dict(
        proposition_id=PROP, seat_id=SEAT, support_class="FULL_SUPPORT",
        wording_permission=NEUTRAL_PERMISSION,
        required_qualifications=("NO_MATERIAL_QUALIFICATION",),
        required_context_present=True, disposition="PUBLISHED",
        published_wording="Agency X operates system Y.",
        reasoning="derived from B, C and D", recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StageEPublication(**kwargs)


def staged(**overrides) -> SCH.StagedAdjudication:
    kwargs = dict(
        adjudication_id="adjudication-1", proposition_id=PROP, seat_id=SEAT,
        protocol_version="V5_6_STAGED_1", stage_b=stage_b(), stage_c=stage_c(),
        stage_d=stage_d(), stage_e=stage_e(), recorded_time=TIME)
    kwargs.update(overrides)
    return SCH.StagedAdjudication(**kwargs)


def bundle(**overrides) -> OBJ.EvidenceBundleRecord:
    kwargs = dict(raw_evidence_ids=("raw-1",), document_context="page 3, table 2",
                  original_language_text="System Y is operational.")
    kwargs.update(overrides)
    return OBJ.evidence_bundle(**kwargs)


def proposition(**overrides) -> OBJ.CanonicalPropositionRecord:
    kwargs = dict(claim_id="claim-1", investigation_id="investigation-1",
                  subject="System Y", predicate="is", object_or_value="operational",
                  evidence_bundle_id="bundle-1")
    kwargs.update(overrides)
    return OBJ.canonical_proposition(**kwargs)


def proposition_of_kind(kind: str) -> OBJ.CanonicalPropositionRecord:
    """A minimal valid proposition of any kind, metaclaim fields included."""
    if kind == "ATTRIBUTED_METACLAIM":
        return proposition(kind=kind, subject="Vendor X", predicate="states that",
                           object_or_value="System Y is operational",
                           attribution="Vendor X",
                           metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED")
    return proposition(kind=kind)


def report_proposition(record: OBJ.CanonicalPropositionRecord,
                       **overrides) -> OBJ.ReportPropositionRecord:
    kwargs = dict(
        report_proposition_id="reportprop-1", report_sentence_id="sentence-1",
        sentence_text="Agency X operates system Y.",
        proposition_id=record.proposition_id, claim_id=record.claim_id,
        evidence_bundle_id=record.evidence_bundle_id, section="DETAILED_REPORT",
        asserts_underlying_fact=True, recorded_time=TIME)
    kwargs.update(overrides)
    return OBJ.ReportPropositionRecord(**kwargs)


# ===========================================================================
# 1.  The core property — cross-surface contradiction is unrepresentable.
# ===========================================================================

@pytest.mark.parametrize("support_class,disposition", list(itertools.product(
    SCH.SUPPORT_CLASSES, SCH.DISPOSITIONS)))
def test_stage_e_over_the_full_cross_product(support_class, disposition):
    """Every one of the 14x6 cells is either constructible or refused.

    The permitted branch is asserted, not skipped: "the contradiction cannot be
    built" is only half the property.  The other half is that every legal pair
    still builds, because a schema that refused everything would also pass a
    one-sided test.
    """
    permitted = SCH.permitted_dispositions(support_class)
    if disposition in permitted:
        record = stage_e(support_class=support_class, disposition=disposition,
                         wording_permission=NEUTRAL_PERMISSION)
        assert record.disposition == disposition
        assert record.support_class == support_class
        assert not SCH.contradicts(support_class, disposition)
        return
    with pytest.raises(SCH.SchemaViolation) as excinfo:
        stage_e(support_class=support_class, disposition=disposition,
                wording_permission=NEUTRAL_PERMISSION)
    assert f"support class {support_class} may not produce {disposition}" in \
        str(excinfo.value)


@pytest.mark.parametrize("support_class,disposition", list(itertools.product(
    sorted(SCH.NEVER_FACTUAL), FACTUAL)))
def test_a_never_factual_class_can_never_be_published(support_class, disposition):
    """The V5.3 failure itself: NOT_SUPPORTED + PUBLISHED and its whole family.

    Includes NOT_SUPPORTED, WRONG_ENTITY, WRONG_MODALITY, CONTRADICTED and
    INFERENCE_ONLY against both factual dispositions.
    """
    assert SCH.contradicts(support_class, disposition)
    with pytest.raises(SCH.SchemaViolation, match="may not produce"):
        stage_e(support_class=support_class, disposition=disposition)


@pytest.mark.parametrize("support_class", sorted(SCH.AFFIRMATIVE_SUPPORT))
def test_affirmative_support_can_never_be_rejected_as_unsupported(support_class):
    """The mirror-image contradiction: 3-0 supported, 3-0 rejected."""
    assert SCH.contradicts(support_class, "REJECTED_UNSUPPORTED")
    with pytest.raises(SCH.SchemaViolation, match="may not produce"):
        stage_e(support_class=support_class, disposition="REJECTED_UNSUPPORTED")


@pytest.mark.parametrize("support_class,disposition", [
    ("NOT_SUPPORTED", "PUBLISHED"),
    ("WRONG_ENTITY", "PUBLISHED"),
    ("WRONG_MODALITY", "PUBLISHED"),
    ("CONTRADICTED", "PUBLISHED"),
    ("INFERENCE_ONLY", "PUBLISHED"),
    ("INFERENCE_ONLY", "PUBLISHED_WITH_QUALIFICATION"),
    ("FULL_SUPPORT", "REJECTED_UNSUPPORTED"),
])
def test_the_named_v5_3_contradictions_are_not_even_offered(support_class,
                                                            disposition):
    """Not on the form, not merely refused: the disposition is not in the set."""
    assert disposition not in SCH.permitted_dispositions(support_class)


@pytest.mark.parametrize("support_class", sorted(SCH.NEVER_FACTUAL))
def test_no_never_factual_class_offers_any_factual_disposition(support_class):
    offered = set(SCH.permitted_dispositions(support_class))
    assert not (offered & SCH.FACTUAL_DISPOSITIONS)


@pytest.mark.parametrize("support_class", SCH.SUPPORT_CLASSES)
def test_every_support_class_offers_at_least_one_disposition(support_class):
    """A class with no outlet would push reviewers back into free judgment."""
    assert SCH.permitted_dispositions(support_class)


@pytest.mark.parametrize("support_class", SCH.SUPPORT_CLASSES)
def test_nothing_offered_is_a_contradiction(support_class):
    for disposition in SCH.permitted_dispositions(support_class):
        assert not SCH.contradicts(support_class, disposition), (
            f"{support_class} offers {disposition}, which contradicts it")


def test_permitted_dispositions_rejects_an_unknown_support_class():
    with pytest.raises(SCH.SchemaViolation, match="unknown support class"):
        SCH.permitted_dispositions("NEARLY_SUPPORTED")


def test_stage_e_rejects_an_unknown_disposition():
    with pytest.raises(SCH.SchemaViolation, match="unknown disposition"):
        stage_e(disposition="PUBLISHED_ANYWAY")


# --------------------------------------------------------------- the matrix
def test_illegal_combination_matrix_is_total_over_the_cross_product():
    matrix = SCH.illegal_combination_matrix()
    assert matrix["cells"] == len(SCH.SUPPORT_CLASSES) * len(SCH.DISPOSITIONS)
    assert len(matrix["matrix"]) == matrix["cells"]
    covered = {(row["support_class"], row["disposition"]) for row in matrix["matrix"]}
    assert covered == set(itertools.product(SCH.SUPPORT_CLASSES, SCH.DISPOSITIONS))


def test_illegal_combination_matrix_counts_are_self_consistent():
    matrix = SCH.illegal_combination_matrix()
    legal = sum(1 for row in matrix["matrix"] if row["legal"])
    assert legal + matrix["illegal_cells"] == matrix["cells"]
    assert matrix["contradiction_cells"] == sum(
        1 for row in matrix["matrix"] if row["is_cross_surface_contradiction"])


def test_every_matrix_row_agrees_with_the_constructor():
    """The published matrix and the runtime refusal must be the same rule."""
    for row in SCH.illegal_combination_matrix()["matrix"]:
        support_class, disposition = row["support_class"], row["disposition"]
        assert row["legal"] == (
            disposition in SCH.permitted_dispositions(support_class))


def test_every_contradiction_cell_is_also_an_illegal_cell():
    """Contradictions are a strict subset of the illegal cells."""
    rows = SCH.illegal_combination_matrix()["matrix"]
    contradictions = [r for r in rows if r["is_cross_surface_contradiction"]]
    assert contradictions, "the matrix must still name the V5.3 failure mode"
    assert all(not r["legal"] for r in contradictions)
    assert len(contradictions) < sum(1 for r in rows if not r["legal"])


def test_the_contradiction_specific_message_is_unreachable_by_construction():
    """Finding: the ``contradicts()`` guard in Stage E is defence in depth only.

    Every contradiction cell is already outside ``PERMITTED_DISPOSITIONS``, so
    the earlier "may not produce" check always fires first and the Section 14.2
    message is dead code.  Recorded rather than worked around: if a future edit
    widens a permitted set, this test flips and says so.
    """
    seen = set()
    for row in SCH.illegal_combination_matrix()["matrix"]:
        if not row["is_cross_surface_contradiction"]:
            continue
        with pytest.raises(SCH.SchemaViolation) as excinfo:
            stage_e(support_class=row["support_class"],
                    disposition=row["disposition"])
        seen.add("unrepresentable" in str(excinfo.value))
    assert seen == {False}


# ------------------------------------------------- Stage E's own wording rules
def test_stage_e_may_not_publish_what_stage_d_forbade():
    with pytest.raises(SCH.SchemaViolation, match="Stage D permitted no publication"):
        stage_e(wording_permission="NO_PUBLICATION_PERMITTED", disposition="PUBLISHED")


def test_publishing_a_metaclaim_requires_the_attribution_qualification():
    """Publishing "Vendor X states ..." without the attribution is the fact."""
    with pytest.raises(SCH.SchemaViolation, match="requires ATTRIBUTION_REQUIRED"):
        stage_e(wording_permission="ATTRIBUTED_METACLAIM_ONLY",
                required_qualifications=("VENDOR_REPORTED",),
                disposition="PUBLISHED")


def test_a_metaclaim_carrying_its_attribution_publishes():
    record = stage_e(wording_permission="ATTRIBUTED_METACLAIM_ONLY",
                     required_qualifications=("ATTRIBUTION_REQUIRED",),
                     disposition="PUBLISHED")
    assert record.disposition == "PUBLISHED"


@pytest.mark.parametrize("disposition", [
    "MOVED_TO_UNCERTAINTY_SECTION", "OMITTED_AS_IMMATERIAL"])
def test_no_publication_permitted_still_allows_the_non_factual_outlets(disposition):
    assert stage_e(wording_permission="NO_PUBLICATION_PERMITTED",
                   disposition=disposition).disposition == disposition


# ===========================================================================
# 2.  Stage dependency — the point of the milestone.
# ===========================================================================

def test_a_consistent_staged_adjudication_constructs():
    """Positive control: the dependency rules must not forbid a valid decision."""
    record = staged()
    assert record.stage_e.disposition == "PUBLISHED"
    assert record.stage_e.wording_permission == record.stage_d.permission
    assert tuple(record.stage_e.required_qualifications) == \
        tuple(record.stage_c.required)


@pytest.mark.parametrize("stage", ["stage_b", "stage_c", "stage_d", "stage_e"])
def test_stages_must_belong_to_one_seat(stage):
    """A composite of several seats' answers is not one seat's decision."""
    base = staged()
    other = dataclasses.replace(getattr(base, stage), seat_id="REVIEWER_B")
    with pytest.raises(SCH.SchemaViolation, match="stages belong to different seats"):
        staged(**{stage: other})


@pytest.mark.parametrize("stage,support_class,extra", [
    ("stage_c", "PARTIAL_SUPPORT", {}),
    ("stage_d", "PARTIAL_SUPPORT", {}),
    ("stage_e", "QUALIFIED_SUPPORT", {"disposition": "PUBLISHED_WITH_QUALIFICATION"}),
])
def test_later_stages_may_not_disagree_with_stage_b_about_support(
        stage, support_class, extra):
    """C, D and E are conditioned on B's verdict; they cannot re-decide it."""
    base = staged()
    other = dataclasses.replace(getattr(base, stage), support_class=support_class,
                                **extra)
    with pytest.raises(SCH.SchemaViolation, match="later stages disagree"):
        staged(**{stage: other})


@pytest.mark.parametrize("stage", ["stage_b", "stage_c", "stage_d", "stage_e"])
def test_stages_must_reference_the_same_proposition(stage):
    base = staged()
    other = dataclasses.replace(getattr(base, stage), proposition_id="proposition-2")
    with pytest.raises(SCH.SchemaViolation,
                       match="stages reference different propositions"):
        staged(**{stage: other})


def test_the_adjudication_proposition_id_must_match_its_stages():
    with pytest.raises(SCH.SchemaViolation,
                       match="stages reference different propositions"):
        staged(proposition_id="proposition-2")


@pytest.mark.parametrize("permission", [
    "ATTRIBUTED_METACLAIM_ONLY", "INFERENCE_OR_UNCERTAINTY_ONLY",
    "NO_PUBLICATION_PERMITTED"])
def test_stage_e_must_use_the_permission_stage_d_granted(permission):
    """Stage E cannot upgrade its own wording licence."""
    with pytest.raises(SCH.SchemaViolation,
                       match="wording permission must be the one Stage D granted"):
        staged(stage_d=stage_d(permission=permission))


def test_stage_e_may_not_invent_qualifications_stage_c_did_not_require():
    with pytest.raises(SCH.SchemaViolation,
                       match="exactly the qualifications Stage C required"):
        staged(stage_e=stage_e(required_qualifications=("VENDOR_REPORTED",)))


def test_stage_e_may_not_drop_a_qualification_stage_c_required():
    """Dropping a qualification is how a qualified claim becomes a flat one."""
    with pytest.raises(SCH.SchemaViolation,
                       match="exactly the qualifications Stage C required"):
        staged(stage_c=stage_c(required=("PILOT_ONLY", "PRELIMINARY")),
               stage_e=stage_e(required_qualifications=("PILOT_ONLY",)))


def test_qualification_order_is_part_of_the_contract():
    """Stage E must carry the required set *as Stage C recorded it*."""
    with pytest.raises(SCH.SchemaViolation,
                       match="exactly the qualifications Stage C required"):
        staged(stage_c=stage_c(required=("PILOT_ONLY", "PRELIMINARY")),
               stage_e=stage_e(required_qualifications=("PRELIMINARY", "PILOT_ONLY")))


def test_a_fully_qualified_chain_constructs_end_to_end():
    """The realistic case: partial support, published with its qualifications."""
    required = ("PILOT_ONLY", "PARTIAL_SCOPE")
    record = staged(
        stage_b=stage_b(support_class="PARTIAL_SUPPORT",
                        scope_alignment="MISALIGNED"),
        stage_c=stage_c(support_class="PARTIAL_SUPPORT", required=required),
        stage_d=stage_d(support_class="PARTIAL_SUPPORT",
                        permission="INFERENCE_OR_UNCERTAINTY_ONLY"),
        stage_e=stage_e(support_class="PARTIAL_SUPPORT",
                        wording_permission="INFERENCE_OR_UNCERTAINTY_ONLY",
                        required_qualifications=required,
                        disposition="PUBLISHED_WITH_QUALIFICATION"))
    assert record.stage_e.disposition == "PUBLISHED_WITH_QUALIFICATION"
    assert record.stage_e.required_qualifications == required


def test_the_stage_order_is_declared():
    assert SCH.STAGES[0].startswith("A_")
    assert SCH.STAGES[-1].startswith("E_")
    assert len(SCH.STAGES) == 5


# ===========================================================================
# 3.  Stage A — extraction validity.
# ===========================================================================

@pytest.mark.parametrize("dimension", SCH.INVARIANT_DIMENSIONS)
def test_every_invariant_dimension_is_accepted(dimension):
    assert stage_a(invariants={dimension: "SATISFIED"}).invariants[dimension] == \
        "SATISFIED"


@pytest.mark.parametrize("label", SCH.INVARIANT_LABELS)
def test_every_invariant_label_is_accepted(label):
    record = stage_a(invariants={"polarity_preserved": label},
                     result="QUARANTINED")
    assert record.invariants["polarity_preserved"] == label


def test_an_unknown_invariant_dimension_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown invariant dimensions"):
        stage_a(invariants={"vibes_preserved": "SATISFIED"})


def test_an_invalid_invariant_label_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="invalid invariant labels"):
        stage_a(invariants={"polarity_preserved": "PROBABLY_FINE"})


@pytest.mark.parametrize("requirement", SCH.MAPPING_REQUIREMENTS)
def test_every_mapping_requirement_is_accepted(requirement):
    """§10.2 — five independent requirements, not one overloaded scalar."""
    record = stage_a(mapping_satisfied=(requirement,))
    assert record.mapping_satisfied == (requirement,)


def test_an_unknown_mapping_requirement_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown mapping requirement"):
        stage_a(mapping_satisfied=("MAPPING_GOOD_ENOUGH",))


def test_an_exact_quotation_needs_an_exactly_locatable_span():
    """§28 mutation 17.  A quoted figure that cannot be located exactly is the
    defect two milestones argued about as 'HTML is worse than PDF'."""
    with pytest.raises(SCH.SchemaViolation, match="EXACT_VALUE_QUOTABLE"):
        stage_a(exact_quotation_permitted=True,
                mapping_satisfied=("TEXT_LOCATABLE",
                                   "PROPOSITION_BOUNDARY_DEFENSIBLE"))


def test_an_exact_quotation_with_an_exact_mapping_is_permitted():
    record = stage_a(exact_quotation_permitted=True,
                     mapping_satisfied=("TEXT_LOCATABLE", "EXACT_VALUE_QUOTABLE"))
    assert record.exact_quotation_permitted


def test_a_candidate_cannot_be_quotable_and_paraphrase_only():
    with pytest.raises(SCH.SchemaViolation, match="quotable and paraphrase-only"):
        stage_a(exact_quotation_permitted=True, paraphrase_only=True,
                mapping_satisfied=("EXACT_VALUE_QUOTABLE",))


def test_paraphrase_only_alone_is_fine():
    """An approximate span can be perfectly adequate for a qualitative claim."""
    assert stage_a(paraphrase_only=True).paraphrase_only


def test_an_accepted_candidate_may_not_record_a_material_failure():
    with pytest.raises(SCH.SchemaViolation,
                       match="accepted candidate cannot record a material"):
        stage_a(result="ACCEPTED_CANDIDATE",
                first_material_failure="polarity_preserved")


@pytest.mark.parametrize("result", [r for r in SCH.EXTRACTION_RESULTS
                                    if r != "ACCEPTED_CANDIDATE"])
def test_a_non_accepted_result_may_record_the_first_material_failure(result):
    record = stage_a(result=result, first_material_failure="polarity_preserved")
    assert record.first_material_failure == "polarity_preserved"


@pytest.mark.parametrize("result", SCH.EXTRACTION_RESULTS)
def test_every_extraction_result_is_accepted(result):
    assert stage_a(result=result, first_material_failure=None).result == result


def test_an_unknown_extraction_result_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown extraction result"):
        stage_a(result="LOOKS_OK")


# ===========================================================================
# 4.  Stage B — claim support.
# ===========================================================================

@pytest.mark.parametrize("support_class", sorted(SCH.SPECIFIC_MISMATCH))
def test_a_specific_mismatch_requires_addressability_first(support_class):
    """§11.1 — "wrong entity" presupposes that the evidence is about the
    proposition family at all.  Answering it out of order is how an unrelated
    span gets a considered-looking verdict."""
    with pytest.raises(SCH.SchemaViolation, match="specific mismatch"):
        stage_b(support_class=support_class, addresses_proposition=False)


@pytest.mark.parametrize("support_class", sorted(SCH.SPECIFIC_MISMATCH))
def test_a_specific_mismatch_records_once_addressability_passes(support_class):
    record = stage_b(support_class=support_class, addresses_proposition=True,
                     first_material_failure="entity_alignment")
    assert record.support_class == support_class


@pytest.mark.parametrize("support_class", sorted(SCH.AFFIRMATIVE_SUPPORT))
def test_affirmative_support_may_not_record_a_material_failure(support_class):
    """If something material failed, the support is not affirmative."""
    with pytest.raises(SCH.SchemaViolation,
                       match="affirmative support cannot record a material failure"):
        stage_b(support_class=support_class,
                first_material_failure="time_alignment")


@pytest.mark.parametrize("field_name", [
    "entity_alignment", "predicate_alignment", "object_alignment",
    "scope_alignment", "time_alignment", "polarity_alignment",
    "modality_alignment", "lifecycle_alignment", "attribution_alignment"])
def test_an_invalid_alignment_value_is_refused(field_name):
    with pytest.raises(SCH.SchemaViolation, match=f"{field_name} must be one of"):
        stage_b(**{field_name: "MOSTLY_ALIGNED"})


@pytest.mark.parametrize("value", SCH.ALIGNMENT_VALUES)
def test_every_alignment_value_is_accepted(value):
    record = stage_b(support_class="PARTIAL_SUPPORT", entity_alignment=value)
    assert record.entity_alignment == value


@pytest.mark.parametrize("support_class", SCH.SUPPORT_CLASSES)
def test_every_support_class_is_constructible(support_class):
    assert stage_b(support_class=support_class).support_class == support_class


def test_an_unknown_support_class_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown support class"):
        stage_b(support_class="BROADLY_SUPPORTED")


def test_the_support_question_order_is_declared_and_starts_with_addressability():
    assert SCH.SUPPORT_QUESTION_ORDER[0] == "addresses_proposition_family"


@pytest.mark.parametrize("strengthening", SCH.FORBIDDEN_STRENGTHENINGS)
def test_the_forbidden_strengthenings_are_named(strengthening):
    """§11.3 — plan-as-implementation and friends, enumerated so a reviewer
    form can show them rather than leaving them to judgment."""
    assert strengthening in SCH.FORBIDDEN_STRENGTHENINGS
    assert strengthening.islower()


# ===========================================================================
# 5.  Stage C — required qualification.
# ===========================================================================

@pytest.mark.parametrize("dimension", SCH.QUALIFICATION_DIMENSIONS)
def test_every_qualification_dimension_is_accepted_alone(dimension):
    assert stage_c(required=(dimension,)).required == (dimension,)


def test_inference_only_support_always_requires_inference_marking():
    with pytest.raises(SCH.SchemaViolation,
                       match="INFERENCE_ONLY support requires INFERENCE_MARKING_REQUIRED"):
        stage_c(support_class="INFERENCE_ONLY", required=("UNCERTAINTY_REQUIRED",))


def test_inference_only_with_inference_marking_constructs():
    record = stage_c(support_class="INFERENCE_ONLY",
                     required=("INFERENCE_MARKING_REQUIRED", "UNCERTAINTY_REQUIRED"))
    assert "INFERENCE_MARKING_REQUIRED" in record.required


@pytest.mark.parametrize("other", [d for d in SCH.QUALIFICATION_DIMENSIONS
                                   if d != "NO_MATERIAL_QUALIFICATION"])
def test_no_material_qualification_is_exclusive(other):
    """"Nothing material is required" and "this is required" cannot both hold."""
    with pytest.raises(SCH.SchemaViolation,
                       match="NO_MATERIAL_QUALIFICATION cannot be combined"):
        stage_c(required=("NO_MATERIAL_QUALIFICATION", other))


def test_an_unknown_qualification_dimension_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown qualification dimensions"):
        stage_c(required=("SOUNDS_FINE",))


def test_an_empty_qualification_set_is_permitted():
    """Distinct from NO_MATERIAL_QUALIFICATION: nothing has been asserted yet."""
    assert stage_c(required=()).required == ()


# ===========================================================================
# 6.  Stage D — permitted factual wording.
# ===========================================================================

@pytest.mark.parametrize("support_class", NEGATIVE_WORDING_CLASSES)
def test_a_negative_support_class_may_not_permit_direct_publication(support_class):
    """The wording contract is where the V5.3 contradiction would re-enter:
    a negative support class that licenses flat factual prose."""
    with pytest.raises(SCH.SchemaViolation,
                       match="may not permit direct factual publication"):
        stage_d(support_class=support_class,
                permission="DIRECT_FACTUAL_PUBLICATION")


def test_inference_only_may_not_permit_unmarked_direct_publication():
    with pytest.raises(SCH.SchemaViolation,
                       match="may not permit unmarked direct factual publication"):
        stage_d(support_class="INFERENCE_ONLY",
                permission="DIRECT_FACTUAL_PUBLICATION")


@pytest.mark.parametrize("support_class,permission", list(itertools.product(
    NEGATIVE_WORDING_CLASSES,
    [p for p in SCH.WORDING_PERMISSIONS if p != "DIRECT_FACTUAL_PUBLICATION"])))
def test_a_negative_class_may_still_grant_a_qualified_permission(support_class,
                                                                 permission):
    """Negative support is not silence: attribution and uncertainty remain."""
    assert stage_d(support_class=support_class,
                   permission=permission).permission == permission


@pytest.mark.parametrize("permission", SCH.WORDING_PERMISSIONS)
def test_full_support_may_take_any_wording_permission(permission):
    assert stage_d(permission=permission).permission == permission


def test_an_unknown_wording_permission_is_refused():
    with pytest.raises(SCH.SchemaViolation, match="unknown wording permission"):
        stage_d(permission="PUBLISH_IF_IT_READS_WELL")


def test_stage_d_carries_the_prohibited_stronger_wordings():
    record = stage_d(support_class="QUALIFIED_SUPPORT",
                     permission="INFERENCE_OR_UNCERTAINTY_ONLY",
                     prohibited_stronger_wording=("has deployed", "operates"),
                     strongest_permitted_verb="has piloted",
                     permitted_lifecycle_term="PILOTED")
    assert "operates" in record.prohibited_stronger_wording


# ===========================================================================
# 7.  Context-dependent support.
# ===========================================================================

def test_context_dependent_support_publishes_when_the_context_is_present():
    record = stage_e(support_class="CONTEXT_DEPENDENT_SUPPORT",
                     required_context_present=True,
                     disposition="PUBLISHED_WITH_QUALIFICATION")
    assert record.disposition == "PUBLISHED_WITH_QUALIFICATION"


def test_context_dependent_support_may_not_publish_without_the_context():
    """The qualification only works if it is in the same section or sentence."""
    with pytest.raises(SCH.SchemaViolation, match="may not produce"):
        stage_e(support_class="CONTEXT_DEPENDENT_SUPPORT",
                required_context_present=False,
                disposition="PUBLISHED_WITH_QUALIFICATION")


def test_context_dependent_support_keeps_its_non_factual_outlets():
    permitted = SCH.permitted_dispositions("CONTEXT_DEPENDENT_SUPPORT",
                                           required_context_present=False)
    assert "MOVED_TO_UNCERTAINTY_SECTION" in permitted
    assert "PUBLISHED_WITH_QUALIFICATION" not in permitted


@pytest.mark.parametrize("support_class", [c for c in SCH.SUPPORT_CLASSES
                                           if c != "CONTEXT_DEPENDENT_SUPPORT"])
def test_the_context_flag_affects_only_context_dependent_support(support_class):
    assert SCH.permitted_dispositions(support_class, required_context_present=False) \
        == SCH.permitted_dispositions(support_class, required_context_present=True)


# ===========================================================================
# 8.  objects.py — canonical identity.
# ===========================================================================

def test_a_fact_and_its_metaclaim_are_two_propositions():
    """"Vendor X states that System Y is operational" is not "System Y is
    operational".  Evidence that the statement was made supports only the
    first; V5.3 had one label for both."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1",
        speaker="Vendor X", subject="System Y", predicate="is",
        object_or_value="operational", evidence_bundle_id="bundle-1")
    assert fact.proposition_id != meta.proposition_id
    assert meta.underlying_proposition_id == fact.proposition_id
    assert fact.underlying_proposition_id is None
    assert meta.kind == "ATTRIBUTED_METACLAIM"
    assert fact.kind == "UNDERLYING_FACT_CLAIM"
    assert meta.attribution == "Vendor X"
    assert meta.metaclaim_assertion == "THE_EXTERNAL_STATEMENT_OCCURRED"


def test_the_split_pair_shares_the_claim_and_the_evidence_bundle():
    """Separate propositions, one evidence bundle: the seats see the same thing."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    assert fact.claim_id == meta.claim_id == "claim-1"
    assert fact.evidence_bundle_id == meta.evidence_bundle_id == "bundle-1"


def test_the_metaclaim_restates_the_underlying_proposition_in_its_object():
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    assert meta.subject == "Vendor X"
    assert meta.object_or_value == "System Y is operational"
    assert fact.subject == "System Y"


def test_shared_fields_propagate_to_both_halves_of_the_split():
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1", geographic_scope=("EU",),
        dependence_state="INDEPENDENCE_SUPPORTED")
    assert fact.geographic_scope == meta.geographic_scope == ("EU",)
    assert fact.dependence_state == meta.dependence_state == "INDEPENDENCE_SUPPORTED"


def test_a_metaclaim_must_name_its_speaker():
    with pytest.raises(OBJ.IdentityViolation, match="must name who made the statement"):
        proposition(kind="ATTRIBUTED_METACLAIM", attribution=None,
                    metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED")


def test_a_metaclaim_must_declare_what_it_asserts():
    """Occurred, or true?  The two need different evidence entirely."""
    with pytest.raises(OBJ.IdentityViolation, match="OCCURRED or that it is TRUE"):
        proposition(kind="ATTRIBUTED_METACLAIM", attribution="Vendor X",
                    metaclaim_assertion=None)


def test_a_metaclaim_may_not_declare_an_unknown_assertion():
    with pytest.raises(OBJ.IdentityViolation, match="OCCURRED or that it is TRUE"):
        proposition(kind="ATTRIBUTED_METACLAIM", attribution="Vendor X",
                    metaclaim_assertion="THE_EXTERNAL_STATEMENT_IS_PLAUSIBLE")


@pytest.mark.parametrize("assertion", OBJ.METACLAIM_ASSERTIONS)
def test_both_metaclaim_assertions_are_accepted(assertion):
    record = proposition(kind="ATTRIBUTED_METACLAIM", attribution="Vendor X",
                         metaclaim_assertion=assertion)
    assert record.is_metaclaim
    assert record.metaclaim_assertion == assertion


@pytest.mark.parametrize("kind", [k for k in OBJ.PROPOSITION_KINDS
                                  if k != "ATTRIBUTED_METACLAIM"])
def test_a_non_metaclaim_may_not_carry_a_metaclaim_assertion(kind):
    with pytest.raises(OBJ.IdentityViolation, match="may not carry a"):
        proposition(kind=kind,
                    metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED")


@pytest.mark.parametrize("kind", [k for k in OBJ.PROPOSITION_KINDS
                                  if k != "ATTRIBUTED_METACLAIM"])
def test_a_non_metaclaim_may_not_point_at_an_underlying_proposition(kind):
    with pytest.raises(OBJ.IdentityViolation,
                       match="may not point at an underlying proposition"):
        proposition(kind=kind, underlying_proposition_id="proposition-9")


@pytest.mark.parametrize("kind", OBJ.PROPOSITION_KINDS)
def test_every_proposition_kind_is_constructible(kind):
    assert proposition_of_kind(kind).kind == kind


def test_an_unknown_proposition_kind_is_refused():
    with pytest.raises(OBJ.IdentityViolation, match="unknown proposition kind"):
        proposition(kind="VIBE")


def test_a_proposition_requires_an_evidence_bundle():
    with pytest.raises(OBJ.IdentityViolation, match="requires an evidence bundle"):
        proposition(evidence_bundle_id="")


def test_identity_covers_the_attribution():
    """Same words, different speaker, different proposition."""
    a = proposition(kind="ATTRIBUTED_METACLAIM", attribution="Vendor X",
                    metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED")
    b = proposition(kind="ATTRIBUTED_METACLAIM", attribution="Vendor Z",
                    metaclaim_assertion="THE_EXTERNAL_STATEMENT_OCCURRED")
    assert a.proposition_id != b.proposition_id


# ------------------------------------------------------- fact/metaclaim audit
def test_the_separation_audit_passes_on_a_clean_pair():
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    audit = OBJ.audit_fact_metaclaim_separation([fact, meta])
    assert audit["verdict"] == "PASS"
    assert audit["metaclaims"] == 1
    assert audit["underlying_facts_present"] == 1


def test_the_separation_audit_catches_a_dangling_underlying_reference():
    """The metaclaim survived a merge and the fact it points at did not."""
    _, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    audit = OBJ.audit_fact_metaclaim_separation([meta])
    assert audit["dangling_underlying_references"] == 1
    assert audit["underlying_facts_present"] == 0
    assert audit["verdict"] == "FAIL"


def test_the_separation_audit_catches_an_unattributed_metaclaim():
    """The audit is a second line of defence over records the constructor never
    saw — a legacy store, a replayed corpus — so it is exercised against an
    instance mutated past the frozen dataclass, not one built by hand."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    object.__setattr__(meta, "attribution", "")
    audit = OBJ.audit_fact_metaclaim_separation([fact, meta])
    assert audit["unattributed_metaclaims"] == 1
    assert audit["verdict"] == "FAIL"


def test_the_separation_audit_catches_an_identity_collision():
    """The V5.3 shape exactly: one identity carrying both propositions."""
    _, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    object.__setattr__(meta, "underlying_proposition_id", meta.proposition_id)
    audit = OBJ.audit_fact_metaclaim_separation([meta])
    assert audit["identity_collisions"] == 1
    assert audit["verdict"] == "FAIL"


def test_the_separation_audit_is_vacuously_clean_without_metaclaims():
    audit = OBJ.audit_fact_metaclaim_separation([proposition()])
    assert audit["metaclaims"] == 0
    assert audit["verdict"] == "PASS"


# --------------------------------------------------------- shared-identity audit
def test_the_shared_identity_audit_passes_on_a_consistent_set():
    fact = proposition()
    records = OBJ.decompose_sentence(
        report_sentence_id="sentence-1", sentence_text="System Y is operational.",
        propositions=[fact])
    audit = OBJ.audit_shared_identity([fact], records)
    assert audit["verdict"] == "PASS"
    assert audit["propositions"] == 1
    assert audit["examples"] == []


def test_the_shared_identity_audit_catches_an_orphan_report_proposition():
    """A report sentence labelled against a proposition nobody adjudicated."""
    fact = proposition()
    orphan = report_proposition(fact, proposition_id="proposition-not-in-the-set")
    audit = OBJ.audit_shared_identity([fact], [orphan])
    assert audit["orphan_report_propositions"] == 1
    assert audit["verdict"] == "FAIL"
    assert orphan.report_proposition_id in audit["examples"]


def test_the_shared_identity_audit_catches_an_evidence_bundle_mismatch():
    """Same proposition, different evidence: the seats were not shown one bundle."""
    fact = proposition()
    drifted = report_proposition(fact, evidence_bundle_id="bundle-2")
    audit = OBJ.audit_shared_identity([fact], [drifted])
    assert audit["evidence_bundle_mismatches"] == 1
    assert audit["verdict"] == "FAIL"


def test_the_shared_identity_audit_catches_a_claim_id_mismatch():
    fact = proposition()
    drifted = report_proposition(fact, claim_id="claim-2")
    audit = OBJ.audit_shared_identity([fact], [drifted])
    assert audit["claim_id_mismatches"] == 1
    assert audit["verdict"] == "FAIL"


def test_the_shared_identity_audit_reports_its_own_denominator():
    """Regression: ``report_propositions`` was hard-wired to ``None`` by an
    ``if False`` guard, so the audit never said how many records it checked — a
    silent ``None`` reads too easily as a count of zero.  It now reports the
    real count, and the iterable is materialised so that consuming it twice
    cannot yield nothing on the second pass."""
    fact = proposition()
    audit = OBJ.audit_shared_identity([fact], [report_proposition(fact)])
    assert audit["report_propositions"] == 1
    assert audit["propositions"] == 1


# --------------------------------------------------------------- report layer
def test_a_report_proposition_must_reference_one_canonical_proposition():
    """No fuzzy sentence matching: that is how a label came to govern something
    other than the object it was recorded against."""
    with pytest.raises(OBJ.IdentityViolation, match="fuzzy sentence matching"):
        report_proposition(proposition(), proposition_id="")


def test_decompose_sentence_produces_one_record_per_proposition():
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    records = OBJ.decompose_sentence(
        report_sentence_id="sentence-1",
        sentence_text="Vendor X states that System Y is operational.",
        propositions=[fact, meta])
    assert len(records) == 2
    assert {r.proposition_id for r in records} == \
        {fact.proposition_id, meta.proposition_id}
    assert len({r.report_proposition_id for r in records}) == 2
    assert {r.report_sentence_id for r in records} == {"sentence-1"}


def test_decompose_sentence_marks_the_fact_and_not_the_metaclaim():
    """The sentence asserts that the statement was made; it does not assert the
    underlying fact, and the flag is what keeps those apart downstream."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    by_id = {r.proposition_id: r for r in OBJ.decompose_sentence(
        report_sentence_id="sentence-1",
        sentence_text="Vendor X states that System Y is operational.",
        propositions=[fact, meta])}
    assert by_id[fact.proposition_id].asserts_underlying_fact is True
    assert by_id[meta.proposition_id].asserts_underlying_fact is False


@pytest.mark.parametrize("kind", OBJ.PROPOSITION_KINDS)
def test_decompose_sentence_sets_the_factual_flag_per_kind(kind):
    record = proposition_of_kind(kind)
    decomposed, = OBJ.decompose_sentence(
        report_sentence_id="sentence-1", sentence_text="text",
        propositions=[record])
    assert decomposed.asserts_underlying_fact == (kind in OBJ.DIRECT_FACTUAL_KINDS)


def test_decompose_sentence_copies_the_canonical_identifiers():
    fact = proposition()
    decomposed, = OBJ.decompose_sentence(
        report_sentence_id="sentence-1", sentence_text="System Y is operational.",
        propositions=[fact], section="EXECUTIVE_SUMMARY")
    assert decomposed.claim_id == fact.claim_id
    assert decomposed.evidence_bundle_id == fact.evidence_bundle_id
    assert decomposed.section == "EXECUTIVE_SUMMARY"


def test_decompose_sentence_on_no_propositions_yields_nothing():
    assert OBJ.decompose_sentence(report_sentence_id="s", sentence_text="t",
                                  propositions=[]) == ()


# ----------------------------------------------------------------- deduplicate
def test_deduplicate_collapses_by_canonical_identity():
    """Two extractions of the same proposition are one proposition."""
    first, second = proposition(), proposition()
    assert first.proposition_id == second.proposition_id
    assert len(OBJ.deduplicate([first, second])) == 1


def test_deduplicate_does_not_collapse_a_fact_into_its_metaclaim():
    """The metaclaim's text contains the fact's verbatim; identity keeps them
    apart where text matching would not."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="claim-1", investigation_id="investigation-1", speaker="Vendor X",
        subject="System Y", predicate="is", object_or_value="operational",
        evidence_bundle_id="bundle-1")
    assert "System Y is operational" in meta.object_or_value
    assert len(OBJ.deduplicate([fact, meta])) == 2


def test_deduplicate_keeps_propositions_that_differ_only_in_scope():
    a = proposition(subject="System Y")
    b = proposition(subject="System Y (pilot deployment)")
    assert len(OBJ.deduplicate([a, b])) == 2


def test_deduplicate_keeps_the_first_record_of_an_identity():
    """First writer wins, so deduplication is deterministic on replay."""
    first, second = proposition(), proposition()
    kept, = OBJ.deduplicate([first, second])
    assert kept is first


def test_deduplicate_on_an_empty_input_is_empty():
    assert OBJ.deduplicate([]) == ()


# ------------------------------------------------------------ evidence bundles
def test_an_evidence_bundle_requires_raw_evidence():
    with pytest.raises(OBJ.IdentityViolation, match="at least one raw record"):
        bundle(raw_evidence_ids=())


def test_a_translation_must_declare_its_language():
    """Original and translation are shown separately and never merged."""
    with pytest.raises(OBJ.IdentityViolation, match="must declare its language"):
        bundle(translation_text="System Y is operational.", translation_language=None)


def test_a_declared_translation_is_accepted():
    record = bundle(translation_text="System Y is operational.",
                    translation_language="en")
    assert record.translation_language == "en"


def test_the_integrity_hash_is_stable_across_reconstruction():
    """Two seats reconstructing the same bundle must agree it is the same one;
    the hash excludes the recording timestamp for exactly that reason."""
    assert bundle().integrity_hash == bundle().integrity_hash


@pytest.mark.parametrize("change", [
    {"raw_evidence_ids": ("raw-1", "raw-2")},
    {"document_context": "page 4, table 1"},
    {"counterevidence": ("counter-1",)},
    {"correction_or_supersession_context": ("correction-1",)},
    {"source_dependence_state": "DERIVATIVE_CONFIRMED"},
    {"exact_mapping_information": {"page": 4}},
    {"original_language": "de"},
    {"original_language_text": "System Y ist einsatzbereit."},
    {"normalized_observation_ids": ("observation-1",)},
])
def test_the_integrity_hash_changes_when_the_content_changes(change):
    assert bundle(**change).integrity_hash != bundle().integrity_hash


def test_the_integrity_hash_is_order_insensitive_for_id_sets():
    """Evidence IDs are a set; their listing order is not content."""
    a = bundle(raw_evidence_ids=("raw-1", "raw-2"))
    b = bundle(raw_evidence_ids=("raw-2", "raw-1"))
    assert a.integrity_hash == b.integrity_hash


def test_title_context_is_covered_by_the_integrity_hash():
    """Regression: ``title_and_metadata_context`` was omitted from
    ``integrity_hash``, so a bundle whose title or metadata context changed
    hashed identically.  §7.3 lists that context as part of what every seat
    sees, so it is evidence and must be covered."""
    assert bundle(title_and_metadata_context="Annual Report 2026 (draft)") \
        .integrity_hash != bundle().integrity_hash


def test_the_declared_translation_language_is_covered_by_the_integrity_hash():
    """Regression: the translation *text* was hashed but its declared language
    was not, so re-labelling a translation from English to German left the hash
    unchanged.  That field is precisely what keeps an original and its
    translation distinct — the rule the bundle refuses to merge them over."""
    english = bundle(translation_text="System Y is operational.",
                     translation_language="en")
    relabelled = bundle(translation_text="System Y is operational.",
                        translation_language="de")
    assert english.integrity_hash != relabelled.integrity_hash


def test_bundle_identity_is_content_addressed_but_narrower_than_the_hash():
    """The bundle ID is derived from the raw IDs and the opening text only, so
    two bundles differing in document context share an ID.  The integrity hash
    is what distinguishes them, which is why both exist."""
    a = bundle()
    b = bundle(document_context="page 4, table 1")
    assert a.evidence_bundle_id == b.evidence_bundle_id
    assert a.integrity_hash != b.integrity_hash


# --------------------------------------------------------- extraction candidates
def test_an_extraction_candidate_requires_a_raw_span():
    with pytest.raises(OBJ.IdentityViolation, match="requires a raw span"):
        OBJ.canonical_candidate(source_id="source-1", source_family_id="family-1",
                                raw_span="   ")


def test_a_candidate_may_not_compete_with_itself():
    candidate = OBJ.canonical_candidate(
        source_id="source-1", source_family_id="family-1",
        raw_span="System Y is operational.")
    with pytest.raises(OBJ.IdentityViolation, match="may not compete with itself"):
        dataclasses.replace(
            candidate, competing_candidate_ids=(candidate.candidate_id,))


def test_a_candidate_defaults_its_normalized_span_to_the_raw_span():
    candidate = OBJ.canonical_candidate(
        source_id="source-1", source_family_id="family-1",
        raw_span="System Y is operational.")
    assert candidate.normalized_span == candidate.raw_span
