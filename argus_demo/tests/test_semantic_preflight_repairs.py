"""Regression locks for the semantic-plane preflight findings (C1/C2/C3):
last-append-wins concurrency, multi-append crash recovery, and
idempotency-by-existence discarding caller state."""
from __future__ import annotations

import pytest

from curunir_operational.store import StoreError
from curunir_semantic.changes import interpret_change
from curunir_semantic.collection import (execute_route, plan_collection_routes,
                                         requirement_for_discriminator)
from curunir_semantic.contracts import ClaimStateRecord, ReviewItem
from curunir_semantic.hypotheses import (link_claim, propose_discriminator,
                                         record_hypothesis, update_discriminator)
from curunir_semantic.store import SemanticStore
from curunir_semantic.pipeline import SemanticPipeline
from curunir_fabric.registry import load_registry

from semantic_support import (GLEIF_RECORD_V1, MARK, T0, clock, make_pipeline,
                              plant_manifestation)

pytestmark = pytest.mark.no_db

GLEIF_RECORD_LAPSED = GLEIF_RECORD_V1.replace(b'"ISSUED"', b'"LAPSED"')

WIKIDATA_SAME_LEI = b"""{
  "entities": {"Q555": {"id": "Q555",
    "labels": {"en": {"value": "Vessia Steel"}},
    "claims": {"P1278": [{"mainsnak": {"datavalue":
      {"value": "TESTLEI0000000000001"}}}]}}}
}"""


def fake_gleif_transport(body: bytes):
    def transport(**kwargs):
        return {"body": body, "status": 200, "final_url": kwargs["url"],
                "headers": {"content-type": "application/vnd.api+json"},
                "redirects": (), "error": None, "truncated": False}
    return transport


def _second_writer(tmp_path, start_minute=30):
    """A second SemanticStore instance on the same root — a concurrent
    writer with its own in-memory replay."""
    store = SemanticStore(tmp_path / "store")
    return SemanticPipeline(store=store, custody_root=tmp_path / "custody",
                            actor="w2", marking=MARK, now_fn=clock(start_minute))


def _seed_claim(pipeline):
    seed = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(seed)
    return next(c for c in pipeline.store.current_claims().values()
                if c["predicate"] == "registration_status")


# ---- CLASS 1: stale writers raise instead of shadowing --------------------


def test_c1_1_concurrent_hypothesis_link_cannot_clobber(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    hypothesis = record_hypothesis(
        pipeline.store, statement="registered", case_id="c",
        analyst_or_provider="a", now=pipeline.now_fn(), actor="a", marking=MARK)
    writer_2 = _second_writer(tmp_path)
    # both writers hold the same snapshot; A links first
    link_claim(pipeline.store, hypothesis["hypothesis_id"], claim["claim_id"],
               "supporting", rationale="A", now=pipeline.now_fn(), actor="a",
               marking=MARK)
    # B's stale re-append must raise, not silently erase A's link + history
    with pytest.raises(StoreError, match="next version"):
        link_claim(writer_2.store, hypothesis["hypothesis_id"], claim["claim_id"],
                   "contradicting", rationale="B", now=writer_2.now_fn(),
                   actor="b", marking=MARK)
    current = pipeline.store.current_hypotheses()[hypothesis["hypothesis_id"]]
    assert claim["claim_id"] in current["supporting_claim_ids"]


def test_c1_2_c1_3_satisfied_discriminator_never_regresses(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    discriminator = propose_discriminator(
        store, question="q?", claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status",
        now=pipeline.now_fn(), actor="a", marking=MARK)
    stale_snapshot = dict(discriminator)  # caller keeps the OPEN mapping
    writer_2 = _second_writer(tmp_path, start_minute=40)  # replayed BEFORE update
    satisfied = update_discriminator(store, discriminator, {"status": "SATISFIED"},
                                     now=pipeline.now_fn(), actor="a", marking=MARK)
    assert satisfied["status"] == "SATISFIED"
    # C1-3: a stale in-process snapshot must not regress SATISFIED
    outcome = requirement_for_discriminator(
        store, stale_snapshot, mission_context="m",
        now=pipeline.now_fn(), actor="a", marking=MARK)
    assert outcome["discriminator"]["status"] == "SATISFIED"
    # C1-2: a concurrent stale writer raises instead of shadowing
    with pytest.raises(StoreError, match="next version"):
        update_discriminator(writer_2.store, stale_snapshot,
                             {"status": "REQUESTED"},
                             now=writer_2.now_fn(), actor="b", marking=MARK)


def test_c1_4_resolved_review_item_cannot_be_reopened_by_stale_writer(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    item = ReviewItem(item_id="item-1", kind="CONTRADICTED",
                      subject_kind="semantic_claim", subject_id="c1",
                      detail="d", evidence_refs=(), status="OPEN",
                      resolution_note="", recorded_time=pipeline.now_fn(),
                      marking=MARK)
    store.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                 actor="svc")
    writer_2 = _second_writer(tmp_path, start_minute=40)  # holds OPEN snapshot
    resolved = ReviewItem(item_id="item-1", kind="CONTRADICTED",
                          subject_kind="semantic_claim", subject_id="c1",
                          detail="d", evidence_refs=(), status="RESOLVED",
                          resolution_note="human reviewed", version=2,
                          recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("REVIEW_ITEM_RECORDED", resolved,
                 recorded_time=resolved.recorded_time, actor="jan")
    reopen = ReviewItem(item_id="item-1", kind="CONTRADICTED",
                        subject_kind="semantic_claim", subject_id="c1",
                        detail="d", evidence_refs=(), status="OPEN",
                        resolution_note="", version=2,
                        recorded_time=writer_2.now_fn(), marking=MARK)
    with pytest.raises(StoreError, match="next version"):
        writer_2.store.append("REVIEW_ITEM_RECORDED", reopen,
                              recorded_time=reopen.recorded_time, actor="svc")
    latest = store.latest_by_id("review_item", "item_id")["item-1"]
    assert latest["status"] == "RESOLVED"
    assert latest["resolution_note"] == "human reviewed"


def test_c1_5_route_task_binding_cannot_be_lost(tmp_path):
    from curunir_semantic.collection import assign_human_route
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    discriminator = propose_discriminator(
        store, question="hq?", claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status",
        now=pipeline.now_fn(), actor="a", marking=MARK)
    opened = requirement_for_discriminator(store, discriminator, mission_context="m",
                                           now=pipeline.now_fn(), actor="a",
                                           marking=MARK)
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, opened["discriminator"],
                                    requirement_id=opened["discriminator"]["requirement_id"],
                                    now=pipeline.now_fn(), actor="p", marking=MARK)
    route = routes[0]
    writer_2 = _second_writer(tmp_path, start_minute=45)  # stale route snapshot
    assign_human_route(store, route, assigned_actor="analyst-1",
                       now=pipeline.now_fn(), actor="a", marking=MARK)
    # the stale writer's re-append of the pre-assignment route raises
    from curunir_semantic.contracts import CollectionRoute
    stale = CollectionRoute(**{
        **{k: v for k, v in route.items() if k != "record_type"},
        "factors": tuple(tuple(f) for f in route["factors"]),
        "version": 2, "recorded_time": writer_2.now_fn(), "marking": MARK})
    with pytest.raises(StoreError, match="next version"):
        writer_2.store.append("COLLECTION_ROUTE_RECORDED", stale,
                              recorded_time=stale.recorded_time, actor="w2")
    assert store.latest_by_id("collection_route", "route_id")[
        route["route_id"]]["task_id"]


# ---- CLASS 2: interrupted flows complete on re-run ------------------------


def _crash_once_on(store, event_type, predicate=None):
    real_append = store.append
    state = {"crashed": False}

    def crashing(etype, record, **kwargs):
        data = record if isinstance(record, dict) else record.to_record()
        if not state["crashed"] and etype == event_type \
                and (predicate is None or predicate(data)):
            state["crashed"] = True
            raise OSError("injected crash")
        return real_append(etype, record, **kwargs)

    store.append = crashing
    return lambda: setattr(store, "append", real_append)


def test_c2_1_interrupted_propagation_completes(tmp_path):
    from semantic_support import PAGE_CORRECTION, PAGE_V1
    pipeline = make_pipeline(tmp_path)
    prior = plant_manifestation(pipeline, source_id="live-web",
                                native_id="https://vessia.example/about",
                                body=PAGE_V1, media_type="text/html",
                                retrieval_time="2026-08-17T12:05:00+00:00")
    current = plant_manifestation(pipeline, source_id="live-web",
                                  native_id="https://vessia.example/about",
                                  body=PAGE_CORRECTION, media_type="text/html",
                                  retrieval_time="2026-08-17T12:30:00+00:00",
                                  prior_manifestation_id=prior["manifestation_id"])
    pipeline.process_manifestation(prior)
    pipeline.process_manifestation(current)
    from curunir_semantic.normalize import load_text
    document = next(d for d in pipeline.store.records_of("semantic_document")
                    if d["manifestation_id"] == current["manifestation_id"])
    text = load_text(pipeline.store, document)
    restore = _crash_once_on(pipeline.store, "SEMANTIC_CLAIM_STATE_RECORDED")
    with pytest.raises(OSError):
        interpret_change(pipeline.context(), prior["manifestation_id"],
                         current["manifestation_id"], current_text=text)
    restore()
    # change records exist, the tail was lost; the identical re-run completes
    interpret_change(pipeline.context(), prior["manifestation_id"],
                     current["manifestation_id"], current_text=text)
    corrected = [claim_id for claim_id in pipeline.store.current_claims()
                 if pipeline.store.claim_state(claim_id) == "CORRECTED"]
    assert corrected, "the source correction must reach claim lifecycle"
    kinds = {r["kind"] for r in pipeline.store.open_review_items()}
    assert "SOURCE_CORRECTED" in kinds


def test_c2_2_interrupted_route_accounting_recovers_without_reacquisition(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    hypothesis = record_hypothesis(store, statement="registered", case_id="c",
                                   analyst_or_provider="a", now=pipeline.now_fn(),
                                   actor="a", marking=MARK)
    link_claim(store, hypothesis["hypothesis_id"], claim["claim_id"], "supporting",
               rationale="gleif", now=pipeline.now_fn(), actor="a", marking=MARK)
    discriminator = propose_discriminator(
        store, question="still ISSUED?", hypothesis_ids=(hypothesis["hypothesis_id"],),
        claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status", source_family_hints=("gleif",),
        now=pipeline.now_fn(), actor="a", marking=MARK)
    opened = requirement_for_discriminator(store, discriminator, mission_context="m",
                                           now=pipeline.now_fn(), actor="a",
                                           marking=MARK)
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, opened["discriminator"],
                                    requirement_id=opened["discriminator"]["requirement_id"],
                                    now=pipeline.now_fn(), actor="p", marking=MARK)
    gleif_route = next(r for r in routes if r["source_id"] == "gleif")
    transports = {"gleif-lei-v1": fake_gleif_transport(GLEIF_RECORD_LAPSED)}
    restore = _crash_once_on(store, "DISCRIMINATOR_RECORDED",
                             lambda d: d.get("status") == "SATISFIED")
    with pytest.raises(OSError):
        execute_route(pipeline, registry, gleif_route, transports=transports)
    restore()
    # the gap is durable state, not silence
    failures = [r for r in store.open_review_items()
                if r["kind"] == "PROCESSING_FAILED"
                and r["subject_kind"] == "collection_route"]
    assert failures, "an interrupted accounting tail must be durably recorded"
    executions_before = len(store.records_of("fabric_execution"))
    result = execute_route(pipeline, registry, gleif_route, transports=transports)
    assert len(store.records_of("fabric_execution")) == executions_before, \
        "recovery completes the accounting; it must not re-acquire"
    assert result["discriminator_status"] == "SATISFIED"
    assert hypothesis["hypothesis_id"] in result["hypotheses_refreshed"]
    failure = store.latest_by_id("review_item", "item_id")[failures[0]["item_id"]]
    assert failure["status"] == "RESOLVED"


def test_c2_3_claim_standing_review_survives_crash(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    # an analyst marked the claim DISPUTED; then the source updates it
    disputed = ClaimStateRecord(
        state_id="st-d", claim_id=claim["claim_id"], state="DISPUTED",
        reason="analyst dispute", caused_by="a", superseded_by="",
        actor_id="jan", actor_kind="HUMAN", recorded_time=pipeline.now_fn(),
        marking=MARK)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", disputed,
                 recorded_time=disputed.recorded_time, actor="jan")
    restore = _crash_once_on(store, "REVIEW_ITEM_RECORDED",
                             lambda d: d.get("kind") == "MANIFESTATION_CHANGED")
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_LAPSED,
                        media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    outcome = pipeline.process_new_evidence()
    restore()
    assert outcome["failed"], "the crash must land as a durable failure"
    retried = pipeline.process_new_evidence()
    assert not retried["failed"]
    items = [r for r in store.open_review_items()
             if r["kind"] == "MANIFESTATION_CHANGED"
             and r["subject_id"] == claim["claim_id"]]
    assert items, ("the retry must complete the unadjudicated-standing review "
                   "item, not report PROCESSED over the gap")


def test_c2_4_identity_ambiguity_queueing_completes(tmp_path):
    pipeline = make_pipeline(tmp_path)
    _seed_claim(pipeline)
    restore = _crash_once_on(pipeline.store, "REVIEW_ITEM_RECORDED",
                             lambda d: d.get("kind") == "IDENTITY_AMBIGUITY")
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q555",
                        body=WIKIDATA_SAME_LEI, media_type="application/json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    try:
        pipeline.process_new_evidence()
    except OSError:
        pass
    restore()
    pipeline.process_new_evidence()
    proposals = pipeline.store.records_of("association_proposal")
    ambiguities = [r for r in pipeline.store.open_review_items()
                   if r["kind"] == "IDENTITY_AMBIGUITY"]
    assert proposals and ambiguities, \
        "every recorded equivalence proposal must be queued for a human"


# ---- CLASS 3: exists paths fold, never silently discard --------------------


def test_c3_1_independence_requirement_cannot_be_downgraded_by_cache(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    weak = propose_discriminator(
        store, question="corroborated?", claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status", independence_required=False,
        now=pipeline.now_fn(), actor="a", marking=MARK)
    assert weak["independence_required"] is False
    hypothesis = record_hypothesis(store, statement="registered", case_id="c",
                                   analyst_or_provider="a", now=pipeline.now_fn(),
                                   actor="a", marking=MARK)
    strong = propose_discriminator(
        store, question="corroborated?", claim_ids=(claim["claim_id"],),
        hypothesis_ids=(hypothesis["hypothesis_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status", independence_required=True,
        now=pipeline.now_fn(), actor="a", marking=MARK)
    assert strong["independence_required"] is True, \
        "an independence requirement is raised, never cached away"
    assert strong["basis_groups_at_pose"], "the pose snapshot is taken on raise"
    linked = store.current_hypotheses()[hypothesis["hypothesis_id"]]
    assert strong["discriminator_id"] in linked["discriminator_ids"], \
        "the new hypothesis link is folded, not discarded"
    # and the epistemic consequence holds: the same-family route scores zero
    opened = requirement_for_discriminator(store, strong, mission_context="m",
                                           now=pipeline.now_fn(), actor="a",
                                           marking=MARK)
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, opened["discriminator"],
                                    requirement_id=opened["discriminator"]["requirement_id"],
                                    now=pipeline.now_fn(), actor="p", marking=MARK)
    gleif_route = next(r for r in routes if r["source_id"] == "gleif")
    assert gleif_route["score"] == 0.0, \
        "same-family evidence cannot outrank the independence requirement"
    assert opened["requirement"]["priority"] == "HIGH"


def test_c3_2_requirement_escalation_is_folded(tmp_path):
    from curunir_operational.missions import MissionWorkflow
    pipeline = make_pipeline(tmp_path)
    workflow = MissionWorkflow(pipeline.store)
    first = workflow.open_requirement(
        mission_context="m", question="q?", affected_ids=("a",),
        priority="MEDIUM", rationale="r", required_evidence_type="E",
        owning_role="ANALYST", closure_criteria="c", due_time=None,
        recorded_time=pipeline.now_fn(), marking=MARK, actor="x")
    escalated = workflow.open_requirement(
        mission_context="m", question="q?", affected_ids=("a", "b", "c"),
        priority="HIGH", rationale="r", required_evidence_type="E",
        owning_role="ANALYST", closure_criteria="c", due_time=None,
        recorded_time=pipeline.now_fn(), marking=MARK, actor="x")
    assert escalated["priority"] == "HIGH"
    assert set(escalated["affected_ids"]) == {"a", "b", "c"}
    # and never de-escalates
    calmed = workflow.open_requirement(
        mission_context="m", question="q?", affected_ids=("a",),
        priority="LOW", rationale="r", required_evidence_type="E",
        owning_role="ANALYST", closure_criteria="c", due_time=None,
        recorded_time=pipeline.now_fn(), marking=MARK, actor="x")
    assert calmed["priority"] == "HIGH"
    del first


def test_c3_3_hypothesis_assumptions_are_folded(tmp_path):
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    record_hypothesis(store, statement="s", case_id="c",
                      assumptions=("a1",), analyst_or_provider="p1",
                      now=pipeline.now_fn(), actor="x", marking=MARK)
    merged = record_hypothesis(store, statement="s", case_id="c",
                               assumptions=("a1", "a2"), unknowns=("u1",),
                               analyst_or_provider="p2",
                               now=pipeline.now_fn(), actor="x", marking=MARK)
    assert set(merged["assumptions"]) == {"a1", "a2"}
    assert merged["unknowns"] == ["u1"]


# ---- verification-round findings (R1–R8) ----------------------------------


def test_r1_analytic_store_protects_semantic_families(tmp_path):
    from curunir_analytic.store import AnalyticStore
    for family in ("hypothesis", "discriminator", "collection_route",
                   "review_item", "semantic_claim", "information_requirement"):
        assert family in AnalyticStore.VERSIONED_RECORD_TYPES, \
            "the analytic store must EXTEND, never replace, the semantic map"
    root = tmp_path / "store"
    AnalyticStore.create(root, "t", T0)
    store_a = AnalyticStore(root)
    store_b = AnalyticStore(root)  # concurrent writer
    item = ReviewItem(item_id="i1", kind="CONTRADICTED", subject_kind="semantic_claim",
                      subject_id="c", detail="d", evidence_refs=(), status="OPEN",
                      resolution_note="", recorded_time="2026-08-17T12:01:00+00:00",
                      marking=MARK)
    store_a.append("REVIEW_ITEM_RECORDED", item, recorded_time=item.recorded_time,
                   actor="svc")
    resolved = ReviewItem(item_id="i1", kind="CONTRADICTED",
                          subject_kind="semantic_claim", subject_id="c", detail="d",
                          evidence_refs=(), status="RESOLVED",
                          resolution_note="human", version=2,
                          recorded_time="2026-08-17T12:02:00+00:00", marking=MARK)
    store_a.append("REVIEW_ITEM_RECORDED", resolved,
                   recorded_time=resolved.recorded_time, actor="jan")
    reopen = ReviewItem(item_id="i1", kind="CONTRADICTED",
                        subject_kind="semantic_claim", subject_id="c", detail="d",
                        evidence_refs=(), status="OPEN", resolution_note="",
                        version=2, recorded_time="2026-08-17T12:03:00+00:00",
                        marking=MARK)
    with pytest.raises(StoreError, match="next version"):
        store_b.append("REVIEW_ITEM_RECORDED", reopen,
                       recorded_time=reopen.recorded_time, actor="svc")


def test_r2_completion_passes_do_not_grow_the_chain(tmp_path):
    from semantic_support import PAGE_CORRECTION, PAGE_V1
    pipeline = make_pipeline(tmp_path)
    prior = plant_manifestation(pipeline, source_id="live-web",
                                native_id="https://vessia.example/about",
                                body=PAGE_V1, media_type="text/html",
                                retrieval_time="2026-08-17T12:05:00+00:00")
    current = plant_manifestation(pipeline, source_id="live-web",
                                  native_id="https://vessia.example/about",
                                  body=PAGE_CORRECTION, media_type="text/html",
                                  retrieval_time="2026-08-17T12:30:00+00:00",
                                  prior_manifestation_id=prior["manifestation_id"])
    from curunir_fabric.contracts import ChangeObservation
    change = ChangeObservation(
        change_id="fc-1", watch_id="w-1", run_id="run-1",
        change_type="CONTENT_CHANGED", detail="content changed",
        prior_ref=prior["manifestation_id"], current_ref=current["manifestation_id"],
        evidence_manifestation_ids=(current["manifestation_id"],),
        observed_time=pipeline.now_fn(), marking=MARK)
    pipeline.store.append("FABRIC_CHANGE_OBSERVED", change,
                          recorded_time=pipeline.now_fn(), actor="w")
    pipeline.process_fabric_changes()
    settled = pipeline.store.head()["event_count"]
    for _ in range(3):
        pipeline.process_fabric_changes()
    assert pipeline.store.head()["event_count"] == settled, \
        "no-op completion passes must append nothing to the append-only chain"


def test_r3_completion_never_restamps_human_adjudication(tmp_path):
    from semantic_support import PAGE_V1, PAGE_V2_SEMANTIC
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    prior = plant_manifestation(pipeline, source_id="live-web",
                                native_id="https://vessia.example/about",
                                body=PAGE_V1, media_type="text/html",
                                retrieval_time="2026-08-17T12:05:00+00:00")
    current = plant_manifestation(pipeline, source_id="live-web",
                                  native_id="https://vessia.example/about",
                                  body=PAGE_V2_SEMANTIC, media_type="text/html",
                                  retrieval_time="2026-08-17T12:30:00+00:00",
                                  prior_manifestation_id=prior["manifestation_id"])
    pipeline.process_manifestation(prior)
    pipeline.process_manifestation(current)
    interpret_change(pipeline.context(), prior["manifestation_id"],
                     current["manifestation_id"])
    changed_claim = next(
        (claim_id for claim_id in store.current_claims()
         if store.claim_state(claim_id) == "STALE"), None)
    if changed_claim is None:  # value advanced; pick the affected claim
        change = next(c for c in store.records_of("semantic_change")
                      if c["affected_claim_ids"])
        changed_claim = change["affected_claim_ids"][0]
    disputed = ClaimStateRecord(
        state_id="st-h", claim_id=changed_claim, state="DISPUTED",
        reason="human dispute", caused_by="analyst", superseded_by="",
        actor_id="jan", actor_kind="HUMAN",
        recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", disputed,
                 recorded_time=disputed.recorded_time, actor="jan")
    # the completion re-scan must not re-stamp the human's judgment
    interpret_change(pipeline.context(), prior["manifestation_id"],
                     current["manifestation_id"])
    assert store.claim_state(changed_claim) == "DISPUTED", \
        "a SERVICE completion pass reverted a HUMAN adjudication"


def test_r4_r5_requirement_fold_preserves_workflow_and_versions(tmp_path):
    from curunir_operational.missions import MissionWorkflow
    from curunir_operational.projection import Projection
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    workflow = MissionWorkflow(store)
    requirement = workflow.open_requirement(
        mission_context="m", question="q?", affected_ids=("a",),
        priority="MEDIUM", rationale="r", required_evidence_type="E",
        owning_role="ANALYST", closure_criteria="c", due_time=None,
        recorded_time=pipeline.now_fn(), marking=MARK, actor="x")
    writer_2 = _second_writer(tmp_path, start_minute=50)  # pre-escalation view
    workflow.request_evidence(requirement["requirement_id"],
                              request_kind="ANALYST_REVIEW", detail="d",
                              affected_ids=(), due_time=None,
                              recorded_time=pipeline.now_fn(), marking=MARK,
                              actor="x")
    escalated = workflow.open_requirement(
        mission_context="m", question="q?", affected_ids=("a", "b"),
        priority="HIGH", rationale="r", required_evidence_type="E",
        owning_role="ANALYST", closure_criteria="c", due_time=None,
        recorded_time=pipeline.now_fn(), marking=MARK, actor="x")
    assert escalated["version"] == 2
    projection = Projection(store)
    entry = projection.requirements[requirement["requirement_id"]]
    assert entry["status"] == "EVIDENCE_PENDING", \
        "the fold must not reset the projected workflow status"
    assert entry["transitions"], "the fold must not wipe the audit trail"
    assert entry["record"]["priority"] == "HIGH"
    # R5: a stale concurrent fold raises instead of de-escalating
    with pytest.raises(StoreError, match="next version"):
        MissionWorkflow(writer_2.store).open_requirement(
            mission_context="m", question="q?", affected_ids=("z",),
            priority="CRITICAL", rationale="r", required_evidence_type="E",
            owning_role="ANALYST", closure_criteria="c", due_time=None,
            recorded_time=writer_2.now_fn(), marking=MARK, actor="y")


def test_r6_noop_reintegration_leaves_standing_untouched(tmp_path):
    from curunir_semantic.worldmodel import IntegrationContext, integrate_all
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    claim = _seed_claim(pipeline)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_LAPSED,
                        media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()
    assert store.current_claims()[claim["claim_id"]]["version"] == 2
    # a HUMAN retracts AFTER the advance
    retracted = ClaimStateRecord(
        state_id="st-r", claim_id=claim["claim_id"], state="RETRACTED",
        reason="human retraction", caused_by="analyst", superseded_by="",
        actor_id="jan", actor_kind="HUMAN",
        recorded_time=pipeline.now_fn(), marking=MARK)
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", retracted,
                 recorded_time=retracted.recorded_time, actor="jan")
    items_before = len(store.records_of("review_item"))
    states_before = len(store.records_of("semantic_claim_state"))
    integration = IntegrationContext(store=store, actor="t", marking=MARK,
                                     now_fn=pipeline.now_fn)
    integrate_all(integration)  # pure no-op re-entry over all evidence
    assert store.claim_state(claim["claim_id"]) == "RETRACTED"
    assert len(store.records_of("semantic_claim_state")) == states_before
    assert len(store.records_of("review_item")) == items_before, \
        "a no-op re-entry must not open spurious standing items"


def test_r7_stale_route_mapping_cannot_resurrect_old_state(tmp_path):
    from curunir_semantic.collection import assign_human_route
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    store = pipeline.store
    discriminator = propose_discriminator(
        store, question="still ISSUED?", claim_ids=(claim["claim_id"],),
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=claim["subject_ref"],
        desired_attribute="registration_status", source_family_hints=("gleif",),
        now=pipeline.now_fn(), actor="a", marking=MARK)
    opened = requirement_for_discriminator(store, discriminator, mission_context="m",
                                           now=pipeline.now_fn(), actor="a",
                                           marking=MARK)
    registry = load_registry(store)
    routes = plan_collection_routes(store, registry, opened["discriminator"],
                                    requirement_id=opened["discriminator"]["requirement_id"],
                                    now=pipeline.now_fn(), actor="p", marking=MARK)
    gleif_route = next(r for r in routes if r["source_id"] == "gleif")
    stale_mapping = dict(gleif_route)  # caller keeps pre-assignment state
    assign_human_route(store, gleif_route, assigned_actor="analyst-1",
                       now=pipeline.now_fn(), actor="a", marking=MARK)
    result = execute_route(pipeline, registry, stale_mapping,
                           transports={"gleif-lei-v1":
                                       fake_gleif_transport(GLEIF_RECORD_LAPSED)})
    final = store.latest_by_id("collection_route", "route_id")[gleif_route["route_id"]]
    assert final["task_id"], \
        "execution from a stale mapping must not erase the task binding"
    assert result["execution_outcome"] == "EXECUTED_WITH_RESULTS"


def test_r8_stale_claim_version_raises(tmp_path):
    pipeline = make_pipeline(tmp_path)
    claim = _seed_claim(pipeline)
    writer_2 = _second_writer(tmp_path, start_minute=55)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_LAPSED,
                        media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()  # claim advances to v2
    from curunir_semantic.contracts import SemanticClaim
    shadow = SemanticClaim(**{
        **{k: v for k, v in claim.items() if k != "record_type"},
        "version": 2, "object_or_value": "B-SHADOW",
        "observation_ids": tuple(claim["observation_ids"]),
        "dependence_group_ids": tuple(claim["dependence_group_ids"]),
        "world_refs": tuple(claim["world_refs"]),
        "recorded_time": writer_2.now_fn(), "marking": MARK})
    with pytest.raises(StoreError, match="next version"):
        writer_2.store.append("SEMANTIC_CLAIM_RECORDED", shadow,
                              recorded_time=shadow.recorded_time, actor="w2")


def test_n1_superseded_change_never_restales_a_current_claim(tmp_path):
    """Second-verification N1: completion must be gap-detection — an old
    change's staleness verdict cannot be re-stamped onto a claim that later
    evidence has advanced past it."""
    from semantic_support import PAGE_V1, PAGE_V2_SEMANTIC
    from curunir_fabric.contracts import ChangeObservation
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    page_v3 = PAGE_V1.replace(b"Kari Nordmann", b"Per Berg")
    v1 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=PAGE_V1, media_type="text/html",
                             retrieval_time="2026-08-17T12:05:00+00:00")
    v2 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=PAGE_V2_SEMANTIC, media_type="text/html",
                             retrieval_time="2026-08-17T12:20:00+00:00",
                             prior_manifestation_id=v1["manifestation_id"])
    v3 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=page_v3, media_type="text/html",
                             retrieval_time="2026-08-17T12:40:00+00:00",
                             prior_manifestation_id=v2["manifestation_id"])
    for index, (prior, current) in enumerate(((v1, v2), (v2, v3)), start=1):
        change = ChangeObservation(
            change_id=f"fc-{index}", watch_id="w-1", run_id=f"run-{index}",
            change_type="CONTENT_CHANGED", detail="content changed",
            prior_ref=prior["manifestation_id"],
            current_ref=current["manifestation_id"],
            evidence_manifestation_ids=(current["manifestation_id"],),
            observed_time=pipeline.now_fn(), marking=MARK)
        store.append("FABRIC_CHANGE_OBSERVED", change,
                     recorded_time=pipeline.now_fn(), actor="w")
    pipeline.process_fabric_changes()
    claim_id = next(c["claim_id"] for c in store.current_claims().values()
                    if c["object_or_value"] == "Per Berg")
    settled_state = store.claim_state(claim_id)
    settled_events = store.head()["event_count"]
    for _ in range(3):
        pipeline.process_fabric_changes()
    assert store.claim_state(claim_id) == settled_state, \
        "a superseded change re-staled a claim whose value is current"
    assert store.head()["event_count"] == settled_events, \
        "no-op polls over a multi-change history must append nothing"


def test_n3_n4_standing_time_gate_is_offset_safe(tmp_path):
    """Final-round N6 lock: the standing-vs-advance gate compares parsed
    times, so a non-UTC offset can neither let a post-advance standing be
    machine-reset nor block a legitimate pre-advance repair."""
    from curunir_semantic.worldmodel import IntegrationContext, integrate_all
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    claim = _seed_claim(pipeline)
    plant_manifestation(pipeline, source_id="gleif",
                        native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_LAPSED,
                        media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T13:00:00+00:00")
    pipeline.process_new_evidence()  # claim advances to v2
    advance_time = next(c for c in reversed(store.records_of("semantic_claim"))
                        if c["claim_id"] == claim["claim_id"])["recorded_time"]
    # a standing recorded AFTER the advance, written with a non-UTC offset
    # that lexically sorts BEFORE the advance's UTC timestamp
    from curunir_operational.canonical import parse_time
    from datetime import timedelta, timezone
    later = (parse_time(advance_time) + timedelta(minutes=30)).astimezone(
        timezone(timedelta(hours=-5)))
    standing = ClaimStateRecord(
        state_id="st-o", claim_id=claim["claim_id"], state="SUPERSEDED",
        reason="post-advance machine bookkeeping", caused_by="x",
        superseded_by="", actor_id="svc", actor_kind="SERVICE",
        recorded_time=later.isoformat(), marking=MARK)
    assert standing.recorded_time < advance_time, \
        "the fixture must exercise the lexical misordering"
    store.append("SEMANTIC_CLAIM_STATE_RECORDED", standing,
                 recorded_time=pipeline.now_fn(), actor="svc")
    states_before = len(store.records_of("semantic_claim_state"))
    integration = IntegrationContext(store=store, actor="t", marking=MARK,
                                     now_fn=pipeline.now_fn)
    integrate_all(integration)  # no-op re-entry
    assert store.claim_state(claim["claim_id"]) == "SUPERSEDED", \
        "a post-advance standing must never be machine-reset, whatever its offset"
    assert len(store.records_of("semantic_claim_state")) == states_before


def test_n5_first_pass_backlog_cannot_stale_a_current_claim(tmp_path):
    """Final-round N5: even on the FIRST interpretation pass, a change whose
    manifestation is older than the evidence the claim already rests on must
    not stamp staleness — the ordering axis is the evidence, not the log."""
    from semantic_support import PAGE_V1, PAGE_V2_SEMANTIC
    pipeline = make_pipeline(tmp_path)
    store = pipeline.store
    page_v3 = PAGE_V1.replace(b"Kari Nordmann", b"Per Berg")
    v1 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=PAGE_V1, media_type="text/html",
                             retrieval_time="2026-08-17T12:05:00+00:00")
    v2 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=PAGE_V2_SEMANTIC, media_type="text/html",
                             retrieval_time="2026-08-17T12:20:00+00:00",
                             prior_manifestation_id=v1["manifestation_id"])
    v3 = plant_manifestation(pipeline, source_id="live-web",
                             native_id="https://vessia.example/about",
                             body=page_v3, media_type="text/html",
                             retrieval_time="2026-08-17T12:40:00+00:00",
                             prior_manifestation_id=v2["manifestation_id"])
    # integration first: the claim advances to the newest value BEFORE any
    # change is interpreted (the demo driver ordering)
    pipeline.process_new_evidence()
    claim = next(c for c in store.current_claims().values()
                 if c["object_or_value"] == "Per Berg")
    # now the backlog is interpreted oldest-first — the v1→v2 change is the
    # NEWEST record in the log but carries the OLDEST evidence
    interpret_change(pipeline.context(), v1["manifestation_id"],
                     v2["manifestation_id"])
    assert store.claim_state(claim["claim_id"]) == "CURRENT", \
        "a change resting on older evidence staled a claim resting on newer"
    interpret_change(pipeline.context(), v2["manifestation_id"],
                     v3["manifestation_id"])
    assert store.claim_state(claim["claim_id"]) == "CURRENT"
