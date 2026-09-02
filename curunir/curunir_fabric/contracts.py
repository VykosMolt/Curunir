"""Typed record contracts for the OSINT fabric event log.

Same write-path discipline as the operational contracts: frozen dataclasses
that validate on construction, serialized once via ``to_record()``, appended
to a hash-chained log, replayed as plain dicts. Absence, failure and
non-attempt are distinct recorded states — an empty result is never evidence
of nonexistence, and an unqueried source is never silently "covered".
"""
from __future__ import annotations

from dataclasses import dataclass

from curunir_operational.access import Marking
from curunir_operational.canonical import require_aware, require_aware_or_none
from curunir_operational.contracts import Record, _member

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
    """What a registered source can actually provide, beyond its descriptor.

    The bitemporal identity/policy metadata lives on the Source Intelligence
    ``SourceDescriptor``; this profile carries the acquisition-shape facts the
    planner and watch layer query: operations, temporal reach, cadence,
    pagination, mutability, cost, fields, biases, gaps, connector binding.
    """
    RECORD_TYPE = "fabric_source_profile"
    profile_id: str; source_id: str; connector_id: str; connector_version: str
    supported_operations: tuple[str, ...]
    time_coverage: tuple[str | None, str | None]
    historical_depth: str; update_latency: str; pagination: str; edit_behaviour: str
    cost_class: str; rate_note: str; authorization: str
    native_id_scheme: str; available_fields: tuple[str, ...]
    known_biases: tuple[str, ...]; known_gaps: tuple[str, ...]
    archive_compatible: bool; created_time: str

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
    """Last-success / last-failure as recorded observations, not mutable fields."""
    RECORD_TYPE = "fabric_source_status"
    status_id: str; source_id: str; connector_id: str; kind: str
    operation: str; detail: str; observed_time: str

    def __post_init__(self):
        _member(self.kind, STATUS_KINDS, "status kind")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.observed_time)


@dataclass(frozen=True)
class InformationNeed(Record):
    """Fabric-side statement of what a mission needs to know.

    ``requirement_id`` links to the mission workflow's InformationRequirement
    when one exists; the fabric never closes requirements itself.
    """
    RECORD_TYPE = "fabric_information_need"
    need_id: str; requirement_id: str; mission_context: str; question: str
    entities: tuple[str, ...]
    identifiers: tuple[tuple[str, str], ...]  # (scheme, value)
    time_bounds: tuple[str | None, str | None]
    geography: tuple[str, ...]; languages: tuple[str, ...]; scripts: tuple[str, ...]
    hypotheses: tuple[str, ...]; urgency: str
    created_by: str; created_time: str; marking: Marking

    def __post_init__(self):
        require_aware(self.created_time)
        _member(self.urgency, URGENCIES, "urgency")
        if not self.question:
            raise ValueError("an information need requires a question")


@dataclass(frozen=True)
class QuerySpec(Record):
    """One typed, attributable, executable discovery query."""
    RECORD_TYPE = "fabric_query"
    query_id: str; family: str; value: str; language: str; script: str
    operation: str; source_id: str  # "" = any capable registered source
    time_bounds: tuple[str | None, str | None]
    origin: str; origin_detail: str; rationale: str
    derived_from: tuple[str, ...]  # pivot ids / manifestation ids that produced it

    def __post_init__(self):
        _member(self.family, QUERY_FAMILIES, "query family")
        _member(self.operation, OPERATIONS, "operation")
        _member(self.origin, ORIGINS, "query origin")
        if not self.value:
            raise ValueError("a query requires a value")


@dataclass(frozen=True)
class DiscoveryPlan(Record):
    RECORD_TYPE = "fabric_discovery_plan"
    plan_id: str; need_id: str; generation: str  # INITIAL | PIVOT_EXPANSION | WATCH
    queries: tuple[QuerySpec, ...]
    considered_source_ids: tuple[str, ...]
    unmatched_query_ids: tuple[str, ...]  # queries no registered source can execute
    budget_max_requests: int; planner_version: str
    created_time: str; marking: Marking

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
    """One query executed (or explicitly not executed) against one source.

    The outcome vocabulary is the load-bearing product feature: EXECUTED_EMPTY,
    SOURCE_FAILED, ACCESS_RESTRICTED, POLICY_REFUSED and NOT_ATTEMPTED are all
    distinct, so "we did not find it" can never silently become "it does not
    exist" and "we never looked" can never masquerade as either.
    """
    RECORD_TYPE = "fabric_execution"
    execution_id: str; plan_id: str; query_id: str; source_id: str
    connector_id: str; connector_version: str; operation: str
    outcome: str; result_count: int
    request_url: str; http_status: int | None
    policy_decision: str; error_class: str | None; error_detail: str
    manifestation_ids: tuple[str, ...]
    started_time: str; completed_time: str | None
    absence_semantics: str; marking: Marking
    truncated: bool = False

    def __post_init__(self):
        _member(self.outcome, EXECUTION_OUTCOMES, "execution outcome")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.started_time); require_aware_or_none(self.completed_time)
        if self.absence_semantics != ABSENCE_SEMANTICS:
            raise ValueError("execution records must carry the fixed absence semantics")
        if self.outcome == "EXECUTED_WITH_RESULTS" and self.result_count < 1:
            raise ValueError("EXECUTED_WITH_RESULTS requires a positive result count")
        if self.outcome == "EXECUTED_EMPTY" and self.result_count != 0:
            raise ValueError("EXECUTED_EMPTY requires a zero result count")


@dataclass(frozen=True)
class ManifestationRecord(Record):
    """One preserved retrieval of one source artifact.

    A live page and an archived capture of the same URL are distinct
    manifestations; re-retrievals of changed content are new manifestations
    linked through ``prior_manifestation_id``. The raw bytes live in the
    immutable custody store; this record binds their identity, retrieval
    context and lineage into the fabric log.
    """
    RECORD_TYPE = "fabric_manifestation"
    manifestation_id: str; source_id: str; connector_id: str; connector_version: str
    native_id: str; request_url: str; final_url: str
    content_sha256: str; content_store_path: str; media_type: str
    temporal_status: str; source_time: str | None; archive_capture_time: str | None
    retrieval_time: str; http_status: int | None
    redirects: tuple[str, ...]; etag: str; last_modified: str; truncated: bool
    retrieval_id: str; custody_ingestion_id: str; source_object_id: str
    execution_id: str; prior_manifestation_id: str | None; marking: Marking

    def __post_init__(self):
        _member(self.temporal_status, TEMPORAL_STATUS, "temporal status")
        require_aware(self.retrieval_time)
        require_aware_or_none(self.source_time); require_aware_or_none(self.archive_capture_time)
        if self.temporal_status == "HISTORICAL" and not self.archive_capture_time:
            raise ValueError("a historical manifestation requires its archive capture time")
        if len(self.content_sha256) != 64:
            raise ValueError("manifestation requires the content sha256")


@dataclass(frozen=True)
class PivotEdge(Record):
    """A typed reason to look somewhere else, grounded in evidence.

    Pivots are proposals: appending a new record with the same ``pivot_id``
    and a different status supersedes the old one on replay (latest wins);
    nothing is merged and nothing is deleted.
    """
    RECORD_TYPE = "fabric_pivot"
    pivot_id: str; from_kind: str; from_ref: str; to_kind: str; to_ref: str
    pivot_type: str; rationale: str
    evidence_manifestation_ids: tuple[str, ...]
    origin: str; status: str; created_time: str; marking: Marking

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
    coverage_id: str; need_id: str; source_id: str; source_family: str
    state: str; basis_execution_ids: tuple[str, ...]
    temporal_coverage: tuple[str | None, str | None]
    language_coverage: tuple[str, ...]; geographic_coverage: tuple[str, ...]
    gaps: tuple[str, ...]; assessed_time: str; assessor: str; marking: Marking

    def __post_init__(self):
        _member(self.state, COVERAGE_STATES, "coverage state")
        require_aware(self.assessed_time)
        if self.state in ("COVERED", "PARTIALLY_COVERED", "SOURCE_FAILED", "ACCESS_RESTRICTED") \
                and not self.basis_execution_ids:
            raise ValueError(f"coverage state {self.state} requires execution evidence")


@dataclass(frozen=True)
class WatchDefinition(Record):
    """A persistent observation binding: keep looking at this, at this cadence.

    Watch state is event-sourced: re-appending the same ``watch_id`` with
    ``active=False`` retires it on replay; runs and change observations are
    separate records, so the full observation history survives restart.
    """
    RECORD_TYPE = "fabric_watch"
    watch_id: str; need_id: str; target_kind: str; target_ref: str
    source_id: str; operation: str; query_value: str
    cadence_seconds: int; active: bool
    blind_spots: tuple[str, ...]
    created_by: str; created_time: str; marking: Marking

    def __post_init__(self):
        _member(self.target_kind, WATCH_TARGET_KINDS, "watch target kind")
        _member(self.operation, OPERATIONS, "operation")
        require_aware(self.created_time)
        if self.cadence_seconds < 60:
            raise ValueError("watch cadence below 60 seconds is not supported")


@dataclass(frozen=True)
class WatchRun(Record):
    RECORD_TYPE = "fabric_watch_run"
    run_id: str; watch_id: str; scheduled_time: str; started_time: str
    completed_time: str | None; outcome: str
    execution_id: str; observed_content_sha256: str
    observed_records: tuple[tuple[str, str], ...]  # (native_id, source_time or "")
    manifestation_id: str; change_observation_ids: tuple[str, ...]
    next_due_time: str; marking: Marking

    def __post_init__(self):
        _member(self.outcome, EXECUTION_OUTCOMES, "watch run outcome")
        require_aware(self.scheduled_time); require_aware(self.started_time)
        require_aware_or_none(self.completed_time); require_aware(self.next_due_time)


@dataclass(frozen=True)
class ChangeObservation(Record):
    RECORD_TYPE = "fabric_change"
    change_id: str; watch_id: str; run_id: str; change_type: str
    detail: str; prior_ref: str; current_ref: str
    evidence_manifestation_ids: tuple[str, ...]
    observed_time: str; marking: Marking

    def __post_init__(self):
        _member(self.change_type, CHANGE_TYPES, "change type")
        require_aware(self.observed_time)
        if self.change_type in ("CONTENT_CHANGED", "METADATA_CHANGED", "CHANGED_OBJECT",
                                "CHANGED_SOURCE_RECORD", "NEW_HISTORICAL_MANIFESTATION") \
                and not self.evidence_manifestation_ids:
            raise ValueError(f"change type {self.change_type} requires evidence manifestations")
