"""Extractor-neutral candidates and reversible evidence resolution for V4."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..canonical import propose_mentions

from .models import (
    CandidateDisagreement, ClaimRelation, ClaimUnit, EntityRecord, EvidenceBasis,
    ExtractionCandidate, IdentityProposal, InvestigationHypothesis, NormalizedDocument,
    SourceOriginEdge, SourceRecord, sha256, stable_id,
)

DETERMINISTIC_PROVIDER = "CURUNIR_SENTENCE_AND_SIGNAL_BASELINE"
REPOSITORY_PROVIDER = "ARGUS_DETERMINISTIC_MENTION_PROPOSER"
AI_PROVIDER = "SOL_AI_ASSISTED_EXTRACTION"
AI_REVIEW_STATE = "AI_SECONDARY_REVIEW"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _section_for(document: NormalizedDocument, start: int) -> str:
    for name, left, right in document.sections:
        if left <= start < right:
            return name
    for page, left, right in document.pages:
        if left <= start < right:
            return f"PAGE:{page}"
    return "DOCUMENT"


def make_candidate(*, case_id: str, document: NormalizedDocument, source: SourceRecord,
                   span_start: int, span_end: int, candidate_type: str,
                   normalized_value: Mapping[str, Any], provider: str,
                   provider_version: str, review_state: str,
                   confidence_dimensions: Mapping[str, float] | None = None,
                   warnings: tuple[str, ...] = ()) -> ExtractionCandidate:
    if source.source_object_id != document.source_object_id:
        raise ValueError("candidate source/document mismatch")
    original = document.text[span_start:span_end]
    precision = document.mappings[0].precision if document.mappings else "UNMAPPED"
    return ExtractionCandidate(
        stable_id("candidate", case_id, document.document_id, span_start, span_end,
                  candidate_type, provider, sha256(original)),
        case_id, document.document_id, source.source_object_id, span_start, span_end,
        _section_for(document, span_start), candidate_type, original, dict(normalized_value),
        provider, provider_version, dict(confidence_dimensions or {"heuristic": 1.0}),
        precision, warnings, source.access_marking, now_utc(), review_state,
    )


def deterministic_candidates(case_id: str, document: NormalizedDocument,
                             source: SourceRecord) -> tuple[ExtractionCandidate, ...]:
    """Propose sentences plus correction/retraction/citation/date signals."""
    output: list[ExtractionCandidate] = []
    sentence_pattern = re.compile(r"(?ms)(?<!\w)([^\n.!?]{20,}[.!?])")
    for match in sentence_pattern.finditer(document.text):
        text = match.group(1).strip()
        left = match.start(1) + len(match.group(1)) - len(match.group(1).lstrip())
        right = left + len(text)
        lowered = text.casefold()
        signal = "CLAIM"
        if any(word in lowered for word in ("correction", "corrected", "erratum", "rectificatif", "korrigiert")):
            signal = "CORRECTION"
        elif any(word in lowered for word in ("retracted", "withdrawn", "retiré", "zurückgezogen")):
            signal = "RETRACTION"
        elif any(word in lowered for word in ("according to", "cites", "source:", "selon", "laut ")):
            signal = "CITATION"
        output.append(make_candidate(
            case_id=case_id, document=document, source=source, span_start=left, span_end=right,
            candidate_type=f"{signal}_CANDIDATE", normalized_value={"statement": text, "signal": signal},
            provider=DETERMINISTIC_PROVIDER, provider_version="4.0", review_state="UNREVIEWED",
            confidence_dimensions={"span": 1.0, "semantic": 0.45},
        ))
    return tuple(output)


def repository_mention_candidates(case_id: str, document: NormalizedDocument,
                                  source: SourceRecord) -> tuple[ExtractionCandidate, ...]:
    output: list[ExtractionCandidate] = []
    for item in propose_mentions(document.text):
        output.append(make_candidate(
            case_id=case_id, document=document, source=source,
            span_start=item.start_char, span_end=item.end_char,
            candidate_type=f"{item.mention_type.upper()}_MENTION_CANDIDATE",
            normalized_value={"surface": item.surface_text, "normalized": item.normalized_text,
                              "entity_type": item.mention_type},
            provider=REPOSITORY_PROVIDER, provider_version="repository-v1",
            review_state="UNREVIEWED", confidence_dimensions={"span": 1.0, "type": 0.5},
        ))
    return tuple(output)


def ai_candidate_from_located_text(*, case_id: str, document: NormalizedDocument,
                                  source: SourceRecord, exact_text: str,
                                  candidate_type: str, normalized_value: Mapping[str, Any],
                                  occurrence: int = 1) -> ExtractionCandidate:
    """Admit a persisted AI suggestion only when its source text is locatable."""
    starts = [match.start() for match in re.finditer(re.escape(exact_text), document.text)]
    if occurrence < 1 or len(starts) < occurrence:
        raise ValueError("AI candidate support span was not found in captured derivative")
    start = starts[occurrence - 1]
    return make_candidate(
        case_id=case_id, document=document, source=source, span_start=start,
        span_end=start + len(exact_text), candidate_type=candidate_type,
        normalized_value=normalized_value, provider=AI_PROVIDER, provider_version="sol-2026-07-22",
        review_state=AI_REVIEW_STATE,
        confidence_dimensions={"span": 1.0, "semantic_uncalibrated": 0.6},
        warnings=("AI_ASSISTED_ADVISORY_NOT_HUMAN_REVIEW",),
    )


def validate_candidate(candidate: ExtractionCandidate, document: NormalizedDocument,
                       source: SourceRecord) -> None:
    if candidate.document_id != document.document_id or candidate.source_object_id != source.source_object_id:
        raise ValueError("candidate provenance mismatch")
    if document.text[candidate.span_start:candidate.span_end] != candidate.original_text:
        raise ValueError("candidate span fabrication or derivative drift")
    if candidate.mapping_precision == "UNMAPPED":
        raise ValueError("candidate is not mapped to immutable source")


def disagreements(candidates: Iterable[ExtractionCandidate]) -> tuple[CandidateDisagreement, ...]:
    grouped: dict[tuple[str, str], list[ExtractionCandidate]] = {}
    for item in candidates:
        key = (item.document_id, item.original_text.casefold())
        grouped.setdefault(key, []).append(item)
    output: list[CandidateDisagreement] = []
    for key, values in grouped.items():
        types = {item.candidate_type for item in values}
        polarities = {str(item.normalized_value.get("polarity")) for item in values
                      if "polarity" in item.normalized_value}
        if len(types) > 1 or len(polarities) > 1:
            kind = "TYPE_MISMATCH" if len(types) > 1 else "POLARITY_MISMATCH"
            output.append(CandidateDisagreement(
                stable_id("candidate-disagreement", key, sorted(item.candidate_id for item in values)),
                tuple(sorted(item.candidate_id for item in values)), kind,
                "Providers preserve incompatible candidate interpretations", "HUMAN_REVIEW_REQUIRED",
            ))
    return tuple(output)


def entity_record(entity_class: str, canonical_name: str, *, aliases: Iterable[str] = (),
                  external_identifiers: Mapping[str, str] | None = None,
                  valid_time: tuple[str | None, str | None] = (None, None)) -> EntityRecord:
    return EntityRecord(stable_id("entity", entity_class, canonical_name), entity_class, canonical_name,
                        tuple(dict.fromkeys((canonical_name, *aliases))), dict(external_identifiers or {}),
                        valid_time, ("CREATED_REVERSIBLY",))


def propose_identity(left: EntityRecord, right: EntityRecord, *,
                     evidence_candidate_ids: tuple[str, ...], factors: Mapping[str, Any],
                     accepted_by_human: bool = False) -> IdentityProposal:
    if left.entity_id == right.entity_id:
        raise ValueError("identity proposal requires separate entity records")
    official_match = bool(set(left.external_identifiers.items()) & set(right.external_identifiers.items()))
    explicit = bool(evidence_candidate_ids) and bool(factors.get("explicit_source_statement"))
    if official_match and explicit and accepted_by_human:
        outcome, review = "SAME_ENTITY_ACCEPTED", "HUMAN_REVIEWED"
    elif official_match or explicit:
        outcome, review = "SAME_ENTITY_PROPOSED", "HUMAN_REVIEW_REQUIRED"
    elif set(alias.casefold() for alias in left.aliases) & set(alias.casefold() for alias in right.aliases):
        outcome, review = "AMBIGUOUS", "HUMAN_REVIEW_REQUIRED"
    else:
        outcome, review = "UNKNOWN", "HUMAN_REVIEW_REQUIRED"
    return IdentityProposal(stable_id("identity-proposal", left.entity_id, right.entity_id),
                            left.entity_id, right.entity_id, outcome, evidence_candidate_ids,
                            dict(factors), True, review)


def normalized_fingerprint(text: str) -> str:
    normalized = re.sub(r"\W+", " ", text.casefold())
    return sha256(" ".join(normalized.split()))


def source_origin_edge(*, source_id: str, target_id: str, relationship: str,
                       evidence_candidate_ids: tuple[str, ...] = (), metadata_basis: tuple[str, ...] = (),
                       provider: str = DETERMINISTIC_PROVIDER, review_state: str = "UNREVIEWED",
                       confidence_dimensions: Mapping[str, float] | None = None,
                       access_marking: Mapping[str, Any] | None = None,
                       valid_time: tuple[str | None, str | None] = (None, None)) -> SourceOriginEdge:
    if relationship not in {"MIRRORS", "UNKNOWN_DEPENDENCE"} and not (evidence_candidate_ids or metadata_basis):
        raise ValueError("source-origin relationship requires evidence or metadata basis")
    return SourceOriginEdge(
        stable_id("origin-edge", source_id, target_id, relationship), source_id, target_id, relationship,
        evidence_candidate_ids, metadata_basis, valid_time, now_utc(), provider, review_state,
        dict(confidence_dimensions or {"relationship_uncalibrated": 0.7}),
        dict(access_marking or {"releasability": ["PUBLIC"]}),
    )


def evidence_basis(*, case_id: str, source_object_ids: tuple[str, ...],
                   candidate_ids: tuple[str, ...], source_family_id: str,
                   independence_state: str, active: bool = True,
                   correction_state: str = "CURRENT",
                   review_state: str = "HUMAN_REVIEW_REQUIRED") -> EvidenceBasis:
    if not source_object_ids or not candidate_ids:
        raise ValueError("evidence basis requires captured sources and mapped candidates")
    if independence_state not in {"INDEPENDENT", "DEPENDENT", "UNKNOWN_DEPENDENCE"}:
        raise ValueError("invalid independence state")
    return EvidenceBasis(stable_id("evidence-basis", case_id, source_family_id,
                                   sorted(source_object_ids), sorted(candidate_ids)), case_id,
                         source_object_ids, candidate_ids, source_family_id, independence_state,
                         active, correction_state, review_state)


def claim_from_candidate(*, case_id: str, candidate: ExtractionCandidate,
                         evidence_basis_ids: tuple[str, ...], normalized_statement: str,
                         subject: str, predicate: str, object_or_value: str,
                         epistemic_state: str = "EXTRACTED", modality: str = "ASSERTED",
                         polarity: str = "POSITIVE", temporal_scope: tuple[str | None, str | None] = (None, None),
                         geographic_scope: tuple[str, ...] = (), review_state: str = "HUMAN_REVIEW_REQUIRED") -> ClaimUnit:
    return ClaimUnit(stable_id("claim", case_id, normalized_statement, candidate.candidate_id), case_id,
                     normalized_statement, candidate.original_text, subject, predicate, object_or_value,
                     temporal_scope, geographic_scope, modality, polarity, epistemic_state,
                     evidence_basis_ids, (candidate.candidate_id,), review_state, 1)


def relate_claims(left: ClaimUnit, right: ClaimUnit, relation_type: str, *,
                  evidence_candidate_ids: tuple[str, ...], temporal_relationship: str,
                  scope_relationship: str, provider: str = DETERMINISTIC_PROVIDER) -> ClaimRelation:
    return ClaimRelation(stable_id("claim-relation", left.claim_id, right.claim_id, relation_type),
                         left.claim_id, right.claim_id, relation_type, evidence_candidate_ids,
                         temporal_relationship, scope_relationship, provider,
                         "HUMAN_REVIEW_REQUIRED", "UNRESOLVED")


def retract_basis(basis: EvidenceBasis, *, correction_state: str = "RETRACTED") -> EvidenceBasis:
    if correction_state not in {"RETRACTED", "CORRECTED", "SUPERSEDED"}:
        raise ValueError("invalid inactive evidence state")
    return replace(basis, active=False, correction_state=correction_state)


def hypothesis(*, case_id: str, statement: str, supporting_claim_ids: tuple[str, ...],
               contradicting_claim_ids: tuple[str, ...], independent_evidence_count: int,
               source_dependence_summary: str, assumptions: tuple[str, ...], unknowns: tuple[str, ...],
               disconfirming_evidence_requirement: str, status: str = "UNRESOLVED") -> InvestigationHypothesis:
    return InvestigationHypothesis(
        stable_id("hypothesis", case_id, statement), case_id, statement, (case_id,), supporting_claim_ids,
        contradicting_claim_ids, independent_evidence_count, source_dependence_summary,
        assumptions, unknowns, disconfirming_evidence_requirement, status,
        "CURUNIR_ANALYTIC_RULES_AND_SOL_AI_SECONDARY", "HUMAN_REVIEW_PENDING",
        ("CREATED_WITHOUT_FORCED_WINNER",),
    )


def analysis_metrics(*, sources: Iterable[SourceRecord], candidates: Iterable[ExtractionCandidate],
                     identities: Iterable[IdentityProposal], origin_edges: Iterable[SourceOriginEdge],
                     bases: Iterable[EvidenceBasis], claims: Iterable[ClaimUnit],
                     relations: Iterable[ClaimRelation]) -> dict[str, Any]:
    source_values = tuple(sources); candidate_values = tuple(candidates); identity_values = tuple(identities)
    edge_values = tuple(origin_edges); basis_values = tuple(bases); claim_values = tuple(claims)
    relation_values = tuple(relations)
    return {
        "publication_count": len(source_values),
        "distinct_source_families": len({item.source_family_id for item in basis_values}),
        "distinct_evidence_bases": len(basis_values),
        "independent_evidence_count": sum(item.independence_state == "INDEPENDENT" and item.active for item in basis_values),
        "unknown_dependence_count": sum(item.independence_state == "UNKNOWN_DEPENDENCE" for item in basis_values),
        "candidates": len(candidate_values),
        "candidates_with_valid_spans": len(candidate_values),
        "ai_secondary_candidates": sum(item.review_state == AI_REVIEW_STATE for item in candidate_values),
        "accepted_identities": sum(item.outcome == "SAME_ENTITY_ACCEPTED" for item in identity_values),
        "ambiguous_identities": sum(item.outcome == "AMBIGUOUS" for item in identity_values),
        "source_origin_edges": len(edge_values),
        "derivative_edges": sum(item.relationship in {"DERIVED_FROM", "SYNDICATED_FROM", "SUMMARIZES", "MIRRORS"} for item in edge_values),
        "translation_edges": sum(item.relationship == "TRANSLATED_FROM" for item in edge_values),
        "claims": len(claim_values),
        "contradiction_edges": sum(item.relation_type in {"LOGICAL_CONTRADICTION", "SOURCE_DISAGREEMENT", "NUMERIC_DISAGREEMENT", "POLARITY_CONFLICT", "UNRESOLVED"} for item in relation_values),
        "corrections": sum(item.relation_type == "CORRECTION" for item in relation_values),
        "retractions": sum(item.relation_type == "RETRACTION" for item in relation_values),
        "qualifications": sum(item.relation_type == "QUALIFICATION" for item in relation_values),
    }
