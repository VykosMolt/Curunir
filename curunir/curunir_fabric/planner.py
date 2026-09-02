"""Federated discovery planner.

Turns an information need into a typed, inspectable, executable plan:
query/pivot families expanded deterministically from the need's entities,
identifiers, languages and time bounds, then matched against registered
source capabilities. Queries no registered source can execute are retained
as ``unmatched_query_ids`` — a planned-but-unsearchable ask is recorded, not
dropped. Model-proposed queries enter as origin=MODEL specs and pass the
same matching and policy gates as everything else.
"""
from __future__ import annotations

from argus.source_intelligence.models import digest_id

from . import PLANNER_VERSION
from .contracts import DiscoveryPlan, InformationNeed, QuerySpec
from .registry import RegistryView

# identifier scheme → (source_id, operation, family)
IDENTIFIER_ROUTES = {
    "LEI": ("gleif", "LOOKUP", "IDENTIFIER"),
    "WIKIDATA_QID": ("wikidata", "LOOKUP", "IDENTIFIER"),
    "SEC_CIK": ("sec-edgar", "SEARCH", "ORG_ID"),
    "SEC_ACCESSION": ("sec-edgar", "SEARCH", "ORG_ID"),
    "URL": ("live-web", "FETCH", "DOMAIN"),
    "OFFICIAL_WEBSITE": ("live-web", "FETCH", "DOMAIN"),
    "DOMAIN": ("wayback", "HISTORICAL_ENUMERATE", "DOMAIN"),
    "FEED": ("federal-register-feed", "FETCH", "FEED_POLL"),
}


def _query_id(*parts: object) -> str:
    return digest_id("query", *parts)


def eligible_sources(query: QuerySpec, registry: RegistryView) -> list[str]:
    """Registered sources able to execute this query (declared, not assumed)."""
    if query.source_id:
        descriptor = registry.descriptor(query.source_id)
        profile = registry.profile(query.source_id)
        if descriptor is None or profile is None:
            return []
        return [query.source_id] if query.operation in profile["supported_operations"] else []
    # native identifiers are source-scoped: an unbound LOOKUP has no honest target
    if query.operation == "LOOKUP":
        return []
    matches = registry.capable_sources(operation=query.operation,
                                       language=query.language or None)
    # generic fetchers cannot run text queries: FETCH needs a URL-shaped value
    if query.operation == "FETCH" and not query.value.startswith(("http://", "https://")):
        return []
    return [descriptor.source_id for descriptor in matches]


def plan_discovery(need: InformationNeed, registry: RegistryView, *,
                   aliases: tuple[str, ...] = (),
                   local_labels: tuple[tuple[str, str], ...] = (),
                   model_queries: tuple[QuerySpec, ...] = (),
                   pivot_queries: tuple[QuerySpec, ...] = (),
                   generation: str = "INITIAL",
                   budget_max_requests: int = 60,
                   now: str, marking) -> DiscoveryPlan:
    from .variants import name_variants  # local import keeps module load light

    queries: list[QuerySpec] = []
    plan_seed = (need.need_id, generation, len(model_queries), len(pivot_queries))

    # non-initial generations carry only their pivot/model queries; the base
    # entity and identifier expansion already ran and is not re-executed
    entities = need.entities if generation == "INITIAL" else ()
    identifiers = need.identifiers if generation == "INITIAL" else ()

    for entity in entities:
        for variant in name_variants(entity, languages=need.languages, scripts=need.scripts,
                                     aliases=aliases, local_labels=local_labels):
            queries.append(QuerySpec(
                query_id=_query_id(need.need_id, variant.family, variant.value, "SEARCH"),
                family=variant.family, value=variant.value,
                language=variant.language, script=variant.script,
                operation="SEARCH", source_id="",
                time_bounds=need.time_bounds,
                origin="RULE", origin_detail=PLANNER_VERSION, rationale=variant.rationale,
                derived_from=(),
            ))

    for scheme, value in identifiers:
        route = IDENTIFIER_ROUTES.get(scheme)
        if route is None:
            queries.append(QuerySpec(
                query_id=_query_id(need.need_id, "IDENTIFIER", scheme, value),
                family="IDENTIFIER", value=value, language="", script="",
                operation="LOOKUP", source_id="", time_bounds=need.time_bounds,
                origin="RULE", origin_detail=PLANNER_VERSION,
                rationale=f"identifier with unrouted scheme {scheme}", derived_from=(),
            ))
            continue
        source_id, operation, family = route
        queries.append(QuerySpec(
            query_id=_query_id(need.need_id, family, scheme, value, source_id),
            family=family, value=value, language="", script="",
            operation=operation, source_id=source_id, time_bounds=need.time_bounds,
            origin="RULE", origin_detail=PLANNER_VERSION,
            rationale=f"{scheme} identifier routed to {source_id}", derived_from=(),
        ))
        if scheme in ("URL", "OFFICIAL_WEBSITE", "DOMAIN"):
            # a live page also has a history worth enumerating
            queries.append(QuerySpec(
                query_id=_query_id(need.need_id, "DOMAIN", scheme, value, "wayback"),
                family="DOMAIN", value=value, language="", script="",
                operation="HISTORICAL_ENUMERATE", source_id="wayback",
                time_bounds=need.time_bounds,
                origin="RULE", origin_detail=PLANNER_VERSION,
                rationale=f"historical captures of {scheme.lower()} {value}", derived_from=(),
            ))

    queries.extend(pivot_queries)
    queries.extend(model_queries)

    deduped: dict[str, QuerySpec] = {}
    for query in queries:
        deduped.setdefault(query.query_id, query)
    bounded = list(deduped.values())[:budget_max_requests]

    considered: set[str] = set()
    unmatched: list[str] = []
    for query in bounded:
        sources = eligible_sources(query, registry)
        considered.update(sources)
        if not sources:
            unmatched.append(query.query_id)

    return DiscoveryPlan(
        plan_id=digest_id("plan", *plan_seed, now),
        need_id=need.need_id, generation=generation,
        queries=tuple(bounded),
        considered_source_ids=tuple(sorted(considered)),
        unmatched_query_ids=tuple(unmatched),
        budget_max_requests=budget_max_requests,
        planner_version=PLANNER_VERSION, created_time=now, marking=marking,
    )


def record_plan(store, plan: DiscoveryPlan, *, recorded_time: str, actor: str) -> None:
    store.append("FABRIC_PLAN_RECORDED", plan, recorded_time=recorded_time, actor=actor)
