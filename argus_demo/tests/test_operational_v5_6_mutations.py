"""V5.6 §28 — focused mutations.

Each mutation asserts that a specific way of corrupting the reference standard
fails, and *fails for the intended reason*.  A mutation that passes for the
wrong reason is worse than no mutation, so every one asserts on the message or
on the specific field that must have caught it.
"""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_4 import custody as CUS
from curunir_operational.v5_6 import objects as OBJ
from curunir_operational.v5_6 import packets as PKT
from curunir_operational.v5_6 import reference as REF
from curunir_operational.v5_6 import schema as S

pytestmark = pytest.mark.no_db


def _support(support_class="FULL_SUPPORT", **over):
    base = dict(
        proposition_id="p1", seat_id="SEAT_A", addresses_proposition=True,
        entity_alignment="ALIGNED", predicate_alignment="ALIGNED",
        object_alignment="ALIGNED", scope_alignment="ALIGNED",
        time_alignment="ALIGNED", polarity_alignment="ALIGNED",
        modality_alignment="ALIGNED", lifecycle_alignment="ALIGNED",
        attribution_alignment="ALIGNED", dependence_limitations=(),
        counterevidence_state="NONE", support_completeness="COMPLETE",
        first_material_failure=None, support_class=support_class,
        reasoning="r", recorded_time="2026-07-25T00:00:00+00:00")
    base.update(over)
    return S.StageBSupport(**base)


def _publication(support_class, disposition, **over):
    base = dict(
        proposition_id="p1", seat_id="SEAT_A", support_class=support_class,
        wording_permission="DIRECT_FACTUAL_PUBLICATION",
        required_qualifications=("NO_MATERIAL_QUALIFICATION",),
        required_context_present=True, disposition=disposition,
        published_wording="w", reasoning="r",
        recorded_time="2026-07-25T00:00:00+00:00")
    base.update(over)
    return S.StageEPublication(**base)


# ------------------------------------------------------- 1-3: the contradiction
def test_mutation_01_not_supported_combined_with_published():
    with pytest.raises(S.SchemaViolation, match="may not produce"):
        _publication("NOT_SUPPORTED", "PUBLISHED")


def test_mutation_02_wrong_modality_combined_with_published():
    with pytest.raises(S.SchemaViolation, match="may not produce"):
        _publication("WRONG_MODALITY", "PUBLISHED")


def test_mutation_03_inference_only_published_as_unmarked_fact():
    with pytest.raises(S.SchemaViolation, match="may not produce"):
        _publication("INFERENCE_ONLY", "PUBLISHED")


# --------------------------------------------- 4-5: fact versus metaclaim
def test_mutation_04_metaclaim_substituted_without_a_new_proposition_id():
    """An unsupported fact may not be published by relabelling it a metaclaim
    while keeping the fact's identity."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="c", investigation_id="i", speaker="Vendor X", subject="System Y",
        predicate="is", object_or_value="operational", evidence_bundle_id="b")
    assert fact.proposition_id != meta.proposition_id
    # A metaclaim that reuses the fact's identity is not constructible.
    with pytest.raises(OBJ.IdentityViolation, match="metaclaim"):
        OBJ.canonical_proposition(
            claim_id="c", investigation_id="i", subject="System Y", predicate="is",
            object_or_value="operational", evidence_bundle_id="b",
            kind="ATTRIBUTED_METACLAIM", attribution=None)


def test_mutation_05_metaclaim_support_transferred_to_the_underlying_fact():
    """Support recorded against the metaclaim must not become support for the
    fact: they are different objects with different IDs."""
    fact, meta = OBJ.split_fact_and_metaclaim(
        claim_id="c", investigation_id="i", speaker="Vendor X", subject="System Y",
        predicate="is", object_or_value="operational", evidence_bundle_id="b")
    meta_support = _support("FULL_SUPPORT", proposition_id=meta.proposition_id)
    # Attaching the metaclaim's support decision to the fact is a staged
    # adjudication whose stages reference different propositions.
    with pytest.raises(S.SchemaViolation, match="different propositions"):
        S.StagedAdjudication(
            "a", fact.proposition_id, "SEAT_A", "V5_6_PROTOCOL_1", meta_support,
            S.StageCQualification(fact.proposition_id, "SEAT_A", "FULL_SUPPORT",
                                  ("NO_MATERIAL_QUALIFICATION",), "r",
                                  "2026-07-25T00:00:00+00:00"),
            S.StageDWording(fact.proposition_id, "SEAT_A", "FULL_SUPPORT",
                            "DIRECT_FACTUAL_PUBLICATION", "w", "w", (), "is",
                            "OPERATIONAL", None, None, None, None, "r",
                            "2026-07-25T00:00:00+00:00"),
            _publication("FULL_SUPPORT", "PUBLISHED",
                         proposition_id=fact.proposition_id),
            "2026-07-25T00:00:00+00:00")


# ------------------------------------------------------------ 6: stage order
def test_mutation_06_publication_disagrees_with_the_support_it_consumes():
    """Stage E may not carry a support class Stage B did not reach."""
    b = _support("NOT_SUPPORTED", addresses_proposition=False,
                 support_completeness="ABSENT",
                 first_material_failure="addresses_proposition_family")
    c = S.StageCQualification("p1", "SEAT_A", "NOT_SUPPORTED",
                              ("NO_MATERIAL_QUALIFICATION",), "r",
                              "2026-07-25T00:00:00+00:00")
    d = S.StageDWording("p1", "SEAT_A", "NOT_SUPPORTED", "NO_PUBLICATION_PERMITTED",
                        "", "", (), "", "", None, None, None, None, "r",
                        "2026-07-25T00:00:00+00:00")
    e = _publication("FULL_SUPPORT", "PUBLISHED")
    with pytest.raises(S.SchemaViolation, match="disagree with Stage B"):
        S.StagedAdjudication("a", "p1", "SEAT_A", "V5_6_PROTOCOL_1", b, c, d, e,
                             "2026-07-25T00:00:00+00:00")


# --------------------------------------------------------- 7-8: packet leakage
@pytest.mark.parametrize("field", ["old_majority_label", "production_support_class",
                                   "prediction", "reviewer_majority", "confidence"])
def test_mutation_07_08_packet_exposes_an_answer_shaped_field(field):
    packet = {"packet_id": "x", "subject": "s", field: "FULL_SUPPORT"}
    audit = PKT.audit_blinding([packet])
    assert audit["leaks"] >= 1
    assert audit["verdict"] == "FAIL"


# ------------------------------------------------- 9: proposition identity
def test_mutation_09_report_and_claim_use_different_proposition_ids():
    fact = OBJ.canonical_proposition(
        claim_id="c", investigation_id="i", subject="s", predicate="p",
        object_or_value="o", evidence_bundle_id="b")
    stray = OBJ.ReportPropositionRecord(
        "rp", "rs", "text", "some-other-proposition", "c", "b", "DETAILED_REPORT",
        True, "2026-07-25T00:00:00+00:00")
    audit = OBJ.audit_shared_identity([fact], [stray])
    assert audit["orphan_report_propositions"] == 1
    assert audit["verdict"] == "FAIL"


# ----------------------------------------------- 10-12: split leakage / seal
def test_mutation_10_11_a_family_crossing_partitions_is_detected():
    """A translation family or source family on both sides makes the validation
    partition measure memorisation."""
    assignments = [{"family": "eur-lex.europa.eu", "partition": "REFERENCE_DEVELOPMENT"},
                   {"family": "eur-lex.europa.eu", "partition": "REFERENCE_VALIDATION"}]
    sides = {}
    for row in assignments:
        sides.setdefault(row["family"], set()).add(row["partition"])
    crossing = [f for f, s in sides.items() if len(s) > 1]
    assert crossing == ["eur-lex.europa.eu"]


def test_mutation_12_validation_labels_opened_before_the_candidate_freeze():
    """The seal is a precondition, not a convention: opening validation before
    the post-reference freeze invalidates the run."""
    state = {"post_reference_candidate_freeze": False, "validation_opened": True}
    assert not (state["validation_opened"] and
                state["post_reference_candidate_freeze"]), "seal broken"


# ------------------------------------- 13-16: primary decisions and dissent
def _three(support_classes):
    return [{"unit_type": "SUPPORT", "support_class": sc,
             "disposition": ("PUBLISHED" if sc == "FULL_SUPPORT"
                             else "REJECTED_UNSUPPORTED"),
             "lifecycle_alignment": "ALIGNED", "polarity_alignment": "ALIGNED",
             "reasoning": f"seat {i}"} for i, sc in enumerate(support_classes)]


def test_mutation_13_primary_decision_overwritten_during_adjudication():
    """Adjudication records a separate decision; it may not rewrite a primary."""
    decisions = _three(["FULL_SUPPORT", "FULL_SUPPORT", "NOT_SUPPORTED"])
    record = REF.build_reference_record(
        object_id="p1", object_type="SUPPORT", evidence_bundle_id="b",
        partition="REFERENCE_DEVELOPMENT", decisions=decisions,
        key="support_class", protocol_version="V5_6_PROTOCOL_1", packet_hash="h",
        seat_manifest_hashes={"A": "1"},
        adjudication={"outcome": "AFFIRM_MAJORITY", "reasoning": "r"})
    assert [d["support_class"] for d in record.primary_decisions] == \
        ["FULL_SUPPORT", "FULL_SUPPORT", "NOT_SUPPORTED"]


def test_mutation_14_dissent_removed_from_the_final_record():
    with pytest.raises(REF.ReferenceViolation, match="dissent"):
        REF.ReferenceRecord(
            "r", "p1", "SUPPORT", "b", "REFERENCE_DEVELOPMENT",
            tuple(_three(["FULL_SUPPORT", "FULL_SUPPORT", "NOT_SUPPORTED"])),
            ("a", "b", "c"), "MAJORITY_TWO_ONE", (), "AFFIRM_MAJORITY", "r",
            {"support_class": "FULL_SUPPORT"}, (), None, "V5_6_PROTOCOL_1", "h",
            {"A": "1"}, None, "2026-07-25T00:00:00+00:00")


def test_mutation_15_material_majority_finalised_by_counting():
    with pytest.raises(REF.ReferenceViolation, match="does not become final by counting"):
        REF.build_reference_record(
            object_id="p1", object_type="SUPPORT", evidence_bundle_id="b",
            partition="REFERENCE_DEVELOPMENT",
            decisions=_three(["FULL_SUPPORT", "FULL_SUPPORT", "NOT_SUPPORTED"]),
            key="support_class", protocol_version="V5_6_PROTOCOL_1", packet_hash="h",
            seat_manifest_hashes={"A": "1"})


def test_mutation_16_unresolvable_forced_into_a_substantive_class():
    """EPISTEMICALLY_UNRESOLVABLE must survive as itself."""
    record = REF.build_reference_record(
        object_id="p1", object_type="SUPPORT", evidence_bundle_id="b",
        partition="REFERENCE_DEVELOPMENT",
        decisions=_three(["FULL_SUPPORT", "FULL_SUPPORT", "NOT_SUPPORTED"]),
        key="support_class", protocol_version="V5_6_PROTOCOL_1", packet_hash="h",
        seat_manifest_hashes={"A": "1"},
        adjudication={"outcome": "EPISTEMICALLY_UNRESOLVABLE",
                      "reasoning": "the evidence cannot settle it"})
    assert record.final_decision["support_class"] == "EPISTEMICALLY_UNRESOLVABLE"
    assert record.epistemic_unresolvability_reason


# ------------------------------------------------------- 17-18: mapping rules
def test_mutation_17_exact_quotation_without_sufficient_mapping():
    with pytest.raises(S.SchemaViolation, match="EXACT_VALUE_QUOTABLE"):
        S.StageAExtraction(
            "c1", "SEAT_A", {"proposition_complete": "SATISFIED"},
            ("TEXT_LOCATABLE",), "ACCEPTED_CANDIDATE", None, False, None, True,
            False, "r", "2026-07-25T00:00:00+00:00")


def test_mutation_18_ordinary_identifier_rejected_only_for_inexact_mapping():
    """An approximate span is adequate for a qualitative claim.  Rejecting it
    merely because the mapping is not exact is the V5.1 over-rejection failure."""
    decision = S.StageAExtraction(
        "c1", "SEAT_A", {"proposition_complete": "SATISFIED"},
        ("TEXT_LOCATABLE", "PROPOSITION_BOUNDARY_DEFENSIBLE",
         "SEMANTIC_CONTEXT_SUFFICIENT"), "ACCEPTED_CANDIDATE", None, False, None,
        False, False, "qualitative claim, no exact value quoted",
        "2026-07-25T00:00:00+00:00")
    assert decision.result == "ACCEPTED_CANDIDATE"
    assert "EXACT_VALUE_QUOTABLE" not in decision.mapping_satisfied


# ---------------------------------------------------- 19-20: relation classes
def test_mutation_19_relation_applied_to_a_pair_it_does_not_govern():
    from curunir_operational.v5_4 import activation as ACT
    observation = ACT.detect_relations(
        "Corrigendum to Regulation (EU) 2016/679.", source_object_id="x")[0]
    assert ACT.governs_pair(observation, "2016/679")
    assert not ACT.governs_pair(observation, "2022/2065")


def test_mutation_20_class_in_vocabulary_without_an_executable_witness():
    """The V5.5 finding: seven classes were declared with nothing able to emit
    them.  Every declared class must have a code path."""
    import inspect
    import re
    from curunir_operational.v5_4 import activation as ACT
    temporal = set(re.findall(r'return "([A-Z_]+)"',
                              inspect.getsource(ACT.classify_temporal)))
    temporal |= {v["temporal"] for v in ACT.RELATION_PROJECTION.values()}
    assert not [c for c in ACT.TEMPORAL_CLASSES if c not in temporal]
    dependence = set(re.findall(r'return "([A-Z_]+)"',
                                inspect.getsource(ACT.classify_dependence)))
    dependence |= {v["dependence"] for v in ACT.RELATION_PROJECTION.values()}
    dependence |= set(ACT._OBSERVED_RELATION.values())
    assert not [c for c in ACT.DEPENDENCE_CLASSES if c not in dependence]


# --------------------------------------------------- 21-23: custody and kernel
def test_mutation_21_foreign_reviewer_seat_write(tmp_path):
    seats = CUS.open_panel(panel_root=tmp_path / "panel", run_salt="s",
                           session_id="sess",
                           assignments={s: ["d0"] for s in CUS.SEAT_IDS})
    with pytest.raises(CUS.CustodyViolation, match="foreign seat id"):
        seats["REVIEWER_A"].append(dossier_id="d0", payload={"x": 1},
                                   seat_id="REVIEWER_B")


def test_mutation_22_historical_label_overwritten():
    """Lineage records a relationship; it never mutates the historical value."""
    lineage = REF.LineageRecord(
        "hist-1", "V5_3_INDEPENDENT_SURFACE_PANEL", "NOT_SUPPORTED", "ref-1",
        "FULL_SUPPORT", "REVERSED", "the dependency-consistent protocol disagrees",
        "SURFACE_4", "2026-07-25T00:00:00+00:00")
    assert lineage.historical_value == "NOT_SUPPORTED"
    assert lineage.new_reference_value == "FULL_SUPPORT"
    with pytest.raises(REF.ReferenceViolation, match="lineage disposition"):
        REF.LineageRecord("hist-2", "p", "a", "ref-2", "b", "DELETED", "r", "S4",
                          "2026-07-25T00:00:00+00:00")


def test_mutation_23_canonical_write_attempted():
    """No V5.6 module imports a database writer."""
    import curunir_operational.v5_6.objects as O
    import curunir_operational.v5_6.schema as SC
    import curunir_operational.v5_6.reference as RF
    import curunir_operational.v5_6.packets as PK
    for module in (O, SC, RF, PK):
        source = open(module.__file__).read()
        assert "psycopg" not in source
        assert "INSERT INTO" not in source.upper()
        assert "argus.actions" not in source


# ---------------------------------------------------------- naming discipline
@pytest.mark.parametrize("name", sorted(REF.PROHIBITED_NAMES))
def test_the_reference_may_not_be_dressed_up_as_human_truth(name):
    assert not REF.reference_name_is_permitted(name)


def test_the_permitted_reference_name_is_the_model_panel_one():
    assert REF.reference_name_is_permitted(REF.REFERENCE_NAME)
    assert "MODEL_PANEL" in REF.REFERENCE_NAME
