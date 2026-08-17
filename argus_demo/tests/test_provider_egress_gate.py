"""V6.7 provider-egress lock (§100 demo) — a provider must not receive evidence
above its declared marking ceiling, and the check happens BEFORE the provider
call (before any bytes leave the process).

The provider seam (AnalyticalAssist.propose) is not yet wired into the live
acquisition pipeline, but it is the one designed gate between an inference
provider and analytical candidates; this lock pins its egress control so a
future wiring cannot send SPECIAL evidence to a PUBLIC provider.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_analytic.providers import AnalyticalAssist, analytical_assist_package
from curunir_fabric.contracts import ManifestationRecord

from analytic_support import make_analytic
from semantic_support import MARK, T0, plant_manifestation
from workbench_support import RESTRICTED_MARK

pytestmark = pytest.mark.no_db


def _gleif(lei: str, status: str = "ACTIVE") -> bytes:
    return (
        '{"data": {"id": "%s", "attributes": {"lei": "%s", "entity": '
        '{"legalName": {"name": "Entity %s"}, "jurisdiction": "NO", "status": '
        '"%s", "otherNames": [], "legalAddress": {"city": "Oslo", "country": '
        '"NO"}}, "registration": {"status": "ISSUED", "initialRegistrationDate": '
        '"2014-03-02", "lastUpdateDate": "2026-08-01"}}}}' % (lei, lei, lei, status)
    ).encode("utf-8")


def _plant_restricted(pipeline, lei: str) -> None:
    body = _gleif(lei)
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    native = f"lei/{lei}"
    pipeline.store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", native, digest),
        source_id="gleif", connector_id="gleif-test", connector_version="1.0",
        native_id=native, request_url=native, final_url=native,
        content_sha256=digest, content_store_path=str(path),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=T0, http_status=200, redirects=(),
        etag="", last_modified="", truncated=False,
        retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", native), prior_manifestation_id=None,
        marking=RESTRICTED_MARK), recorded_time=pipeline.now_fn(), actor="t")


def _claim(store, lei: str) -> dict:
    return next(c for c in store.current_claims().values()
               if c["subject_ref"] == f"LEI:{lei}" and c["predicate"] == "entity_status")


def test_public_provider_refuses_special_evidence_before_egress(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    store = ctx.store
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/PUB0000000000000001",
                        body=_gleif("PUB0000000000000001"), media_type="application/json",
                        retrieval_time=T0)
    _plant_restricted(pipeline, "SEC0000000000000001")
    pipeline.process_new_evidence()
    public_claim = _claim(store, "PUB0000000000000001")
    special_claim = _claim(store, "SEC0000000000000001")
    assert public_claim["marking"]["compartments"] == []
    assert special_claim["marking"]["compartments"] == ["SPECIAL"]

    calls: list = []

    def infer(task, inputs):
        calls.append(inputs)
        return {"title": "candidate", "supporting_claim_ids": [public_claim["claim_id"]]}

    # a PUBLIC external provider: ceiling is the PUBLIC-releasable mission mark
    public_provider = AnalyticalAssist(
        package=analytical_assist_package("ext-cloud", "gpt-x", "1"),
        infer_fn=infer, allowed_input_marking=MARK)

    # SPECIAL evidence → refused, and the provider is NEVER called
    refused = public_provider.propose(
        ctx, task="propose-theme", target_kind="analytic_theme",
        inputs={"claims": "restricted material"},
        input_refs=(special_claim["claim_id"],))
    assert refused["status"] == "EGRESS_REFUSED", refused
    assert refused["input_marking"]["compartments"] == ["SPECIAL"]
    assert calls == [], "SPECIAL evidence must not reach a PUBLIC provider"

    # PUBLIC evidence → egress permitted, the provider runs
    ok = public_provider.propose(
        ctx, task="propose-theme", target_kind="analytic_theme",
        inputs={"claims": "public material"},
        input_refs=(public_claim["claim_id"],))
    assert ok["status"] == "PROPOSED", ok
    assert len(calls) == 1, "the provider runs for PUBLIC evidence"


def test_ungoverned_provider_refuses_compartmented_but_allows_public(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    plant_manifestation(pipeline, source_id="gleif", native_id="lei/PUB0000000000000002",
                        body=_gleif("PUB0000000000000002"), media_type="application/json",
                        retrieval_time=T0)
    _plant_restricted(pipeline, "SEC0000000000000003")
    pipeline.process_new_evidence()
    public_claim = _claim(ctx.store, "PUB0000000000000002")
    special_claim = _claim(ctx.store, "SEC0000000000000003")
    called: list = []
    provider = AnalyticalAssist(
        package=analytical_assist_package("ext", "m", "1"),
        infer_fn=lambda t, i: called.append(i) or {
            "title": "x", "supporting_claim_ids": [public_claim["claim_id"]]},
        allowed_input_marking=None)  # no declared policy → public-releasable only

    # SPECIAL/compartmented evidence is refused without any operator config
    refused = provider.propose(ctx, task="t", target_kind="analytic_theme",
                               inputs={"x": "y"}, input_refs=(special_claim["claim_id"],))
    assert refused["status"] == "EGRESS_REFUSED"
    assert called == []
    # public-releasable evidence still flows to the (external) provider
    ok = provider.propose(ctx, task="t", target_kind="analytic_theme",
                          inputs={"x": "y"}, input_refs=(public_claim["claim_id"],))
    assert ok["status"] == "PROPOSED", ok
    assert len(called) == 1


def test_cleared_provider_receives_special_evidence(tmp_path):
    pipeline, ctx = make_analytic(tmp_path)
    _plant_restricted(pipeline, "SEC0000000000000002")
    pipeline.process_new_evidence()
    special_claim = _claim(ctx.store, "SEC0000000000000002")
    called: list = []
    # a trusted provider cleared for the SPECIAL compartment
    provider = AnalyticalAssist(
        package=analytical_assist_package("local-trusted", "m", "1"),
        infer_fn=lambda t, i: called.append(i) or {
            "title": "x", "supporting_claim_ids": [special_claim["claim_id"]]},
        allowed_input_marking=RESTRICTED_MARK)
    out = provider.propose(ctx, task="t", target_kind="analytic_theme",
                           inputs={"x": "y"}, input_refs=(special_claim["claim_id"],))
    assert out["status"] == "PROPOSED", out
    assert len(called) == 1


def test_provider_surrogate_output_does_not_crash_and_is_recorded(tmp_path):
    # review F-P1 (MAJOR): a lone surrogate in the external provider's returned
    # mapping must not crash the InferenceRecord's serialization and thereby
    # suppress the invocation's own non-repudiation record.
    pipeline, ctx = make_analytic(tmp_path)
    _plant_restricted(pipeline, "SEC0000000000000009")
    pipeline.process_new_evidence()
    claim = _claim(ctx.store, "SEC0000000000000009")
    provider = AnalyticalAssist(
        package=analytical_assist_package("local-surrogate", "m", "1"),
        infer_fn=lambda t, i: {"title": "bad\ud800title",
                               "supporting_claim_ids": [claim["claim_id"]]},
        allowed_input_marking=RESTRICTED_MARK)
    out = provider.propose(ctx, task="t", target_kind="analytic_theme",
                           inputs={"x": "y"}, input_refs=(claim["claim_id"],))  # must NOT raise
    assert out["status"] == "PROPOSED", out
    # the invocation was recorded (audit not lost), with the surrogate scrubbed
    inferences = ctx.store.records_of("inference")
    assert inferences
    assert "\ud800" not in inferences[-1]["output"].get("title", "")


def test_provider_scrub_covers_sets_and_caller_task_inputs(tmp_path):
    # review F-P1 residual: (a) _scrub_output must cover set/frozenset (canonical
    # serialization DOES serialize sets, so a surrogate inside one would crash the
    # append like any container); (b) the caller-supplied task / inputs — hashed
    # OUTSIDE the try for inference_id / input_hash — must be scrubbed too, or a
    # surrogate there crashes the record build and suppresses the audit.
    import json
    from curunir_analytic.providers import _scrub_output
    from curunir_operational.canonical import sha256
    # (a) sets/frozensets scrub cleanly (no surrogate survives into canonical):
    sha256(_scrub_output({"tags": {"bad\ud800"}, "f": frozenset({"x\udfff"})}))  # must not raise

    # (b) a surrogate in the caller task / inputs must not crash the audit build:
    pipeline, ctx = make_analytic(tmp_path)
    _plant_restricted(pipeline, "SEC0000000000000009")
    pipeline.process_new_evidence()
    claim = _claim(ctx.store, "SEC0000000000000009")
    provider = AnalyticalAssist(
        package=analytical_assist_package("local-surrogate-2", "m", "1"),
        infer_fn=lambda t, i: {"title": "ok", "supporting_claim_ids": [claim["claim_id"]]},
        allowed_input_marking=RESTRICTED_MARK)
    out = provider.propose(ctx, task="task-\ud800", target_kind="analytic_theme",
                           inputs={"k": "in-\udfff"}, input_refs=(claim["claim_id"],))  # must NOT raise
    assert out["status"] == "PROPOSED", out
    rec = json.dumps(ctx.store.records_of("inference")[-1], ensure_ascii=False)
    assert "\ud800" not in rec and "\udfff" not in rec       # audit written, fully scrubbed


def test_provider_non_finite_output_is_scrubbed_and_recorded(tmp_path):
    # review MAJOR-2: a non-finite float from the external provider (the non-finite
    # HALF of F-P1) must be neutralized to null, not crash the InferenceRecord
    # append and lose the invocation's own non-repudiation record.
    import json
    pipeline, ctx = make_analytic(tmp_path)
    _plant_restricted(pipeline, "SEC0000000000000009")
    pipeline.process_new_evidence()
    claim = _claim(ctx.store, "SEC0000000000000009")
    provider = AnalyticalAssist(
        package=analytical_assist_package("local-nonfinite", "m", "1"),
        infer_fn=lambda t, i: {"title": "ok", "confidence": float("nan"),
                               "scores": [float("inf"), 1.0],
                               "supporting_claim_ids": [claim["claim_id"]]},
        allowed_input_marking=RESTRICTED_MARK)
    out = provider.propose(ctx, task="t", target_kind="analytic_theme",
                           inputs={"x": "y"}, input_refs=(claim["claim_id"],))  # must NOT raise
    assert out["status"] == "PROPOSED", out
    rec = ctx.store.records_of("inference")[-1]              # audit landed
    assert rec["output"]["confidence"] is None               # non-finite -> null
    assert json.dumps(rec, allow_nan=False)                  # whole record re-serializes clean
