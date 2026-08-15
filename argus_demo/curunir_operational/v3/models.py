"""Strict contracts for V3 distributed histories, collaboration and studies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..canonical import parse_time, require_aware, sha256
from . import POLICY_VERSION, PROTOCOL_VERSION, SCHEMA_VERSION


class CausalRelation(str, Enum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"
    CONCURRENT = "CONCURRENT"
    EQUIVALENT = "EQUIVALENT"
    UNKNOWN = "UNKNOWN"


class AdmissionOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    QUARANTINED = "QUARANTINED"
    CAUSAL_GAP = "CAUSAL_GAP"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    TAMPERED = "TAMPERED"


class ConflictStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"
    SUPERSEDED = "SUPERSEDED"


CONFLICT_TYPES = (
    "CONCURRENT_ATTRIBUTE_CHANGE",
    "CONTRADICTORY_OPERATIONAL_STATUS",
    "TASK_ASSIGNMENT_CONFLICT",
    "RECOMMENDATION_CONFLICT",
    "DECISION_CONFLICT",
    "ACCESS_POLICY_CONFLICT",
    "IDENTITY_ASSOCIATION_CONFLICT",
    "CORRECTION_CONFLICT",
    "SCHEMA_MAPPING_CONFLICT",
    "STALE_BASE_CONFLICT",
    "ORIGINATOR_AUTHORITY_CONFLICT",
)


ANNOTATION_STATES = (
    "PRIVATE_DRAFT", "NODE_LOCAL", "SHARED", "UNDER_REVIEW",
    "ACCEPTED_ANALYTICAL_STATE", "REJECTED", "SUPERSEDED",
)

DECISION_DISPOSITIONS = (
    "PROPOSED", "ACCEPTED", "REJECTED", "MODIFIED", "DEFERRED",
    "RETURNED_FOR_EVIDENCE", "ESCALATED",
)


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_record"):
        return value.to_record()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


class Contract:
    def to_record(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class VersionVector(Contract):
    values: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if any(not node or not isinstance(seq, int) or seq < 0 for node, seq in self.values.items()):
            raise ValueError("version-vector entries require non-empty node ids and non-negative integers")

    def increment(self, node_id: str) -> "VersionVector":
        values = dict(self.values)
        values[node_id] = values.get(node_id, 0) + 1
        return VersionVector(values)

    def merged(self, *others: "VersionVector") -> "VersionVector":
        values = dict(self.values)
        for other in others:
            for node, seq in other.values.items():
                values[node] = max(values.get(node, 0), seq)
        return VersionVector(values)

    def relation(self, other: "VersionVector | None") -> CausalRelation:
        if other is None:
            return CausalRelation.UNKNOWN
        nodes = set(self.values) | set(other.values)
        left_le = all(self.values.get(node, 0) <= other.values.get(node, 0) for node in nodes)
        right_le = all(other.values.get(node, 0) <= self.values.get(node, 0) for node in nodes)
        if left_le and right_le:
            return CausalRelation.EQUIVALENT
        if left_le:
            return CausalRelation.BEFORE
        if right_le:
            return CausalRelation.AFTER
        return CausalRelation.CONCURRENT


@dataclass(frozen=True)
class AccessMarkingV3(Contract):
    owning_authority: str
    compartments: tuple[str, ...] = ()
    releasability: tuple[str, ...] = ()
    mission_scopes: tuple[str, ...] = ()
    min_role: str = "OBSERVER"
    originator_controls: tuple[str, ...] = ()
    sanitized: bool = False

    def __post_init__(self) -> None:
        if not self.owning_authority:
            raise ValueError("access marking requires an owning authority")
        object.__setattr__(self, "compartments", tuple(sorted(set(self.compartments))))
        object.__setattr__(self, "releasability", tuple(sorted(set(self.releasability))))
        object.__setattr__(self, "mission_scopes", tuple(sorted(set(self.mission_scopes))))
        object.__setattr__(self, "originator_controls", tuple(sorted(set(self.originator_controls))))


@dataclass(frozen=True)
class ActorIdentity(Contract):
    actor_id: str
    display_name: str
    organization: str
    roles: tuple[str, ...]
    attributes: dict[str, str] = field(default_factory=dict)
    compartments: tuple[str, ...] = ()
    mission_scopes: tuple[str, ...] = ()
    authority_scopes: tuple[str, ...] = ()
    valid_from: str = ""
    valid_to: str | None = None
    status: str = "ACTIVE"
    identity_provider_ref: str = "curunir-v3-test-identity-provider"

    def __post_init__(self) -> None:
        if not self.actor_id or not self.organization or not self.roles:
            raise ValueError("actor identity requires id, organization and roles")
        require_aware(self.valid_from)
        if self.valid_to:
            require_aware(self.valid_to)
            if parse_time(self.valid_to) <= parse_time(self.valid_from):
                raise ValueError("actor validity interval is empty")
        if self.status not in ("ACTIVE", "EXPIRED", "REVOKED", "SUSPENDED"):
            raise ValueError(f"invalid actor status: {self.status}")

    def valid_at(self, when: str) -> bool:
        require_aware(when)
        point = parse_time(when)
        return (parse_time(self.valid_from) <= point and
                (self.valid_to is None or point < parse_time(self.valid_to)))


@dataclass(frozen=True)
class NodeIdentity(Contract):
    node_id: str
    owning_authority: str
    role: str
    trust_state: str
    sharing_scopes: tuple[str, ...]
    supported_protocol_versions: tuple[str, ...] = (PROTOCOL_VERSION,)
    status: str = "ACTIVE"

    def __post_init__(self) -> None:
        if not self.node_id or not self.owning_authority or not self.role:
            raise ValueError("node identity requires id, authority and role")
        if self.trust_state not in ("TRUSTED_TEST", "LIMITED", "REVOKED", "UNKNOWN"):
            raise ValueError(f"invalid trust state: {self.trust_state}")
        if self.status not in ("ACTIVE", "REVOKED", "SUSPENDED"):
            raise ValueError(f"invalid node status: {self.status}")


@dataclass(frozen=True)
class NodeManifest(Contract):
    node_identifier: str
    authority: str
    role: str
    store_path: str
    protocol_version: str = PROTOCOL_VERSION
    schema_versions: tuple[str, ...] = (SCHEMA_VERSION,)
    policy_versions: tuple[str, ...] = (POLICY_VERSION,)
    trust_state: str = "TRUSTED_TEST"
    sharing_scopes: tuple[str, ...] = ()
    integrity_state: str = "VALID"
    peer_configuration: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthenticatedActionEnvelope(Contract):
    action_id: str
    actor_id: str
    node_id: str
    action_type: str
    payload_hash: str
    policy_version: str
    recorded_time: str
    nonce: str
    authentication_method: str
    signature: str
    verification_result: str = "UNVERIFIED"

    def __post_init__(self) -> None:
        require_aware(self.recorded_time)

    def signing_record(self) -> dict[str, Any]:
        return {k: v for k, v in self.to_record().items() if k not in ("signature", "verification_result")}


@dataclass(frozen=True)
class AuthorizationDecision(Contract):
    decision_id: str
    policy_version: str
    actor_id: str
    action: str
    object_ref: str
    result: str
    rationale: str
    time: str

    def __post_init__(self) -> None:
        require_aware(self.time)
        if self.result not in ("ALLOW", "DENY"):
            raise ValueError("authorization result must ALLOW or DENY")


@dataclass(frozen=True)
class RevocationRecord(Contract):
    revocation_id: str
    subject_type: str
    subject_id: str
    effective_time: str
    recorded_time: str
    authority: str
    rationale: str

    def __post_init__(self) -> None:
        if self.subject_type not in ("ACTOR", "NODE"):
            raise ValueError("revocation subject must be ACTOR or NODE")
        require_aware(self.effective_time)
        require_aware(self.recorded_time)


@dataclass(frozen=True)
class DistributedEventEnvelope(Contract):
    event_id: str
    originating_node_id: str
    node_local_sequence: int
    causal_context: dict[str, int]
    parent_event_ids: tuple[str, ...]
    event_type: str
    valid_time: dict[str, str | None]
    recorded_time: str
    actor_id: str
    access_marking: dict[str, Any]
    schema_version: str
    payload: dict[str, Any]
    payload_hash: str
    event_hash: str
    authentication_state: str
    action_envelope: dict[str, Any] | None = None
    admitted_time: str | None = None

    def __post_init__(self) -> None:
        if self.node_local_sequence < 1:
            raise ValueError("node-local sequence begins at one")
        require_aware(self.recorded_time)
        if self.admitted_time:
            require_aware(self.admitted_time)
        start = self.valid_time.get("from")
        end = self.valid_time.get("to")
        if start:
            require_aware(start)
        if end:
            require_aware(end)
        if start and end and parse_time(end) <= parse_time(start):
            raise ValueError("event valid interval is empty")

    def hash_record(self) -> dict[str, Any]:
        record = self.to_record()
        record.pop("event_hash", None)
        record.pop("admitted_time", None)
        return record

    def verify_hashes(self) -> bool:
        return self.payload_hash == sha256(self.payload) and self.event_hash == sha256(self.hash_record())


@dataclass(frozen=True)
class MergePolicy(Contract):
    name: str
    version: str
    category: str
    record_types: tuple[str, ...]
    fail_closed: bool = False


@dataclass(frozen=True)
class DistributedConflictRecord(Contract):
    conflict_id: str
    conflict_type: str
    affected_object_or_workflow: str
    involved_events: tuple[str, ...]
    involved_nodes: tuple[str, ...]
    causal_relationship: str
    competing_values: tuple[dict[str, Any], ...]
    detected_at: str
    access_marking: dict[str, Any]
    status: str
    resolver_role: str
    resolution_policy: str
    resolution_event_id: str | None
    evidence_snapshot: tuple[str, ...]
    audit_history: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.conflict_type not in CONFLICT_TYPES:
            raise ValueError(f"invalid conflict type: {self.conflict_type}")
        if self.status not in tuple(s.value for s in ConflictStatus):
            raise ValueError(f"invalid conflict status: {self.status}")
        require_aware(self.detected_at)


@dataclass(frozen=True)
class SyncOffer(Contract):
    source_node: str
    protocol_version: str
    opaque_state_token: str
    schema_versions: tuple[str, ...]
    policy_versions: tuple[str, ...]
    authorized_sharing_scope: tuple[str, ...]
    integrity_metadata: dict[str, str]


@dataclass(frozen=True)
class SyncRequest(Contract):
    destination_node: str
    known_causal_context: dict[str, str]
    requested_scope: tuple[str, ...]
    access_and_releasability_context: dict[str, Any]
    supported_schemas: tuple[str, ...]
    maximum_bundle_constraints: dict[str, int]


@dataclass(frozen=True)
class SyncBundle(Contract):
    bundle_id: str
    source_node: str
    destination_node: str
    protocol_version: str
    base_state_token: str
    resulting_state_token: str
    events: tuple[dict[str, Any], ...]
    causal_metadata: dict[str, Any]
    schema_and_mapping_references: tuple[str, ...]
    provenance: dict[str, Any]
    access_markings: tuple[str, ...]
    omissions_declaration: str
    integrity_manifest: dict[str, str]
    creation_time: str
    expiry: str | None
    signature: str


@dataclass(frozen=True)
class SyncReceipt(Contract):
    bundle_identifier: str
    verification_state: str
    accepted_events: tuple[str, ...]
    duplicates: tuple[str, ...]
    quarantines: tuple[str, ...]
    causal_gaps: tuple[str, ...]
    conflicts: tuple[str, ...]
    resulting_opaque_state_token: str
    public_summary: dict[str, str]


@dataclass(frozen=True)
class CollaborativeAnnotation(Contract):
    annotation_id: str
    subject_id: str
    body: str
    state: str
    evidence_snapshot: tuple[str, ...]
    access_marking: dict[str, Any]

    def __post_init__(self) -> None:
        if self.state not in ANNOTATION_STATES:
            raise ValueError(f"invalid annotation state: {self.state}")


@dataclass(frozen=True)
class AnalystHandoff(Contract):
    handoff_id: str
    originating_actor: str
    receiving_actor_or_role: str
    mission: str
    affected_objects: tuple[str, ...]
    evidence_snapshot: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    due_time: str | None
    access_marking: dict[str, Any]
    acceptance_state: str
    follow_up_actions: tuple[str, ...]


@dataclass(frozen=True)
class ReviewRecord(Contract):
    review_id: str
    review_type: str
    subject_id: str
    requesting_actor: str
    responding_actor: str | None
    state: str
    findings: tuple[str, ...]
    evidence_snapshot: tuple[str, ...]


@dataclass(frozen=True)
class TaskRecord(Contract):
    task_id: str
    mission: str
    assigned_to: str
    state: str
    blocking_dependencies: tuple[str, ...]
    evidence_snapshot: tuple[str, ...]
    access_marking: dict[str, Any]


@dataclass(frozen=True)
class DecisionRecordV3(Contract):
    decision_id: str
    subject_id: str
    disposition: str
    rationale: str
    evidence_snapshot: tuple[str, ...]
    causal_context: dict[str, int]
    policy_version: str
    access_marking: dict[str, Any]

    def __post_init__(self) -> None:
        if self.disposition not in DECISION_DISPOSITIONS:
            raise ValueError(f"invalid decision disposition: {self.disposition}")


@dataclass(frozen=True)
class Indicator(Contract):
    indicator_id: str
    statement: str
    argus_evidence_refs: tuple[dict[str, Any], ...]
    source_dependence_group: str | None
    correction_state: str
    access_marking: dict[str, Any]


@dataclass(frozen=True)
class StrategicHypothesis(Contract):
    hypothesis_id: str
    statement: str
    scope: str
    valid_interval: dict[str, str | None]
    supporting_indicators: tuple[str, ...]
    contradicting_indicators: tuple[str, ...]
    evidence_bases: tuple[str, ...]
    source_dependence: tuple[str, ...]
    assumptions: tuple[str, ...]
    confidence_dimensions: dict[str, str]
    analyst_state: str
    review_state: str
    operational_implications: tuple[str, ...]
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class WarningAssessment(Contract):
    assessment_id: str
    hypothesis_ids: tuple[str, ...]
    viable: tuple[str, ...]
    rejected: tuple[str, ...]
    unresolved: tuple[str, ...]
    refusal_statements: tuple[str, ...]
    evidence_snapshot: tuple[str, ...]


@dataclass(frozen=True)
class OperationalImplicationProposal(Contract):
    implication_id: str
    source_hypothesis_id: str
    implication_type: str
    proposed_consequence: str
    status: str
    evidence_snapshot: tuple[str, ...]
    direct_operational_mutation: bool = False

    def __post_init__(self) -> None:
        if self.direct_operational_mutation:
            raise ValueError("strategic implications may not directly mutate operational state")


@dataclass(frozen=True)
class EvidenceHandoff(Contract):
    handoff_id: str
    argus_evidence_refs: tuple[dict[str, Any], ...]
    strategic_subject_id: str
    receiving_workbench: str
    information_requirement_id: str
    review_state: str
    unresolved_state: str


@dataclass(frozen=True)
class OperatorStudyDefinition(Contract):
    study_id: str
    version: str
    modes: tuple[str, ...]
    conditions: tuple[str, ...]
    tasks: tuple[dict[str, Any], ...]
    metrics: tuple[str, ...]
    privacy_exclusions: tuple[str, ...]


@dataclass(frozen=True)
class OperatorSession(Contract):
    session_id: str
    study_id: str
    anonymized_participant_id: str
    role: str
    mode: str
    condition: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class OperatorTask(Contract):
    task_id: str
    prompt: str
    expected_answer: dict[str, Any]
    evidence_path: tuple[str, ...]
    policy_constraints: tuple[str, ...]


@dataclass(frozen=True)
class TaskEvent(Contract):
    event_id: str
    session_id: str
    task_id: str
    event_type: str
    time: str
    detail: dict[str, Any]


@dataclass(frozen=True)
class OperatorAnswer(Contract):
    task_id: str
    answer: dict[str, Any]
    confidence: float
    completion_state: str


@dataclass(frozen=True)
class GroundedEvaluationResult(Contract):
    task_id: str
    correct: bool
    evidence_trace_correct: bool
    policy_compliant: bool
    inappropriate_certainty: bool
    score: float
    rationale: tuple[str, ...]


@dataclass(frozen=True)
class SubjectiveFeedbackRecord(Contract):
    session_id: str
    workload: int | None
    trust: int | None
    usability_issues: tuple[str, ...]
    optional_comment: str = ""
