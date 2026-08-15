"""Operational record contracts for the mission-data fabric.

Write-path discipline: records are constructed as frozen dataclasses (which
validate), serialized once via `to_record()`, and appended to the event log.
Read paths work on the plain-dict form. Unknown stays a valid value everywhere:
quality dimensions, times and confidences may be "UNKNOWN" or None, never
silently defaulted to certainty.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Mapping

from .access import ACTOR_KINDS, ROLE_RANK, Marking
from .canonical import require_aware, require_aware_or_none, require_sha256, sha256
from .geometry import Geometry

SOURCE_TYPES = ("SYSTEM", "SENSOR", "ORGANISATION", "REPORTER", "PUBLICATION", "SYNTHETIC_FIXTURE", "EVIDENCE_ADAPTER")
SOURCE_STATUS = ("ACTIVE", "DEGRADED", "SUSPENDED", "RETIRED")
VALIDATION_STATES = ("VALID", "INVALID", "UNSUPPORTED_SCHEMA_VERSION", "DUPLICATE")
OBJECT_TYPES = ("LOCATION", "INFRASTRUCTURE", "ROUTE", "ASSET", "RESOURCE_STOCK", "MOVEMENT", "ORGANISATION", "OPERATIONAL_CONCERN", "OBSERVATION", "PLAN",
                # V6 world-model extension: universal evidence-bound entity classes
                "PERSON", "GROUP", "WEB_DOMAIN", "INTELLECTUAL_WORK", "PUBLIC_IDENTIFIER")
LIFECYCLES = ("PROPOSED", "ACTIVE", "SUPERSEDED", "RETIRED", "QUARANTINED")
EPISTEMIC_STATES = ("OBSERVED", "REPORTED", "EXTRACTED", "INFERRED", "PREDICTED", "PLANNED", "DISPUTED", "CANCELLED", "CORRECTED", "UNKNOWN")
RELATION_TYPES = ("LOCATED_AT", "MOVING_ALONG", "SUPPLIES", "DEPENDS_ON", "REPORTS_ON", "DERIVED_FROM", "CONFLICTS_WITH", "PLANNED_FOR", "AFFECTS", "REPLACES", "ASSOCIATED_WITH", "POSSIBLY_SAME_AS", "SAME_AS", "ALTERNATE_OF",
                  # V6 world-model extension: compact typed predicates for the semantic plane
                  "HOLDS_ROLE", "OWNS", "PUBLISHED_BY", "OPERATES", "SUCCESSOR_OF", "HAS_IDENTIFIER", "MENTIONS")
RELATION_STATUS = ("PROPOSED", "ACTIVE", "REJECTED", "RETIRED")
DERIVATIONS = ("MAPPING", "RULE", "MODEL", "ANALYST", "EVIDENCE")
TIME_PRECISIONS = ("EXACT", "MINUTE", "HOUR", "DAY", "APPROXIMATE", "UNKNOWN")
SYNC_STATES = ("SYNCHRONIZED", "STALE", "UNKNOWN")
PROVENANCE_MODES = ("OPERATIONAL", "EVIDENTIARY")
ASSOCIATION_OUTCOMES = ("AUTO_ASSOCIATE", "PROPOSE_ASSOCIATION", "REJECT_ASSOCIATION", "UNKNOWN")
ASSOCIATION_RESOLUTIONS = ("ACCEPTED", "REJECTED", "SPLIT", "REVERSED")
ALERT_STATUS = ("OPEN", "ACKNOWLEDGED", "RESOLVED", "EXPIRED")
SEVERITIES = ("INFO", "WARNING", "HIGH", "CRITICAL")
ANALYST_ACTION_KINDS = ("ACKNOWLEDGE", "ANNOTATE", "ACCEPT", "REJECT", "DEFER", "ESCALATE")
DECISION_STATES = ("ACCEPTED", "REJECTED", "MODIFIED", "DEFERRED")
RECOMMENDATION_KINDS = ("INFORMATION_REQUEST", "SOURCE_INSPECTION", "ROUTE_CHANGE", "SCHEDULE_CHANGE", "OTHER_NON_EXECUTING")
ACCREDITATION_STATES = ("SYNTHETIC_EVALUATION_ONLY", "UNACCREDITED")
PROPOSAL_TYPES = ("ALERT_CANDIDATE", "ASSOCIATION_CANDIDATE", "ASSESSMENT", "RELATIONSHIP_CANDIDATE",
                  "STATE_CANDIDATE", "RECOMMENDATION_CANDIDATE",
                  # V6 analytical layer: model-proposed theme/narrative/stakeholder/
                  # impact candidates awaiting explicit human acceptance
                  "ANALYTICAL_OBJECT_CANDIDATE")
PROPOSAL_STATUS = ("PROPOSED", "ACCEPTED", "REJECTED")
QUALITY_DIMENSIONS = ("schema_validity", "completeness", "freshness", "temporal_precision", "geospatial_precision",
                      "source_reliability", "information_credibility", "identity_confidence", "mapping_confidence",
                      "transformation_lossiness", "synchronization_state", "evidence_independence", "review_state")


def _member(value: str, allowed: tuple[str, ...], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {label}: {value!r}")


def _check_quality(quality: Mapping[str, Any]) -> None:
    unknown = [k for k in quality if k not in QUALITY_DIMENSIONS]
    if unknown:
        raise ValueError(f"unknown quality dimensions: {unknown}")
    for key, value in quality.items():
        if value is None:
            raise ValueError(f"quality dimension {key} must be a value or 'UNKNOWN', not None")


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_record"):
        return value.to_record()
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    return value


class Record:
    RECORD_TYPE = ""

    def to_record(self) -> dict[str, Any]:
        data = {f.name: _plain(getattr(self, f.name)) for f in fields(self)}
        data["record_type"] = self.RECORD_TYPE
        return data


@dataclass(frozen=True)
class ExternalRef(Record):
    RECORD_TYPE = "external_ref"
    system: str; external_id: str; imported_version: str; ingestion_id: str
    source_time: str | None = None; sync_status: str = "UNKNOWN"
    # entity identities (registry ids) bear on association; report identities
    # (sighting/report numbers) do not — two reports from one system may still
    # describe one entity.
    identity_bearing: bool = True

    def __post_init__(self):
        _member(self.sync_status, SYNC_STATES, "sync status"); require_aware_or_none(self.source_time)


@dataclass(frozen=True)
class EvidenceRef(Record):
    """Evidentiary provenance carried from ARGUS Source Intelligence, unstripped."""
    RECORD_TYPE = "evidence_ref"
    source_object_id: str; document_id: str; content_sha256: str; assertion_id: str
    evidence_basis_id: str; identity_status: str; authority_state: str; independence_status: str
    claim_basis_status: str; review_state: str; mapping_status: str
    dependence_group_id: str | None = None; unresolved: tuple[str, ...] = ()

    def __post_init__(self):
        require_sha256(self.content_sha256)


@dataclass(frozen=True)
class ProvenanceSummary(Record):
    RECORD_TYPE = "provenance_summary"
    mode: str; source_ids: tuple[str, ...] = (); ingestion_ids: tuple[str, ...] = ()
    transformation_ids: tuple[str, ...] = (); evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self):
        _member(self.mode, PROVENANCE_MODES, "provenance mode")
        if self.mode == "EVIDENTIARY" and not self.evidence:
            raise ValueError("evidentiary provenance requires evidence references")


@dataclass(frozen=True)
class SourceRecord(Record):
    RECORD_TYPE = "source"
    source_id: str; source_type: str; source_system: str; external_id: str
    operator: str; authority: str; reliability: dict[str, Any]; marking: Marking
    status: str; note: str; created_time: str

    def __post_init__(self):
        _member(self.source_type, SOURCE_TYPES, "source type"); _member(self.status, SOURCE_STATUS, "source status")
        require_aware(self.created_time)


@dataclass(frozen=True)
class IngestionEvent(Record):
    RECORD_TYPE = "ingestion"
    ingestion_id: str; connector_id: str; connector_version: str; source_id: str
    source_time: str | None; received_time: str; content_sha256: str
    schema_id: str; schema_version: str; idempotency_key: str; validation: str
    quarantined: bool; quarantine_reasons: tuple[str, ...]; duplicate_of: str | None
    late: bool; payload_ref: str | None; marking: Marking

    def __post_init__(self):
        _member(self.validation, VALIDATION_STATES, "validation state")
        require_aware(self.received_time); require_aware_or_none(self.source_time); require_sha256(self.content_sha256)
        if self.quarantined and not self.quarantine_reasons:
            raise ValueError("quarantine requires reasons")


@dataclass(frozen=True)
class TransformationRecord(Record):
    RECORD_TYPE = "transformation"
    transformation_id: str; implementation_id: str; implementation_version: str
    mapping_id: str; mapping_version: str
    input_refs: tuple[dict[str, Any], ...]; output_refs: tuple[dict[str, Any], ...]
    actor: str; time: str; warnings: tuple[str, ...]; lossy_operations: tuple[str, ...]; validation: str

    def __post_init__(self):
        _member(self.validation, VALIDATION_STATES, "validation state"); require_aware(self.time)
        for ref in (*self.input_refs, *self.output_refs):
            if "kind" not in ref or "ref" not in ref:
                raise ValueError("transformation refs need kind and ref")


@dataclass(frozen=True)
class ObjectVersion(Record):
    RECORD_TYPE = "object_version"
    object_id: str; version: int; object_type: str; lifecycle: str
    labels: tuple[str, ...]; external_refs: tuple[ExternalRef, ...]
    valid_from: str | None; valid_to: str | None; source_time: str | None; time_precision: str
    recorded_time: str; geometry: Geometry | None; attributes: dict[str, Any]
    quality: dict[str, Any]; epistemic_state: str; marking: Marking; provenance: ProvenanceSummary
    correction_of: str | None = None; correction_reason: str = ""; supersedes_version: int | None = None

    def __post_init__(self):
        _member(self.object_type, OBJECT_TYPES, "object type"); _member(self.lifecycle, LIFECYCLES, "lifecycle")
        _member(self.epistemic_state, EPISTEMIC_STATES, "epistemic state"); _member(self.time_precision, TIME_PRECISIONS, "time precision")
        require_aware(self.recorded_time)
        for value in (self.valid_from, self.valid_to, self.source_time):
            require_aware_or_none(value)
        _check_quality(self.quality)
        if self.version < 1:
            raise ValueError("versions start at 1")
        if self.correction_of and not self.correction_reason:
            raise ValueError("corrections require a reason")

    @property
    def version_id(self) -> str:
        return f"{self.object_id}@v{self.version}"


@dataclass(frozen=True)
class RelationshipVersion(Record):
    RECORD_TYPE = "relationship_version"
    relationship_id: str; version: int; relation_type: str
    source_object_id: str; target_object_id: str
    valid_from: str | None; valid_to: str | None; recorded_time: str
    evidence_refs: tuple[str, ...]; derivation: str; confidence: float | str
    status: str; marking: Marking; provenance: ProvenanceSummary; rationale: str = ""

    def __post_init__(self):
        _member(self.relation_type, RELATION_TYPES, "relation type"); _member(self.derivation, DERIVATIONS, "derivation")
        _member(self.status, RELATION_STATUS, "relation status"); require_aware(self.recorded_time)
        require_aware_or_none(self.valid_from); require_aware_or_none(self.valid_to)
        if isinstance(self.confidence, str) and self.confidence != "UNKNOWN":
            raise ValueError("confidence is a float or 'UNKNOWN'")
        if self.version < 1:
            raise ValueError("versions start at 1")


@dataclass(frozen=True)
class ActivityRecord(Record):
    RECORD_TYPE = "activity"
    activity_id: str; activity_type: str; epistemic_state: str
    subject_ids: tuple[str, ...]; description: str
    valid_from: str | None; valid_to: str | None; source_time: str | None
    recorded_time: str; evidence_refs: tuple[str, ...]; marking: Marking; provenance: ProvenanceSummary
    # V6 world-model extension: events carry role-typed participants and a
    # bounded time precision; defaults keep every pre-extension record valid.
    participants: tuple[tuple[str, str], ...] = ()  # (object_id, role)
    time_precision: str = "UNKNOWN"

    def __post_init__(self):
        _member(self.epistemic_state, EPISTEMIC_STATES, "epistemic state"); require_aware(self.recorded_time)
        _member(self.time_precision, TIME_PRECISIONS, "time precision")
        for value in (self.valid_from, self.valid_to, self.source_time):
            require_aware_or_none(value)


@dataclass(frozen=True)
class AssociationProposal(Record):
    RECORD_TYPE = "association_proposal"
    proposal_id: str; left_object_id: str; right_object_id: str; object_type: str
    features: dict[str, Any]; outcome: str; rationale: tuple[str, ...]
    engine_version: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.outcome, ASSOCIATION_OUTCOMES, "association outcome"); require_aware(self.recorded_time)
        if self.left_object_id == self.right_object_id:
            raise ValueError("association requires two distinct objects")


@dataclass(frozen=True)
class AssociationResolution(Record):
    RECORD_TYPE = "association_resolution"
    resolution_id: str; proposal_id: str; resolution: str
    actor_id: str; actor_kind: str; rationale: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.resolution, ASSOCIATION_RESOLUTIONS, "association resolution")
        _member(self.actor_kind, ACTOR_KINDS, "actor kind"); require_aware(self.recorded_time)


@dataclass(frozen=True)
class Alert(Record):
    RECORD_TYPE = "alert"
    alert_id: str; rule_id: str; rule_version: str; trigger: str
    affected_ids: tuple[str, ...]; evidence_refs: tuple[str, ...]; quality_note: str
    severity: str; severity_rationale: str; dedup_key: str
    expiry_condition: str; recorded_time: str; marking: Marking; status: str = "OPEN"

    def __post_init__(self):
        _member(self.severity, SEVERITIES, "severity"); _member(self.status, ALERT_STATUS, "alert status")
        require_aware(self.recorded_time)
        if not self.evidence_refs:
            raise ValueError("alerts must be evidence-bound")


@dataclass(frozen=True)
class AlertTransition(Record):
    RECORD_TYPE = "alert_transition"
    transition_id: str; alert_id: str; from_status: str; to_status: str
    actor_id: str; actor_kind: str; note: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.from_status, ALERT_STATUS, "alert status"); _member(self.to_status, ALERT_STATUS, "alert status")
        _member(self.actor_kind, ACTOR_KINDS, "actor kind"); require_aware(self.recorded_time)


@dataclass(frozen=True)
class Recommendation(Record):
    RECORD_TYPE = "recommendation"
    recommendation_id: str; alert_ids: tuple[str, ...]; action_kind: str; proposed_action: str
    rationale: str; assumptions: tuple[str, ...]; alternatives: tuple[str, ...]
    evidence_refs: tuple[str, ...]; evidence_snapshot_hash: str; uncertainty: str
    expected_benefit: str; potential_risk: str; expiry: str | None
    required_role: str; provider_id: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.action_kind, RECOMMENDATION_KINDS, "recommendation kind")
        if self.required_role not in ROLE_RANK:
            raise ValueError(f"unknown required role: {self.required_role}")
        require_aware(self.recorded_time); require_aware_or_none(self.expiry); require_sha256(self.evidence_snapshot_hash)
        if not self.evidence_refs:
            raise ValueError("recommendations must be evidence-bound")


@dataclass(frozen=True)
class AnalystAction(Record):
    RECORD_TYPE = "analyst_action"
    action_id: str; actor_id: str; actor_kind: str; actor_roles: tuple[str, ...]
    kind: str; subject_kind: str; subject_id: str; note: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.kind, ANALYST_ACTION_KINDS, "analyst action kind")
        _member(self.actor_kind, ACTOR_KINDS, "actor kind"); require_aware(self.recorded_time)


@dataclass(frozen=True)
class DecisionRecord(Record):
    RECORD_TYPE = "decision"
    decision_id: str; recommendation_id: str; actor_id: str; actor_role: str
    state: str; modification: str; rationale: str; evidence_snapshot_hash: str
    recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.state, DECISION_STATES, "decision state")
        if self.actor_role not in ROLE_RANK:
            raise ValueError(f"unknown role: {self.actor_role}")
        require_aware(self.recorded_time); require_sha256(self.evidence_snapshot_hash)


@dataclass(frozen=True)
class ModelPackage(Record):
    RECORD_TYPE = "model_package"
    model_id: str; version: str; provider: str; task: str
    input_schema_id: str; output_schema_id: str; training_data: str; evaluation_summary: str
    limitations: tuple[str, ...]; approved_uses: tuple[str, ...]; prohibited_uses: tuple[str, ...]
    latency_profile: str; hardware: str; licence: str; accreditation_state: str
    integrity_hash: str = ""

    def __post_init__(self):
        _member(self.accreditation_state, ACCREDITATION_STATES, "accreditation state")
        expected = model_integrity_hash(self)
        if not self.integrity_hash:
            object.__setattr__(self, "integrity_hash", expected)
        elif self.integrity_hash != expected:
            raise ValueError("model package integrity hash mismatch")


def model_integrity_hash(package: ModelPackage) -> str:
    data = {f.name: _plain(getattr(package, f.name)) for f in fields(package) if f.name != "integrity_hash"}
    return sha256(data)


@dataclass(frozen=True)
class AccreditationRecord(Record):
    RECORD_TYPE = "accreditation"
    accreditation_id: str; model_id: str; model_version: str; evaluator: str
    protocol: str; dataset: str; domain: str; metrics: dict[str, Any]
    robustness_checks: tuple[str, ...]; security_checks: tuple[str, ...]
    known_failure_modes: tuple[str, ...]; expires: str | None
    approval_state: str; restrictions: tuple[str, ...]; recorded_time: str

    def __post_init__(self):
        _member(self.approval_state, ACCREDITATION_STATES, "approval state")
        require_aware(self.recorded_time); require_aware_or_none(self.expires)


@dataclass(frozen=True)
class InferenceRecord(Record):
    RECORD_TYPE = "inference"
    inference_id: str; model_id: str; model_version: str
    input_refs: tuple[str, ...]; input_hash: str; output: dict[str, Any]; output_hash: str
    started: str; completed: str; parameters: dict[str, Any]; errors: tuple[str, ...]
    validation: str; marking: Marking; downstream_use: tuple[str, ...] = ()

    def __post_init__(self):
        _member(self.validation, VALIDATION_STATES, "validation state")
        require_aware(self.started); require_aware(self.completed)
        require_sha256(self.input_hash); require_sha256(self.output_hash)


@dataclass(frozen=True)
class AnalyticalProposal(Record):
    RECORD_TYPE = "analytical_proposal"
    proposal_id: str; inference_id: str; proposal_type: str
    content: dict[str, Any]; status: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.proposal_type, PROPOSAL_TYPES, "proposal type"); _member(self.status, PROPOSAL_STATUS, "proposal status")
        require_aware(self.recorded_time)


REQUIREMENT_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
REQUIREMENT_STATUS = ("OPEN", "EVIDENCE_PENDING", "ANSWERED", "CLOSED_UNANSWERED")
EVIDENCE_REQUEST_KINDS = ("STRUCTURED_OBSERVATION", "PUBLIC_SOURCE_CONFIRMATION", "ANALYST_REVIEW",
                          "SOURCE_ORIGIN_REVIEW", "UPDATED_INFRASTRUCTURE_REPORT")
EVIDENCE_REQUEST_STATUS = ("OPEN", "FULFILLED", "FAILED")
TASK_TYPES = ("REVIEW", "COLLECTION_FOLLOWUP", "ASSESSMENT", "COORDINATION")
TASK_STATUS = ("ASSIGNED", "IN_PROGRESS", "BLOCKED", "DONE", "ABANDONED")
WORKFLOW_SUBJECT_KINDS = ("requirement", "analyst_task", "evidence_request")


@dataclass(frozen=True)
class InformationRequirement(Record):
    RECORD_TYPE = "information_requirement"
    requirement_id: str; mission_context: str; question: str
    affected_ids: tuple[str, ...]; priority: str; rationale: str
    required_evidence_type: str; owning_role: str
    created_time: str; due_time: str | None; status: str
    closure_criteria: str; marking: Marking
    # V6: escalation folds re-append; strict next-version keeps a stale
    # concurrent fold from silently de-escalating priority
    version: int = 1

    def __post_init__(self):
        _member(self.priority, REQUIREMENT_PRIORITIES, "requirement priority")
        if self.version < 1:
            raise ValueError("requirement versions start at 1")
        _member(self.status, REQUIREMENT_STATUS, "requirement status")
        if self.owning_role not in ROLE_RANK:
            raise ValueError(f"unknown owning role: {self.owning_role}")
        require_aware(self.created_time); require_aware_or_none(self.due_time)
        if not self.question or not self.closure_criteria:
            raise ValueError("requirements need a question and closure criteria")


@dataclass(frozen=True)
class EvidenceRequest(Record):
    RECORD_TYPE = "evidence_request"
    request_id: str; requirement_id: str; request_kind: str; detail: str
    affected_ids: tuple[str, ...]; status: str
    created_time: str; due_time: str | None; marking: Marking

    def __post_init__(self):
        _member(self.request_kind, EVIDENCE_REQUEST_KINDS, "evidence request kind")
        _member(self.status, EVIDENCE_REQUEST_STATUS, "evidence request status")
        require_aware(self.created_time); require_aware_or_none(self.due_time)


@dataclass(frozen=True)
class AnalystTask(Record):
    RECORD_TYPE = "analyst_task"
    task_id: str; assigned_role: str; assigned_actor: str; task_type: str
    affected_ids: tuple[str, ...]; required_action: str; status: str
    created_time: str; due_time: str | None; depends_on: tuple[str, ...]
    evidence_refs: tuple[str, ...]; completion_result: str; marking: Marking

    def __post_init__(self):
        _member(self.task_type, TASK_TYPES, "task type"); _member(self.status, TASK_STATUS, "task status")
        if self.assigned_role not in ROLE_RANK:
            raise ValueError(f"unknown assigned role: {self.assigned_role}")
        require_aware(self.created_time); require_aware_or_none(self.due_time)


@dataclass(frozen=True)
class WorkflowTransition(Record):
    RECORD_TYPE = "workflow_transition"
    transition_id: str; subject_kind: str; subject_id: str
    from_status: str; to_status: str; actor_id: str; actor_kind: str
    evidence_refs: tuple[str, ...]; note: str; recorded_time: str; marking: Marking

    def __post_init__(self):
        _member(self.subject_kind, WORKFLOW_SUBJECT_KINDS, "workflow subject kind")
        _member(self.actor_kind, ACTOR_KINDS, "actor kind"); require_aware(self.recorded_time)


RECORD_CLASSES = {cls.RECORD_TYPE: cls for cls in (
    SourceRecord, IngestionEvent, TransformationRecord, ObjectVersion, RelationshipVersion, ActivityRecord,
    AssociationProposal, AssociationResolution, Alert, AlertTransition, Recommendation, AnalystAction,
    DecisionRecord, ModelPackage, AccreditationRecord, InferenceRecord, AnalyticalProposal,
    InformationRequirement, EvidenceRequest, AnalystTask, WorkflowTransition,
)}
