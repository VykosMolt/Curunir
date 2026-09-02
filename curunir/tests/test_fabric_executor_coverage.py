"""Running a plan, keeping the bytes, and reporting what was actually searched.
Fake transports stand in for the network."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.custody import SourceCustodyStore
from argus.source_intelligence.models import SourceDescriptor
from curunir_fabric.catalog import seed_starter_catalog, starter_catalog
from curunir_fabric.contracts import CapabilityProfile, InformationNeed
from curunir_fabric.coverage import assess_coverage, coverage_summary, unsearched_families
from curunir_fabric.executor import ExecutionContext, RateGate, execute_plan, execute_single
from curunir_fabric.planner import plan_discovery
from curunir_fabric.contracts import QuerySpec
from curunir_fabric.registry import load_registry, register_source
from curunir_fabric.store import FabricStore
from curunir_operational.access import Marking

pytestmark = pytest.mark.no_db

NOW = "2026-08-15T12:00:00+00:00"
MARK = Marking(owning_authority="test-fabric", releasability=("PUBLIC",))

WIKIDATA_BODY = json.dumps({"search": [
    {"id": "Q4416184", "label": "Severstal", "description": "Russian steel company",
     "concepturi": "http://www.wikidata.org/entity/Q4416184"}]}).encode()
# A real empty EDGAR answer still carries its hits container; a 200 without one
# is a source error, not a finding of nothing.
EMPTY_SEARCH_BODY = json.dumps({"hits": {"hits": [], "total": {"value": 0}}}).encode()
GLEIF_BODY = json.dumps({
    "meta": {"pagination": {"currentPage": 1, "lastPage": 1}},
    "data": [{"id": "2534000WLRB86TSQ3245", "attributes": {
        "lei": "2534000WLRB86TSQ3245",
        "entity": {"legalName": {"name": "Severstal"}, "jurisdiction": "RU",
                   "status": "ACTIVE", "otherNames": [],
                   "legalAddress": {"city": "Cherepovets", "country": "RU"}},
        "registration": {"status": "ISSUED", "lastUpdateDate": "2026-01-01T00:00:00+00:00"}}}],
}).encode()


def _transport(body: bytes, status: int = 200, error: str | None = None,
               content_type: str = "application/json"):
    def transport(*, url, request_headers, timeout_seconds, maximum_bytes):
        return {"body": body, "status": status, "final_url": url,
                "headers": {"content-type": content_type}, "redirects": (),
                "error": error, "truncated": False}
    return transport


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
    store = FabricStore.create(tmp_path / "store", "executor-test", NOW)
    seed_starter_catalog(store, recorded_time=NOW, actor="t")
    now = _clock()
    return ExecutionContext(
        store=store, registry=load_registry(store),
        custody=SourceCustodyStore(tmp_path / "custody"),
        actor="t", marking=MARK, now_fn=now,
        transports={"wikidata-v1": _transport(WIKIDATA_BODY),
                    "gleif-lei-v1": _transport(GLEIF_BODY),
                    "sec-edgar-fts-v1": _transport(EMPTY_SEARCH_BODY)},
        rate_gate=RateGate(0.0),
    )


def _need(**overrides) -> InformationNeed:
    base = dict(
        need_id="need-1", requirement_id="req-1", mission_context="m",
        question="What is Severstal's corporate identity?",
        entities=("Severstal",), identifiers=(), time_bounds=(None, None),
        geography=(), languages=("en",), scripts=(), hypotheses=(),
        urgency="ROUTINE", created_by="t", created_time=NOW, marking=MARK,
    )
    base.update(overrides)
    return InformationNeed(**base)


def _record_need(store, need):
    store.append("FABRIC_NEED_RECORDED", need, recorded_time=NOW, actor="t")


def _plan(ctx, need, **kwargs):
    plan = plan_discovery(need, ctx.registry, now=NOW, marking=MARK, **kwargs)
    ctx.store.append("FABRIC_PLAN_RECORDED", plan, recorded_time=NOW, actor="t")
    return plan


def test_execution_outcomes_are_truthful_per_source(ctx):
    need = _need()
    _record_need(ctx.store, need)
    plan = _plan(ctx, need)
    outcomes = execute_plan(ctx, plan)
    by_source = {}
    for outcome in outcomes:
        by_source.setdefault(outcome.execution.source_id, set()).add(outcome.execution.outcome)
    assert "EXECUTED_WITH_RESULTS" in by_source["wikidata"]
    assert "EXECUTED_WITH_RESULTS" in by_source["gleif"]
    # EDGAR answered with nothing, which is a result, not a failure.
    assert by_source["sec-edgar"] == {"EXECUTED_EMPTY"}


def test_empty_search_responses_are_preserved_as_evidence(ctx):
    need = _need()
    _record_need(ctx.store, need)
    query = QuerySpec(query_id="q-edgar", family="EXACT_NAME", value="Severstal",
                      language="en", script="Latn", operation="SEARCH", source_id="sec-edgar",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    outcome = execute_single(ctx, query=query, source_id="sec-edgar")
    assert outcome.execution.outcome == "EXECUTED_EMPTY"
    assert outcome.manifestations, "the empty response bytes are evidence the search ran"
    manifestation = outcome.manifestations[0]
    digest = hashlib.sha256(EMPTY_SEARCH_BODY).hexdigest()
    assert manifestation.content_sha256 == digest
    stored = Path(ctx.custody.root) / "sha256" / digest[:2] / digest[2:4] / digest
    assert stored.exists() and stored.read_bytes() == EMPTY_SEARCH_BODY


def test_connector_fault_is_source_failed_but_memory_error_propagates(ctx):
    query = QuerySpec(query_id="q-fault", family="EXACT_NAME", value="Severstal",
                      language="", script="", operation="SEARCH", source_id="gleif",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())

    def source_fault(**kwargs):
        raise ConnectionResetError("peer reset")

    ctx.transports["gleif-lei-v1"] = source_fault
    result = execute_single(ctx, query=query, source_id="gleif")
    assert result.execution.outcome == "SOURCE_FAILED"

    def local_exhaustion(**kwargs):
        raise MemoryError("out of memory")

    ctx.transports["gleif-lei-v1"] = local_exhaustion
    with pytest.raises(MemoryError):
        execute_single(ctx, query=query, source_id="gleif")


def test_local_custody_fault_propagates_without_blame(ctx):
    from curunir_fabric.executor import LocalStorageFault

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    ctx.custody.preserve = full_disk
    query = QuerySpec(query_id="q-disk", family="EXACT_NAME", value="Severstal",
                      language="", script="", operation="SEARCH", source_id="wikidata",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    with pytest.raises(LocalStorageFault):
        execute_single(ctx, query=query, source_id="wikidata")
    assert not [record for record in ctx.store.records_of("fabric_source_status")
                if record["source_id"] == "wikidata" and record["kind"] == "FAILURE"]


def test_manifestation_links_prior_version_of_same_target(ctx):
    query = QuerySpec(query_id="q-w", family="EXACT_NAME", value="Severstal", language="en",
                      script="", operation="SEARCH", source_id="wikidata",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    first = execute_single(ctx, query=query, source_id="wikidata")
    ctx.transports["wikidata-v1"] = _transport(WIKIDATA_BODY + b"\n")
    second = execute_single(ctx, query=query, source_id="wikidata")
    assert second.manifestations[0].prior_manifestation_id == \
        first.manifestations[0].manifestation_id
    assert second.manifestations[0].content_sha256 != first.manifestations[0].content_sha256


def test_policy_refusal_never_reaches_the_network(tmp_path):
    store = FabricStore.create(tmp_path / "store", "policy-test", NOW)
    restricted_descriptor, profile = starter_catalog(NOW)[0]
    restricted = SourceDescriptor(**{
        **{f: getattr(restricted_descriptor, f) for f in restricted_descriptor.__dataclass_fields__},
        "source_id": "restricted-api", "access_class": "RESTRICTED"})
    restricted_profile = CapabilityProfile(**{
        **{f: getattr(profile, f) for f in profile.__dataclass_fields__},
        "profile_id": "p-restricted", "source_id": "restricted-api"})
    register_source(store, restricted, restricted_profile, recorded_time=NOW, actor="t")

    calls = []
    def tripwire(**kw):
        calls.append(kw)
        return {"body": b"", "status": 200, "final_url": "", "headers": {}, "redirects": (),
                "error": None, "truncated": False}

    ctx = ExecutionContext(store=store, registry=load_registry(store),
                           custody=SourceCustodyStore(tmp_path / "custody"),
                           actor="t", marking=MARK, now_fn=_clock(),
                           transports={"wikidata-v1": tripwire}, rate_gate=RateGate(0.0))
    query = QuerySpec(query_id="q-r", family="EXACT_NAME", value="x", language="",
                      script="", operation="SEARCH", source_id="restricted-api",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    outcome = execute_single(ctx, query=query, source_id="restricted-api")
    assert outcome.execution.outcome == "POLICY_REFUSED"
    assert outcome.execution.policy_decision == "NOT_PUBLIC"
    assert calls == [], "policy refusal must precede any network activity"


def test_source_failure_is_recorded_with_status_event(ctx):
    ctx.transports["gleif-lei-v1"] = _transport(b"", status=500, error="HTTP_500")
    query = QuerySpec(query_id="q-f", family="EXACT_NAME", value="Severstal", language="",
                      script="", operation="SEARCH", source_id="gleif",
                      time_bounds=(None, None), origin="RULE", origin_detail="test",
                      rationale="r", derived_from=())
    outcome = execute_single(ctx, query=query, source_id="gleif")
    assert outcome.execution.outcome == "SOURCE_FAILED"
    statuses = [r for r in ctx.store.records_of("fabric_source_status")
                if r["source_id"] == "gleif" and r["kind"] == "FAILURE"]
    assert statuses, "failures must land in the source status stream"


def test_coverage_distinguishes_searched_failed_and_unsearched(ctx):
    need = _need()
    _record_need(ctx.store, need)
    plan = _plan(ctx, need)
    execute_plan(ctx, plan)
    now = ctx.now_fn()
    assess_coverage(ctx.store, ctx.registry, "need-1", now=now, actor="t", marking=MARK)
    summary = coverage_summary(ctx.store, "need-1")
    flat = {source: state for state, sources in summary.items() for source in sources}
    assert flat["wikidata"] == "COVERED"
    assert flat["gleif"] == "COVERED"
    assert flat["sec-edgar"] == "COVERED"          # searched, found nothing
    assert flat["wayback"] == "NOT_SEARCHED"       # never asked, and it says so
    assert flat["live-web"] == "NOT_SEARCHED"
    # Finding nothing is recorded with the caveat that it proves nothing.
    edgar = [r for r in ctx.store.records_of("fabric_coverage")
             if r["need_id"] == "need-1" and r["source_id"] == "sec-edgar"][-1]
    assert any("not evidence of nonexistence" in gap for gap in edgar["gaps"])


def test_coverage_marks_source_failures_distinctly(ctx):
    need = _need(need_id="need-2")
    _record_need(ctx.store, need)
    ctx.transports["wikidata-v1"] = _transport(b"", status=503, error="HTTP_503")
    ctx.transports["gleif-lei-v1"] = _transport(b"", status=503, error="HTTP_503")
    ctx.transports["sec-edgar-fts-v1"] = _transport(b"", status=503, error="HTTP_503")
    plan = _plan(ctx, need)
    execute_plan(ctx, plan)
    assess_coverage(ctx.store, ctx.registry, "need-2", now=ctx.now_fn(), actor="t", marking=MARK)
    summary = coverage_summary(ctx.store, "need-2")
    assert set(summary.get("SOURCE_FAILED", ())) >= {"wikidata", "gleif", "sec-edgar"}


def test_time_window_outside_source_coverage_is_not_available(ctx):
    need = _need(need_id="need-3",
                 time_bounds=("1970-01-01T00:00:00+00:00", "1980-01-01T00:00:00+00:00"))
    _record_need(ctx.store, need)
    plan = _plan(ctx, need)
    # Nothing is executed: coverage must judge from the declared profiles alone.
    assess_coverage(ctx.store, ctx.registry, "need-3", now=ctx.now_fn(), actor="t", marking=MARK)
    summary = coverage_summary(ctx.store, "need-3")
    flat = {source: state for state, sources in summary.items() for source in sources}
    assert flat["sec-edgar"] == "NOT_AVAILABLE"    # its full text starts in 2001
    assert flat["wikidata"] == "NOT_SEARCHED"


def test_unsearched_families_lists_untouched_source_types(ctx):
    need = _need(need_id="need-4")
    _record_need(ctx.store, need)
    plan = _plan(ctx, need)
    execute_plan(ctx, plan)
    assess_coverage(ctx.store, ctx.registry, "need-4", now=ctx.now_fn(), actor="t", marking=MARK)
    families = unsearched_families(ctx.store, ctx.registry, "need-4")
    assert "PUBLIC_ARCHIVE" in families            # wayback was never asked
    assert "OFFICIAL_GAZETTE" in families
    assert "PUBLIC_DATASET" not in families        # wikidata was searched
