"""Shared V5.1 vocabularies, records, and capability-completion metrics.

CURUNIR_EPISTEMIC_CAPABILITY_CLOSURE_AND_CLEAN_GENERALIZATION_V5_1.

This module is the single authority for the V5.1 ontologies.  Every other
v5_1 module imports its vocabularies from here; no surface module may define
a private outcome, role, dependence, support, or relation vocabulary.

Frozen V4/V5 modules are composed, never edited.  V4 records remain valid
inputs everywhere; V5.1 wraps them with the richer states required by the
milestone contract.

Research shadow only.  Nothing here claims human validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from ..v4.models import Record, canonical_json, sha256, stable_id  # noqa: F401


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Section 9 — capability outcome model
# ---------------------------------------------------------------------------

CAPABILITY_OUTCOMES = frozenset({
    "RESOLVED_CORRECTLY",
    "RESOLVED_WITH_MATERIAL_QUALIFICATION",
    "EPISTEMICALLY_UNRESOLVABLE",
    "REJECTED_UNSUPPORTED",
    "SYSTEM_CAPABILITY_FAILURE",
})

# The task-unit kinds the outcome model applies to (Section 9).
OUTCOME_SUBJECT_KINDS = frozenset({
    "EXTRACTION_CANDIDATE", "ENTITY_IDENTITY", "INSTITUTIONAL_IDENTITY",
    "SOURCE_ROLE", "SOURCE_ORIGIN_EDGE", "SOURCE_DEPENDENCE_RELATION",
    "CLAIM_SUPPORT_RELATION", "CONTRADICTION_UPDATE_RELATION",
    "HYPOTHESIS", "REPORT_PROPOSITION",
})

CAPABILITY_FAILURE_CLASSES = frozenset({
    "PARSER_INCAPABILITY", "READING_ORDER_LOSS", "POLARITY_LOSS",
    "MODALITY_LOSS", "TEMPORAL_SCOPE_LOSS", "GEOGRAPHIC_SCOPE_LOSS",
    "ROLE_METADATA_UNINTERPRETED", "HOST_PUBLISHER_CONFLATION",
    "TRANSLATION_UNRECOGNIZED", "MIRROR_UNRECOGNIZED",
    "CITATION_UNCONNECTED", "CORRECTION_LOST", "UPDATE_CONTRADICTION_CONFUSED",
    "PROPOSITION_RENDER_FAILURE", "CONTEXT_INSUFFICIENT_BY_CONSTRUCTION",
    "SEMANTIC_TYPE_ERROR", "IDENTITY_EVIDENCE_UNINTERPRETED",
    "DEPENDENCE_EVIDENCE_UNINTERPRETED", "OTHER_SOFTWARE_DEFECT",
})


@dataclass(frozen=True)
class CapabilityOutcome(Record):
    """One adjudicated outcome for one task unit.

    ``EPISTEMICALLY_UNRESOLVABLE`` is only constructible when the packet
    demonstrates that the underlying public evidence is insufficient: the
    ``evidence_insufficiency_demonstration`` must name the missing evidence
    and why it is not recoverable from the available public record.

    ``SYSTEM_CAPABILITY_FAILURE`` always carries a defect class and counts
    as an error in every metric.
    """

    outcome_id: str
    subject_kind: str
    subject_id: str
    outcome: str
    rationale: str
    material_qualifications: tuple[str, ...]
    evidence_insufficiency_demonstration: str | None
    capability_failure_class: str | None
    recorded_time: str

    def __post_init__(self) -> None:
        if self.subject_kind not in OUTCOME_SUBJECT_KINDS:
            raise ValueError(f"unknown outcome subject kind: {self.subject_kind}")
        if self.outcome not in CAPABILITY_OUTCOMES:
            raise ValueError(f"unknown capability outcome: {self.outcome}")
        if not self.rationale.strip():
            raise ValueError("capability outcome requires a rationale")
        if self.outcome == "EPISTEMICALLY_UNRESOLVABLE":
            demonstration = (self.evidence_insufficiency_demonstration or "").strip()
            if len(demonstration) < 40:
                raise ValueError(
                    "EPISTEMICALLY_UNRESOLVABLE requires a substantive "
                    "evidence-insufficiency demonstration, not a bare unknown")
        if self.outcome == "SYSTEM_CAPABILITY_FAILURE":
            if self.capability_failure_class not in CAPABILITY_FAILURE_CLASSES:
                raise ValueError("SYSTEM_CAPABILITY_FAILURE requires a defect class")
        if self.outcome == "RESOLVED_WITH_MATERIAL_QUALIFICATION" and not self.material_qualifications:
            raise ValueError("qualified resolution requires the preserved qualifications")


def capability_outcome(*, subject_kind: str, subject_id: str, outcome: str, rationale: str,
                       material_qualifications: tuple[str, ...] = (),
                       evidence_insufficiency_demonstration: str | None = None,
                       capability_failure_class: str | None = None) -> CapabilityOutcome:
    return CapabilityOutcome(
        stable_id("capability-outcome", subject_kind, subject_id, outcome),
        subject_kind, subject_id, outcome, rationale, material_qualifications,
        evidence_insufficiency_demonstration, capability_failure_class, now_utc())


# ---------------------------------------------------------------------------
# Section 12 — candidate admission and function
# ---------------------------------------------------------------------------

ADMISSION_STAGES = (
    "STRUCTURALLY_VALID",
    "SEMANTICALLY_PARSED",
    "EVIDENCE_BOUND",
    "ACCEPTED_CANDIDATE",
    "QUARANTINED",
    "REJECTED",
)

CANDIDATE_FUNCTIONS = frozenset({
    "ANALYTICALLY_MATERIAL", "SUPPORTING_DETAIL", "NAVIGATIONAL_METADATA",
    "DOCUMENT_STRUCTURE", "DUPLICATE", "NOISE", "UNKNOWN_VALUE",
})

# Section 12.7 — required candidate-type coverage in the clean corpus.
REQUIRED_CANDIDATE_TYPES = (
    "ENTITY_MENTION", "CLAIM", "RELATION", "EVENT", "QUOTATION", "DATE",
    "NUMERIC_VALUE", "CORRECTION", "RETRACTION", "CITATION",
    "POLARITY", "MODALITY", "TEMPORAL_SCOPE", "GEOGRAPHIC_SCOPE",
)

POLARITIES = frozenset({"POSITIVE", "NEGATIVE"})

MODALITIES = frozenset({
    "ASSERTED", "REPORTED", "ATTRIBUTED", "PLANNED", "PROPOSED", "INTENDED",
    "VENDOR_DESCRIBED", "EXERCISED", "PILOTED", "DEPLOYED", "OPERATIONAL",
    "PRELIMINARY", "FINAL", "DISPUTED", "PREDICTED", "CONDITIONAL",
    "RECOMMENDED", "REQUIRED_BY_LAW", "ALLEGED",
})


# ---------------------------------------------------------------------------
# Section 13 — source roles and manifestation model
# ---------------------------------------------------------------------------

SOURCE_ROLES = frozenset({
    "AUTHORED_BY", "EDITED_BY", "SUBMITTED_BY", "ISSUED_BY", "PUBLISHED_BY",
    "HOSTED_BY", "MIRRORED_BY", "ARCHIVED_BY", "TRANSLATED_BY",
    "SYNDICATED_BY", "COMMISSIONED_BY", "OWNED_BY", "OPERATED_BY",
})

# Content-derivation relations between publications/manifestations (kept
# distinct from agent roles; V4 mixed them in one vocabulary).
CONTENT_RELATIONS = frozenset({
    "CITES", "DERIVED_FROM", "SYNDICATED_FROM", "TRANSLATED_FROM", "MIRRORS",
    "ARCHIVES", "SUMMARIZES", "UPDATES", "CORRECTS", "RETRACTS", "SUPERSEDES",
    "COMMON_EVIDENCE_BASIS", "UNKNOWN_DEPENDENCE", "REPUBLISHES",
})

MANIFESTATION_KINDS = frozenset({
    "DOCUMENT_MANIFESTATION", "PUBLICATION_RECORD", "INTELLECTUAL_WORK",
    "SOURCE_FAMILY", "EVIDENCE_BASIS",
})

ENTITY_CLASSES = frozenset({
    "ORGANIZATION", "LEGAL_ENTITY", "PROGRAMME", "PLATFORM",
    "PROCUREMENT_VEHICLE", "VENDOR", "PRODUCT", "PERSON_PUBLIC_ROLE",
    "PUBLICATION_VENUE", "GOVERNMENT_BODY", "COURT_OR_TRIBUNAL", "UNKNOWN_CLASS",
})

IDENTITY_EVIDENCE_KINDS = frozenset({
    "OFFICIAL_IDENTIFIER", "LEGAL_NAME", "DOMAIN", "PUBLICATION_METADATA",
    "EXPLICIT_INSTITUTIONAL_ATTRIBUTION", "ORGANIZATIONAL_HIERARCHY",
    "DOCUMENT_SERIES", "PROGRAMME_OWNERSHIP", "PRODUCT_OWNERSHIP",
    "TEMPORAL_CONTINUITY", "LANGUAGE_ALIAS", "HISTORICAL_NAME",
    "PARENT_SUBSIDIARY_RELATION",
})

ROLE_EVIDENCE_KINDS = frozenset({
    "EXPLICIT_METADATA", "MASTHEAD_OR_IMPRINT", "BYLINE", "DOCUMENT_COVER",
    "SUBMISSION_RECORD", "OFFICIAL_REGISTER", "DOMAIN_OWNERSHIP_RECORD",
    "ARCHIVE_BANNER", "TRANSLATION_NOTICE", "SYNDICATION_NOTICE",
    "COMMISSIONING_STATEMENT", "OPERATING_NOTICE", "EXPLICIT_TEXT_SPAN",
})


# ---------------------------------------------------------------------------
# Section 14 — dependence states
# ---------------------------------------------------------------------------

DEPENDENCE_STATES = frozenset({
    "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
    "MIRROR_MANIFESTATION", "COMMON_EVIDENCE_BASIS_CONFIRMED",
    "PARTIAL_DEPENDENCE", "SHARED_DATA_INDEPENDENT_ANALYSIS",
    "INDEPENDENCE_SUPPORTED", "NO_DEPENDENCE_FOUND", "INDEPENDENCE_UNKNOWN",
    "DEPENDENCE_DISPUTED",
})

# States that may NEVER be counted as independent corroboration.
NON_CORROBORATING_STATES = frozenset({
    "DERIVATIVE_CONFIRMED", "TRANSLATION_DERIVATIVE", "SYNDICATION_DERIVATIVE",
    "MIRROR_MANIFESTATION", "NO_DEPENDENCE_FOUND", "INDEPENDENCE_UNKNOWN",
    "DEPENDENCE_DISPUTED",
})

DEPENDENCE_SIGNAL_KINDS = frozenset({
    "EXPLICIT_CITATION", "EXPLICIT_ATTRIBUTION", "COMMON_SOURCE_LINK",
    "IDENTICAL_QUOTATION", "NORMALIZED_PARAGRAPH_OVERLAP", "PUBLICATION_TIMING",
    "WIRE_SERVICE_INDICATION", "PRESS_RELEASE_FINGERPRINT", "DOCUMENT_METADATA",
    "TRANSLATION_ALIGNMENT", "SHARED_TABLE_OR_GRAPHIC", "SHARED_UNIQUE_ERROR",
    "COMMON_QUOTED_INDIVIDUAL", "COMMON_ANONYMOUS_ATTRIBUTION",
    "COMMON_PRIMARY_DATASET", "COMMON_OFFICIAL_ANNEX", "EXPLICIT_REUSE_STATEMENT",
    "DISTINCT_AUTHORSHIP_EVIDENCE", "DISTINCT_REPORTING_DETAIL",
    "ON_THE_RECORD_ORIGINAL_INTERVIEW",
})


# ---------------------------------------------------------------------------
# Section 15 — claim support
# ---------------------------------------------------------------------------

SUPPORT_STATES = frozenset({
    "FULL_SUPPORT", "PARTIAL_SUPPORT", "QUALIFIED_SUPPORT",
    "CONTEXT_DEPENDENT_SUPPORT", "CONTRADICTED", "NOT_SUPPORTED",
    "WRONG_SCOPE", "WRONG_TIME", "WRONG_ENTITY", "WRONG_MODALITY",
    "WRONG_POLARITY", "INFERENCE_ONLY",
})

REQUIRED_CLAIM_CLASSES = (
    "DIRECT_FACTUAL_ASSERTION", "ATTRIBUTED_REPORT", "CAPABILITY_CLAIM",
    "PROGRAMME_EXISTENCE_CLAIM", "OWNERSHIP_OR_GOVERNANCE_CLAIM",
    "DEPLOYMENT_CLAIM", "PROCUREMENT_CLAIM", "TIMELINE_CLAIM",
    "NUMERIC_CLAIM", "CAUSAL_CLAIM", "LEGAL_OR_REGULATORY_CLAIM",
    "INFERENCE", "QUALIFICATION", "UNCERTAINTY", "NEGATIVE_CLAIM",
    "ABSENCE_OF_EVIDENCE_STATEMENT",
)


# Shared evidence/claim lifecycle (V4 ``retract_basis`` inactive states plus
# CURRENT).  Single authority: claim_support blocking and contradiction
# versioning must consume the same vocabulary.
EVIDENCE_LIFECYCLE_STATES = frozenset({
    "CURRENT", "CORRECTED", "RETRACTED", "SUPERSEDED",
})


# ---------------------------------------------------------------------------
# Section 16 — contradiction, correction, supersession
# ---------------------------------------------------------------------------

RELATION_CLASSES = frozenset({
    "LOGICAL_CONTRADICTION", "TEMPORAL_UPDATE", "SCOPE_DIFFERENCE",
    "DEFINITION_DIFFERENCE", "SOURCE_DISAGREEMENT", "NUMERIC_DISAGREEMENT",
    "IDENTITY_DISAGREEMENT", "POLARITY_CONFLICT", "QUALIFICATION",
    "CORRECTION", "RETRACTION", "SUPERSESSION", "UNRESOLVED", "NO_CONFLICT",
})


# ---------------------------------------------------------------------------
# Section 17 — report faithfulness
# ---------------------------------------------------------------------------

MATERIAL_QUALIFICATION_MARKERS = frozenset({
    "PLANNED", "PROPOSED", "INTENDED", "REPORTED", "VENDOR_DESCRIBED",
    "EXERCISED", "PILOTED", "DEPLOYED", "OPERATIONAL", "DISPUTED",
    "PRELIMINARY", "FINAL", "CORRECTED", "SUPERSEDED", "UNRESOLVED",
})

# Phrases that require affirmative dependence-graph support (Section 17.3).
INDEPENDENCE_LANGUAGE_PATTERNS = (
    r"independently\s+(?:confirmed|verified|corroborated|established)",
    r"multiple\s+independent\s+sources",
    r"corroborated\s+by\s+several\s+sources",
    r"independent\s+(?:confirmation|corroboration|verification)",
    r"independently\s+retrieved",
)

PROPOSITION_DISPOSITIONS = frozenset({
    "PUBLISHED_FAITHFULLY", "PUBLISHED_WITH_QUALIFICATION",
    "OMITTED_EVIDENCE_INSUFFICIENT", "OMITTED_NOT_MATERIAL",
    "OMITTED_ACCESS_RESTRICTED", "REFUSED_UNSUPPORTED",
    "SYSTEM_CAPABILITY_FAILURE",
})


# ---------------------------------------------------------------------------
# Section 18 — packet system
# ---------------------------------------------------------------------------

CONTEXT_SUFFICIENCY = frozenset({
    "CONTEXT_SUFFICIENT", "CONTEXT_PARTIALLY_SUFFICIENT",
    "CONTEXT_INSUFFICIENT_BY_NATURE", "PACKET_CONSTRUCTION_DEFECT",
})

REVIEW_DECISIONS = frozenset({
    "CORRECT", "INCORRECT", "PARTIALLY_CORRECT", "INSUFFICIENT_INFORMATION",
    "AMBIGUOUS", "CANNOT_ADJUDICATE", "PACKET_DEFECT",
    "EPISTEMICALLY_UNRESOLVABLE",
})


# ---------------------------------------------------------------------------
# Section 10 — capability-completion metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CapabilityMetrics:
    total_required_cases: int
    adjudicable_cases: int
    epistemically_unresolvable_cases: int
    correctly_identified_unresolvable_cases: int
    system_capability_failures: int
    correctly_resolved_cases: int
    qualified_correct_cases: int
    unsupported_rejections: int
    correctly_rejected_unsupported_cases: int
    partial_results: int
    incorrect_results: int
    packet_defects: int
    reviewer_disagreements: int

    def as_report(self) -> dict[str, Any]:
        total = max(1, self.total_required_cases)
        adjudicable = max(1, self.adjudicable_cases)
        completion = (self.correctly_resolved_cases + self.qualified_correct_cases +
                      self.correctly_identified_unresolvable_cases +
                      self.correctly_rejected_unsupported_cases)
        resolved = (self.correctly_resolved_cases + self.qualified_correct_cases +
                    self.correctly_rejected_unsupported_cases)
        semantic_denominator = max(1, self.adjudicable_cases)
        return {
            "total_required_cases": self.total_required_cases,
            "adjudicable_cases": self.adjudicable_cases,
            "epistemically_unresolvable_cases": self.epistemically_unresolvable_cases,
            "system_capability_failures": self.system_capability_failures,
            "correctly_resolved_cases": self.correctly_resolved_cases,
            "qualified_correct_cases": self.qualified_correct_cases,
            "unsupported_rejections": self.unsupported_rejections,
            "partial_results": self.partial_results,
            "incorrect_results": self.incorrect_results,
            "semantic_correctness": round(resolved / semantic_denominator, 4),
            "task_completion_rate": round(completion / total, 4),
            "resolved_capability_rate": round(resolved / adjudicable, 4),
            "capability_failure_rate": round(self.system_capability_failures / total, 4),
            "critical_error_rate": round(self.incorrect_results / total, 4),
            "packet_defect_rate": round(self.packet_defects / total, 4),
            "reviewer_disagreement_rate": round(self.reviewer_disagreements / total, 4),
            "genuine_epistemic_unresolvability_rate": round(
                self.epistemically_unresolvable_cases / total, 4),
            "software_capability_failure_rate": round(
                self.system_capability_failures / total, 4),
        }


# ---------------------------------------------------------------------------
# Shared confidence contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRef(Record):
    """A typed pointer from a V5.1 decision to its supporting evidence."""

    evidence_kind: str
    source_object_id: str | None
    candidate_id: str | None
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError("evidence reference requires detail")
        if not (self.source_object_id or self.candidate_id):
            raise ValueError("evidence reference requires a source object or candidate")
