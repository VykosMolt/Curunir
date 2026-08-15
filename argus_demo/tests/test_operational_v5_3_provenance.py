"""Evaluation-provenance tests (contract Sections 4-5, 19).

The V5.2 defect these close: a capability figure derived from a label the
corpus builder invented. Every path to that outcome is asserted refused.
"""
from __future__ import annotations

import json

import pytest

from curunir_operational.v5_2 import dossiers as v52_dossiers
from curunir_operational.v5_2 import semantics as v52_semantics
from curunir_operational.v5_3 import corpus, scoring
from curunir_operational.v5_3 import provenance as P

pytestmark = pytest.mark.no_db


def _prediction(value: str = "ACCEPTED_CANDIDATE", surface: str | None = None):
    return P.record_prediction(
        surface=surface or "SURFACE_1_SEMANTIC_EXTRACTION",
        production_object_id="object-1", producer=v52_semantics.resolve_candidate,
        prediction=value, structured_rationale={"level": "NARROW"})


# ---------------------------------------------------------------------------
# Layer separation
# ---------------------------------------------------------------------------

def test_the_four_layers_are_distinct_record_types():
    assert P.RawEvidenceRecord is not P.NormalizedObservationRecord
    assert P.NormalizedObservationRecord is not P.ProductionPredictionRecord
    assert P.ProductionPredictionRecord is not P.ReviewerDecisionRecord


def test_no_observation_type_names_a_source_role():
    roles = {"AUTHORED_BY", "PUBLISHED_BY", "ISSUED_BY", "HOSTED_BY",
             "OWNED_BY", "OPERATED_BY", "ARCHIVED_BY", "MIRRORED_BY",
             "TRANSLATED_BY", "SYNDICATED_BY", "COMMISSIONED_BY",
             "SUBMITTED_BY", "EDITED_BY"}
    assert not (set(P.OBSERVATION_TYPES) & roles)


def test_an_observation_may_not_carry_a_role_value():
    with pytest.raises(P.ProvenanceViolation, match="role name"):
        P.normalized_observation(
            raw_evidence_ids=["r1"], observation_type="EXPLICIT_PUBLISHER_LINE",
            observed_value="PUBLISHED_BY", normalization="upper")


def test_an_observation_requires_raw_support():
    with pytest.raises(P.ProvenanceViolation, match="raw support"):
        P.normalized_observation(
            raw_evidence_ids=[], observation_type="DOMAIN_HOST",
            observed_value="example.org", normalization="netloc")


def test_an_observation_may_not_declare_a_semantic_role():
    observation = P.normalized_observation(
        raw_evidence_ids=["r1"], observation_type="DOMAIN_HOST",
        observed_value="example.org", normalization="netloc")
    assert observation.semantic_role_supplied is False
    with pytest.raises(P.ProvenanceViolation, match="semantic role"):
        P.NormalizedObservationRecord(
            **{**observation.to_record(), "semantic_role_supplied": True})


def test_raw_evidence_requires_a_locator_and_custody():
    with pytest.raises(P.ProvenanceViolation):
        P.raw_evidence(source_object_id="", raw_text="x", locator={"k": 1},
                       content_hash="0" * 64)
    with pytest.raises(P.ProvenanceViolation):
        P.raw_evidence(source_object_id="s", raw_text="x", locator={},
                       content_hash="0" * 64)


# ---------------------------------------------------------------------------
# Production prediction provenance
# ---------------------------------------------------------------------------

def test_a_prediction_is_bound_to_the_code_that_made_it():
    prediction = _prediction()
    assert prediction.producer_module == "curunir_operational/v5_2/semantics.py"
    assert len(prediction.production_code_sha256) == 64
    assert prediction.producer_function.endswith("resolve_candidate")


def test_an_evaluator_module_may_not_produce_a_prediction():
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.record_prediction(
            surface="SURFACE_3_DEPENDENCE_AND_CORROBORATION",
            production_object_id="pair-1", producer=v52_dossiers.build_dossier,
            prediction="TRANSLATION_DERIVATIVE", structured_rationale={})


def test_the_corpus_builder_may_not_produce_a_prediction():
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.record_prediction(
            surface="SURFACE_5_TEMPORAL_RELATIONS", production_object_id="p",
            producer=corpus.seal, prediction="NO_CONFLICT",
            structured_rationale={})


def test_the_scorer_may_not_produce_a_prediction():
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.record_prediction(
            surface="SURFACE_5_TEMPORAL_RELATIONS", production_object_id="p",
            producer=scoring.score, prediction="NO_CONFLICT",
            structured_rationale={})


def test_an_unregistered_module_may_not_produce_a_prediction():
    def local_heuristic():  # defined in a test file, not production
        return "TRANSLATION_DERIVATIVE"
    with pytest.raises(P.ProvenanceViolation, match="not a registered production"):
        P.record_prediction(
            surface="SURFACE_3_DEPENDENCE_AND_CORROBORATION",
            production_object_id="pair-2", producer=local_heuristic,
            prediction="TRANSLATION_DERIVATIVE", structured_rationale={})


def test_a_tampered_prediction_fails_its_integrity_hash():
    prediction = _prediction()
    with pytest.raises(P.ProvenanceViolation, match="integrity hash"):
        P.ProductionPredictionRecord(
            **{**prediction.to_record(), "prediction": "REJECTED"})


def test_production_and_evaluator_sets_are_disjoint():
    assert not (set(P.PRODUCTION_MODULES) & set(P.EVALUATOR_MODULES))


def test_prediction_manifest_records_producers(tmp_path):
    manifest = P.write_prediction_manifest(
        [_prediction(), _prediction("REJECTED")], tmp_path / "pm.jsonl")
    assert manifest["evaluator_produced_predictions"] == 0
    assert "curunir_operational/v5_2/semantics.py" in manifest["producer_modules"]
    loaded = P.load_prediction_manifest(tmp_path / "pm.jsonl")
    assert loaded


# ---------------------------------------------------------------------------
# Scoring provenance
# ---------------------------------------------------------------------------

def test_a_unit_without_a_prediction_cannot_be_scored():
    comparison = P.compare(
        dossier_id="d1", surface="SURFACE_3_DEPENDENCE_AND_CORROBORATION",
        prediction=None, reviewer_majority="TRANSLATION_DERIVATIVE",
        reviewer_votes={"TRANSLATION_DERIVATIVE": 3}, unanimous=True)
    assert comparison.outcome == "INVALID_FOR_CAPABILITY_SCORING"
    assert comparison.production_prediction is None


def test_a_capability_outcome_requires_a_prediction_id():
    with pytest.raises(P.ProvenanceViolation, match="frozen production prediction"):
        P.ScoringComparisonRecord(
            "c", "d", "SURFACE_1_SEMANTIC_EXTRACTION", None, "ACCEPTED_CANDIDATE",
            "curunir_operational/v5_2/semantics.py", "ACCEPTED_CANDIDATE",
            {"ACCEPTED_CANDIDATE": 3}, True, "CORRECT", "detail")


def test_a_comparison_may_not_cite_evaluation_code_as_producer():
    with pytest.raises(P.ProvenanceViolation, match="evaluation code"):
        P.ScoringComparisonRecord(
            "c", "d", "SURFACE_1_SEMANTIC_EXTRACTION", "p1", "ACCEPTED_CANDIDATE",
            "curunir_operational/v5_3/corpus.py", "ACCEPTED_CANDIDATE",
            {"ACCEPTED_CANDIDATE": 3}, True, "CORRECT", "detail")


def test_matching_prediction_and_majority_scores_correct():
    comparison = P.compare(
        dossier_id="d", surface="SURFACE_1_SEMANTIC_EXTRACTION",
        prediction=_prediction(), reviewer_majority="ACCEPTED_CANDIDATE",
        reviewer_votes={"ACCEPTED_CANDIDATE": 3}, unanimous=True)
    assert comparison.outcome == "CORRECT"


def test_divergent_prediction_and_majority_scores_incorrect():
    comparison = P.compare(
        dossier_id="d", surface="SURFACE_1_SEMANTIC_EXTRACTION",
        prediction=_prediction(), reviewer_majority="REJECTED",
        reviewer_votes={"REJECTED": 2, "ACCEPTED_CANDIDATE": 1}, unanimous=False)
    assert comparison.outcome == "INCORRECT"


def test_certified_unresolvable_is_not_counted_as_an_error():
    comparison = P.compare(
        dossier_id="d", surface="SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN",
        prediction=_prediction("NO_ROLE_ESTABLISHED",
                               "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN"),
        reviewer_majority="EPISTEMICALLY_UNRESOLVABLE",
        reviewer_votes={"EPISTEMICALLY_UNRESOLVABLE": 3}, unanimous=True,
        unresolvable_certified=True)
    assert comparison.outcome == "GENUINELY_UNRESOLVABLE"


def test_provenance_audit_requires_zero_evaluator_answers():
    good = P.compare(dossier_id="d1", surface="SURFACE_1_SEMANTIC_EXTRACTION",
                     prediction=_prediction(), reviewer_majority="ACCEPTED_CANDIDATE",
                     reviewer_votes={"ACCEPTED_CANDIDATE": 3}, unanimous=True)
    orphan = P.compare(dossier_id="d2", surface="SURFACE_1_SEMANTIC_EXTRACTION",
                       prediction=None, reviewer_majority="REJECTED",
                       reviewer_votes={"REJECTED": 3}, unanimous=True)
    audit = P.provenance_audit([good, orphan])
    assert audit["scored_units"] == 1
    assert audit["scored_answers_from_evaluator"] == 0
    assert audit["clean"] is True


# ---------------------------------------------------------------------------
# Reviewer isolation
# ---------------------------------------------------------------------------

def test_a_reviewer_decision_that_saw_other_reviews_is_refused():
    with pytest.raises(P.ProvenanceViolation, match="isolation"):
        P.ReviewerDecisionRecord("d", "dossier", "SURFACE_1_SEMANTIC_EXTRACTION",
                                 "REVIEWER_A", "LABELED", "ACCEPTED_CANDIDATE",
                                 "reasoning", "HIGH", True, False)


def test_a_model_decision_may_not_claim_human_review():
    with pytest.raises(P.ProvenanceViolation, match="human"):
        P.ReviewerDecisionRecord("d", "dossier", "SURFACE_1_SEMANTIC_EXTRACTION",
                                 "REVIEWER_A", "LABELED", "ACCEPTED_CANDIDATE",
                                 "reasoning", "HIGH", False, True)


# ---------------------------------------------------------------------------
# Dossier rendering
# ---------------------------------------------------------------------------

def test_a_rendered_observation_withholds_the_semantic_role():
    observation = P.normalized_observation(
        raw_evidence_ids=["r1"], observation_type="INSTITUTIONAL_ATTRIBUTION",
        observed_value="European Commission", normalization="front matter",
        mode="INFERRED", strength="STRONGLY_IMPLIED")
    rendered = corpus.render_observation(observation)
    assert rendered["semantic_role"] == corpus.REVIEWER_WITHHELD
    assert rendered["observation_type"] == "INSTITUTIONAL_ATTRIBUTION"
    assert rendered["observed_value"] == "European Commission"
    # The V5.2 shape — a bare answer-shaped field — is not produced.
    assert "publisher" not in json.dumps(rendered).casefold()
