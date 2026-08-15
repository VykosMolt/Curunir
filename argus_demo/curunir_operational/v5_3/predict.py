"""Production prediction generation for all six surfaces (Section 5.1).

Every scored capability figure in V5.3 traces to a record minted here, and
every record here is bound by ``provenance.record_prediction`` to the frozen
production function that actually decided it.  The evaluator cannot reach
these functions to substitute a label: ``dossiers``, ``corpus`` and
``scoring`` are on the refused-producer list.

This module is orchestration over frozen classifiers.  It chooses what to ask
them; it never decides an answer itself.

Research shadow only.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..v4.models import ExtractionCandidate, NormalizedDocument
from ..v5_2 import claim_support as v52_claims
from ..v5_2 import dependence as v52_dependence
from ..v5_2 import reporting as v52_reporting
from ..v5_2 import temporal as v52_temporal
from . import ranking, revision, roles
from .provenance import ProductionPredictionRecord, record_prediction

SURFACE_1 = "SURFACE_1_SEMANTIC_EXTRACTION"
SURFACE_2 = "SURFACE_2_SOURCE_IDENTITY_AND_ORIGIN"
SURFACE_3 = "SURFACE_3_DEPENDENCE_AND_CORROBORATION"
SURFACE_4 = "SURFACE_4_CLAIM_SUPPORT"
SURFACE_5 = "SURFACE_5_TEMPORAL_RELATIONS"
SURFACE_6 = "SURFACE_6_REPORT_FAITHFULNESS"


def _explanation_text(assessment: Any) -> str:
    """DependenceAssessment carries an ``explanation`` record, not a string."""
    explanation = getattr(assessment, "explanation", None)
    if explanation is None:
        return ""
    for field in ("rationale", "summary", "detail", "reason"):
        value = getattr(explanation, field, None)
        if isinstance(value, str) and value.strip():
            return value
    return str(explanation)[:400]


def predict_extraction(candidate: ExtractionCandidate, document: NormalizedDocument,
                       model: ranking.RankingModel, layout_annotation: Any = None, *,
                       language: str | None = None
                       ) -> tuple[ProductionPredictionRecord, ranking.RankedDecision]:
    decision = ranking.rank_candidate(candidate, document, model, layout_annotation,
                                      language=language)
    prediction = record_prediction(
        surface=SURFACE_1, production_object_id=candidate.candidate_id,
        producer=ranking.rank_candidate, prediction=decision.stage,
        structured_rationale={
            "selected_level": decision.selected_level,
            "ranked_scores": dict(decision.ranked_scores),
            "top_unvetoed": decision.top_unvetoed,
            "veto_reasons": sorted({v["reason"] for v in decision.vetoes}),
            "boundary_warnings": list(decision.boundary_warnings),
            "lifecycle_state": decision.lifecycle_state,
            "rationale": decision.rationale,
            "ranker_version": decision.ranker_version},
        input_evidence_ids=(candidate.candidate_id,))
    return prediction, decision


def predict_source_role(observations: Sequence[Any], *, subject_id: str, role: str
                        ) -> tuple[ProductionPredictionRecord, roles.RoleResolution]:
    resolution = roles.resolve_role(observations, role, subject_id=subject_id)
    value = (resolution.role if resolution.state == "RESOLVED"
             else "NO_ROLE_ESTABLISHED")
    prediction = record_prediction(
        surface=SURFACE_2, production_object_id=f"{subject_id}:{role}",
        producer=roles.resolve_role, prediction=value,
        structured_rationale={
            "state": resolution.state, "agent": resolution.agent_value,
            "evidence_strength": resolution.evidence_strength,
            "support_weight": resolution.support_weight,
            "refused_inference": resolution.refused_inference,
            "rationale": resolution.rationale},
        input_observation_ids=tuple(resolution.decisive_observation_ids) +
        tuple(resolution.supporting_observation_ids))
    return prediction, resolution


def predict_dependence(left_pub: Any, right_pub: Any, *, pair_id: str,
                       left_text: str = "", right_text: str = "",
                       left_identifiers: Sequence[str] = (),
                       right_identifiers: Sequence[str] = (),
                       left_language: str = "en", right_language: str = "en",
                       **kwargs: Any
                       ) -> tuple[ProductionPredictionRecord, Any]:
    """Explicit revision language first, then the frozen classifier.

    A document that says it revises another is stating a derivation. V5.2
    reasoned only from overlap and publisher identity and missed nine such
    pairs; reading the sentence is both stronger evidence and cheaper.
    """
    statement = revision.relate_documents(
        left_text, right_text, left_id=pair_id + ":left", right_id=pair_id + ":right",
        left_identifiers=left_identifiers, right_identifiers=right_identifiers,
        left_language=left_language, right_language=right_language)
    if statement is not None:
        prediction = record_prediction(
            surface=SURFACE_3, production_object_id=pair_id,
            producer=revision.relate_documents,
            prediction=statement.dependence_class,
            structured_rationale={
                "route": "EXPLICIT_REVISION_STATEMENT",
                "relation": statement.relation,
                "target_reference": statement.target_reference,
                "matched_text": statement.matched_text,
                "content_relation": statement.content_relation,
                "temporal_projection": statement.temporal_relation})
        return prediction, statement
    assessment = v52_dependence.classify_dependence(left_pub, right_pub, **kwargs)
    prediction = record_prediction(
        surface=SURFACE_3, production_object_id=pair_id,
        producer=v52_dependence.classify_dependence, prediction=assessment.state,
        structured_rationale={
            "route": "CONTENT_AND_MANIFESTATION_CLASSIFIER",
            "explanation": _explanation_text(assessment),
            "signal_kinds": sorted({s.kind for s in assessment.signals})
            if getattr(assessment, "signals", None) else []})
    return prediction, assessment


def predict_temporal(left_claim: Any, right_claim: Any, *, pair_id: str,
                     **kwargs: Any) -> tuple[ProductionPredictionRecord, Any]:
    assessment = v52_temporal.relate(left_claim, right_claim, **kwargs)
    prediction = record_prediction(
        surface=SURFACE_5, production_object_id=pair_id,
        producer=v52_temporal.relate, prediction=assessment.relation,
        structured_rationale={"rationale": assessment.rationale,
                              "temporal_basis": getattr(assessment, "temporal_basis", None),
                              "identity_blocker": getattr(assessment, "identity_blocker", None)})
    return prediction, assessment


def predict_claim_support(claim_like: Any, evidence_items: Sequence[Any], *,
                          claim_id: str, **kwargs: Any
                          ) -> tuple[ProductionPredictionRecord, Any]:
    support = v52_claims.gated_assess_support(claim_like, evidence_items, **kwargs)
    prediction = record_prediction(
        surface=SURFACE_4, production_object_id=claim_id,
        producer=v52_claims.gated_assess_support, prediction=support.support_state,
        structured_rationale={
            "lifecycle_verdict": support.lifecycle_verdict,
            "critical_error": support.critical_error,
            "claimed_state": support.claimed_state,
            "supported_state": support.supported_state,
            "rationale": support.rationale})
    return prediction, support


def predict_report_disposition(proposition: Any, rendered_sentence: str, *,
                               proposition_id: str, **kwargs: Any
                               ) -> tuple[ProductionPredictionRecord, Any]:
    disposition = v52_reporting.plan_proposition(proposition, rendered_sentence,
                                                 **kwargs)
    prediction = record_prediction(
        surface=SURFACE_6, production_object_id=proposition_id,
        producer=v52_reporting.plan_proposition, prediction=disposition.disposition,
        structured_rationale={"reason": disposition.reason,
                              "rationale": disposition.rationale,
                              "rendered": bool(disposition.rendered_sentence)})
    return prediction, disposition
