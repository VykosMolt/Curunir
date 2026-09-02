"""Marking inheritance in the world model: a hidden version's state must not
resurface in a lower-marked one, and every derived record inherits the marking
of what it describes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_fabric.contracts import ManifestationRecord
from curunir_workbench.projections import MissionProjection

from semantic_support import MARK, T0
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

LEI = "TESTLEI7777777777"


def _gleif(name: str, city: str, other: str = "") -> bytes:
    others = f'{{"name": "{other}"}}' if other else ""
    return (
        '{"data": {"id": "%s", "attributes": {"lei": "%s", "entity": '
        '{"legalName": {"name": "%s"}, "jurisdiction": "NO", "status": "ACTIVE", '
        '"otherNames": [%s], "legalAddress": {"city": "%s", "country": "NO"}}, '
        '"registration": {"status": "ISSUED", "initialRegistrationDate": '
        '"2014-03-02", "lastUpdateDate": "2026-08-01T00:00:00+00:00"}}}}'
        % (LEI, LEI, name, others, city)).encode("utf-8")


def _plant(pipeline, *, marking, body: bytes, native_id: str, source_id="gleif"):
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native_id, digest),
        source_id=source_id, connector_id=f"{source_id}-test", connector_version="1.0",
        native_id=native_id, request_url=native_id, final_url=native_id,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=T0, http_status=200, redirects=(),
        etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native_id), prior_manifestation_id=None,
        marking=marking)
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", record,
                          recorded_time=pipeline.now_fn(), actor="t")


def test_entity_merge_does_not_declassify_carried_forward_state(tmp_path):
    """A hidden entity version's names and attributes do not reappear in a later
    PUBLIC version."""
    pipeline, ctx = make_workbench(tmp_path)
    # A restricted acquisition creates the entity, carrying a codeword name.
    _plant(pipeline, marking=RESTRICTED_MARK,
           body=_gleif("NIGHTFALL Holding SPV", "Kompartmentgrad",
                       other="NIGHTFALL Holding SPV"),
           native_id=f"lei/{LEI}/special")
    pipeline.process_new_evidence()
    # An ordinary public acquisition then updates the same entity.
    _plant(pipeline, marking=MARK,
           body=_gleif("Public Registry Name", "Oslo"),
           native_id=f"lei/{LEI}/public")
    pipeline.process_new_evidence()

    view_b = MissionProjection(ctx.store, CTX_B)
    blob = json.dumps([o for o in view_b.visible_objects()]) \
        + json.dumps(view_b.family("semantic_claim"))
    assert "NIGHTFALL" not in blob and "Kompartmentgrad" not in blob
    object_id = next((o["object_id"] for o in
                      MissionProjection(ctx.store, CTX_A).visible_objects()
                      if "NIGHTFALL" in json.dumps(o)), None)
    assert object_id is not None
    assert view_b.object_current(object_id) is None


def test_inline_identity_ambiguity_inherits_endpoint_markings(tmp_path):
    """A cross-scheme identity match marks its review item with both endpoints, so
    a public route naming a hidden object's identifier does not expose the match."""
    from curunir_operational.contracts import (ExternalRef, ObjectVersion,
                                               ProvenanceSummary)
    pipeline, ctx = make_workbench(tmp_path)
    now = ctx.now_fn
    # A hidden world object already carries the LEI as an external ref.
    from curunir_semantic.worldmodel import world_object_id
    special_id = world_object_id(f"LEI:{LEI}")
    ctx.store.append("OBJECT_VERSION_APPENDED", ObjectVersion(
        object_id=special_id, version=1, object_type="ORGANISATION",
        lifecycle="ACTIVE", labels=("Compartmented Entity",),
        external_refs=(ExternalRef(system="LEI", external_id=LEI,
                                   imported_version="d", ingestion_id="i",
                                   identity_bearing=True),),
        valid_from=None, valid_to=None, source_time=None, time_precision="UNKNOWN",
        recorded_time=now(), geometry=None, attributes={}, quality={},
        epistemic_state="REPORTED", marking=RESTRICTED_MARK,
        provenance=ProvenanceSummary(mode="OPERATIONAL")),
        recorded_time=now(), actor="t")
    # A public Wikidata page gives the same LEI for a different subject, which
    # makes a second object and triggers the identity match against the hidden one.
    body = (b'{"entities": {"Q999": {"id": "Q999", '
            b'"labels": {"en": {"value": "Public Co"}}, '
            b'"claims": {"P1278": [{"mainsnak": {"datavalue": '
            b'{"value": "%s"}}}]}}}}' % LEI.encode())
    _plant(pipeline, marking=MARK, body=body, native_id="wikidata/Q999",
           source_id="wikidata")
    pipeline.process_new_evidence()

    view_b = MissionProjection(ctx.store, CTX_B)
    identity_items = [r for r in view_b.family("review_item")
                      if r["kind"] == "IDENTITY_AMBIGUITY"]
    for item in identity_items:
        assert special_id not in json.dumps(item)
    assert special_id not in json.dumps(view_b.family("review_item"))


def _special_claim(ctx):
    from curunir_semantic.contracts import SemanticClaim
    claim = SemanticClaim(
        claim_id="claim-conflict-special", version=1,
        statement="compartmented proposition", subject_ref="URL:x.example",
        subject_object_id="obj-x", predicate="managing_director",
        object_or_value="KariCodewordPerson", object_object_id="",
        valid_from=None, valid_to=None, time_precision="UNKNOWN", polarity="AFFIRMED",
        observation_ids=("obs-special",), dependence_group_ids=("evgroup-a",),
        independent_basis_count=1, basis_note="", world_refs=(),
        epistemic_state="EXTRACTED", review_state="UNREVIEWED",
        recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK)
    ctx.store.append("SEMANTIC_CLAIM_RECORDED", claim, recorded_time=claim.recorded_time,
                     actor="t")
    return claim.to_record()


def test_fa_claim_conflict_inherits_claim_marking(tmp_path):
    """A DISPUTED state and CONTRADICTED item about a hidden claim inherit that
    claim's marking, even when the conflicting observation arrives publicly."""
    pipeline, ctx = make_workbench(tmp_path)
    from curunir_semantic.worldmodel import _record_claim_conflict
    claim = _special_claim(ctx)
    public_obs = {"observation_id": "obs-public", "source_id": "live-web",
                  "value": "Ola Public"}
    _record_claim_conflict(pipeline.context(), claim, public_obs, {})  # public context
    view_b = MissionProjection(ctx.store, CTX_B)
    blob = json.dumps(view_b.family("review_item")) \
        + json.dumps(view_b.family("semantic_claim_state"))
    assert "KariCodewordPerson" not in blob
    assert not [r for r in view_b.family("review_item") if r["kind"] == "CONTRADICTED"]


def test_fb_claim_standing_inherits_claim_marking(tmp_path):
    """A standing reset about a hidden claim inherits that claim's marking."""
    pipeline, ctx = make_workbench(tmp_path)
    from curunir_semantic.contracts import ClaimStateRecord, SemanticClaim
    from curunir_semantic.worldmodel import _ensure_claim_standing
    claim = _special_claim(ctx)
    # The earlier conflict recorded a restricted standing.
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", ClaimStateRecord(
        state_id="cs-1", claim_id=claim["claim_id"], state="SUPERSEDED",
        reason="prior", caused_by="", superseded_by="", actor_id="t",
        actor_kind="SERVICE", recorded_time=ctx.now_fn(), marking=RESTRICTED_MARK),
        recorded_time=ctx.now_fn(), actor="t")
    # A public pass has already appended a PUBLIC v2, so the marking has to come
    # from the earlier state record, not from the current claim version.
    raw = ctx.store.current_claims()[claim["claim_id"]]
    ctx.store.append("SEMANTIC_CLAIM_RECORDED", SemanticClaim(**{
        **{k: v for k, v in raw.items() if k != "record_type"},
        "observation_ids": ("obs-public",), "dependence_group_ids": ("evgroup-b",),
        "object_or_value": "Nils Publicsen", "version": 2,
        "recorded_time": ctx.now_fn(), "marking": MARK}),
        recorded_time=ctx.now_fn(), actor="t")
    _ensure_claim_standing(pipeline.context(), claim["claim_id"], version=2,
                           new_value="Nils Publicsen", observation_id="obs-fresh")
    view_b = MissionProjection(ctx.store, CTX_B)
    assert not any(s["claim_id"] == claim["claim_id"]
                   for s in view_b.family("semantic_claim_state"))
    assert not [r for r in view_b.family("review_item")
                if r["kind"] == "MANIFESTATION_CHANGED"]


def test_f2_stale_basis_inherits_claim_state_marking(tmp_path):
    """A stale-basis item names the standing that caused it, so it inherits that
    standing's marking even when the hypothesis is public."""
    pipeline, ctx = make_workbench(tmp_path)
    from curunir_semantic.contracts import ClaimStateRecord
    from curunir_semantic.hypotheses import _queue_stale_basis
    claim = _special_claim(ctx)
    ctx.store.append("SEMANTIC_CLAIM_STATE_RECORDED", ClaimStateRecord(
        state_id="cs-disputed", claim_id=claim["claim_id"], state="DISPUTED",
        reason="compartmented conflict", caused_by="", superseded_by="",
        actor_id="t", actor_kind="SERVICE", recorded_time=ctx.now_fn(),
        marking=RESTRICTED_MARK), recorded_time=ctx.now_fn(), actor="t")
    public_hyp = {"hypothesis_id": "hyp-public", "marking": MARK.to_record(),
                  "status": "UNRESOLVED"}
    _queue_stale_basis(pipeline.context(), public_hyp,
                       [{"claim": claim, "state": "DISPUTED"}])
    view_b = MissionProjection(ctx.store, CTX_B)
    assert not [r for r in view_b.family("review_item") if r["kind"] == "STALE_BASIS"]
