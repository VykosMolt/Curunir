"""Typed pivot graph: evidence-grounded proposals, reversible, query-generating."""
from __future__ import annotations

import json

import pytest

from argus.source_intelligence.custody import SourceCustodyStore
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import PivotEdge, QuerySpec
from curunir_fabric.executor import ExecutionContext, RateGate, execute_single
from curunir_fabric.pivots import current_pivots, propose_pivots, queries_from_pivots, resolve_pivot
from curunir_fabric.registry import load_registry
from curunir_fabric.store import FabricStore
from curunir_operational.access import Marking

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
MARK = Marking(owning_authority="test-fabric", releasability=("PUBLIC",))

LOOKUP_BODY = json.dumps({"entities": {"Q4416184": {
    "labels": {"en": {"language": "en", "value": "Severstal"},
               "ru": {"language": "ru", "value": "Северсталь"}},
    "aliases": {},
    "descriptions": {"en": {"language": "en", "value": "steel company"}},
    "claims": {
        "P1278": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                "datavalue": {"value": "2534000WLRB86TSQ3245", "type": "string"}}}],
        "P856": [{"mainsnak": {"snaktype": "value", "datatype": "url",
                               "datavalue": {"value": "https://severstal.com/", "type": "string"}}}],
    },
}}}).encode()


def _clock():
    from itertools import count
    tick = count(1)
    def now():
        minutes = next(tick)
        return f"2026-08-15T12:{minutes:02d}:00+00:00" if minutes < 60 \
            else f"2026-08-15T{12 + minutes // 60}:{minutes % 60:02d}:00+00:00"
    return now


@pytest.fixture()
def ctx(tmp_path):
    store = FabricStore.create(tmp_path / "store", "pivot-test", NOW)
    seed_starter_catalog(store, recorded_time=NOW, actor="t")
    def transport(**kw):
        return {"body": LOOKUP_BODY, "status": 200, "final_url": kw["url"],
                "headers": {"content-type": "application/json"}, "redirects": (),
                "error": None, "truncated": False}
    return ExecutionContext(store=store, registry=load_registry(store),
                            custody=SourceCustodyStore(tmp_path / "custody"),
                            actor="t", marking=MARK, now_fn=_clock(),
                            transports={"wikidata-v1": transport}, rate_gate=RateGate(0.0))


def _lookup_outcome(ctx):
    query = QuerySpec(query_id="q-l", family="IDENTIFIER", value="Q4416184", language="",
                      script="", operation="LOOKUP", source_id="wikidata",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    return execute_single(ctx, query=query, source_id="wikidata")


def test_pivots_derive_from_results_with_evidence(ctx):
    outcome = _lookup_outcome(ctx)
    pivots = propose_pivots(ctx.store, outcome, subject="Severstal",
                            now=ctx.now_fn(), actor="t", marking=MARK)
    edges = {(p.from_ref, p.to_kind, p.to_ref, p.pivot_type) for p in pivots}
    assert ("Severstal", "IDENTIFIER", "LEI:2534000WLRB86TSQ3245", "IDENTIFIER_OF") in edges
    assert ("LEI:2534000WLRB86TSQ3245", "SOURCE_FAMILY", "gleif", "LEADS_TO_SOURCE_FAMILY") in edges
    assert ("Severstal", "DOMAIN", "severstal.com", "OPERATES_DOMAIN") in edges
    assert ("Severstal", "ENTITY", "Северсталь", "ALIAS_OF") in edges
    manifestation_id = outcome.manifestations[0].manifestation_id
    for pivot in pivots:
        assert manifestation_id in pivot.evidence_manifestation_ids
        assert pivot.status == "PROPOSED"


def test_rule_pivot_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="evidence"):
        PivotEdge(pivot_id="p1", from_kind="ENTITY", from_ref="A", to_kind="ENTITY",
                  to_ref="B", pivot_type="ALIAS_OF", rationale="r",
                  evidence_manifestation_ids=(), origin="RULE", status="PROPOSED",
                  created_time=NOW, marking=MARK)


def test_pivot_resolution_is_reversible_latest_wins(ctx):
    outcome = _lookup_outcome(ctx)
    pivots = propose_pivots(ctx.store, outcome, subject="Severstal",
                            now=ctx.now_fn(), actor="t", marking=MARK)
    target = next(p for p in pivots if p.pivot_type == "ALIAS_OF")
    record = ctx.store.latest_by_id("fabric_pivot", "pivot_id")[target.pivot_id]
    resolve_pivot(ctx.store, record, "REJECTED", rationale="not the same entity",
                  now=ctx.now_fn(), actor="analyst", marking=MARK)
    latest = ctx.store.latest_by_id("fabric_pivot", "pivot_id")[target.pivot_id]
    assert latest["status"] == "REJECTED"
    resolve_pivot(ctx.store, latest, "ACCEPTED", rationale="confirmed after review",
                  now=ctx.now_fn(), actor="analyst", marking=MARK)
    assert ctx.store.latest_by_id("fabric_pivot", "pivot_id")[target.pivot_id]["status"] == "ACCEPTED"
    # full history retained
    history = [r for r in ctx.store.records_of("fabric_pivot") if r["pivot_id"] == target.pivot_id]
    assert [r["status"] for r in history] == ["PROPOSED", "REJECTED", "ACCEPTED"]


def test_rejected_pivots_do_not_feed_next_generation(ctx):
    outcome = _lookup_outcome(ctx)
    pivots = propose_pivots(ctx.store, outcome, subject="Severstal",
                            now=ctx.now_fn(), actor="t", marking=MARK)
    lei_pivot = next(p for p in pivots if p.to_ref == "LEI:2534000WLRB86TSQ3245"
                     and p.pivot_type == "IDENTIFIER_OF")
    record = ctx.store.latest_by_id("fabric_pivot", "pivot_id")[lei_pivot.pivot_id]
    resolve_pivot(ctx.store, record, "REJECTED", rationale="wrong entity",
                  now=ctx.now_fn(), actor="analyst", marking=MARK)
    live = current_pivots(ctx.store)
    queries = queries_from_pivots(live, need_id="need-1")
    assert not any(q.value == "2534000WLRB86TSQ3245" and q.source_id == "gleif" for q in queries)


def test_queries_from_pivots_route_identifiers_and_domains(ctx):
    outcome = _lookup_outcome(ctx)
    propose_pivots(ctx.store, outcome, subject="Severstal",
                   now=ctx.now_fn(), actor="t", marking=MARK)
    queries = queries_from_pivots(current_pivots(ctx.store), need_id="need-1")
    lei = [q for q in queries if q.value == "2534000WLRB86TSQ3245"]
    assert lei and lei[0].source_id == "gleif" and lei[0].operation == "LOOKUP"
    assert lei[0].derived_from, "pivot lineage must be carried on the query"
    domain = [q for q in queries if "severstal.com" in q.value]
    assert domain and domain[0].source_id == "wayback" \
        and domain[0].operation == "HISTORICAL_ENUMERATE"
    alias = [q for q in queries if q.value == "Северсталь"]
    assert alias and alias[0].family == "ALIAS" and alias[0].operation == "SEARCH"
