"""V6.7 exploit lock — the known non-workbench forecast-resolution value leak.

A forecast's typed machine resolution reads the live claim named by its
resolution rule and writes the claim's *value* into the resolution reason, which
lands in the new forecast version's `change_reason` and the RESOLVED_*
transition's `detail`. When the claim is RESTRICTED but the resolution runs on
the lower-marked watch/propagation path, the derived records were stamped with
the context marking — so a lower-cleared actor could read the restricted claim
value out of a forecast they can otherwise see.

Realistic scenario: a genuinely PUBLIC forecast is authored about a PUBLIC
registry claim; a later RESTRICTED source updates that claim to a compartmented
value; the machine then resolves the forecast against the now-restricted value.
The resolution transition and version must inherit the claim's marking, so the
value never surfaces in a record the uncleared actor can read.

Behavioral lock: it asserts on what each access context can read through the
authorized MissionProjection, not on a marking field value.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.contracts import ResolutionRule
from curunir_analytic.forecasts import create_forecast, refresh_forecast
from curunir_fabric.contracts import ManifestationRecord
from curunir_workbench.projections import MissionProjection

from semantic_support import T0, plant_manifestation
from workbench_support import CTX_A, CTX_B, RESTRICTED_MARK, make_workbench

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"
SECRET_STATUS = "CLASSIFIEDXYZZY"
HORIZON = "2026-08-17T12:02:00+00:00"
EVIDENCE_TIME = "2026-08-17T14:00:00+00:00"


def _gleif_body(status: str) -> bytes:
    return (
        '{"data": {"id": "ACMELEI000000000001", "attributes": {'
        '"lei": "ACMELEI000000000001", "entity": {"legalName": {"name": '
        '"Acme Industri AS"}, "jurisdiction": "NO", "status": "%s", '
        '"otherNames": [], "legalAddress": {"city": "Oslo", "country": "NO"}}, '
        '"registration": {"status": "ISSUED", "initialRegistrationDate": '
        '"2014-03-02", "lastUpdateDate": "2026-08-16"}}}}' % status
    ).encode("utf-8")


def _plant_restricted(pipeline, *, body: bytes, retrieval_time: str, prior: str) -> None:
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native_id = "lei/ACMELEI000000000001"
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", native_id, digest),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native_id, request_url=native_id, final_url=native_id,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=retrieval_time, http_status=200,
        redirects=(), etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native_id, retrieval_time),
        prior_manifestation_id=prior, marking=RESTRICTED_MARK),
        recorded_time=pipeline.now_fn(), actor="t")


def _status_claim(store):
    return next(c for c in store.current_claims().values()
               if c["subject_ref"] == ACME and c["predicate"] == "entity_status")


def test_machine_resolution_does_not_leak_restricted_claim_value(tmp_path):
    pipeline, ctx = make_workbench(tmp_path)
    store = ctx.store

    # a PUBLIC registry manifestation → a PUBLIC entity_status claim
    v1 = plant_manifestation(
        pipeline, source_id="gleif", native_id="lei/ACMELEI000000000001",
        body=_gleif_body("ACTIVE"), media_type="application/json", retrieval_time=T0)
    pipeline.process_new_evidence()
    claim = _status_claim(store)
    assert claim["marking"]["compartments"] == [], "claim is PUBLIC at authoring time"

    # a genuinely PUBLIC forecast, authored about that public claim
    rule = ResolutionRule(
        kind="CLAIM_PREDICATE",
        criteria=f"GLEIF entity_status for {ACME} reads INACTIVE",
        claim_subject_ref=ACME, claim_attribute="entity_status",
        expected_value="INACTIVE",
        absence_min_successful_sources=1, absence_required_source_ids=("gleif",))
    forecast = create_forecast(
        ctx, question=f"Will {ACME} entity_status read INACTIVE by the horizon?",
        outcome_semantics="TRUE iff GLEIF entity_status == 'INACTIVE' by horizon",
        proposition_refs=(("claim", claim["claim_id"]),),
        horizon_time=HORIZON, resolution=rule, probability=0.4,
        probability_basis="base rate", author="jan", domain="corporate-registry")
    fid = forecast["forecast_id"]
    assert forecast["marking"]["compartments"] == [], "forecast authored PUBLIC"
    assert MissionProjection(store, CTX_B).get("analytic_forecast", fid) is not None

    # a RESTRICTED source then updates the claim to a compartmented secret value,
    # postdating the horizon
    _plant_restricted(pipeline, body=_gleif_body(SECRET_STATUS),
                      retrieval_time=EVIDENCE_TIME, prior=v1["manifestation_id"])
    pipeline.process_new_evidence()
    claim = _status_claim(store)
    assert claim["object_or_value"] == SECRET_STATUS
    assert claim["marking"]["compartments"] == ["SPECIAL"], "claim is now restricted"

    # the machine resolves FALSE, and the reason embeds the restricted claim value
    resolved = refresh_forecast(ctx, fid, caused_by="watch-tick")
    assert resolved["status"] == "RESOLVED_FALSE", resolved["status"]
    assert SECRET_STATUS in resolved["change_reason"], \
        "precondition: the resolution reason embeds the restricted claim value"

    # the cleared actor sees the full resolution (capability preserved)
    proj_a = MissionProjection(store, CTX_A)
    assert any(t["transition_type"] == "RESOLVED_FALSE"
               for t in proj_a.transitions(fid)), \
        "a SPECIAL-cleared actor still sees the machine resolution"

    # the uncleared actor learns nothing restricted
    proj_b = MissionProjection(store, CTX_B)
    b_blob = json.dumps([proj_b.transitions(fid),
                         proj_b.versions("analytic_forecast", fid),
                         proj_b.get("analytic_forecast", fid),
                         proj_b.overview()])
    assert SECRET_STATUS not in b_blob, \
        "the restricted claim value leaked into an uncleared actor's view"
    assert not any(t["transition_type"] == "RESOLVED_FALSE"
                   for t in proj_b.transitions(fid)), \
        "the restricted resolution transition is visible to an uncleared actor"
