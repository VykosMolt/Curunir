"""V6.7 §99 demo — cryptographic actor identity end-to-end on a load-bearing
workbench action (report approval).

Actor A authenticates by key possession, signs an approval bound to the report's
exact target and version, and it is applied and recorded only after the act
commits. A service actor's signature is refused by the human-only gate; the
author cannot self-approve (four-eyes); an expired session, a tampered payload,
and a stale target version are all refused; after B rotates its key, new actions
verify and the historical signed action still verifies on replay.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

from argus.source_intelligence.models import digest_id
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import ManifestationRecord
from curunir_identity import (KeyRegistry, SessionManager, SignatureRejected,
                              generate_keypair, sign, sign_action, verify_all)
from curunir_identity.sessions import AuthError
from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time
from curunir_semantic.pipeline import SemanticPipeline
from curunir_workbench import commands
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.commands import CommandContext
from curunir_workbench.signed_ops import SignedOperations
from curunir_workbench.store import WorkbenchStore

from semantic_support import T0

pytestmark = pytest.mark.no_db
MISSION = "workbench-mission"
MARK = Marking(owning_authority=MISSION, releasability=("PUBLIC",))
ACME = "LEI:ACMELEI000000000001"


class Clock:
    def __init__(self, start): self.t = parse_time(start)
    def now(self): return self.t.isoformat()
    def advance(self, seconds): self.t += timedelta(seconds=seconds)


def _gleif() -> bytes:
    return (b'{"data": {"id": "ACMELEI000000000001", "attributes": {"lei": '
            b'"ACMELEI000000000001", "entity": {"legalName": {"name": "Acme AS"}, '
            b'"jurisdiction": "NO", "status": "ACTIVE", "otherNames": [], '
            b'"legalAddress": {"city": "Oslo", "country": "NO"}}, "registration": '
            b'{"status": "ISSUED", "initialRegistrationDate": "2014-03-02", '
            b'"lastUpdateDate": "2026-08-01"}}}}')


def _mission(tmp_path):
    root = tmp_path / "mission"
    store = WorkbenchStore.create(root / "store", MISSION, T0)
    seed_starter_catalog(store, recorded_time=T0, actor="t")
    clock = Clock(T0)
    pipeline = SemanticPipeline(store=store, custody_root=root / "custody",
                               actor="t", marking=MARK, now_fn=clock.now)
    body = _gleif()
    digest = hashlib.sha256(body).hexdigest()
    path = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("manifestation", "acme", digest), source_id="gleif",
        connector_id="gleif-test", connector_version="1.0", native_id="lei/ACME",
        request_url="lei/ACME", final_url="lei/ACME", content_sha256=digest,
        content_store_path=str(path), media_type="application/json",
        temporal_status="LIVE", source_time=None, archive_capture_time=None,
        retrieval_time=T0, http_status=200, redirects=(), etag="", last_modified="",
        truncated=False, retrieval_id=digest_id("retrieval", digest),
        custody_ingestion_id=digest_id("ingestion", digest),
        source_object_id=digest_id("source-object", digest),
        execution_id=digest_id("execution", "acme"), prior_manifestation_id=None,
        marking=MARK), recorded_time=clock.now(), actor="t")
    pipeline.process_new_evidence()
    claim = next(c for c in store.current_claims().values()
                 if c["subject_ref"] == ACME and c["predicate"] == "entity_status")
    actors_path = root / "actors.json"
    write_registry(actors_path, [
        {"token": "tok-a", "actor_id": "analyst-a", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "compartments": [], "releasability": ["PUBLIC"],
         "organisation": MISSION, "enabled": True},
        {"token": "tok-b", "actor_id": "analyst-b", "actor_kind": "HUMAN",
         "roles": ["SUPERVISOR"], "compartments": [], "releasability": ["PUBLIC"],
         "organisation": MISSION, "enabled": True},
        {"token": "tok-svc", "actor_id": "watch-service", "actor_kind": "SERVICE",
         "roles": ["OBSERVER"], "compartments": [], "releasability": ["PUBLIC"],
         "organisation": MISSION, "enabled": True},
    ])
    return root, store, clock, claim["claim_id"], actors_path


def _cc(store, root, authz, actor_id, clock):
    return CommandContext(store=store, root=root,
                          context=authz.context_for_actor(actor_id),
                          marking=MARK, now_fn=clock.now)


def _report(store, root, authz, clock, claim_id):
    ctx = _cc(store, root, authz, "analyst-a", clock)
    sections = [{"kind": "key_judgments", "title": "KJ", "sentences": [
        {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
         "basis_refs": [claim_id]}]}]
    report = commands.create_report(ctx, title="Acme standing", question="?",
                                    sections=sections)
    submitted = commands.submit_report(ctx, report["report_id"], expected_version=1)
    return report["report_id"], submitted["version"]


def _ops(store, root, clock, actors_path):
    registry = KeyRegistry(store, marking=MARK, now_fn=clock.now)
    sessions = SessionManager(now_fn=clock.now)
    authz = ActorRegistry(actors_path)
    ops = SignedOperations(store=store, root=root, registry=registry,
                           sessions=sessions, authz=authz, now_fn=clock.now,
                           mission_id=MISSION)
    return ops, registry, sessions, authz


def _login(ops, registry, sessions, actor_id, pem):
    ch = sessions.issue_challenge(actor_id)
    sig = sign(pem, sessions.challenge_payload(actor_id, ch["nonce"]))
    return ops.authenticate(actor_id=actor_id, nonce=ch["nonce"], signature_hex=sig)


def _vtoken(report_id, version):
    return f"workbench_report:{report_id}@v{version}"


def _approve(ops, session, pem, *, report_id, version, nonce, current=None, apply_fn):
    signed = sign_action(pem, actor_id=session.actor_id, actor_kind=session.actor_kind,
                         action_type="approve_report", target_kind="workbench_report",
                         target_id=report_id, target_version_token=_vtoken(report_id, version),
                         mission_id=MISSION, nonce=nonce, timestamp=ops.now_fn(),
                         command={"disposition": "APPROVED", "note": "sound"})
    return ops.apply_signed(
        session_id=session.session_id, payload=signed["payload"],
        signature_hex=signed["signature"], action_type="approve_report",
        target_kind="workbench_report", target_id=report_id,
        current_version_token=current or _vtoken(report_id, version),
        target_marking=MARK, apply=apply_fn)


def test_signed_report_approval_end_to_end(tmp_path):
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem_a, pub_a = generate_keypair()
    registry.enroll(actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=pub_a)
    pem_b, pub_b = generate_keypair()
    registry.enroll(actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)

    # four-eyes: the author (A) cannot approve its own report, even with a valid
    # signature — and the refused act mints no signed-action record
    session_a = _login(ops, registry, sessions, "analyst-a", pem_a)
    with pytest.raises(PermissionError):
        _approve(ops, session_a, pem_a, report_id=report_id, version=version, nonce="a1",
                 apply_fn=lambda ctx: commands.approve_report(ctx, report_id,
                                                             expected_version=version))
    assert not store.records_of("signed_action"), \
        "a refused act must not leave a signed-action record"

    # a different human (B) signs the approval → applied and recorded
    session_b = _login(ops, registry, sessions, "analyst-b", pem_b)
    out = _approve(ops, session_b, pem_b, report_id=report_id, version=version, nonce="b1",
                   apply_fn=lambda ctx: commands.approve_report(
                       ctx, report_id, expected_version=version, note="sound"))
    assert out["result"]["status"] == "APPROVED"
    assert store.records_of("signed_action")[-1]["actor_id"] == "analyst-b"
    assert verify_all(store)["all_genuine"]


def test_service_identity_cannot_satisfy_human_only_approval(tmp_path):
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    svc_pem, svc_pub = generate_keypair()
    registry.enroll(actor_id="watch-service", actor_kind="SERVICE", public_key_hex=svc_pub)
    session = _login(ops, registry, sessions, "watch-service", svc_pem)
    with pytest.raises((ValueError, PermissionError)):
        _approve(ops, session, svc_pem, report_id=report_id, version=version, nonce="s1",
                 apply_fn=lambda ctx: commands.approve_report(ctx, report_id,
                                                             expected_version=version))
    assert not [r for r in store.records_of("workbench_report_disposition")
                if r["disposition"] == "APPROVED"]


def test_expired_session_and_tamper_and_stale_version_refused(tmp_path):
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem_b, pub_b = generate_keypair()
    registry.enroll(actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    session = _login(ops, registry, sessions, "analyst-b", pem_b)

    # tampered payload
    signed = sign_action(pem_b, actor_id="analyst-b", actor_kind="HUMAN",
                        action_type="approve_report", target_kind="workbench_report",
                        target_id=report_id, target_version_token=_vtoken(report_id, version),
                        mission_id=MISSION, nonce="t1", timestamp=ops.now_fn(),
                        command={"disposition": "APPROVED"})
    tampered = {**signed["payload"], "command": {"disposition": "REJECTED"}}
    with pytest.raises(SignatureRejected):
        ops.apply_signed(session_id=session.session_id, payload=tampered,
                         signature_hex=signed["signature"], action_type="approve_report",
                         target_kind="workbench_report", target_id=report_id,
                         current_version_token=_vtoken(report_id, version),
                         target_marking=MARK, apply=lambda ctx: None)

    # stale target version
    with pytest.raises(SignatureRejected):
        _approve(ops, session, pem_b, report_id=report_id, version=version, nonce="t2",
                 current=_vtoken(report_id, version + 5), apply_fn=lambda ctx: None)

    # expired session
    clock.advance(sessions.session_ttl + 1)
    with pytest.raises(SignatureRejected):
        _approve(ops, session, pem_b, report_id=report_id, version=version, nonce="t3",
                 apply_fn=lambda ctx: None)
    assert not store.records_of("signed_action")


def test_actor_kind_mismatch_between_key_and_registry_refused(tmp_path):
    # the enrolled key kind and the authz-registry kind must agree, so a SERVICE
    # key listed HUMAN (or vice versa) cannot slip past a kind-gated command
    # with a mis-attributed record (adversarial-review minor finding)
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem, pub = generate_keypair()
    # analyst-b is HUMAN in the workbench registry, but enrolled as SERVICE here
    registry.enroll(actor_id="analyst-b", actor_kind="SERVICE", public_key_hex=pub)
    session = _login(ops, registry, sessions, "analyst-b", pem)
    with pytest.raises(SignatureRejected) as e:
        _approve(ops, session, pem, report_id=report_id, version=version, nonce="mm1",
                 apply_fn=lambda ctx: commands.approve_report(ctx, report_id,
                                                             expected_version=version))
    assert e.value.status == "ACTOR_KIND_MISMATCH"


def test_key_rotation_preserves_historical_verification(tmp_path):
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem_b, pub_b = generate_keypair()
    registry.enroll(actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    session = _login(ops, registry, sessions, "analyst-b", pem_b)
    _approve(ops, session, pem_b, report_id=report_id, version=version, nonce="r1",
             apply_fn=lambda ctx: commands.approve_report(ctx, report_id,
                                                         expected_version=version))
    clock.advance(120)
    new_pem, new_pub = generate_keypair()
    registry.rotate(actor_id="analyst-b", new_public_key_hex=new_pub)
    assert verify_all(store)["all_genuine"], "history stays verifiable after rotation"
    with pytest.raises(AuthError):
        sessions.resolve(registry, session.session_id)
    assert _login(ops, registry, sessions, "analyst-b", new_pem).actor_id == "analyst-b"
