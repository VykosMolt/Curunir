"""Typed pivot graph: evidence-grounded reasons to look somewhere else.

Rule-based proposers walk acquired native results and emit PivotEdge
proposals (entity → identifier → source family → document history …), each
citing the manifestation that produced it. Pivots are reversible proposals:
resolution appends a new record with the same pivot_id (latest wins on
replay); identity is never merged here. ``queries_from_pivots`` turns
accepted or proposed pivots into next-generation query specs so discovery
can continue from what it found.
"""
from __future__ import annotations

from urllib.parse import urlparse

from argus.source_intelligence.models import digest_id

from . import PLANNER_VERSION
from .contracts import PivotEdge, QuerySpec
from .executor import ExecutionResult
from .store import FabricStore

# identifier scheme → source family whose records it unlocks
_SCHEME_TO_SOURCE = {
    "LEI": "gleif",
    "SEC_CIK": "sec-edgar",
    "SEC_ACCESSION": "sec-edgar",
    "WIKIDATA_QID": "wikidata",
}

# One acquired response can carry source-controlled cardinality (a Wikidata
# entity with tens of thousands of aliases/labels), and each becomes a pivot
# proposal append. Bound the proposals recorded per response so a single hostile
# or verbose response cannot drive an unbounded number of store appends. Pivots
# are human-review PROPOSALS, not evidence, so a generous deterministic cap is
# safe; a real entity yields a handful.
MAX_PIVOTS_PER_RESPONSE = 500


def propose_pivots(store: FabricStore, outcome: ExecutionResult, *, subject: str,
                   now: str, actor: str, marking) -> list[PivotEdge]:
    """Derive rule-based pivots from one execution's native results."""
    if not outcome.manifestations:
        return []
    evidence = tuple(item.manifestation_id for item in outcome.manifestations)
    pivots: list[PivotEdge] = []

    def add(from_kind: str, from_ref: str, to_kind: str, to_ref: str,
            pivot_type: str, rationale: str) -> None:
        if not to_ref or from_ref == to_ref:
            return
        pivots.append(PivotEdge(
            pivot_id=digest_id("pivot", from_kind, from_ref, to_kind, to_ref, pivot_type),
            from_kind=from_kind, from_ref=from_ref, to_kind=to_kind, to_ref=to_ref,
            pivot_type=pivot_type, rationale=rationale,
            evidence_manifestation_ids=evidence,
            origin="RULE", status="PROPOSED", created_time=now, marking=marking,
        ))

    for result in outcome.results:
        for scheme, value in result.identifiers:
            reference = f"{scheme}:{value}"
            add("ENTITY", subject, "IDENTIFIER", reference, "IDENTIFIER_OF",
                f"{scheme} identifier found on {result.native_id}")
            source_family = _SCHEME_TO_SOURCE.get(scheme)
            if source_family:
                add("IDENTIFIER", reference, "SOURCE_FAMILY", source_family,
                    "LEADS_TO_SOURCE_FAMILY", f"{scheme} identifiers resolve in {source_family}")
            if scheme in ("OFFICIAL_WEBSITE", "URL"):
                domain = urlparse(value).hostname or ""
                add("ENTITY", subject, "DOMAIN", domain, "OPERATES_DOMAIN",
                    f"official website recorded on {result.native_id}")
        for key, value in result.attributes:
            if key == "successor_lei":
                add("IDENTIFIER", f"LEI:{result.native_id}", "IDENTIFIER", f"LEI:{value}",
                    "SUCCESSOR_OF", "successor entity recorded in LEI registration")
            if key.startswith(("alias:", "label:")) and value != subject:
                add("ENTITY", subject, "ENTITY", value, "ALIAS_OF",
                    f"{key} recorded on {result.native_id}")

    deduped: dict[str, PivotEdge] = {}
    for pivot in pivots:
        deduped.setdefault(pivot.pivot_id, pivot)
    # bound the appends a single response can drive (source-controlled fan-out)
    recorded = list(deduped.values())[:MAX_PIVOTS_PER_RESPONSE]
    for pivot in recorded:
        store.append("FABRIC_PIVOT_RECORDED", pivot, recorded_time=now, actor=actor)
    return recorded


def resolve_pivot(store: FabricStore, pivot_record: dict, status: str, *, rationale: str,
                  now: str, actor: str, marking) -> PivotEdge:
    """Append a superseding record for the same pivot_id — reversible by design."""
    updated = PivotEdge(
        pivot_id=pivot_record["pivot_id"], from_kind=pivot_record["from_kind"],
        from_ref=pivot_record["from_ref"], to_kind=pivot_record["to_kind"],
        to_ref=pivot_record["to_ref"], pivot_type=pivot_record["pivot_type"],
        rationale=rationale or pivot_record["rationale"],
        evidence_manifestation_ids=tuple(pivot_record["evidence_manifestation_ids"]),
        origin="HUMAN", status=status, created_time=now, marking=marking,
    )
    store.append("FABRIC_PIVOT_RECORDED", updated, recorded_time=now, actor=actor)
    return updated


def current_pivots(store: FabricStore, *, statuses: tuple[str, ...] = ("PROPOSED", "ACCEPTED")) -> list[dict]:
    latest = store.latest_by_id("fabric_pivot", "pivot_id")
    return [record for record in latest.values() if record["status"] in statuses]


def queries_from_pivots(pivot_records: list[dict], *, need_id: str) -> tuple[QuerySpec, ...]:
    """Next-generation queries from the pivot graph (typed, attributable)."""
    queries: list[QuerySpec] = []
    for record in pivot_records:
        to_kind, to_ref = record["to_kind"], record["to_ref"]
        derived = (record["pivot_id"],)
        if to_kind == "IDENTIFIER" and ":" in to_ref:
            scheme, _, value = to_ref.partition(":")
            source_id = _SCHEME_TO_SOURCE.get(scheme, "")
            if source_id:
                queries.append(QuerySpec(
                    query_id=digest_id("query", need_id, "RELATIONSHIP_PIVOT", to_ref),
                    family="RELATIONSHIP_PIVOT", value=value, language="", script="",
                    operation="LOOKUP" if scheme in ("LEI", "WIKIDATA_QID") else "SEARCH",
                    source_id=source_id, time_bounds=(None, None),
                    origin="RULE", origin_detail=PLANNER_VERSION,
                    rationale=f"pivot {record['pivot_type']} from {record['from_ref']}",
                    derived_from=derived,
                ))
        elif to_kind == "DOMAIN":
            queries.append(QuerySpec(
                query_id=digest_id("query", need_id, "DOMAIN", to_ref, "wayback"),
                family="DOMAIN", value=f"https://{to_ref}/", language="", script="",
                operation="HISTORICAL_ENUMERATE", source_id="wayback", time_bounds=(None, None),
                origin="RULE", origin_detail=PLANNER_VERSION,
                rationale=f"enumerate history of pivoted domain {to_ref}", derived_from=derived,
            ))
        elif to_kind == "ENTITY":
            queries.append(QuerySpec(
                query_id=digest_id("query", need_id, "ALIAS", to_ref),
                family="ALIAS", value=to_ref, language="", script="",
                operation="SEARCH", source_id="", time_bounds=(None, None),
                origin="RULE", origin_detail=PLANNER_VERSION,
                rationale=f"alias discovered through pivot {record['pivot_id'][:16]}",
                derived_from=derived,
            ))
    deduped: dict[str, QuerySpec] = {}
    for query in queries:
        deduped.setdefault(query.query_id, query)
    # bound lookups and archive enumeration before unbound alias fan-out
    priority = {"RELATIONSHIP_PIVOT": 0, "DOMAIN": 1, "ALIAS": 2}
    return tuple(sorted(deduped.values(), key=lambda q: (priority.get(q.family, 3), q.query_id)))
