"""Active collection: uncertainty → requirement → ranked routes → execution.

The planner ranks candidate collection actions with a transparent
deterministic factor model — discriminating power, coverage gap, independence
gain, latency, cost, historical depth, access — and every route carries its
explanation. Coverage states and dependence families genuinely move the
ranking: a source family already covering the need scores below an unsearched
one, and a source in the same origin family as the existing basis scores
below an independent one. Automatable routes execute through the existing
OSINT fabric (policy gate, custody, manifestation lineage); human-required
routes become mission analyst tasks and are never silently completed.
"""
from __future__ import annotations

from typing import Any, Mapping

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import digest_id
from argus.source_intelligence.policy import acquisition_eligible, classify_access
from curunir_fabric.contracts import QuerySpec
from curunir_fabric.coverage import coverage_summary
from curunir_fabric.executor import ExecutionContext as FabricContext
from curunir_fabric.executor import RateGate, execute_single
from curunir_fabric.registry import RegistryView
from curunir_operational.access import Marking, marking_from_record
from curunir_operational.missions import MissionWorkflow

from .contracts import CollectionRoute, ReviewItem
from .hypotheses import (discriminator_satisfied_by, existing_basis_groups,
                         refresh_hypotheses_for_claims, update_discriminator)
from .pipeline import SemanticPipeline
from .store import SemanticStore
from .worldmodel import dependence_group_for, parse_subject

_LATENCY_SCORE = {"REALTIME": 1.0, "HOURLY": 0.9, "DAILY": 0.8, "WEEKLY": 0.6,
                  "MONTHLY": 0.4, "IRREGULAR": 0.4, "STATIC": 0.2, "UNKNOWN": 0.5}
_COST_SCORE = {"FREE": 1.0, "RATE_LIMITED_FREE": 0.8, "METERED": 0.4, "UNKNOWN": 0.6}
_COVERAGE_SCORE = {"NOT_SEARCHED": 1.0, "UNKNOWN": 0.9, "SOURCE_FAILED": 0.7,
                   "PARTIALLY_COVERED": 0.45, "ACCESS_RESTRICTED": 0.2,
                   "NOT_APPLICABLE": 0.05, "NOT_AVAILABLE": 0.05, "COVERED": 0.15}

# identifier scheme → (source_id, operation) route for a direct lookup
_SCHEME_ROUTES = {
    "LEI": ("gleif", "LOOKUP"),
    "WIKIDATA_QID": ("wikidata", "LOOKUP"),
    "SEC_CIK": ("sec-edgar", "SEARCH"),
}


def requirement_for_discriminator(store: SemanticStore, discriminator: Mapping[str, Any], *,
                                  mission_context: str, need_id: str = "",
                                  now: str, actor: str, marking: Marking) -> dict[str, Any]:
    """Open (or reuse) the mission information requirement for a discriminator."""
    workflow = MissionWorkflow(store)
    # decide from the store's CURRENT record, never the caller's snapshot: a
    # stale mapping must not regress a discriminator that has since been
    # SATISFIED (or otherwise advanced) back to REQUESTED
    current = store.latest_by_id("discriminator", "discriminator_id").get(
        discriminator["discriminator_id"], discriminator)
    requirement = workflow.open_requirement(
        mission_context=mission_context, question=current["question"],
        affected_ids=tuple(current["hypothesis_ids"]) + tuple(current["claim_ids"]),
        priority="HIGH" if current["independence_required"] else "MEDIUM",
        rationale="discriminating observation for unresolved world-model uncertainty",
        required_evidence_type="PUBLIC_SOURCE_EVIDENCE", owning_role="ANALYST",
        closure_criteria="human review of the discriminating observation",
        due_time=None, recorded_time=now, marking=marking, actor=actor)
    updates: dict = {}
    if current["requirement_id"] != requirement["requirement_id"]:
        updates["requirement_id"] = requirement["requirement_id"]
    if current["status"] == "OPEN":
        updates["status"] = "REQUESTED"  # only OPEN advances; nothing regresses
    if updates:
        current = update_discriminator(store, current, updates,
                                       now=now, actor=actor, marking=marking)
    return {"requirement": requirement, "discriminator": current}


def _source_origin_family(source_id: str, query_value: str) -> str:
    """The dependence family a retrieval from this source would join.

    Must compute the same family a real retrieval's manifestation would get:
    for archive/web sources that is the target site, so a Wayback route over
    a site already in the basis is correctly scored as dependent."""
    manifestation_like = {"source_id": source_id, "native_id": query_value,
                          "request_url": query_value, "final_url": query_value}
    return dependence_group_for(manifestation_like)


def _distinctive_name(name: str) -> str:
    """European legal names quote the distinctive part («Северсталь», "Rix");
    text engines match that core far better than the full legal form."""
    import re
    match = re.search(r'[«"“]([^»"”]{2,60})[»"”]', name)
    return match.group(1) if match else name


def _subject_search_term(store: SemanticStore, subject_ref: str) -> str:
    """The subject's best-known name from the world model — text engines
    search names, not foreign identifiers."""
    from .worldmodel import world_object_id
    object_id = world_object_id(subject_ref)
    versions = [v for v in store.records_of("object_version") if v["object_id"] == object_id]
    if versions:
        attributes = versions[-1].get("attributes", {})
        for key in ("name", "legal_name", "display_name"):
            if attributes.get(key):
                return _distinctive_name(attributes[key])
        labels = versions[-1].get("labels", ())
        if labels:
            return _distinctive_name(labels[0])
    return parse_subject(subject_ref)[1]


def _query_for(store: SemanticStore, discriminator: Mapping[str, Any], source_id: str,
               profile: Mapping[str, Any]) -> tuple[str, str] | None:
    """(operation, value) this source would need to answer the discriminator."""
    scheme, value = parse_subject(discriminator["desired_subject_ref"])
    route = _SCHEME_ROUTES.get(scheme)
    if route and route[0] == source_id:
        return route[1], value
    operations = profile["supported_operations"]
    if scheme in ("URL", "DOMAIN"):
        if source_id == "wayback" and "HISTORICAL_ENUMERATE" in operations:
            return "HISTORICAL_ENUMERATE", value
        if "FETCH" in operations:
            return "FETCH", value if value.startswith("http") else f"https://{value}"
        return None
    if "SEARCH" in operations:
        return "SEARCH", _subject_search_term(store, discriminator["desired_subject_ref"])
    return None


def plan_collection_routes(store: SemanticStore, registry: RegistryView,
                           discriminator: Mapping[str, Any], *, requirement_id: str,
                           need_id: str = "", now: str, actor: str,
                           marking: Marking) -> list[dict[str, Any]]:
    """Rank candidate collection actions with explained EIV factors."""
    coverage = coverage_summary(store, need_id) if need_id else {}
    coverage_by_source = {source: state for state, sources in coverage.items()
                          for source in sources}
    basis_groups = existing_basis_groups(store, discriminator)
    hints = set(discriminator["source_family_hints"])
    candidates = []
    for descriptor in registry.current_descriptors():
        profile = registry.profile(descriptor.source_id)
        if profile is None:
            continue
        query = _query_for(store, discriminator, descriptor.source_id, profile)
        if query is None:
            continue
        operation, value = query
        factors: list[tuple[str, float]] = []
        discriminating = 1.0 if (descriptor.source_id in hints
                                 or descriptor.source_type in hints) else 0.6
        factors.append(("discriminating_power", discriminating))
        coverage_state = coverage_by_source.get(descriptor.source_id, "NOT_SEARCHED")
        factors.append(("coverage_gap", _COVERAGE_SCORE.get(coverage_state, 0.5)))
        origin = _source_origin_family(descriptor.source_id, value)
        independence = 1.0 if origin not in basis_groups else 0.1
        factors.append(("independence_gain", independence))
        factors.append(("latency", _LATENCY_SCORE.get(profile["update_latency"], 0.5)))
        factors.append(("cost", _COST_SCORE.get(profile["cost_class"], 0.6)))
        wants_history = "histor" in discriminator["question"].casefold()
        depth = 1.0 if (wants_history and profile["historical_depth"]
                        in ("DEEP_ARCHIVE", "VERSIONED")) else \
            0.3 if wants_history else 0.6
        factors.append(("historical_depth", depth))
        decision = classify_access(descriptor, descriptor.base_urls[0], now=now)
        eligible = acquisition_eligible(decision)
        factors.append(("access", 1.0 if eligible else 0.0))
        last_failure = registry.last_status(descriptor.source_id, "FAILURE")
        last_success = registry.last_status(descriptor.source_id, "SUCCESS")
        reliability = 0.5 if (last_failure and not last_success) else 1.0
        factors.append(("recent_reliability", reliability))

        if discriminator["independence_required"] and independence < 0.5:
            score = 0.0
        else:
            # the question shapes the weights: an explicit source-family
            # recheck values the hinted family; an independence question
            # values a new origin family
            if hints and not discriminator["independence_required"]:
                weights = {"discriminating_power": 0.4, "coverage_gap": 0.15,
                           "independence_gain": 0.05, "latency": 0.05, "cost": 0.05,
                           "historical_depth": 0.1, "access": 0.1, "recent_reliability": 0.1}
            else:
                weights = {"discriminating_power": 0.25, "coverage_gap": 0.2,
                           "independence_gain": 0.2, "latency": 0.05, "cost": 0.05,
                           "historical_depth": 0.1, "access": 0.1, "recent_reliability": 0.05}
            score = round(sum(weights[name] * value for name, value in factors), 4)
        automatable = eligible
        explanation = "; ".join(f"{name}={value:.2f}" for name, value in factors)
        if independence < 0.5:
            explanation += ("; this source joins an origin family already in the basis — "
                            "it corroborates nothing independently")
        if coverage_state != "NOT_SEARCHED":
            explanation += f"; coverage={coverage_state}"
        candidates.append({
            "source_id": descriptor.source_id, "operation": operation, "value": value,
            "factors": tuple(factors), "score": score, "automatable": automatable,
            "human_reason": "" if eligible else
            f"access decision {decision.decision} requires accountable human handling",
            "explanation": explanation,
        })
    candidates.sort(key=lambda c: (-c["score"], c["source_id"]))
    existing_routes = store.latest_by_id("collection_route", "route_id")
    routes = []
    now_time = now
    from curunir_analytic.substrate import resolve_reference_markings
    from curunir_operational.access import inherited_marking
    route_marking = inherited_marking(
        marking,
        resolve_reference_markings(
            store, (discriminator.get("desired_subject_ref"),
                    discriminator.get("discriminator_id"))))
    for rank, candidate in enumerate(candidates, 1):
        route_id = digest_id("route", requirement_id, discriminator["discriminator_id"],
                             candidate["source_id"], candidate["operation"])
        known = existing_routes.get(route_id)
        if known is not None and known["status"] != "PROPOSED":
            # re-planning never regresses a route that already progressed:
            # its execution/task audit state stands
            routes.append(known)
            continue
        route = CollectionRoute(
            route_id=route_id, version=store.next_family_version(
                "collection_route", "route_id", route_id),
            requirement_id=requirement_id,
            discriminator_id=discriminator["discriminator_id"],
            source_id=candidate["source_id"], operation=candidate["operation"],
            query_value=candidate["value"], automatable=candidate["automatable"],
            human_reason=candidate["human_reason"],
            factors=candidate["factors"], score=candidate["score"], rank=rank,
            explanation=candidate["explanation"],
            status="PROPOSED" if candidate["automatable"] else "HUMAN_REQUIRED",
            execution_id="", task_id="", recorded_time=now_time,
            marking=route_marking)
        store.append("COLLECTION_ROUTE_RECORDED", route, recorded_time=now_time, actor=actor)
        routes.append(route.to_record())
    if discriminator["independence_required"] and all(r["score"] == 0 for r in routes):
        _queue_coverage_gap(store, discriminator, routes, now=now_time, actor=actor,
                            marking=marking)
    return routes


def _queue_coverage_gap(store: SemanticStore, discriminator: Mapping[str, Any],
                        routes: list[Mapping[str, Any]], *, now: str, actor: str,
                        marking: Marking) -> None:
    """No registered source family can independently answer the question:
    the gap itself is a typed review signal, not a silent dead end."""
    item_id = digest_id("review-coverage", discriminator["discriminator_id"])
    if any(r["item_id"] == item_id for r in store.records_of("review_item")):
        return
    item = ReviewItem(
        item_id=item_id, kind="COVERAGE_GAP", subject_kind="discriminator",
        subject_id=discriminator["discriminator_id"],
        detail=f"no registered source family can independently answer: "
               f"{discriminator['question'][:200]} ({len(routes)} routes, all scored 0)",
        evidence_refs=tuple(r["route_id"] for r in routes[:5]),
        status="OPEN", resolution_note="", recorded_time=now,
        # the gap is ABOUT the discriminator — inherit its marking
        marking=marking_from_record(discriminator["marking"])
        if isinstance(discriminator.get("marking"), dict) else discriminator["marking"])
    store.append("REVIEW_ITEM_RECORDED", item, recorded_time=now, actor=actor)


def assign_human_route(store: SemanticStore, route: Mapping[str, Any], *,
                       assigned_actor: str, now: str, actor: str,
                       marking: Marking) -> dict[str, Any]:
    """A non-automatable route becomes a mission analyst task, never faked."""
    # the assignment and the spawned task inherit the ROUTE's marking: the
    # task text embeds the route's query, and a re-append never re-classifies
    route_marking = marking_from_record(route["marking"]) \
        if isinstance(route.get("marking"), dict) else route["marking"]
    workflow = MissionWorkflow(store)
    task = workflow.assign_task(
        assigned_role="ANALYST", assigned_actor=assigned_actor, task_type="COLLECTION_FOLLOWUP",
        affected_ids=(route["requirement_id"], route["discriminator_id"]),
        required_action=f"Collect from {route['source_id']}: {route['query_value']} "
                        f"({route['human_reason'] or 'human judgment required'})",
        due_time=None, depends_on=(), recorded_time=now, marking=route_marking, actor=actor)
    updated = CollectionRoute(**{
        **{k: v for k, v in route.items() if k != "record_type"},
        "factors": tuple(tuple(f) for f in route["factors"]),
        "version": store.next_family_version("collection_route", "route_id",
                                             route["route_id"]),
        "task_id": task["task_id"], "recorded_time": now, "marking": route_marking})
    store.append("COLLECTION_ROUTE_RECORDED", updated, recorded_time=now, actor=actor)
    return task


def execute_route(pipeline: SemanticPipeline, registry: RegistryView,
                  route: Mapping[str, Any], *, transports: Mapping[str, Any] | None = None
                  ) -> dict[str, Any]:
    """Execute one automatable route through the OSINT fabric, then run the
    acquired evidence through semantic understanding and refresh the
    originating discriminator and hypotheses.

    Re-run safe in both directions: a route already EXECUTED is NOT
    re-acquired — only its accounting tail (satisfaction, uncertainty
    signals, hypothesis refresh) is completed idempotently; and a failure
    inside the tail records a durable PROCESSING_FAILED review item on the
    route, so the gap is visible and repairable instead of silently
    marooning the collection loop over already-integrated evidence.
    """
    if not route["automatable"]:
        raise ValueError("a human-required route cannot be executed by the machine; "
                         "assign it with assign_human_route instead")
    store = pipeline.store
    current_route = store.latest_by_id("collection_route", "route_id").get(
        route["route_id"], route)
    # everything below operates on the STORE-CURRENT route: a caller's stale
    # mapping must not resurrect pre-assignment or pre-execution state
    route = current_route
    if current_route["status"] == "EXECUTED" and current_route["execution_id"]:
        # completion path: the acquisition already happened; re-running the
        # query would be re-collection, not recovery
        execution = next((e for e in store.records_of("fabric_execution")
                          if e["execution_id"] == current_route["execution_id"]), None)
        if execution is None:
            raise ValueError(
                f"route {route['route_id'][:24]} records execution "
                f"{current_route['execution_id'][:24]} but no such execution "
                "exists in the log: refusing to guess an outcome")
        execution_outcome = execution["outcome"]
        manifestation_ids: list[str] = []
    else:
        fabric_ctx = FabricContext(
            store=store, registry=registry,
            custody=SourceCustodyStore(pipeline.custody_root),
            actor=pipeline.actor, marking=pipeline.marking, now_fn=pipeline.now_fn,
            transports=dict(transports or {}), rate_gate=RateGate(0.5))
        query = QuerySpec(
            query_id=digest_id("routequery", route["route_id"]),
            family="RELATIONSHIP_PIVOT", value=route["query_value"], language="", script="",
            operation=route["operation"], source_id=route["source_id"],
            time_bounds=(None, None), origin="RULE", origin_detail="collection-planner",
            rationale=f"route {route['route_id'][:18]} for requirement "
                      f"{route['requirement_id'][:18]}",
            derived_from=(route["discriminator_id"],))
        outcome = execute_single(fabric_ctx, query=query, source_id=route["source_id"])
        now = pipeline.now_fn()
        updated = CollectionRoute(**{
            **{k: v for k, v in route.items() if k != "record_type"},
            "factors": tuple(tuple(f) for f in route["factors"]),
            "version": store.next_family_version("collection_route", "route_id",
                                                 route["route_id"]),
            "status": "EXECUTED" if outcome.execution.outcome in
            ("EXECUTED_WITH_RESULTS", "EXECUTED_EMPTY") else "FAILED",
            "execution_id": outcome.execution.execution_id,
            "recorded_time": now,
            # a re-append never re-classifies the route
            "marking": marking_from_record(route["marking"])
            if isinstance(route.get("marking"), dict) else route["marking"]})
        store.append("COLLECTION_ROUTE_RECORDED", updated, recorded_time=now,
                     actor=pipeline.actor)
        execution_outcome = outcome.execution.outcome
        manifestation_ids = [m.manifestation_id for m in outcome.manifestations]

    try:
        return _route_accounting(pipeline, route, execution_outcome,
                                 manifestation_ids)
    except Exception as error:
        # the acquired evidence is preserved and (partially) integrated; the
        # accounting gap must be durable state, not a swallowed exception
        item_id = digest_id("review-processing", "route", route["route_id"])
        latest = store.latest_by_id("review_item", "item_id").get(item_id)
        detail = (f"route accounting failed after execution: "
                  f"{type(error).__name__}: {str(error)[:240]}; re-run "
                  f"execute_route on this route to complete satisfaction and "
                  f"hypothesis accounting (the acquisition is NOT repeated)")
        if latest is None or latest["status"] != "OPEN" or latest["detail"] != detail:
            item = ReviewItem(
                item_id=item_id, kind="PROCESSING_FAILED",
                subject_kind="collection_route", subject_id=route["route_id"],
                detail=detail, evidence_refs=(route["route_id"],),
                status="OPEN", resolution_note="",
                version=store.next_family_version("review_item", "item_id", item_id),
                recorded_time=pipeline.now_fn(), marking=pipeline.marking)
            store.append("REVIEW_ITEM_RECORDED", item,
                         recorded_time=item.recorded_time, actor=pipeline.actor)
        raise


def _route_accounting(pipeline: SemanticPipeline, route: Mapping[str, Any],
                      execution_outcome: str,
                      manifestation_ids: list[str]) -> dict[str, Any]:
    """The idempotent tail: understand the evidence, account discriminator
    satisfaction, record typed uncertainty, refresh hypotheses."""
    store = pipeline.store
    processed = pipeline.process_new_evidence()

    # a previously recorded accounting failure resolves on a completed pass
    failure_id = digest_id("review-processing", "route", route["route_id"])
    open_failure = store.latest_by_id("review_item", "item_id").get(failure_id)

    # discriminator satisfaction + evidence request + hypothesis refresh
    discriminator = store.latest_by_id("discriminator", "discriminator_id").get(
        route["discriminator_id"])
    satisfied_by = discriminator_satisfied_by(store, discriminator) if discriminator else []
    route_execution_id = store.latest_by_id("collection_route", "route_id").get(
        route["route_id"], route).get("execution_id", "")
    if discriminator and satisfied_by and discriminator["status"] != "SATISFIED":
        discriminator = update_discriminator(
            store, discriminator, {"status": "SATISFIED"},
            now=pipeline.now_fn(), actor=pipeline.actor, marking=pipeline.marking)
    elif discriminator and not satisfied_by \
            and execution_outcome == "EXECUTED_EMPTY":
        # the search ran and the expected observation was not there: a typed
        # uncertainty signal, never an inferred event — absence of results is
        # not evidence of absence
        item_id = digest_id("review-expected", discriminator["discriminator_id"],
                            route["route_id"])
        if not any(r["item_id"] == item_id for r in store.records_of("review_item")):
            item = ReviewItem(
                item_id=item_id, kind="EXPECTED_NOT_OBSERVED",
                subject_kind="discriminator", subject_id=discriminator["discriminator_id"],
                detail=f"route {route['source_id']}/{route['operation']} executed and "
                       f"returned no results for: {discriminator['question'][:180]}. "
                       f"Absence of results is not evidence of absence; coverage of "
                       f"other families remains open.",
                evidence_refs=(route_execution_id or route["route_id"],),
                status="OPEN", resolution_note="",
                recorded_time=pipeline.now_fn(),
                # the item is ABOUT the discriminator — inherit its marking
                marking=marking_from_record(discriminator["marking"])
                if isinstance(discriminator.get("marking"), dict)
                else discriminator["marking"])
            store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                         actor=pipeline.actor)
    if discriminator:
        ctx = pipeline.context()
        touched_claims = set(discriminator["claim_ids"])
        for hypothesis_id in discriminator["hypothesis_ids"]:
            hypothesis = store.current_hypotheses().get(hypothesis_id)
            if hypothesis:
                touched_claims |= set(hypothesis["supporting_claim_ids"])
                touched_claims |= set(hypothesis["contradicting_claim_ids"])
        refreshed = refresh_hypotheses_for_claims(ctx, touched_claims) if touched_claims else []
    else:
        refreshed = []
    if open_failure is not None and open_failure["status"] == "OPEN":
        resolved = ReviewItem(
            item_id=failure_id, kind="PROCESSING_FAILED",
            subject_kind="collection_route", subject_id=route["route_id"],
            detail=open_failure["detail"],
            evidence_refs=tuple(open_failure["evidence_refs"]),
            status="RESOLVED", resolution_note="route accounting completed",
            version=store.next_family_version("review_item", "item_id", failure_id),
            recorded_time=pipeline.now_fn(),
            # a re-append never re-classifies: keep the failure item's marking
            marking=marking_from_record(open_failure["marking"])
            if isinstance(open_failure.get("marking"), dict)
            else open_failure["marking"])
        store.append("REVIEW_ITEM_RECORDED", resolved,
                     recorded_time=resolved.recorded_time, actor=pipeline.actor)
    return {"route_id": route["route_id"],
            "execution_outcome": execution_outcome,
            "manifestations": manifestation_ids,
            "processed": len(processed["processed"]),
            "discriminator_status": discriminator["status"] if discriminator else "UNKNOWN",
            "observations_satisfying": len(satisfied_by),
            "hypotheses_refreshed": [h["hypothesis_id"] for h in refreshed]}
