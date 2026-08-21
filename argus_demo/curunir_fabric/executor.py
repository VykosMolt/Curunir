"""Plan execution: policy-gated acquisition terminating in immutable custody.

Every query × source attempt produces exactly one ExecutionRecord with a
truthful outcome; every retrieved response body is preserved through the
established Source Intelligence custody path (content-addressed store +
validated chain of custody) and bound into the fabric log as a
ManifestationRecord. Empty results are preserved too: the response bytes are
the evidence that the search ran and returned nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import RetrievalAttempt, digest_id
from argus.source_intelligence.policy import POLICY_VERSION, acquisition_eligible, classify_access
from curunir_operational.store import StoreError

from . import ABSENCE_SEMANTICS, PACKAGE_VERSION
from .connectors import BUILTIN_CONNECTORS
from .connectors.base import (ConnectorRequest, ConnectorResponse, NativeResult,
                              SourceConnector, scrub_surrogates, utc_now)
from .contracts import DiscoveryPlan, ExecutionRecord, ManifestationRecord, QuerySpec
from .planner import eligible_sources
from .registry import RegistryView, record_source_status
from .store import FabricStore

_CONTENT_CLASSES = (
    ("json", "API_RESPONSE"), ("xml", "FEED"), ("rss", "FEED"), ("atom", "FEED"),
    ("html", "GENERIC_WEB_PAGE"), ("pdf", "SOURCE_DOCUMENT_CANDIDATE"),
    ("text/plain", "SOURCE_DOCUMENT_CANDIDATE"),
)

_STATUS_TO_OUTCOME = {
    "OK": "EXECUTED_WITH_RESULTS",
    "EMPTY": "EXECUTED_EMPTY",
    "FAILED": "SOURCE_FAILED",
    "ACCESS_RESTRICTED": "ACCESS_RESTRICTED",
    "NOT_SUPPORTED": "NOT_ATTEMPTED",
}


class LocalStorageFault(OSError):
    """A custody/local-disk failure, never attributable to the source."""


def content_class_for(media_type: str, body: bytes) -> str:
    if not body:
        return "EMPTY_RESPONSE"
    lowered = media_type.casefold()
    for token, content_class in _CONTENT_CLASSES:
        if token in lowered:
            return content_class
    return "BINARY_UNKNOWN"


class RateGate:
    """Per-source minimum spacing between live requests."""

    def __init__(self, default_interval_seconds: float = 0.5,
                 per_source: Mapping[str, float] | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.default = default_interval_seconds
        self.per_source = dict(per_source or {})
        self.sleeper = sleeper
        self._last: dict[str, float] = {}

    def acquire(self, source_id: str) -> None:
        interval = self.per_source.get(source_id, self.default)
        last = self._last.get(source_id)
        now = time.monotonic()
        if last is not None and now - last < interval:
            self.sleeper(interval - (now - last))
        self._last[source_id] = time.monotonic()


@dataclass
class ExecutionContext:
    store: FabricStore
    registry: RegistryView
    custody: SourceCustodyStore
    actor: str
    marking: object
    now_fn: Callable[[], str] = utc_now
    connectors: Mapping[str, SourceConnector] = field(default_factory=lambda: dict(BUILTIN_CONNECTORS))
    transports: Mapping[str, Callable] = field(default_factory=dict)  # connector_id → transport override
    rate_gate: RateGate = field(default_factory=RateGate)


@dataclass(frozen=True)
class ExecutionResult:
    execution: ExecutionRecord
    response: ConnectorResponse | None
    results: tuple[NativeResult, ...]
    manifestations: tuple[ManifestationRecord, ...]


def _manifestation(ctx: ExecutionContext, *, source_id: str, connector: SourceConnector,
                   execution_id: str, response: ConnectorResponse, native_id: str,
                   temporal_status: str, source_time: str | None,
                   archive_capture_time: str | None) -> ManifestationRecord:
    retrieval_id = digest_id("fabric-retrieval", execution_id, response.body_sha256())
    body = response.raw_body
    content_class = content_class_for(response.media_type, body)
    retrieval = RetrievalAttempt(
        retrieval_id=retrieval_id, plan_id=execution_id, lead_id="", source_id=source_id,
        requested_url=response.request_url, resolved_url=response.final_url,
        redirect_chain=response.redirects, request_timestamp=response.retrieved_time,
        response_timestamp=response.retrieved_time, http_status=response.http_status,
        content_type=response.media_type or None, content_length=len(body),
        etag=response.etag or None, last_modified=response.last_modified or None,
        body_sha256=response.body_sha256(), body_size=len(body),
        transport_status="RESPONSE_RECEIVED", content_class=content_class,
        rate_state="WITHIN_POLICY", error_code=None,
        tool_name=connector.connector_id, tool_version=connector.connector_version,
        policy_version=POLICY_VERSION, created_at=response.retrieved_time,
    )
    filename = (native_id or response.request_url).replace("/", "_")[:120] or "response"
    try:
        content, source_object, _events, _new = ctx.custody.preserve(
            retrieval, body, filename=filename, source_descriptor_id=source_id,
            document_type=content_class, now=response.retrieved_time,
        )
    except (OSError, ValueError) as error:
        raise LocalStorageFault(f"custody preservation failed: {error}") from error
    prior_id = None
    observation_key = f"{source_id}|{native_id or response.request_url}"
    for record in reversed(ctx.store.records_of("fabric_manifestation")):
        if f"{record['source_id']}|{record['native_id'] or record['request_url']}" == observation_key:
            prior_id = record["manifestation_id"]
            break
    return ManifestationRecord(
        manifestation_id=digest_id("manifestation", source_id, native_id or response.request_url,
                                   response.body_sha256(), response.retrieved_time),
        source_id=source_id, connector_id=connector.connector_id,
        connector_version=connector.connector_version,
        native_id=native_id, request_url=response.request_url, final_url=response.final_url,
        content_sha256=response.body_sha256(), content_store_path=content.content_store_path,
        media_type=response.media_type, temporal_status=temporal_status,
        source_time=source_time, archive_capture_time=archive_capture_time,
        retrieval_time=response.retrieved_time, http_status=response.http_status,
        redirects=response.redirects, etag=response.etag, last_modified=response.last_modified,
        truncated=response.truncated, retrieval_id=retrieval_id,
        custody_ingestion_id=digest_id("web-ingestion", retrieval_id),
        source_object_id=source_object.source_object_id,
        execution_id=execution_id, prior_manifestation_id=prior_id, marking=ctx.marking,
    )


def execute_single(ctx: ExecutionContext, *, query: QuerySpec, source_id: str,
                   plan_id: str = "") -> ExecutionResult:
    """One query against one source: policy gate, connector call, custody, records."""
    started = ctx.now_fn()
    connector_id = ""
    connector = None
    profile = ctx.registry.profile(source_id)
    descriptor = ctx.registry.descriptor(source_id)
    if profile is not None:
        connector = ctx.connectors.get(profile["connector_id"])
        connector_id = profile["connector_id"]

    def finish(outcome: str, *, response: ConnectorResponse | None = None,
               results: tuple[NativeResult, ...] = (),
               manifestations: tuple[ManifestationRecord, ...] = (),
               policy_decision: str = "", error_class: str | None = None,
               error_detail: str = "") -> ExecutionResult:
        completed = ctx.now_fn()
        error_detail = scrub_surrogates(error_detail)
        execution = ExecutionRecord(
            execution_id=digest_id("execution", plan_id, query.query_id, source_id, started),
            plan_id=plan_id, query_id=query.query_id, source_id=source_id,
            connector_id=connector_id,
            connector_version=connector.connector_version if connector else "",
            operation=query.operation, outcome=outcome, result_count=len(results),
            request_url=response.request_url if response else "",
            http_status=response.http_status if response else None,
            policy_decision=policy_decision,
            error_class=error_class, error_detail=error_detail,
            manifestation_ids=tuple(item.manifestation_id for item in manifestations),
            started_time=started, completed_time=completed,
            absence_semantics=ABSENCE_SEMANTICS, marking=ctx.marking,
            truncated=bool(response.truncated) if response else False,
        )
        # Referents land first. An interruption can leave an unreferenced
        # manifestation, but never an execution containing a dangling id.
        for manifestation in manifestations:
            ctx.store.append("FABRIC_MANIFESTATION_RECORDED", manifestation,
                             recorded_time=completed, actor=ctx.actor)
        ctx.store.append("FABRIC_EXECUTION_RECORDED", execution,
                         recorded_time=completed, actor=ctx.actor)
        if descriptor is not None and outcome in ("EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY"):
            record_source_status(ctx.store, source_id=source_id, connector_id=connector_id,
                                 kind="SUCCESS", operation=query.operation,
                                 detail=outcome, observed_time=completed, actor=ctx.actor)
        elif descriptor is not None and outcome in ("SOURCE_FAILED", "ACCESS_RESTRICTED"):
            record_source_status(ctx.store, source_id=source_id, connector_id=connector_id,
                                 kind="FAILURE", operation=query.operation,
                                 detail=error_detail or outcome, observed_time=completed, actor=ctx.actor)
        return ExecutionResult(execution, response, results, manifestations)

    if descriptor is None or profile is None:
        return finish("NOT_ATTEMPTED", error_detail=f"source {source_id} is not registered")
    if connector is None:
        return finish("NOT_ATTEMPTED", error_detail=f"no connector {profile['connector_id']} available")
    if query.operation not in profile["supported_operations"]:
        return finish("NOT_ATTEMPTED",
                      error_detail=f"{source_id} does not support {query.operation}")

    candidate_url = query.value if query.value.startswith(("http://", "https://")) \
        else descriptor.base_urls[0]
    decision = classify_access(descriptor, candidate_url, now=started)
    if not acquisition_eligible(decision):
        return finish("POLICY_REFUSED", policy_decision=decision.decision,
                      error_detail="; ".join(decision.reason_codes))

    ctx.rate_gate.acquire(source_id)
    request = ConnectorRequest(operation=query.operation, value=query.value,
                               language=query.language, time_bounds=query.time_bounds)
    transport = ctx.transports.get(connector.connector_id)
    try:
        response = connector.execute(request, transport=transport, now=ctx.now_fn())
        outcome = _STATUS_TO_OUTCOME[response.status]
        manifestations: tuple[ManifestationRecord, ...] = ()
        if response.status in ("OK", "EMPTY") and response.raw_body:
            historical = query.operation.startswith("HISTORICAL_")
            single = response.results[0] if len(response.results) == 1 else None
            capture_time = single.source_time if historical and single else None
            # a multi-result response is identified by the target it enumerates.
            native_id = single.native_id if single else (
                query.value if query.operation in
                ("HISTORICAL_ENUMERATE", "ENUMERATE", "POLL") else "")
            manifestations = (_manifestation(
                ctx, source_id=source_id, connector=connector,
                execution_id=digest_id(
                    "execution", plan_id, query.query_id, source_id, started),
                response=response, native_id=native_id,
                temporal_status="HISTORICAL" if historical and capture_time else "LIVE",
                source_time=single.source_time if single else None,
                archive_capture_time=capture_time,
            ),)
    except (MemoryError, StoreError, LocalStorageFault):
        raise
    except Exception as error:
        return finish("SOURCE_FAILED", error_class="CONNECTOR_ERROR",
                      error_detail=f"{type(error).__name__}: {error}"[:500],
                      policy_decision=decision.decision)

    return finish(outcome, response=response, results=response.results,
                  manifestations=manifestations,
                  policy_decision=decision.decision,
                  error_class=response.error_class, error_detail=response.error_detail)


def execute_plan(ctx: ExecutionContext, plan: DiscoveryPlan, *,
                 max_sources_per_query: int = 3,
                 max_requests: int | None = None) -> list[ExecutionResult]:
    budget = max_requests if max_requests is not None else plan.budget_max_requests
    outcomes: list[ExecutionResult] = []
    spent = 0
    for query in plan.queries:
        sources = eligible_sources(query, ctx.registry)[:max_sources_per_query]
        for source_id in sources:
            if spent >= budget:
                return outcomes
            outcomes.append(execute_single(ctx, query=query, source_id=source_id,
                                           plan_id=plan.plan_id))
            spent += 1
    return outcomes
