"""Record types for the fabric event log. Each validates itself on construction."""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware, require_aware_or_none
from curunir_operational.contracts import Record, _member
from curunir_operational.references import Label, OptionalRef, Ref, Refs

from . import ABSENCE_SEMANTICS

OPERATIONS = ("SEARCH", "LOOKUP", "ENUMERATE", "FETCH",
              "HISTORICAL_ENUMERATE", "HISTORICAL_FETCH", "POLL")

QUERY_FAMILIES = ("EXACT_NAME", "QUOTED_PHRASE", "ALIAS", "FORMER_NAME", "TRANSLITERATION",
                  "LOCAL_LANGUAGE", "IDENTIFIER", "DOMAIN", "ADDRESS", "ORG_ID",
                  "DATE_BOUNDED", "SITE_BOUNDED", "RELATIONSHIP_PIVOT", "DISAMBIGUATION",
                  "FEED_POLL", "ENUMERATION", "NATIVE_OBJECT")

ORIGINS = ("RULE", "HUMAN", "MODEL")

EXECUTION_OUTCOMES = ("EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY", "SOURCE_FAILED",
                      "ACCESS_RESTRICTED", "POLICY_REFUSED", "RATE_DEFERRED", "NOT_ATTEMPTED")

TEMPORAL_STATUS = ("LIVE", "HISTORICAL")

PIVOT_KINDS = ("ENTITY", "ORGANISATION", "IDENTIFIER", "DOCUMENT", "FILING",
               "DOMAIN", "URL", "SOURCE", "SOURCE_FAMILY", "MANIFESTATION", "QUERY")

PIVOT_TYPES = ("IDENTIFIER_OF", "NAMED_IN", "REGISTERED_AS", "OPERATES_DOMAIN",
               "LINKS_TO", "MENTIONS", "OFFICER_OF", "SUBSIDIARY_OF", "SUCCESSOR_OF",
               "ALIAS_OF", "HISTORICAL_VERSION_OF", "FILED_BY", "LEADS_TO_SOURCE_FAMILY")

PIVOT_STATUS = ("PROPOSED", "ACCEPTED", "REJECTED", "RETIRED")

COVERAGE_STATES = ("COVERED", "PARTIALLY_COVERED", "NOT_AVAILABLE", "ACCESS_RESTRICTED",
                   "SOURCE_FAILED", "NOT_SEARCHED", "NOT_APPLICABLE", "UNKNOWN")

CHANGE_TYPES = ("NEW_OBJECT", "CHANGED_OBJECT", "DISAPPEARED_OBJECT", "NEW_RELATIONSHIP",
                "CHANGED_SOURCE_RECORD", "NEW_HISTORICAL_MANIFESTATION",
                "CONTENT_CHANGED", "METADATA_CHANGED", "RETRIEVAL_FAILURE", "COVERAGE_CHANGE")

WATCH_TARGET_KINDS = ("URL", "QUERY", "NATIVE_OBJECT", "FEED")

HISTORICAL_DEPTHS = ("CURRENT_ONLY", "VERSIONED", "DEEP_ARCHIVE", "UNKNOWN")
UPDATE_LATENCIES = ("REALTIME", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "IRREGULAR", "STATIC", "UNKNOWN")
PAGINATION_KINDS = ("NONE", "PAGE_NUMBER", "CURSOR", "TIME_RANGE", "UNKNOWN")
EDIT_BEHAVIOURS = ("APPEND_ONLY", "EDITS_VISIBLE", "EDITS_SILENT", "DELETIONS_POSSIBLE", "UNKNOWN")
COST_CLASSES = ("FREE", "RATE_LIMITED_FREE", "METERED", "UNKNOWN")
AUTHORIZATIONS = ("NONE", "USER_AGENT_ONLY", "API_KEY", "ACCOUNT", "UNKNOWN")
STATUS_KINDS = ("SUCCESS", "FAILURE")
URGENCIES = ("ROUTINE", "PRIORITY", "IMMEDIATE")


@dataclass(frozen=True)
class CapabilityProfile(Record):
    """What a source can provide: operations, reach, cadence, cost, connector."""
    RECORD_TYPE = "fabric_source_profile"
    ID_FIELD = "profile_id"
    profile_id: str
    source_id: Ref("source")
    connector_id: Label(str)
    connector_version: str
    supported_operations: tuple[str, ...]
    time_coverage: tuple[str | None, str | None]
    historical_depth: str
    update_latency: str
    pagination: str
    edit_behaviour: str
    cost_class: str
    rate_note: str
    authorization: str
    native_id_scheme: str
    available_fields: tuple[str, ...]
    known_biases: tuple[str, ...]
    known_gaps: tuple[str, ...]
    archive_compatible: bool
    created_time: str

    def __post_init__(self):
        require_aware(self.created_time)
        _member(self.historical_depth, HISTORICAL_DEPTHS, "historical depth")
        _member(self.update_latency, UPDATE_LATENCIES, "update latency")
        _member(self.pagination, PAGINATION_KINDS, "pagination kind")
        _member(self.edit_behaviour, EDIT_BEHAVIOURS, "edit behaviour")
        _member(self.cost_class, COST_CLASSES, "cost class")
        _member(self.authorization, AUTHORIZATIONS, "authorization")
        for op in self.supported_operations:
            _member(op, OPERATIONS, "operation")
        if not self.supported_operations:
            raise ValueError("a source profile must declare at least one operation")


@dataclass(frozen=True)
class SourceStatusEvent(Record):
    """One recorded success or failure against a source."""
    RECORD_TYPE = "fabric_source_status"
    ID_FIELD = "status_id"
    status_id: str
    source_id: Ref("source")
    connector_id: Label(str)
    kind: str
    operation: str
    detail: str
    observed_time: str

    def __post_init__(self):
        _member(self.kind, STATUS_KINDS, "status kind")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.observed_time)


@dataclass(frozen=True)
class InformationNeed(Record):
    """What a mission needs to know, from the collection side."""
    RECORD_TYPE = "fabric_information_need"
    ID_FIELD = "need_id"
    need_id: str
    requirement_id: Ref("information_requirement")
    mission_context: str
    question: str
    entities: tuple[str, ...]
    identifiers: Label(tuple[tuple[str, str], ...])  # (scheme, value)
    time_bounds: tuple[str | None, str | None]
    geography: tuple[str, ...]
    languages: tuple[str, ...]
    scripts: tuple[str, ...]
    hypotheses: tuple[str, ...]
    urgency: str
    created_by: str
    created_time: str
    marking: Marking

    def __post_init__(self):
        require_aware(self.created_time)
        _member(self.urgency, URGENCIES, "urgency")
        if not self.question:
            raise ValueError("an information need requires a question")


@dataclass(frozen=True)
class QuerySpec(Record):
    """One executable query with its origin."""
    RECORD_TYPE = "fabric_query"
    ID_FIELD = "query_id"
    query_id: str
    family: str
    value: str
    language: str
    script: str
    operation: str
    source_id: Ref("source")  # "" means any capable registered source
    time_bounds: tuple[str | None, str | None]
    origin: str
    origin_detail: str
    rationale: str
    derived_from: Refs("*")  # pivot ids / manifestation ids that produced it

    def __post_init__(self):
        _member(self.family, QUERY_FAMILIES, "query family")
        _member(self.operation, OPERATIONS, "operation")
        _member(self.origin, ORIGINS, "query origin")
        if not self.value:
            raise ValueError("a query requires a value")


@dataclass(frozen=True)
class DiscoveryPlan(Record):
    RECORD_TYPE = "fabric_discovery_plan"
    ID_FIELD = "plan_id"
    plan_id: str
    need_id: Ref("fabric_information_need")
    generation: str  # INITIAL, PIVOT_EXPANSION or WATCH
    queries: tuple[QuerySpec, ...]
    considered_source_ids: Refs("source")
    unmatched_query_ids: Label(tuple[str, ...])  # queries no registered source can execute
    budget_max_requests: int
    planner_version: str
    created_time: str
    marking: Marking

    def __post_init__(self):
        require_aware(self.created_time)
        if self.generation not in ("INITIAL", "PIVOT_EXPANSION", "WATCH"):
            raise ValueError(f"invalid plan generation: {self.generation!r}")
        if not 1 <= self.budget_max_requests <= 500:
            raise ValueError("plan budget out of bounds")
        if not self.queries:
            raise ValueError("a discovery plan requires at least one query")


@dataclass(frozen=True)
class ExecutionRecord(Record):
    """One query attempt against one source and its outcome.

    Empty, failed, refused and not-attempted are distinct outcomes.
    """
    RECORD_TYPE = "fabric_execution"
    ID_FIELD = "execution_id"
    execution_id: str
    plan_id: Ref("fabric_discovery_plan")
    query_id: Ref("fabric_query")
    source_id: Ref("source")
    connector_id: Label(str)
    connector_version: str
    operation: str
    outcome: str
    result_count: int
    request_url: str
    http_status: int | None
    policy_decision: str
    error_class: str | None
    error_detail: str
    manifestation_ids: Refs("fabric_manifestation")
    started_time: str
    completed_time: str | None
    absence_semantics: str
    marking: Marking
    truncated: bool = False

    def __post_init__(self):
        _member(self.outcome, EXECUTION_OUTCOMES, "execution outcome")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.started_time)
        require_aware_or_none(self.completed_time)
        if self.absence_semantics != ABSENCE_SEMANTICS:
            raise ValueError("execution records must carry the fixed absence semantics")
        if self.outcome == "EXECUTED_WITH_RESULTS" and self.result_count < 1:
            raise ValueError("EXECUTED_WITH_RESULTS requires a positive result count")
        if self.outcome == "EXECUTED_EMPTY" and self.result_count != 0:
            raise ValueError("EXECUTED_EMPTY requires a zero result count")


@dataclass(frozen=True)
class ManifestationRecord(Record):
    """One preserved retrieval. The bytes live in custody; this record names them."""
    RECORD_TYPE = "fabric_manifestation"
    ID_FIELD = "manifestation_id"
    manifestation_id: str
    source_id: Ref("source")
    connector_id: Label(str)
    connector_version: str
    native_id: Label(str)
    request_url: str
    final_url: str
    content_sha256: str
    content_store_path: str
    media_type: str
    temporal_status: str
    source_time: str | None
    archive_capture_time: str | None
    retrieval_time: str
    http_status: int | None
    redirects: tuple[str, ...]
    etag: str
    last_modified: str
    truncated: bool
    retrieval_id: Label(str)
    custody_ingestion_id: Ref("ingestion")
    source_object_id: Ref("object_version")
    execution_id: Ref("fabric_execution")
    prior_manifestation_id: OptionalRef("fabric_manifestation")
    marking: Marking

    def __post_init__(self):
        _member(self.temporal_status, TEMPORAL_STATUS, "temporal status")
        require_aware(self.retrieval_time)
        require_aware_or_none(self.source_time)
        require_aware_or_none(self.archive_capture_time)
        if self.temporal_status == "HISTORICAL" and not self.archive_capture_time:
            raise ValueError("a historical manifestation requires its archive capture time")
        if len(self.content_sha256) != 64:
            raise ValueError("manifestation requires the content sha256")


@dataclass(frozen=True)
class PivotEdge(Record):
    """A proposed reason to look somewhere else, citing its evidence."""
    RECORD_TYPE = "fabric_pivot"
    ID_FIELD = "pivot_id"
    pivot_id: str
    from_kind: str
    from_ref: Ref("*")
    to_kind: str
    to_ref: Ref("*")
    pivot_type: str
    rationale: str
    evidence_manifestation_ids: Refs("fabric_manifestation")
    origin: str
    status: str
    created_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.from_kind, PIVOT_KINDS, "pivot kind")
        _member(self.to_kind, PIVOT_KINDS, "pivot kind")
        _member(self.pivot_type, PIVOT_TYPES, "pivot type")
        _member(self.origin, ORIGINS, "pivot origin")
        _member(self.status, PIVOT_STATUS, "pivot status")
        require_aware(self.created_time)
        if self.status != "PROPOSED" and self.origin != "HUMAN" and not self.rationale:
            raise ValueError("non-proposed pivot status requires a rationale")
        if not self.evidence_manifestation_ids and self.origin != "HUMAN":
            raise ValueError("a rule or model pivot requires evidence manifestations")


@dataclass(frozen=True)
class CoverageAssessment(Record):
    RECORD_TYPE = "fabric_coverage"
    ID_FIELD = "coverage_id"
    coverage_id: str
    need_id: Ref("fabric_information_need")
    source_id: Ref("source")
    source_family: str
    state: str
    basis_execution_ids: Refs("fabric_execution")
    temporal_coverage: tuple[str | None, str | None]
    language_coverage: tuple[str, ...]
    geographic_coverage: tuple[str, ...]
    gaps: tuple[str, ...]
    assessed_time: str
    assessor: str
    marking: Marking

    def __post_init__(self):
        _member(self.state, COVERAGE_STATES, "coverage state")
        require_aware(self.assessed_time)
        if self.state in ("COVERED", "PARTIALLY_COVERED", "SOURCE_FAILED", "ACCESS_RESTRICTED") \
                and not self.basis_execution_ids:
            raise ValueError(f"coverage state {self.state} requires execution evidence")


@dataclass(frozen=True)
class WatchDefinition(Record):
    """Keep observing one target at a cadence."""
    RECORD_TYPE = "fabric_watch"
    ID_FIELD = "watch_id"
    watch_id: str
    need_id: Ref("fabric_information_need")
    target_kind: str
    target_ref: Ref("*")
    source_id: Ref("source")
    operation: str
    query_value: str
    cadence_seconds: int
    active: bool
    blind_spots: tuple[str, ...]
    created_by: str
    created_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.target_kind, WATCH_TARGET_KINDS, "watch target kind")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.created_time)
        if self.cadence_seconds < 60:
            raise ValueError("watch cadence below 60 seconds is not supported")


@dataclass(frozen=True)
class WatchRun(Record):
    RECORD_TYPE = "fabric_watch_run"
    ID_FIELD = "run_id"
    run_id: str
    watch_id: Ref("fabric_watch")
    scheduled_time: str
    started_time: str
    completed_time: str | None
    outcome: str
    execution_id: Ref("fabric_execution")
    observed_content_sha256: str
    observed_records: Label(tuple[tuple[str, str], ...])  # (native_id, source_time or "")
    manifestation_id: Ref("fabric_manifestation")
    change_observation_ids: Refs("fabric_change")
    next_due_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.outcome, EXECUTION_OUTCOMES, "watch run outcome")
        require_aware(self.scheduled_time)
        require_aware(self.started_time)
        require_aware_or_none(self.completed_time)
        require_aware(self.next_due_time)


@dataclass(frozen=True)
class ChangeObservation(Record):
    RECORD_TYPE = "fabric_change"
    ID_FIELD = "change_id"
    change_id: str
    watch_id: Ref("fabric_watch")
    run_id: Ref("fabric_watch_run")
    change_type: str
    detail: str
    prior_ref: Ref("*")
    current_ref: Ref("*")
    evidence_manifestation_ids: Refs("fabric_manifestation")
    observed_time: str
    marking: Marking

    def __post_init__(self):
        _member(self.change_type, CHANGE_TYPES, "change type")
        require_aware(self.observed_time)
        if self.change_type in ("CONTENT_CHANGED", "METADATA_CHANGED", "CHANGED_OBJECT",
                                "CHANGED_SOURCE_RECORD", "NEW_HISTORICAL_MANIFESTATION") \
                and not self.evidence_manifestation_ids:
            raise ValueError(f"change type {self.change_type} requires evidence manifestations")
