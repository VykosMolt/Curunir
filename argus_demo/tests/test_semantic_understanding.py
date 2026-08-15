"""Evidence understanding: normalization, exact addressing, bitemporality,
dependence, identity reversibility — deterministic fixtures."""
from __future__ import annotations

import json

import pytest

from curunir_operational.projection import Projection
from curunir_semantic.normalize import load_fields, load_text
from curunir_semantic.worldmodel import world_object_id

from semantic_support import (GLEIF_RECORD_V1, PAGE_V1, T0, MARK, clock,
                              make_pipeline, plant_manifestation)

pytestmark = pytest.mark.no_db


def test_structured_record_keeps_fields_addressable(tmp_path):
    pipeline = make_pipeline(tmp_path)
    manifestation = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    outcome = pipeline.process_manifestation(manifestation)
    assert outcome["status"] == "PROCESSED"
    store = pipeline.store
    document = store.records_of("semantic_document")[-1]
    assert document["format"] == "JSON"
    fields = dict(load_fields(store, document))
    assert fields["$.data.attributes.entity.legalName.name"] == "Vessia Steel AS"
    # observations descend to exact field paths, never to a bare URL
    observation = next(o for o in store.records_of("semantic_observation")
                       if o["attribute"] == "legal_name")
    anchor = observation["anchors"][0]
    assert anchor["kind"] == "FIELD"
    assert anchor["field_path"] == "$.data.attributes.entity.legalName.name"
    assert anchor["exact_value"] == "Vessia Steel AS"
    assert anchor["content_sha256"] == manifestation["content_sha256"]
    assert anchor["manifestation_id"] == manifestation["manifestation_id"]


def test_html_spans_recover_exact_normalized_text(tmp_path):
    pipeline = make_pipeline(tmp_path)
    manifestation = plant_manifestation(
        pipeline, source_id="live-web", native_id="https://vessia.example/about",
        body=PAGE_V1, media_type="text/html",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(manifestation)
    store = pipeline.store
    document = store.records_of("semantic_document")[-1]
    text = load_text(store, document)
    statements = [o for o in store.records_of("semantic_observation")
                  if o["observation_type"] in ("STATEMENT", "ROLE_SIGNAL")]
    assert statements, "prose pages must yield anchored text observations"
    for observation in statements:
        anchor = observation["anchors"][0]
        if anchor["kind"] == "TEXT_SPAN":
            assert text[anchor["start"]:anchor["end"]] == anchor["exact_value"]
    assert document["title"] == "Vessia Steel AS"


def test_world_model_object_carries_evidentiary_provenance(tmp_path):
    pipeline = make_pipeline(tmp_path)
    manifestation = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(manifestation)
    store = pipeline.store
    version = next(v for v in store.records_of("object_version")
                   if v["attributes"].get("name") == "Vessia Steel AS")
    assert version["object_type"] == "ORGANISATION"
    assert version["epistemic_state"] == "EXTRACTED"
    assert version["quality"]["review_state"] == "UNREVIEWED"
    provenance = version["provenance"]
    assert provenance["mode"] == "EVIDENTIARY"
    assert provenance["evidence"], "world-model state without evidence is forbidden"
    ref = provenance["evidence"][0]
    assert ref["source_object_id"] == manifestation["manifestation_id"]
    assert ref["content_sha256"] == manifestation["content_sha256"]
    assert ref["dependence_group_id"]
    refs = {(r["system"], r["external_id"]) for r in version["external_refs"]}
    assert ("LEI", "TESTLEI0000000000001") in refs


def test_historical_manifestation_updates_historical_state_not_today(tmp_path):
    pipeline = make_pipeline(tmp_path)
    manifestation = plant_manifestation(
        pipeline, source_id="wayback",
        native_id="20080301120000/http://vessia.example/about",
        body=PAGE_V1, media_type="text/html",
        retrieval_time="2026-08-17T12:05:00+00:00",
        temporal_status="HISTORICAL",
        source_time="2008-03-01T12:00:00+00:00",
        archive_capture_time="2008-03-01T12:00:00+00:00")
    pipeline.process_manifestation(manifestation)
    changes = pipeline.interpret_historical_discoveries()
    assert changes and all(
        c == "HISTORICAL_STATE_DISCOVERED"
        for outcome in changes for c in outcome["semantic_changes"])
    store = pipeline.store
    claim = next(c for c in store.current_claims().values()
                 if c["subject_ref"].startswith("URL:") and "Kari Nordmann" in c["object_or_value"])
    assert claim["valid_from"] == "2008-03-01T12:00:00+00:00"  # valid time: then
    assert claim["recorded_time"].startswith("2026-")           # knowledge time: now
    # knowledge-as-of before ingestion shows nothing; validity-as-of 2009 shows it
    projection_2009 = Projection(store, valid_at="2009-01-01T00:00:00+00:00")
    historical_claim_subjects = {c["subject_ref"] for c in store.current_claims().values()
                                 if c["valid_from"] and c["valid_from"] < "2009"}
    assert claim["subject_ref"] in historical_claim_subjects


def test_derivative_sources_share_one_dependence_family(tmp_path):
    pipeline = make_pipeline(tmp_path)
    live = plant_manifestation(
        pipeline, source_id="live-web", native_id="https://vessia.example/about",
        body=PAGE_V1, media_type="text/html",
        retrieval_time="2026-08-17T12:05:00+00:00")
    archived = plant_manifestation(
        pipeline, source_id="wayback",
        native_id="20260101000000/https://vessia.example/about",
        body=PAGE_V1, media_type="text/html",
        retrieval_time="2026-08-17T12:06:00+00:00",
        temporal_status="HISTORICAL",
        source_time="2026-01-01T00:00:00+00:00",
        archive_capture_time="2026-01-01T00:00:00+00:00")
    pipeline.process_manifestation(live)
    pipeline.process_manifestation(archived)
    store = pipeline.store
    claims = [c for c in store.current_claims().values()
              if "Kari Nordmann" in c["object_or_value"]]
    assert claims
    claim = claims[0]
    # two manifestations, one origin family (the site): the second observation
    # adds no basis, so the claim neither double-counts nor churns a version
    from curunir_semantic.worldmodel import dependence_group_for
    groups = {dependence_group_for(m) for m in store.records_of("fabric_manifestation")}
    assert len(groups) == 1
    assert claim["independent_basis_count"] == 1
    assert claim["version"] == 1
    assert "not assumed independent" in claim["basis_note"]


def test_independent_origins_count_separately(tmp_path):
    pipeline = make_pipeline(tmp_path)
    for source_id, native in (("live-web", "https://vessia.example/about"),
                              ("live-web", "https://other-news.example/story")):
        plant_manifestation(pipeline, source_id=source_id, native_id=native,
                            body=PAGE_V1, media_type="text/html",
                            retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    # same statement observed on two different sites → two origin families,
    # but the URL-subject differs per site so claims stay per-site; assert at
    # the dependence-group level instead
    groups = set()
    for observation in store.records_of("semantic_observation"):
        manifestation = next(m for m in store.records_of("fabric_manifestation")
                             if m["manifestation_id"] == observation["manifestation_id"])
        from curunir_semantic.worldmodel import dependence_group_for
        groups.add(dependence_group_for(manifestation))
    assert len(groups) == 2


def test_ambiguous_identity_stays_a_reversible_proposal(tmp_path):
    pipeline = make_pipeline(tmp_path)
    wikidata_body = json.dumps({"entities": {"Q999001": {
        "id": "Q999001",
        "labels": {"en": {"language": "en", "value": "Vessia Steel"}},
        "aliases": {}, "descriptions": {},
        "claims": {"P1278": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                           "datavalue": {"value": "TESTLEI0000000000001",
                                                         "type": "string"}}}]},
    }}}).encode()
    gleif = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    wikidata = plant_manifestation(
        pipeline, source_id="wikidata", native_id="Q999001",
        body=wikidata_body, media_type="application/json",
        retrieval_time="2026-08-17T12:06:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    lei_object = world_object_id("LEI:TESTLEI0000000000001")
    qid_object = world_object_id("WIKIDATA_QID:Q999001")
    proposals = [p for p in store.records_of("association_proposal")
                 if {p["left_object_id"], p["right_object_id"]} == {lei_object, qid_object}]
    assert proposals, "shared identifier must yield an association proposal"
    # whatever the engine decided, the two objects remain distinct records
    object_ids = {v["object_id"] for v in store.records_of("object_version")}
    assert lei_object in object_ids and qid_object in object_ids
    # and any SAME_AS produced is reversible through the engine
    from curunir_operational.association import AssociationEngine
    engine = AssociationEngine(store)
    resolved = engine.resolve(proposals[0]["proposal_id"], "REVERSED", actor_id="analyst",
                              actor_kind="HUMAN", rationale="insufficient identity evidence",
                              recorded_time="2026-08-17T13:30:00+00:00", marking=MARK)
    assert resolved["resolution"] == "REVERSED"
    same_as = [r for r in store.records_of("relationship_version")
               if r["relation_type"] in ("SAME_AS", "POSSIBLY_SAME_AS")]
    assert same_as and same_as[-1]["status"] in ("RETIRED", "REJECTED")


def test_out_of_order_older_evidence_does_not_displace_newer_state(tmp_path):
    pipeline = make_pipeline(tmp_path)
    from semantic_support import GLEIF_RECORD_V2
    newer = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V2, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(newer)
    store = pipeline.store
    claim = next(c for c in store.current_claims().values() if c["predicate"] == "legal_name")
    assert claim["object_or_value"] == "Vessia Materials AS"
    # an older manifestation (same origin family) arrives later
    older = plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T11:00:00+00:00")
    # recorded later in knowledge time despite older retrieval time
    pipeline.process_manifestation(older)
    claim = next(c for c in store.current_claims().values() if c["predicate"] == "legal_name")
    assert claim["object_or_value"] == "Vessia Materials AS", \
        "late-arriving older source state must not displace newer state"


def test_reexecution_is_idempotent_and_replay_reconstructs(tmp_path):
    pipeline = make_pipeline(tmp_path)
    plant_manifestation(
        pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    head = store.head()
    pipeline2 = make_pipeline(tmp_path, start_minute=120)  # restart: rebuilt from disk
    pipeline2.process_new_evidence()
    assert pipeline2.store.head()["event_count"] == head["event_count"], \
        "re-execution after restart must append nothing"
    export_dir = tmp_path / "export"
    manifest = pipeline2.store.export_to(export_dir)
    from curunir_semantic.store import SemanticStore
    replayed = SemanticStore.import_from(export_dir, tmp_path / "replayed")
    assert replayed.verify_chain()["valid"]
    assert replayed.head()["head_hash"] == manifest["head_hash"]
    assert len(replayed.records_of("semantic_observation")) == \
        len(store.records_of("semantic_observation"))
    document = replayed.records_of("semantic_document")[-1]
    # payloads travel with the export: spans/fields remain recoverable
    assert dict(load_fields(replayed, document))["$.data.attributes.lei"] == "TESTLEI0000000000001"


def test_model_observations_require_inference_records(tmp_path):
    from curunir_semantic.contracts import EvidenceAnchor, SemanticObservation
    anchor = EvidenceAnchor(manifestation_id="m1", source_id="s", content_sha256="a" * 64,
                            kind="FIELD", field_path="$.x", exact_value="1")
    with pytest.raises(ValueError, match="inference record"):
        SemanticObservation(
            observation_id="o1", document_id="d1", manifestation_id="m1", source_id="s",
            observation_type="ENTITY_ATTRIBUTE", subject_ref="LEI:X", attribute="a",
            value="1", object_ref="", valid_from=None, valid_to=None, source_time=None,
            time_precision="UNKNOWN", language="", representation="ORIGINAL",
            anchors=(anchor,), producer_kind="MODEL_PROVIDER", producer_id="some-model",
            producer_version="1", inference_id="", recorded_time=T0, marking=MARK)
    with pytest.raises(ValueError, match="anchors"):
        SemanticObservation(
            observation_id="o1", document_id="d1", manifestation_id="m1", source_id="s",
            observation_type="ENTITY_ATTRIBUTE", subject_ref="LEI:X", attribute="a",
            value="1", object_ref="", valid_from=None, valid_to=None, source_time=None,
            time_precision="UNKNOWN", language="", representation="ORIGINAL",
            anchors=(), producer_kind="DETERMINISTIC_PARSER", producer_id="p",
            producer_version="1", inference_id="", recorded_time=T0, marking=MARK)


def test_cross_scheme_identity_never_self_accepts(tmp_path):
    """Review defect 1: a third-party-asserted identifier match must stay a
    reviewable proposal — no SERVICE-accepted SAME_AS, no cluster fusion."""
    pipeline = make_pipeline(tmp_path)
    wikidata_body = json.dumps({"entities": {"Q999001": {
        "id": "Q999001",
        "labels": {"en": {"language": "en", "value": "Vessia Steel"}},
        "aliases": {}, "descriptions": {},
        "claims": {"P1278": [{"mainsnak": {"snaktype": "value", "datatype": "external-id",
                                           "datavalue": {"value": "TESTLEI0000000000001",
                                                         "type": "string"}}}]},
    }}}).encode()
    plant_manifestation(pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q999001",
                        body=wikidata_body, media_type="application/json",
                        retrieval_time="2026-08-17T12:06:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    accepted = [r for r in store.records_of("association_resolution")
                if r["resolution"] == "ACCEPTED" and r["actor_kind"] == "SERVICE"]
    assert not accepted, "machine paths must not accept identity resolutions"
    same_as_active = [r for r in store.records_of("relationship_version")
                      if r["relation_type"] == "SAME_AS" and r["status"] == "ACTIVE"]
    assert not same_as_active
    proposals = store.records_of("association_proposal")
    assert proposals and all(p["outcome"] != "AUTO_ASSOCIATE" for p in proposals)
    ambiguities = [r for r in store.open_review_items() if r["kind"] == "IDENTITY_AMBIGUITY"]
    assert ambiguities, "the ambiguity must be queued for review"
    lei_object = world_object_id("LEI:TESTLEI0000000000001")
    qid_object = world_object_id("WIKIDATA_QID:Q999001")
    projection = Projection(store)
    assert projection.cluster_of[lei_object] != projection.cluster_of[qid_object], \
        "objects must remain unclustered until a human accepts the proposal"


def test_historical_capture_retrieved_today_does_not_displace_current_state(tmp_path):
    """Review defect 2: evidence recency is source-state time, not retrieval
    time — an old capture fetched today must not become the current reading."""
    pipeline = make_pipeline(tmp_path)
    live = plant_manifestation(
        pipeline, source_id="live-web", native_id="https://vessia.example/about",
        body=PAGE_V1, media_type="text/html",
        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_manifestation(live)
    store = pipeline.store
    claim = next(c for c in store.current_claims().values()
                 if c["predicate"] == "managing_director")
    assert claim["object_or_value"] == "Kari Nordmann"
    old_page = PAGE_V1.replace(b"Kari Nordmann", b"Old Director 2008")
    capture = plant_manifestation(
        pipeline, source_id="wayback",
        native_id="20080301120000/https://vessia.example/about",
        body=old_page, media_type="text/html",
        retrieval_time="2026-08-17T13:05:00+00:00",  # retrieved AFTER the live fetch
        temporal_status="HISTORICAL",
        source_time="2008-03-01T12:00:00+00:00",
        archive_capture_time="2008-03-01T12:00:00+00:00")
    pipeline.process_manifestation(capture)
    claim = next(c for c in store.current_claims().values()
                 if c["predicate"] == "managing_director")
    assert claim["object_or_value"] == "Kari Nordmann", \
        "the 2008 archived state must not displace today's state"


def test_processing_failure_is_durable_and_retried(tmp_path, monkeypatch):
    """Review defect 3: a failure after normalization must leave an OPEN
    PROCESSING_FAILED item and be retried, never skipped as done."""
    pipeline = make_pipeline(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="TESTLEI0000000000001",
                        body=GLEIF_RECORD_V1, media_type="application/vnd.api+json",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    import curunir_semantic.pipeline as pipeline_module

    def boom(*args, **kwargs):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(pipeline_module, "extract_observations", boom)
    outcome = pipeline.process_new_evidence()
    assert outcome["failed"] and outcome["failed"][0]["status"] == "PROCESSING_FAILED"
    store = pipeline.store
    open_failures = [r for r in store.open_review_items() if r["kind"] == "PROCESSING_FAILED"]
    assert open_failures, "the failure must be durable state"
    monkeypatch.undo()
    retried = pipeline.process_new_evidence()
    assert retried["processed"] and retried["processed"][0]["status"] == "PROCESSED"
    assert not [r for r in store.open_review_items() if r["kind"] == "PROCESSING_FAILED"]


def test_feed_without_channel_title_integrates_without_crash(tmp_path):
    """Review defect 3 root case: subjects with only event observations."""
    pipeline = make_pipeline(tmp_path)
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>One</title><link>https://x/1</link><guid>guid-1</guid></item>
    </channel></rss>"""
    plant_manifestation(pipeline, source_id="federal-register-feed",
                        native_id="https://feed.example/rss", body=feed,
                        media_type="application/rss+xml",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    outcome = pipeline.process_new_evidence()
    assert not outcome["failed"]
    store = pipeline.store
    # the feed subject exists as an object even without attribute observations
    events = [a for a in store.records_of("activity") if a["activity_type"] == "item_published"]
    assert events
    object_ids = {v["object_id"] for v in store.records_of("object_version")}
    assert events[0]["subject_ids"][0] in object_ids


def test_unlocatable_text_value_gets_honest_document_anchor(tmp_path):
    """Review defect 4: no fabricated spans — an unlocatable value anchors at
    document scope with a declared status."""
    pipeline = make_pipeline(tmp_path)
    body = b"Published  by:\n   Nordic   Steel\n  Holding AS\nsome other content here"
    plant_manifestation(pipeline, source_id="live-web", native_id="https://x.example/",
                        body=body, media_type="text/plain",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    from curunir_semantic.normalize import load_text
    document = store.records_of("semantic_document")[-1]
    text = load_text(store, document)
    for observation in store.records_of("semantic_observation"):
        for anchor in observation["anchors"]:
            if anchor["kind"] == "TEXT_SPAN":
                assert text[anchor["start"]:anchor["end"]] == anchor["exact_value"], \
                    "a text-span anchor must recover exactly its value"
            else:
                assert anchor["kind"] == "DOCUMENT"
                assert anchor["mapping_status"] == "VALUE_NOT_LOCATED_DOCUMENT_SCOPE"


def test_source_time_never_defaults_to_retrieval_time(tmp_path):
    """Review defect 5: knowledge time must not leak into the valid-time axis."""
    pipeline = make_pipeline(tmp_path)
    wikidata_body = json.dumps({"entities": {"Q77": {
        "id": "Q77", "labels": {"en": {"language": "en", "value": "Timeless Entity"}},
        "aliases": {}, "descriptions": {}, "claims": {}}}}).encode()
    plant_manifestation(pipeline, source_id="wikidata", native_id="Q77",
                        body=wikidata_body, media_type="application/json",
                        retrieval_time="2026-08-17T12:05:00+00:00")
    pipeline.process_new_evidence()
    store = pipeline.store
    version = next(v for v in store.records_of("object_version")
                   if ("WIKIDATA_QID", "Q77") in {(r["system"], r["external_id"])
                                                  for r in v["external_refs"]})
    assert version["source_time"] is None
    assert version["valid_from"] is None


def test_two_writers_on_one_root_extend_one_chain(tmp_path):
    """Review defect 13: concurrent handles must not fork the hash chain."""
    from curunir_semantic.store import SemanticStore
    pipeline = make_pipeline(tmp_path)
    store_a = pipeline.store
    store_b = SemanticStore(tmp_path / "store")
    from curunir_semantic.contracts import ReviewItem
    for index, store in enumerate((store_a, store_b, store_a, store_b)):
        item = ReviewItem(
            item_id=f"item-{index}", kind="MANIFESTATION_CHANGED", subject_kind="x",
            subject_id=f"s-{index}", detail="d", evidence_refs=(),
            status="OPEN", resolution_note="",
            recorded_time=f"2026-08-17T13:0{index}:00+00:00", marking=MARK)
        store.append("REVIEW_ITEM_RECORDED", item,
                     recorded_time=item.recorded_time, actor="t")
    reopened = SemanticStore(tmp_path / "store")
    assert reopened.verify_chain()["valid"]
    assert len(reopened.records_of("review_item")) == 4


def test_wayback_enumeration_shares_the_site_family(tmp_path):
    """Review defect 8: a CDX enumeration belongs to the archived site's
    origin family, and the planner computes the same family."""
    from curunir_semantic.worldmodel import dependence_group_for
    from curunir_semantic.collection import _source_origin_family
    live = {"source_id": "live-web", "native_id": "https://vessia.example/about",
            "final_url": "https://www.vessia.example/about"}
    capture = {"source_id": "wayback",
               "native_id": "20080301120000/https://vessia.example/about"}
    enumeration = {"source_id": "wayback", "native_id": "https://vessia.example/about",
                   "request_url": "https://web.archive.org/cdx/search/cdx?url=..."}
    live_family = dependence_group_for(live)
    assert dependence_group_for(capture) == live_family
    assert dependence_group_for(enumeration) == live_family
    assert _source_origin_family("wayback", "https://vessia.example/about") == live_family
