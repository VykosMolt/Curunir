"""A restricted record can be cited but never quoted: its prose must not reach a
public forecast, collection or indicator."""
from __future__ import annotations

import hashlib
import json

import pytest

from argus.source_intelligence.models import digest_id

from curunir_analytic.collect import (
    analytic_collection_needs,
    open_analytic_requirements,
)
from curunir_analytic.contracts import IndicatorEffect, ResolutionRule
from curunir_analytic.forecasts import create_forecast
from curunir_analytic.indicators import arm_indicator, check_indicators
from curunir_analytic.substrate import AnalyticContext
from curunir_analytic.themes import create_theme
from curunir_identity import KeyRegistry, SessionManager, generate_keypair, sign
from curunir_identity.sessions import AuthError, Session
from curunir_fabric.contracts import ManifestationRecord
from curunir_operational.access import Marking, can_view
from curunir_workbench.projections import MissionProjection
from curunir_workbench.store import WorkbenchStore
from curunir_semantic.contracts import EvidenceAnchor, SemanticObservation

from analytic_support import (
    GLEIF_ACME,
    GLEIF_ACME_SUSPENDED,
    MARK,
    T0,
    make_analytic,
)
from semantic_support import plant_manifestation
from workbench_support import CTX_B, RESTRICTED_MARK

pytestmark = pytest.mark.no_db

ACME = "LEI:ACMELEI000000000001"
HORIZON = "2026-08-17T18:00:00+00:00"
SECRET_RATIONALE = "COVERT-RATIONALE-BLUEJAY-SEALED"
SECRET_INDICATOR_DESCRIPTION = "COVERT-WATCH-BLUEJAY-DESCRIPTION"
SECRET_THEME = "COVERT-OP-BLUEJAY-SEALED"


def _seed(pipeline, ctx):
    plant_manifestation(
        pipeline,
        source_id="gleif",
        native_id="lei/ACMELEI000000000001",
        body=GLEIF_ACME,
        media_type="application/json",
        retrieval_time=T0,
    )
    pipeline.process_new_evidence()
    return {
        claim["predicate"]: claim["claim_id"]
        for claim in ctx.store.current_claims().values()
        if claim["subject_ref"] == ACME
    }


def test_restricted_indicator_prose_never_enters_public_forecast(tmp_path):
    """A public forecast may cite a restricted indicator but not quote it."""
    pipeline, public_ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, public_ctx)
    forecast = create_forecast(
        public_ctx,
        question=f"Will {ACME} be suspended by {HORIZON}?",
        outcome_semantics="TRUE iff entity_status is INACTIVE by the horizon",
        proposition_refs=(("claim", claims["entity_status"]),),
        horizon_time=HORIZON,
        resolution=ResolutionRule(
            kind="HUMAN_JUDGMENT", criteria="analyst settles"),
        probability=0.35,
        probability_basis="stable registry; low base rate",
        author="jan",
        domain="corporate-registry",
        supporting_claim_ids=[claims["entity_status"]],
    )
    restricted_ctx = AnalyticContext(
        store=public_ctx.store,
        actor="cleared",
        marking=RESTRICTED_MARK,
        now_fn=public_ctx.now_fn,
    )
    indicator = arm_indicator(
        restricted_ctx,
        description=SECRET_INDICATOR_DESCRIPTION,
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE",
        direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME,
        desired_attribute="entity_status",
        expected_value="INACTIVE",
        effect=IndicatorEffect(
            mode="APPLY_PROBABILITY",
            target_probability=0.62,
            rationale=SECRET_RATIONALE,
            authorized_by="jan",
            authorized_kind="HUMAN",
        ),
    )
    plant_manifestation(
        pipeline,
        source_id="gleif",
        native_id="lei/ACMELEI000000000001",
        body=GLEIF_ACME_SUSPENDED,
        media_type="application/json",
        retrieval_time=public_ctx.now_fn(),
    )
    pipeline.process_new_evidence()
    check_indicators(public_ctx)

    moved = public_ctx.store.current_forecasts()[forecast["forecast_id"]]
    assert moved["probability"] == 0.62
    assert can_view(moved["marking"], CTX_B)
    assert SECRET_RATIONALE not in json.dumps(moved)
    assert SECRET_INDICATOR_DESCRIPTION not in json.dumps(moved)

    versions = public_ctx.store.analytic_versions(
        "analytic_forecast", forecast["forecast_id"])
    assert all(
        SECRET_RATIONALE not in json.dumps(version)
        and SECRET_INDICATOR_DESCRIPTION not in json.dumps(version)
        for version in versions
        if can_view(version["marking"], CTX_B)
    )

    transitions = public_ctx.store.transitions_for(forecast["forecast_id"])
    assert SECRET_RATIONALE not in json.dumps(transitions)
    assert SECRET_INDICATOR_DESCRIPTION not in json.dumps(transitions)
    material = [
        transition for transition in transitions
        if transition["transition_type"] in {
            "PROBABILITY_UPDATED", "INDICATOR_FIRED"}
    ]
    assert material
    assert all(indicator["indicator_id"] in transition["evidence_refs"]
               for transition in material)
    assert all(not can_view(transition["marking"], CTX_B)
               for transition in material)


def test_collection_cites_and_inherits_restricted_analytic_source(tmp_path):
    """A collection cites the source id and takes its marking."""
    pipeline, public_ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, public_ctx)
    restricted_ctx = AnalyticContext(
        store=public_ctx.store,
        actor="cleared",
        marking=RESTRICTED_MARK,
        now_fn=public_ctx.now_fn,
    )
    theme = create_theme(
        restricted_ctx,
        title=SECRET_THEME,
        description="compartmented tasking",
        supporting_claim_ids=[claims["entity_status"]],
    )

    needs = analytic_collection_needs(public_ctx.store)
    theme_need = next(
        need for need in needs if need["source_id"] == theme["theme_id"])
    assert SECRET_THEME not in json.dumps(theme_need)
    assert theme["theme_id"] in theme_need["question"]

    opened = open_analytic_requirements(
        public_ctx, mission_context="review-regression", needs=[theme_need])
    discriminator = opened[0]["discriminator"]
    requirement = opened[0]["requirement"]
    assert SECRET_THEME not in json.dumps(opened)
    assert ("analytic_theme", theme["theme_id"]) \
        in tuple(tuple(ref) for ref in discriminator["source_refs"])
    assert "SPECIAL" in discriminator["marking"]["compartments"]
    assert "SPECIAL" in requirement["marking"]["compartments"]

    uncleared = MissionProjection(public_ctx.store, CTX_B)
    assert uncleared.get("analytic_theme", theme["theme_id"]) is None
    assert uncleared.get("discriminator", discriminator["discriminator_id"]) \
        is None


def test_restricted_observation_value_never_enters_public_indicator(tmp_path):
    """Evidence that fires raises the indicator without quoting its values."""
    secret = "COVERT-SANCTION-CODE-MOONLIGHT"
    pipeline, public_ctx = make_analytic(tmp_path)
    claims = _seed(pipeline, public_ctx)
    forecast = create_forecast(
        public_ctx,
        question=f"Will {ACME} change by {HORIZON}?",
        outcome_semantics="TRUE iff entity_status changes by the horizon",
        proposition_refs=(("claim", claims["entity_status"]),),
        horizon_time=HORIZON,
        resolution=ResolutionRule(
            kind="HUMAN_JUDGMENT", criteria="analyst settles"),
        probability=0.35,
        probability_basis="stable registry",
        author="jan",
        domain="corporate-registry",
        supporting_claim_ids=[claims["entity_status"]],
    )
    indicator = arm_indicator(
        public_ctx,
        description="any entity_status observation on ACME",
        forecast_ids=(forecast["forecast_id"],),
        kind="PRESENCE",
        direction="SUPPORTS",
        desired_observation_type="ENTITY_ATTRIBUTE",
        desired_subject_ref=ACME,
        desired_attribute="entity_status",
        expected_value="",
        effect=IndicatorEffect(mode="REVIEW_ONLY"),
    )
    now = public_ctx.now_fn()
    body = b'{"restricted": true}'
    digest = hashlib.sha256(body).hexdigest()
    custody = tmp_path / "restricted" / digest
    custody.parent.mkdir(parents=True, exist_ok=True)
    custody.write_bytes(body)
    manifestation = ManifestationRecord(
        manifestation_id=digest_id("manifestation", "restricted", digest),
        source_id="gleif", connector_id="gleif-test",
        connector_version="1.0", native_id="lei/restricted",
        request_url="lei/restricted", final_url="lei/restricted",
        content_sha256=digest, content_store_path=str(custody),
        media_type="application/json", temporal_status="LIVE",
        source_time=None, archive_capture_time=None, retrieval_time=now,
        http_status=200, redirects=(), etag="", last_modified="",
        truncated=False, retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", digest),
        prior_manifestation_id=None, marking=RESTRICTED_MARK,
    )
    pipeline.store.append(
        "FABRIC_MANIFESTATION_RECORDED", manifestation,
        recorded_time=now, actor="review-fixture")
    observation = SemanticObservation(
        observation_id=digest_id("observation", "restricted", now),
        document_id=digest_id("document", "restricted"),
        manifestation_id=manifestation.manifestation_id,
        source_id="gleif", observation_type="ENTITY_ATTRIBUTE",
        subject_ref=ACME, attribute="entity_status", value=secret,
        object_ref="", valid_from=None, valid_to=None, source_time=None,
        time_precision="UNKNOWN", language="en", representation="ORIGINAL",
        anchors=(EvidenceAnchor(
            manifestation_id=manifestation.manifestation_id,
            source_id="gleif", content_sha256=digest, kind="FIELD",
            field_path="restricted.value", exact_value=secret),),
        producer_kind="DETERMINISTIC_PARSER", producer_id="review-fixture",
        producer_version="1.0", inference_id="", recorded_time=now,
        marking=RESTRICTED_MARK,
    )
    pipeline.store.append(
        "SEMANTIC_OBSERVATION_RECORDED", observation,
        recorded_time=now, actor="review-fixture")

    check_indicators(public_ctx)
    fired = public_ctx.store.current_indicators()[indicator["indicator_id"]]
    assert fired["status"] == "FIRED"
    assert observation.observation_id in fired["fired_evidence_refs"]
    assert "SPECIAL" in fired["marking"]["compartments"]
    assert secret not in json.dumps(fired)
    transitions = public_ctx.store.transitions_for(indicator["indicator_id"])
    assert secret not in json.dumps(transitions)
    fired_transition = next(
        transition for transition in transitions
        if transition["transition_type"] == "FIRED")
    assert observation.observation_id in fired_transition["evidence_refs"]
    assert not can_view(fired_transition["marking"], CTX_B)


def test_active_key_reenroll_is_idempotent(tmp_path):
    store = WorkbenchStore.create(tmp_path / "store", "identity-test", T0)
    registry = KeyRegistry(store, marking=MARK, now_fn=lambda: T0)
    _, public = generate_keypair()
    first = registry.enroll(
        actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=public)
    event_count = store.head()["event_count"]
    second = registry.enroll(
        actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=public)
    assert second == first
    assert store.head()["event_count"] == event_count


def test_new_principal_cannot_evict_another_pending_challenge(monkeypatch):
    from curunir_identity import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "MAX_PENDING_CHALLENGES", 2)
    manager = SessionManager(now_fn=lambda: T0)
    victim = manager.issue_challenge("victim")["nonce"]
    peer = manager.issue_challenge("peer")["nonce"]
    with pytest.raises(AuthError, match="cannot evict"):
        manager.issue_challenge("newcomer")
    assert set(manager._pending) == {victim, peer}


def test_new_principal_cannot_evict_another_session(monkeypatch):
    from curunir_identity import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 2)
    manager = SessionManager(now_fn=lambda: T0)
    manager._sessions = {
        "victim": Session(
            "victim", "victim", "HUMAN", "key-victim", T0,
            "2026-08-17T13:00:00+00:00"),
        "peer": Session(
            "peer", "peer", "HUMAN", "key-peer", T0,
            "2026-08-17T13:00:00+00:00"),
    }
    with pytest.raises(AuthError, match="cannot evict"):
        manager._prune_sessions(T0, "newcomer")
    assert set(manager._sessions) == {"victim", "peer"}


def test_session_capacity_refusal_preserves_verified_challenge(
        monkeypatch, tmp_path):
    from curunir_identity import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "MAX_SESSIONS", 1)
    manager = SessionManager(now_fn=lambda: T0)
    store = WorkbenchStore.create(tmp_path / "store", "identity-test", T0)
    registry = KeyRegistry(store, marking=MARK, now_fn=lambda: T0)
    private, public = generate_keypair()
    registry.enroll(
        actor_id="newcomer", actor_kind="HUMAN", public_key_hex=public)
    challenge = manager.issue_challenge("newcomer")
    nonce = challenge["nonce"]
    signature = sign(
        private, manager.challenge_payload("newcomer", nonce))
    manager._sessions["victim"] = Session(
        "victim", "victim", "HUMAN", "key-victim", T0,
        "2026-08-17T13:00:00+00:00")

    with pytest.raises(AuthError, match="cannot evict"):
        manager.authenticate(
            registry, actor_id="newcomer", nonce=nonce,
            signature_hex=signature)
    assert nonce in manager._pending
    assert set(manager._sessions) == {"victim"}

    manager.revoke_session("victim")
    session = manager.authenticate(
        registry, actor_id="newcomer", nonce=nonce,
        signature_hex=signature)
    assert session.actor_id == "newcomer"
    assert nonce not in manager._pending
