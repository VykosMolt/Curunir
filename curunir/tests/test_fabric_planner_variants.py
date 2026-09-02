"""The discovery planner and the name variants it plans over, offline."""
from __future__ import annotations

import pytest

from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import InformationNeed, QuerySpec
from curunir_fabric.planner import eligible_sources, plan_discovery
from curunir_fabric.registry import load_registry
from curunir_fabric.store import FabricStore
from curunir_fabric.variants import (cyrillic_to_latin, fold_diacritics, latin_to_cyrillic,
                                     name_variants)
from curunir_operational.access import Marking

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
MARK = Marking(owning_authority="test-fabric", releasability=("PUBLIC",))


@pytest.fixture()
def registry(tmp_path):
    store = FabricStore.create(tmp_path / "store", "planner-test", NOW)
    seed_starter_catalog(store, recorded_time=NOW, actor="t")
    return load_registry(store)


def _need(**overrides) -> InformationNeed:
    base = dict(
        need_id="need-1", requirement_id="req-1", mission_context="m",
        question="Who are the historical directors of Severstal?",
        entities=("Severstal",), identifiers=(), time_bounds=(None, None),
        geography=("RU",), languages=("en", "ru"), scripts=("Cyrl",),
        hypotheses=(), urgency="ROUTINE", created_by="t", created_time=NOW, marking=MARK,
    )
    base.update(overrides)
    return InformationNeed(**base)


def test_variant_generation_is_deterministic_and_multiscript():
    variants = name_variants("Севергрупп", languages=("ru",), aliases=("Severgroup",))
    families = {(v.family, v.value) for v in variants}
    assert ("EXACT_NAME", "Севергрупп") in families
    assert ("ALIAS", "Severgroup") in families
    assert ("TRANSLITERATION", cyrillic_to_latin("Севергрупп")) in families
    assert name_variants("Севергрупп", languages=("ru",), aliases=("Severgroup",)) == variants


def test_latin_name_gets_cyrillic_variant_only_when_relevant():
    with_ru = name_variants("Severstal", languages=("ru",))
    assert any(v.script == "Cyrl" for v in with_ru)
    without_ru = name_variants("Severstal", languages=("en",))
    assert not any(v.script == "Cyrl" for v in without_ru)


def test_diacritics_fold_and_local_labels_become_queries():
    variants = name_variants("Škoda", local_labels=(("cs", "Škoda Auto"),))
    values = {v.value for v in variants}
    assert fold_diacritics("Škoda") == "Skoda"
    assert "Skoda" in values
    assert any(v.family == "LOCAL_LANGUAGE" and v.value == "Škoda Auto" for v in variants)


def test_latin_to_cyrillic_handles_digraphs():
    assert latin_to_cyrillic("shchuka") == "щука"


def test_plan_is_typed_attributable_and_deduplicated(registry):
    plan = plan_discovery(_need(), registry, now=NOW, marking=MARK)
    assert plan.queries, "plan must contain queries"
    assert len({q.query_id for q in plan.queries}) == len(plan.queries)
    for query in plan.queries:
        assert query.origin == "RULE"
        assert query.rationale
    families = {q.family for q in plan.queries}
    assert "EXACT_NAME" in families and "TRANSLITERATION" in families


def test_identifier_routing_binds_known_schemes(registry):
    need = _need(identifiers=(("LEI", "5493001KJTIIGC8Y1R12"),
                              ("URL", "https://severstal.com/"),
                              ("MYSTERY", "123")))
    plan = plan_discovery(need, registry, now=NOW, marking=MARK)
    by_family = {}
    for query in plan.queries:
        by_family.setdefault(query.family, []).append(query)
    lei = [q for q in by_family["IDENTIFIER"] if q.value == "5493001KJTIIGC8Y1R12"]
    assert lei and lei[0].source_id == "gleif" and lei[0].operation == "LOOKUP"
    domains = by_family.get("DOMAIN", [])
    assert any(q.source_id == "wayback" and q.operation == "HISTORICAL_ENUMERATE" for q in domains)
    assert any(q.source_id == "live-web" and q.operation == "FETCH" for q in domains)
    # An unknown identifier scheme is kept as an unrouted query, never dropped.
    mystery = [q for q in plan.queries if q.value == "123"]
    assert mystery and mystery[0].source_id == ""
    assert mystery[0].query_id in plan.unmatched_query_ids


def test_unmatched_queries_are_recorded_not_dropped(registry):
    # No source can FETCH plain text, so this query matches nothing.
    fetch_query = QuerySpec(query_id="q-fetch", family="EXACT_NAME", value="plain text",
                            language="", script="", operation="FETCH", source_id="",
                            time_bounds=(None, None), origin="HUMAN", origin_detail="test",
                            rationale="r", derived_from=())
    assert eligible_sources(fetch_query, registry) == []
    plan = plan_discovery(_need(), registry, model_queries=(fetch_query,), now=NOW, marking=MARK)
    assert "q-fetch" in plan.unmatched_query_ids


def test_model_queries_keep_model_origin_and_pass_matching(registry):
    model_query = QuerySpec(query_id="q-model", family="DISAMBIGUATION",
                            value="Severstal steel Cherepovets", language="en", script="Latn",
                            operation="SEARCH", source_id="", time_bounds=(None, None),
                            origin="MODEL", origin_detail="analyst-model-1",
                            rationale="disambiguate from similarly named entities", derived_from=())
    plan = plan_discovery(_need(), registry, model_queries=(model_query,), now=NOW, marking=MARK)
    recorded = [q for q in plan.queries if q.query_id == "q-model"]
    assert recorded and recorded[0].origin == "MODEL"


def test_budget_caps_query_count(registry):
    need = _need(entities=tuple(f"Entity {i}" for i in range(40)))
    plan = plan_discovery(need, registry, now=NOW, marking=MARK, budget_max_requests=10)
    assert len(plan.queries) == 10


def test_search_queries_match_search_capable_sources(registry):
    plan = plan_discovery(_need(languages=("en",)), registry, now=NOW, marking=MARK)
    search_query = next(q for q in plan.queries if q.operation == "SEARCH")
    sources = set(eligible_sources(search_query, registry))
    assert "wikidata" in sources
    assert "wayback" not in sources  # it cannot run a text search
