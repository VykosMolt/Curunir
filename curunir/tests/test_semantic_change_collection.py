"""What changed between two readings of a source, what that does to the claims
resting on it, and how an open question becomes a plan to go and look."""
from __future__ import annotations

import json

import pytest

from curunir_fabric.registry import load_registry
from curunir_semantic.changes import interpret_change
from curunir_semantic.collection import (assign_human_route, execute_route,
                                         plan_collection_routes, requirement_for_discriminator)
from curunir_semantic.hypotheses import (link_claim, propose_discriminator, record_hypothesis,
                                         refresh_hypothesis)
from curunir_semantic.normalize import load_text

from semantic_support import (GLEIF_RECORD_LAPSED, GLEIF_RECORD_V1, GLEIF_RECORD_V2,
                              PAGE_CORRECTION, PAGE_V1, PAGE_V1_CHROME_ONLY, PAGE_V2_SEMANTIC,
                              MARK, make_pipeline, plant_manifestation)

pytestmark = pytest.mark.no_db


def _plant_page_pair(pipeline, first: bytes, second: bytes):
    prior = plant_manifestation(
        pipeline, source_id="live-web", native_id="https://vessia.example/about",
        body=first, media_type="text/html", retrieval_time="2026-08-17T12:05:00+00:00")
    current = plant_manifestation(
        pipeline, source_id="live-web", native_id="https://vessia.example/about",
        body=second, media_type="text/html", retrieval_time="2026-08-17T13:05:00+00:00",
        prior_manifestation_id=prior["manifestation_id"])
    pipeline.process_manifestation(prior)
    pipeline.process_manifestation(current)
    return prior, current


def _interpret(pipeline, prior, current, **kwargs):
    document = next(d for d in pipeline.store.records_of("semantic_document")
                    if d["manifestation_id"] == current["manifestation_id"])
    return interpret_change(pipeline.context(), prior["manifestation_id"],
                            current["manifestation_id"],
                            current_text=load_text(pipeline.store, document), **kwargs)


def test_chrome_only_change_is_semantically_unchanged(tmp_path):
    pipeline = make_pipeline(tmp_path)
    prior, current = _plant_page_pair(pipeline, PAGE_V1, PAGE_V1_CHROME_ONLY)
    assert prior["content_sha256"] != current["content_sha256"]  # the bytes differ
    changes = _interpret(pipeline, prior, current)
    assert [c["change_class"] for c in changes] == ["SEMANTICALLY_UNCHANGED"]


def test_real_text_change_identifies_affected_claim_and_explains(tmp_path):
    pipeline = make_pipeline(tmp_path)
    prior, current = _plant_page_pair(pipeline, PAGE_V1, PAGE_V2_SEMANTIC)
    changes = _interpret(pipeline, prior, current)
    value_changes = [c for c in changes if c["change_class"] == "VALUE_CHANGED"]
    assert value_changes
    change = next(c for c in value_changes if c["attribute"] == "managing_director")
    assert change["prior_value"] == "Kari Nordmann"
    assert change["current_value"] == "Ola Hansen"
    assert change["affected_claim_ids"], "the changed proposition must be identified"
    assert change["affected_object_ids"]
    store = pipeline.store
    items = store.open_review_items()
    assert any(i["kind"] == "MANIFESTATION_CHANGED" for i in items)
    versions = [c for c in store.records_of("semantic_claim")
                if c["claim_id"] == change["affected_claim_ids"][0]]
    values = [v["object_or_value"] for v in versions]
    assert "Kari Nordmann" in values and "Ola Hansen" in values


def test_structured_field_change_supersedes_claim_within_origin(tmp_path):
    pipeline = make_pipeline(tmp_path)
    prior = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    current = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V2, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T13:05:00+00:00",
        prior_manifestation_id=prior["manifestation_id"])
    pipeline.process_manifestation(prior)
    pipeline.process_manifestation(current)
    changes = _interpret(pipeline, prior, current)
    classes = {c["change_class"] for c in changes}
    assert "ENTITY_ATTRIBUTE_CHANGED" in classes
    store = pipeline.store
    claim = next(c for c in store.current_claims().values() if c["predicate"] == "legal_name")
    assert claim["object_or_value"] == "Vessia Materials AS"
    assert claim["version"] == 2  # one source revising itself, history kept


def test_correction_classifies_and_marks_claim_corrected(tmp_path):
    pipeline = make_pipeline(tmp_path)
    prior, current = _plant_page_pair(pipeline, PAGE_V1, PAGE_CORRECTION)
    changes = _interpret(pipeline, prior, current)
    corrections = [c for c in changes if c["change_class"] == "SOURCE_CORRECTION"]
    assert corrections
    store = pipeline.store
    corrected = [claim_id for change in corrections
                 for claim_id in change["affected_claim_ids"]
                 if store.claim_state(claim_id) == "CORRECTED"]
    assert corrected, "a source correction must mark the affected proposition CORRECTED"
    items = store.open_review_items()
    assert any(i["kind"] == "SOURCE_CORRECTED" for i in items)


def test_contradiction_between_independent_sources_disputes_not_overwrites(tmp_path):
    pipeline = make_pipeline(tmp_path)
    gleif = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(gleif)
    store = pipeline.store
    claim = next(c for c in store.current_claims().values() if c["predicate"] == "legal_name")
    assert claim["object_or_value"] == "Vessia Steel AS"
    # A second, independent source gives a different value for the same subject.
    wikidata_body = json.dumps({"entities": {"Q999001": {
        "id": "Q999001",
        "labels": {"en": {"language": "en", "value": "Vessia Holding"}},
        "aliases": {}, "descriptions": {}, "claims": {}}}}).encode()
    conflicting = plant_manifestation(
        pipeline, source_id="wikidata", native_id="Q999001", body=wikidata_body,
        media_type="application/json", retrieval_time="2026-08-17T13:05:00+00:00")
    pipeline.process_manifestation(conflicting)
    # Point the conflicting observation at the same claim by hand.
    from curunir_semantic.worldmodel import _record_claim_conflict
    ctx = pipeline.context()
    observation = next(o for o in store.records_of("semantic_observation")
                       if o["value"] == "Vessia Holding")
    _record_claim_conflict(ctx, claim, observation, {})
    assert store.claim_state(claim["claim_id"]) == "DISPUTED"
    current = store.current_claims()[claim["claim_id"]]
    assert current["object_or_value"] == "Vessia Steel AS", \
        "a contradiction must not overwrite the standing value"
    assert any(i["kind"] == "CONTRADICTED" for i in store.open_review_items())


def test_hypothesis_degrades_when_supporting_claim_is_corrected(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    prior, current = _plant_page_pair(pipeline, PAGE_V1, PAGE_CORRECTION)
    claim = next(c for c in store.current_claims().values()
                 if c["predicate"] == "managing_director")
    hypothesis = record_hypothesis(
        store, statement="Kari Nordmann runs Vessia Steel", case_id="vessia",
        analyst_or_provider="analyst", now=pipeline.now_fn(), actor="analyst", marking=MARK)
    assert hypothesis["status"] == "UNRESOLVED"
    hypothesis = link_claim(store, hypothesis["hypothesis_id"], claim["claim_id"],
                            "supporting", rationale="site statement",
                            now=pipeline.now_fn(), actor="analyst", marking=MARK)
    hypothesis = refresh_hypothesis(pipeline.context(), hypothesis["hypothesis_id"])
    assert hypothesis["status"] == "WEAKLY_SUPPORTED"
    _interpret(pipeline, prior, current)  # the correction arrives
    hypothesis = refresh_hypothesis(pipeline.context(), hypothesis["hypothesis_id"])
    assert hypothesis["status"] == "UNRESOLVED", \
        "corrected support must degrade the hypothesis"
    assert claim["claim_id"] in hypothesis["unresolved_claim_ids"]
    assert len(hypothesis["history"]) >= 4  # every assessment is kept


def _fake_gleif_transport(body: bytes):
    def transport(**kwargs):
        return {"body": body, "status": 200, "final_url": kwargs["url"],
                "headers": {"content-type": "application/vnd.api+json"},
                "redirects": (), "error": None, "truncated": False}
    return transport


def test_collection_loop_closes_with_ranked_routes_and_updates(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    seed = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(seed)
    claim = next(c for c in store.current_claims().values()
                 if c["predicate"] == "registration_status")
    hypothesis = record_hypothesis(
        store, statement="Vessia Steel remains registered", case_id="vessia",
        analyst_or_provider="analyst", now=pipeline.now_fn(), actor="analyst", marking=MARK)
    hypothesis = link_claim(store, hypothesis["hypothesis_id"], claim["claim_id"],
                            "supporting", rationale="GLEIF says ISSUED",
                            now=pipeline.now_fn(), actor="analyst", marking=MARK)
    discriminator = propose_discriminator(
        store, question="Does GLEIF still show the registration as ISSUED?",
        hypothesis_ids=(hypothesis["hypothesis_id"],), claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref="LEI:TESTLEI0000000000001",
        desired_attribute="registration_status", source_family_hints=("gleif",),
        now=pipeline.now_fn(), actor="analyst", marking=MARK)
    opened = requirement_for_discriminator(
        store, discriminator, mission_context="vessia",
        now=pipeline.now_fn(), actor="analyst", marking=MARK)
    discriminator = opened["discriminator"]
    assert opened["requirement"]["requirement_id"]
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, discriminator,
                                    requirement_id=discriminator["requirement_id"],
                                    now=pipeline.now_fn(), actor="planner", marking=MARK)
    assert len(routes) >= 2, "at least two plausible routes must be ranked"
    for route in routes:
        assert route["explanation"] and route["factors"]
    gleif_route = next(r for r in routes if r["source_id"] == "gleif")
    assert gleif_route["rank"] == 1, "hinted source family outranks alternatives here"
    # The source has since changed its record, so the search finds LAPSED.
    result = execute_route(pipeline, registry, gleif_route,
                           transports={"gleif-lei-v1": _fake_gleif_transport(GLEIF_RECORD_LAPSED)})
    assert result["execution_outcome"] == "EXECUTED_WITH_RESULTS"
    assert result["manifestations"], "acquired evidence must enter custody"
    assert result["discriminator_status"] == "SATISFIED"
    updated = store.current_claims()[claim["claim_id"]]
    assert updated["object_or_value"] == "LAPSED", "the world model must update"
    assert updated["version"] == 2
    hypothesis = store.current_hypotheses()[hypothesis["hypothesis_id"]]
    assert hypothesis["hypothesis_id"] in result["hypotheses_refreshed"]
    route_state = store.latest_by_id("collection_route", "route_id")[gleif_route["route_id"]]
    assert route_state["status"] == "EXECUTED"
    assert route_state["execution_id"]
    # The requirement stays open: only a person closes it.
    requirement = next(r for r in store.records_of("information_requirement")
                       if r["requirement_id"] == discriminator["requirement_id"])
    assert requirement["status"] in ("OPEN", "EVIDENCE_PENDING")


def test_dependence_and_coverage_shape_the_ranking(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    seed = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(seed)
    claim = next(c for c in store.current_claims().values()
                 if c["predicate"] == "registration_status")
    discriminator = propose_discriminator(
        store, question="Does an independent source corroborate the registration?",
        claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref="LEI:TESTLEI0000000000001",
        desired_attribute="registration_status",
        independence_required=True,
        now=pipeline.now_fn(), actor="analyst", marking=MARK)
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, discriminator,
                                    requirement_id="req-x",
                                    now=pipeline.now_fn(), actor="planner", marking=MARK)
    gleif_route = next(r for r in routes if r["source_id"] == "gleif")
    assert gleif_route["score"] == 0.0, \
        "under independence_required, the same origin family scores zero"
    assert "corroborates nothing independently" in gleif_route["explanation"]
    independents = [r for r in routes if r["source_id"] != "gleif" and r["score"] > 0]
    assert independents, "independent families must remain rankable"


def test_human_required_route_becomes_task_never_fake_completed(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    from curunir_semantic.contracts import CollectionRoute
    route = CollectionRoute(
        route_id="route-h1", requirement_id="req-1", discriminator_id="disc-1",
        source_id="restricted-registry", operation="LOOKUP", query_value="X",
        automatable=False, human_reason="credentialed access requires accountable judgment",
        factors=(("access", 0.0),), score=0.0, rank=9,
        explanation="access requires human authority", status="HUMAN_REQUIRED",
        execution_id="", task_id="", recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("COLLECTION_ROUTE_RECORDED", route, recorded_time=pipeline.now_fn(), actor="t")
    with pytest.raises(ValueError, match="human-required"):
        execute_route(pipeline, load_registry(store), route.to_record())
    task = assign_human_route(store, route.to_record(), assigned_actor="analyst-jan",
                              now=pipeline.now_fn(), actor="t", marking=MARK)
    assert task["status"] == "ASSIGNED"
    stored_route = store.latest_by_id("collection_route", "route_id")["route-h1"]
    assert stored_route["task_id"] == task["task_id"]
    assert stored_route["status"] == "HUMAN_REQUIRED"


def test_watch_change_flows_to_semantic_alert(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    prior, current = _plant_page_pair(pipeline, PAGE_V1, PAGE_V2_SEMANTIC)
    from curunir_fabric.contracts import ChangeObservation
    fabric_change = ChangeObservation(
        change_id="fc-1", watch_id="w-1", run_id="r-1", change_type="CONTENT_CHANGED",
        detail="content hash changed", prior_ref=prior["manifestation_id"],
        current_ref=current["manifestation_id"],
        evidence_manifestation_ids=(current["manifestation_id"],),
        observed_time=pipeline.now_fn(), marking=MARK)
    store.append("FABRIC_CHANGE_OBSERVED", fabric_change,
                 recorded_time=pipeline.now_fn(), actor="t")
    outcomes = pipeline.process_fabric_changes()
    assert outcomes and outcomes[0]["alerts"]
    alert = next(a for a in store.records_of("alert") if a["rule_id"] == "semantic-change")
    assert "managing_director" in alert["trigger"]
    assert "'Kari Nordmann' → 'Ola Hansen'" in alert["trigger"]
    assert "hash" not in alert["trigger"].split("\n")[0]
    assert pipeline.process_fabric_changes() == []


def test_many_observation_historical_discovery_does_not_crash(tmp_path):
    """More differences than can be listed are summarised, not raised as an error."""
    pipeline = make_pipeline(tmp_path)
    rows = "".join(f"<p>Field {i}: Value {i}.</p>\n" for i in range(80)).encode()
    body = b"<html><head><title>Big</title></head><body>" + rows + b"</body></html>"
    manifestation = plant_manifestation(
        pipeline, source_id="wayback", native_id="20080301120000/https://big.example/",
        body=body, media_type="text/html", retrieval_time="2026-08-17T12:05:00+00:00",
        temporal_status="HISTORICAL", source_time="2008-03-01T12:00:00+00:00",
        archive_capture_time="2008-03-01T12:00:00+00:00")
    pipeline.process_manifestation(manifestation)
    outcomes = pipeline.interpret_historical_discoveries()
    assert outcomes, "interpretation must complete"
    classes = [c for outcome in outcomes for c in outcome["semantic_changes"]]
    assert "HISTORICAL_STATE_DISCOVERED" in classes
    assert "UNRESOLVED_CHANGE" in classes  # what did not fit is still recorded
    unresolved = [c for c in pipeline.store.records_of("semantic_change")
                  if c["change_class"] == "UNRESOLVED_CHANGE"]
    assert unresolved and "not" in unresolved[0]["detail"]


def test_machine_never_reverts_human_retraction(tmp_path):
    """New evidence never undoes a person's decision, though it may clear a
    state the machine set itself."""
    from curunir_semantic.contracts import ClaimStateRecord
    from semantic_support import GLEIF_RECORD_V2
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    prior = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(prior)
    claim = next(c for c in store.current_claims().values() if c["predicate"] == "legal_name")
    retraction = ClaimStateRecord(
        state_id="st-human-1", claim_id=claim["claim_id"], state="RETRACTED",
        reason="analyst retracted pending investigation", caused_by="analyst-note",
        superseded_by="", actor_id="analyst-jan", actor_kind="HUMAN",
        recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", retraction,
                 recorded_time=retraction.recorded_time, actor="analyst-jan")
    current = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V2, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T13:05:00+00:00")
    pipeline.process_manifestation(current)
    assert store.claim_state(claim["claim_id"]) == "RETRACTED", \
        "a SERVICE actor must not revert a human adjudication"
    standing_items = [r for r in store.open_review_items()
                      if r["subject_id"] == claim["claim_id"]
                      and "unadjudicated" in r["detail"]]
    assert standing_items, "the tension must be queued for review"
    # A state the machine set is a different matter: mark STALE and move on.
    stale = ClaimStateRecord(
        state_id="st-mach-1", claim_id=claim["claim_id"], state="STALE",
        reason="basis changed", caused_by="x", superseded_by="",
        actor_id="svc", actor_kind="SERVICE",
        recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", stale,
                 recorded_time=stale.recorded_time, actor="svc")
    newer = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V2.replace(b"Vessia Materials AS", b"Vessia Advanced AS"),
        media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T14:05:00+00:00")
    pipeline.process_manifestation(newer)
    assert store.claim_state(claim["claim_id"]) == "CURRENT"


def test_historical_document_reintegration_appends_nothing(tmp_path):
    """Re-reading an old document adds nothing, however often it is processed."""
    from semantic_support import GLEIF_RECORD_V2
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    live = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V2, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(live)
    historical = plant_manifestation(
        pipeline, source_id="wayback",
        native_id="20240101000000/https://gleif-mirror.example/TESTLEI0000000000001",
        body=GLEIF_RECORD_V1.replace(b'"lei"', b'"lei"'), media_type="application/json",
        retrieval_time="2026-08-17T13:05:00+00:00", temporal_status="HISTORICAL",
        source_time="2024-01-01T00:00:00+00:00",
        archive_capture_time="2024-01-01T00:00:00+00:00")
    pipeline.process_manifestation(historical)
    count_after_first = len(store.records_of("object_version"))
    for _ in range(3):
        pipeline.process_manifestation(historical)
        pipeline.process_manifestation(live)
    assert len(store.records_of("object_version")) == count_after_first, \
        "re-integration must append no object versions"
