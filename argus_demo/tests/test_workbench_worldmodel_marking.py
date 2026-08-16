"""Regression pins for the confirmatory review's two V6.6-blocking findings:
the world-model entity integration carried a compartmented version's
attributes/labels forward into a lower-marked version, and the in-line
cross-scheme identity call (sibling of the sweep round 6 fixed) leaked the
equivalence review item. Both on the launch_route path, in ordinary use.
"""
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


def _plant(pipeline, *, marking, body: bytes, native_id: str):
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    record = ManifestationRecord(
        manifestation_id=digest_id("manifestation", native_id, digest),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
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
    """Finding 1: a compartmented entity version's other-name/attributes are
    not re-materialized into a PUBLIC version by a later ordinary run."""
    pipeline, ctx = make_workbench(tmp_path)
    # step 1: a COMPARTMENTED acquisition creates the entity with a codeword
    _plant(pipeline, marking=RESTRICTED_MARK,
           body=_gleif("NIGHTFALL Holding SPV", "Kompartmentgrad",
                       other="NIGHTFALL Holding SPV"),
           native_id=f"lei/{LEI}/special")
    pipeline.process_new_evidence()
    # step 2: an ORDINARY PUBLIC acquisition updates the same entity
    _plant(pipeline, marking=MARK,
           body=_gleif("Public Registry Name", "Oslo"),
           native_id=f"lei/{LEI}/public")
    pipeline.process_new_evidence()

    view_b = MissionProjection(ctx.store, CTX_B)
    # the merged-forward compartmented other-name never reaches the uncleared
    # analyst — via the entity, its dossier, the entity list, or search
    blob = json.dumps([o for o in view_b.visible_objects()]) \
        + json.dumps(view_b.family("semantic_claim"))
    assert "NIGHTFALL" not in blob and "Kompartmentgrad" not in blob
    # the entity object itself (current version) is not visible to B
    object_id = next((o["object_id"] for o in
                      MissionProjection(ctx.store, CTX_A).visible_objects()
                      if "NIGHTFALL" in json.dumps(o)), None)
    assert object_id is not None  # A sees it
    assert view_b.object_current(object_id) is None  # B does not


def test_inline_identity_ambiguity_inherits_endpoint_markings(tmp_path):
    """Finding 2: the in-line cross-scheme identity path marks its review item
    with the join of both endpoints — an ordinary public route naming a
    compartmented object's identifier does not leak the equivalence."""
    from curunir_operational.contracts import (ExternalRef, ObjectVersion,
                                               ProvenanceSummary)
    pipeline, ctx = make_workbench(tmp_path)
    now = ctx.now_fn
    # a compartmented world object already carries the LEI as an external ref
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
    # an ordinary PUBLIC wikidata-style page states the same LEI for a public
    # entity, integrated through the pipeline (an in-line identity match)
    body = (b'{"data": {"id": "Q999", "attributes": {"lei": "%s", "entity": '
            b'{"legalName": {"name": "Public Co"}, "jurisdiction": "NO", '
            b'"status": "ACTIVE", "otherNames": [], "legalAddress": '
            b'{"city": "Oslo", "country": "NO"}}, "registration": {"status": '
            b'"ISSUED", "initialRegistrationDate": "2014-03-02", '
            b'"lastUpdateDate": "2026-08-01T00:00:00+00:00"}}}}' % LEI.encode())
    _plant(pipeline, marking=MARK, body=body, native_id=f"lei/{LEI}/public2")
    pipeline.process_new_evidence()

    view_b = MissionProjection(ctx.store, CTX_B)
    identity_items = [r for r in view_b.family("review_item")
                      if r["kind"] == "IDENTITY_AMBIGUITY"]
    for item in identity_items:
        assert special_id not in json.dumps(item)
    assert special_id not in json.dumps(view_b.family("review_item"))
