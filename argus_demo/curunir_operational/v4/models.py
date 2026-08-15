"""Strict records for V4 investigations, evidence resolution and shadow admission."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(value: bytes | str | Mapping[str, Any] | list[Any]) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{sha256('|'.join(str(part) for part in parts))[:24]}"


def require_aware(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def require_hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA-256")


class Record:
    def to_record(self) -> dict[str, Any]:
        return asdict(self)


CASE_STATUSES = frozenset({
    "DRAFT", "PREREGISTERED", "DISCOVERY_ACTIVE", "ACQUISITION_ACTIVE",
    "ANALYSIS_ACTIVE", "REPORT_DRAFT", "REVIEW_PENDING", "COMPLETE_RESEARCH_SHADOW",
    "BLOCKED", "CANCELLED", "SUPERSEDED",
})


@dataclass(frozen=True)
class InvestigationCase(Record):
    case_id: str
    title: str
    research_question: str
    purpose: str
    scope: tuple[str, ...]
    exclusions: tuple[str, ...]
    geographic_scope: tuple[str, ...]
    temporal_scope: tuple[str | None, str | None]
    languages: tuple[str, ...]
    entities_of_interest: tuple[str, ...]
    source_classes: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    prohibited_inference_classes: tuple[str, ...]
    discovery_budget: int
    acquisition_budget: int
    review_policy: str
    stop_rules: tuple[str, ...]
    owning_node: str
    access_marking: Mapping[str, Any]
    created_time: str
    current_phase: str
    status: str
    version: int
    integrity_hash: str

    def __post_init__(self) -> None:
        require_aware(self.created_time); require_hash(self.integrity_hash)
        if self.status not in CASE_STATUSES:
            raise ValueError("invalid case status")
        if not 1 <= self.discovery_budget <= 140 or not 1 <= self.acquisition_budget <= 80:
            raise ValueError("unbounded case budget")
        if len(self.research_question.split()) < 8 or "everything about" in self.research_question.casefold():
            raise ValueError("research question is not bounded")
        if self.version < 1 or not self.stop_rules or not self.evidence_requirements:
            raise ValueError("case requires versioned evidence and stop rules")


@dataclass(frozen=True)
class CaseVersion(Record):
    version_id: str
    case_id: str
    version: int
    parent_version_id: str | None
    changed_fields: tuple[str, ...]
    rationale: str
    created_time: str
    status: str
    integrity_hash: str

    def __post_init__(self) -> None:
        require_aware(self.created_time); require_hash(self.integrity_hash)


@dataclass(frozen=True)
class ResearchQuestion(Record):
    question_id: str
    case_id: str
    statement: str
    purpose: str


@dataclass(frozen=True)
class SubQuestion(Record):
    subquestion_id: str
    question_id: str
    statement: str
    expected_source_classes: tuple[str, ...]
    languages: tuple[str, ...]
    contradiction_need: str


@dataclass(frozen=True)
class EvidenceRequirement(Record):
    requirement_id: str
    case_id: str
    statement: str
    source_classes: tuple[str, ...]
    minimum_sources: int
    contradiction_condition: str
    status: str = "OPEN"


@dataclass(frozen=True)
class StopRule(Record):
    rule_id: str
    case_id: str
    condition: str
    priority: int


@dataclass(frozen=True)
class DiscoveryQuery(Record):
    query_id: str
    case_id: str
    parent_question_id: str
    subquestion_id: str
    formulation: str
    language: str
    provider: str
    provider_category: str
    reason: str
    expected_source_class: str
    execution_time: str
    result_lead_ids: tuple[str, ...] = ()
    novelty: float | None = None
    follow_up_decision: str = "PENDING"

    def __post_init__(self) -> None:
        require_aware(self.execution_time)
        if self.provider_category not in {"OFFICIAL_SITE", "PUBLIC_WEB", "CITATION_EXPANSION"}:
            raise ValueError("unsupported discovery provider category")
        if self.novelty is not None and not 0 <= self.novelty <= 1:
            raise ValueError("novelty must be bounded and uncalibrated")


@dataclass(frozen=True)
class DiscoveryRun(Record):
    run_id: str
    case_id: str
    query_ids: tuple[str, ...]
    request_count: int
    budget: int
    started_time: str
    completed_time: str
    stop_reason: str

    def __post_init__(self) -> None:
        require_aware(self.started_time); require_aware(self.completed_time)
        if self.request_count > self.budget:
            raise ValueError("discovery budget exceeded")


@dataclass(frozen=True)
class SearchLead(Record):
    lead_id: str
    case_id: str
    title: str
    url: str
    provider: str
    rank: int
    snippet: str
    query_id: str
    language: str
    discovery_time: str
    apparent_source_authority: str
    media_type: str
    relevance_reason: str
    evidence_eligible: bool = False

    def __post_init__(self) -> None:
        require_aware(self.discovery_time)
        if self.evidence_eligible:
            raise ValueError("search leads and snippets are never evidence")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("lead must be an ordinary public URL")


@dataclass(frozen=True)
class DiscoveryFailure(Record):
    failure_id: str
    query_id: str
    provider: str
    category: str
    detail: str
    occurred_time: str


@dataclass(frozen=True)
class CoverageUpdate(Record):
    update_id: str
    case_id: str
    requirement_id: str
    acquired_source_ids: tuple[str, ...]
    source_classes_present: tuple[str, ...]
    novelty: float
    decision: str


ACCESS_STATES = frozenset({
    "ALLOW_PUBLIC_RETRIEVAL", "ALLOW_METADATA_ONLY", "ALLOW_MANUAL_BROWSER_CAPTURE",
    "DENY_CREDENTIAL_REQUIRED", "DENY_ACCESS_CONTROLLED", "DENY_LEGAL_RESTRICTION",
    "DENY_OUT_OF_SCOPE", "DENY_PERSONAL_DATA_RISK", "BLOCKED_TECHNICALLY",
    "UNKNOWN_REQUIRES_REVIEW",
})


@dataclass(frozen=True)
class AccessDecision(Record):
    decision_id: str
    case_id: str
    lead_id: str
    url: str
    state: str
    rationale: tuple[str, ...]
    policy_version: str
    decided_time: str
    access_marking: Mapping[str, Any]

    def __post_init__(self) -> None:
        require_aware(self.decided_time)
        if self.state not in ACCESS_STATES:
            raise ValueError("invalid access decision")


@dataclass(frozen=True)
class RetrievalRecord(Record):
    retrieval_id: str
    case_id: str
    lead_id: str
    requested_url: str
    final_url: str | None
    redirects: tuple[str, ...]
    source_authority: str
    request_time: str
    response_time: str | None
    status: int | None
    headers: Mapping[str, str]
    media_type: str | None
    byte_length: int | None
    content_hash: str | None
    access_decision_id: str
    acquisition_method: str
    failure: str | None
    retry_of: str | None
    language: str
    access_marking: Mapping[str, Any]
    content_state: str

    def __post_init__(self) -> None:
        require_aware(self.request_time)
        if self.response_time: require_aware(self.response_time)
        if self.content_hash: require_hash(self.content_hash)


@dataclass(frozen=True)
class SourceRecord(Record):
    source_object_id: str
    case_id: str
    retrieval_ids: tuple[str, ...]
    content_hash: str
    content_path: str
    requested_urls: tuple[str, ...]
    final_urls: tuple[str, ...]
    publisher: str
    source_class: str
    title: str
    language: str
    publication_time: str | None
    admitted_time: str
    review_state: str
    access_marking: Mapping[str, Any]

    def __post_init__(self) -> None:
        require_hash(self.content_hash); require_aware(self.admitted_time)


MAPPING_PRECISIONS = frozenset({
    "EXACT_BYTE", "EXACT_CHARACTER", "EXACT_PAGE_CHARACTER", "APPROXIMATE_PAGE",
    "APPROXIMATE_SECTION", "UNMAPPED",
})


@dataclass(frozen=True)
class DerivativeMapping(Record):
    mapping_id: str
    source_object_id: str
    source_hash: str
    derivative_hash: str
    source_locator: str
    derivative_start: int
    derivative_end: int
    precision: str

    def __post_init__(self) -> None:
        require_hash(self.source_hash); require_hash(self.derivative_hash)
        if self.precision not in MAPPING_PRECISIONS:
            raise ValueError("invalid mapping precision")


@dataclass(frozen=True)
class NormalizedDocument(Record):
    document_id: str
    source_object_id: str
    source_hash: str
    derivative_hash: str
    parser: str
    parser_version: str
    language: str
    text: str
    sections: tuple[tuple[str, int, int], ...]
    pages: tuple[tuple[int, int, int], ...]
    mappings: tuple[DerivativeMapping, ...]
    warnings: tuple[str, ...]
    omitted_content: tuple[str, ...]
    generated_time: str

    def __post_init__(self) -> None:
        require_hash(self.source_hash); require_hash(self.derivative_hash); require_aware(self.generated_time)
        if self.text and not self.mappings:
            raise ValueError("normalized text requires source mapping")


@dataclass(frozen=True)
class TranslationDerivative(Record):
    translation_id: str
    document_id: str
    source_language: str
    target_language: str
    provider: str
    provider_version: str
    translated_text: str
    alignment_precision: str
    warnings: tuple[str, ...]
    derivative_hash: str
    review_state: str
    independent_source: bool = False

    def __post_init__(self) -> None:
        require_hash(self.derivative_hash)
        if self.independent_source:
            raise ValueError("translation derivative cannot be independent evidence")


@dataclass(frozen=True)
class ExtractionCandidate(Record):
    candidate_id: str
    case_id: str
    document_id: str
    source_object_id: str
    span_start: int
    span_end: int
    page_or_section: str
    candidate_type: str
    original_text: str
    normalized_value: Mapping[str, Any]
    provider: str
    provider_version: str
    confidence_dimensions: Mapping[str, float]
    mapping_precision: str
    warnings: tuple[str, ...]
    access_marking: Mapping[str, Any]
    creation_time: str
    review_state: str

    def __post_init__(self) -> None:
        require_aware(self.creation_time)
        if self.span_start < 0 or self.span_end <= self.span_start or not self.original_text:
            raise ValueError("candidate requires a non-empty source span")
        if self.mapping_precision not in MAPPING_PRECISIONS - {"UNMAPPED"}:
            raise ValueError("candidate requires mapped support")
        if not self.provider or not self.provider_version:
            raise ValueError("candidate requires provider identity")


@dataclass(frozen=True)
class CandidateDisagreement(Record):
    disagreement_id: str
    candidate_ids: tuple[str, ...]
    disagreement_type: str
    details: str
    review_state: str


IDENTITY_OUTCOMES = frozenset({
    "SAME_ENTITY_ACCEPTED", "SAME_ENTITY_PROPOSED", "DIFFERENT_ENTITY", "AMBIGUOUS", "UNKNOWN",
})


@dataclass(frozen=True)
class EntityRecord(Record):
    entity_id: str
    entity_class: str
    canonical_name: str
    aliases: tuple[str, ...]
    external_identifiers: Mapping[str, str]
    valid_time: tuple[str | None, str | None]
    history: tuple[str, ...]


@dataclass(frozen=True)
class IdentityProposal(Record):
    proposal_id: str
    left_entity_id: str
    right_entity_id: str
    outcome: str
    evidence_candidate_ids: tuple[str, ...]
    factors: Mapping[str, Any]
    reversible: bool
    review_state: str

    def __post_init__(self) -> None:
        if self.outcome not in IDENTITY_OUTCOMES or not self.reversible:
            raise ValueError("identity proposals must be reversible and bounded")


ORIGIN_RELATIONSHIPS = frozenset({
    "PUBLISHED_BY", "AUTHORED_BY", "HOSTED_BY", "CITES", "DERIVED_FROM",
    "SYNDICATED_FROM", "TRANSLATED_FROM", "MIRRORS", "ARCHIVES", "SUMMARIZES",
    "UPDATES", "CORRECTS", "RETRACTS", "SUPERSEDES", "COMMON_EVIDENCE_BASIS",
    "UNKNOWN_DEPENDENCE",
})


@dataclass(frozen=True)
class SourceOriginEdge(Record):
    edge_id: str
    source_id: str
    target_id: str
    relationship: str
    evidence_candidate_ids: tuple[str, ...]
    metadata_basis: tuple[str, ...]
    valid_time: tuple[str | None, str | None]
    recorded_time: str
    provider: str
    review_state: str
    confidence_dimensions: Mapping[str, float]
    access_marking: Mapping[str, Any]

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)
        if self.source_id == self.target_id or self.relationship not in ORIGIN_RELATIONSHIPS:
            raise ValueError("invalid directional source-origin edge")


EPISTEMIC_STATES = frozenset({
    "DIRECTLY_STATED", "REPORTED", "EXTRACTED", "SUPPORTED", "PARTIALLY_SUPPORTED",
    "CONTRADICTED", "QUALIFIED", "INFERRED", "PREDICTED", "DISPUTED", "RETRACTED", "UNKNOWN",
})


@dataclass(frozen=True)
class EvidenceBasis(Record):
    basis_id: str
    case_id: str
    source_object_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    source_family_id: str
    independence_state: str
    active: bool
    correction_state: str
    review_state: str


@dataclass(frozen=True)
class ClaimUnit(Record):
    claim_id: str
    case_id: str
    normalized_statement: str
    original_wording: str
    subject: str
    predicate: str
    object_or_value: str
    temporal_scope: tuple[str | None, str | None]
    geographic_scope: tuple[str, ...]
    modality: str
    polarity: str
    epistemic_state: str
    evidence_basis_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    review_state: str
    version: int

    def __post_init__(self) -> None:
        if self.epistemic_state not in EPISTEMIC_STATES:
            raise ValueError("invalid epistemic state")
        if self.epistemic_state != "UNKNOWN" and not self.candidate_ids:
            raise ValueError("claim requires source-span candidate")


CONTRADICTION_TYPES = frozenset({
    "LOGICAL_CONTRADICTION", "TEMPORAL_UPDATE", "SCOPE_DIFFERENCE", "DEFINITION_DIFFERENCE",
    "SOURCE_DISAGREEMENT", "NUMERIC_DISAGREEMENT", "IDENTITY_DISAGREEMENT", "POLARITY_CONFLICT",
    "CORRECTION", "RETRACTION", "SUPERSESSION", "UNRESOLVED",
})


@dataclass(frozen=True)
class ClaimRelation(Record):
    relation_id: str
    left_claim_id: str
    right_claim_id: str
    relation_type: str
    evidence_candidate_ids: tuple[str, ...]
    temporal_relationship: str
    scope_relationship: str
    provider: str
    review_state: str
    resolution_state: str

    def __post_init__(self) -> None:
        valid = CONTRADICTION_TYPES | {"SUPPORT", "QUALIFICATION", "APPLICABILITY"}
        if self.relation_type not in valid:
            raise ValueError("invalid claim/evidence relationship")
        if self.left_claim_id == self.right_claim_id:
            raise ValueError("claim relationship must be directional")


HYPOTHESIS_STATUSES = frozenset({
    "OPEN", "SUPPORTED", "WEAKLY_SUPPORTED", "DISPUTED", "REJECTED", "UNRESOLVED", "SUPERSEDED",
})


@dataclass(frozen=True)
class InvestigationHypothesis(Record):
    hypothesis_id: str
    case_id: str
    statement: str
    scope: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    independent_evidence_count: int
    source_dependence_summary: str
    assumptions: tuple[str, ...]
    unknowns: tuple[str, ...]
    disconfirming_evidence_requirement: str
    status: str
    analyst_or_provider: str
    review_state: str
    history: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError("invalid hypothesis status")


@dataclass(frozen=True)
class InferenceRecord(Record):
    inference_id: str
    conclusion: str
    premise_claim_ids: tuple[str, ...]
    evidence_basis_ids: tuple[str, ...]
    rule_or_provider: str
    assumptions: tuple[str, ...]
    alternatives: tuple[str, ...]
    uncertainty: str
    prohibited_overreach: tuple[str, ...]


@dataclass(frozen=True)
class SentenceEvidence(Record):
    sentence_id: str
    section: str
    sentence: str
    sentence_type: str
    claim_ids: tuple[str, ...]
    evidence_basis_ids: tuple[str, ...]
    source_object_ids: tuple[str, ...]
    epistemic_state: str
    inference_id: str | None = None


@dataclass(frozen=True)
class MissionEvidenceHandoff(Record):
    handoff_id: str
    case_id: str
    claim_or_hypothesis_ids: tuple[str, ...]
    evidence_basis_ids: tuple[str, ...]
    source_dependence_summary: str
    correction_retraction_state: str
    uncertainty: str
    proposed_implication: str
    recipient: str
    action_kind: str
    access_marking: Mapping[str, Any]
    review_state: str
    expires_at: str
    integrity_hash: str
    direct_operational_mutation: bool = False

    def __post_init__(self) -> None:
        require_aware(self.expires_at); require_hash(self.integrity_hash)
        if self.direct_operational_mutation:
            raise ValueError("OSINT handoff cannot directly mutate operational state")


ADMISSION_STATUSES = frozenset({
    "DRAFT", "STRUCTURALLY_VALID", "EVIDENCE_INCOMPLETE", "IDENTITY_UNRESOLVED",
    "CONTRADICTION_UNRESOLVED", "HUMAN_REVIEW_REQUIRED", "AI_SECONDARY_REVIEW_ONLY",
    "APPROVAL_BLOCKED", "REJECTED", "SUPERSEDED", "READY_FOR_FUTURE_HUMAN_REVIEW",
})


@dataclass(frozen=True)
class KernelAdmissionProposalV4(Record):
    proposal_id: str
    case_id: str
    proposed_concept: str
    target_table_or_action: str
    proposed_field_values: Mapping[str, Any]
    source_object_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    evidence_basis_ids: tuple[str, ...]
    source_independence_state: str
    identity_state: str
    temporal_scope: tuple[str | None, str | None]
    contradiction_state: str
    correction_retraction_state: str
    mapping_precision: str
    review_requirements: tuple[str, ...]
    access_marking: Mapping[str, Any]
    reversibility: str
    dependencies: tuple[str, ...]
    policy_version: str
    provider: str
    creator_actor_id: str
    status: str
    integrity_hash: str

    def __post_init__(self) -> None:
        require_hash(self.integrity_hash)
        if self.status not in ADMISSION_STATUSES:
            raise ValueError("forbidden admission status")
        if self.status in {"HUMAN_APPROVED", "ADMITTED", "CANONICAL_WRITE_COMPLETE"}:
            raise ValueError("V4 cannot approve or admit canonical state")


@dataclass(frozen=True)
class KernelAdmissionValidation(Record):
    validation_id: str
    proposal_id: str
    structurally_valid: bool
    evidence_complete: bool
    identity_resolved: bool
    contradiction_resolved: bool
    dependencies_valid: bool
    separation_of_duties_valid: bool
    resulting_status: str
    rationale: tuple[str, ...]
    validator_node_id: str
    validated_time: str


@dataclass(frozen=True)
class KernelShadowOperation(Record):
    operation_id: str
    proposal_id: str
    operation_type: str
    target: str
    field_mapping: Mapping[str, Any]
    dependencies: tuple[str, ...]
    blocked: bool
    block_reasons: tuple[str, ...]
    reversible: bool


@dataclass(frozen=True)
class KernelShadowDiff(Record):
    diff_id: str
    fixture_hash_before: str
    proposed_operations: tuple[KernelShadowOperation, ...]
    blocked_operation_ids: tuple[str, ...]
    conflicts: tuple[str, ...]
    expected_fixture_hash_after: str
    fixture_mutated: bool
    canonical_write_attempts: int
    canonical_writes: int
    integrity_hash: str

    def __post_init__(self) -> None:
        for value in (self.fixture_hash_before, self.expected_fixture_hash_after, self.integrity_hash):
            require_hash(value)
        if self.fixture_mutated or self.canonical_write_attempts or self.canonical_writes:
            raise ValueError("kernel shadow diff must be zero-write")
