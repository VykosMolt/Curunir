"""Signing a report approval, end to end.

An analyst proves it holds its key, signs an approval bound to the report's
exact version, and the signature is recorded only after the act commits. A
service key, a self-approval, a tampered payload, a stale version and an expired
session are all refused, and a rotated key leaves old signatures verifiable.
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

    # The author cannot approve its own report, valid signature or not, and the
    # refusal leaves no signed-action record.
    session_a = _login(ops, registry, sessions, "analyst-a", pem_a)
    with pytest.raises(PermissionError):
        _approve(ops, session_a, pem_a, report_id=report_id, version=version, nonce="a1",
                 apply_fn=lambda ctx: commands.approve_report(ctx, report_id,
                                                             expected_version=version))
    assert not store.records_of("signed_action"), \
        "a refused act must not leave a signed-action record"

    # A second analyst signs the approval, which is applied and recorded.
    session_b = _login(ops, registry, sessions, "analyst-b", pem_b)
    out = _approve(ops, session_b, pem_b, report_id=report_id, version=version, nonce="b1",
                   apply_fn=lambda ctx: commands.approve_report(
                       ctx, report_id, expected_version=version, note="sound"))
    assert out["result"]["status"] == "APPROVED"
    assert store.records_of("signed_action")[-1]["actor_id"] == "analyst-b"
    assert verify_all(store)["all_genuine"]


def test_signed_approval_interrupted_before_commit_is_exactly_one(tmp_path, monkeypatch):
    """A crash between the approval and its signature leaves one outcome.

    The approval took effect once, no signature was invented for it, and a
    retry after restart cannot approve it a second time.
    """
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem_b, pub_b = generate_keypair()
    registry.enroll(actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    session_b = _login(ops, registry, sessions, "analyst-b", pem_b)

    # Die after the command commits but before the signature is appended.
    import curunir_workbench.signed_ops as signed_ops_module

    def _crash(*args, **kwargs):
        raise RuntimeError("process killed before the signed-action append")
    monkeypatch.setattr(signed_ops_module, "commit_action", _crash)
    with pytest.raises(RuntimeError):
        _approve(ops, session_b, pem_b, report_id=report_id, version=version, nonce="b1",
                 apply_fn=lambda ctx: commands.approve_report(
                     ctx, report_id, expected_version=version, note="sound"))

    # On restart the log is what counts.
    reopened = WorkbenchStore(root / "store")
    report = reopened.current_reports()[report_id]
    assert report["status"] in ("APPROVED", "APPROVED_WITH_DISSENT")
    assert not reopened.records_of("signed_action"), (
        "the crash left the act unsigned, which is the safe direction; a "
        "signature must never be invented for a partial act")

    # The target has moved on, so the same signed approval is now stale.
    monkeypatch.undo()
    ops2, registry2, sessions2, authz2 = _ops(reopened, root, clock, actors_path)
    session_b2 = _login(ops2, registry2, sessions2, "analyst-b", pem_b)
    new_version = reopened.current_reports()[report_id]["version"]
    with pytest.raises(PermissionError):
        _approve(ops2, session_b2, pem_b, report_id=report_id, version=version, nonce="b2",
                 current=_vtoken(report_id, new_version),
                 apply_fn=lambda ctx: commands.approve_report(
                     ctx, report_id, expected_version=new_version, note="retry"))
    assert not reopened.records_of("signed_action")


def test_full_mission_survives_signed_approval_crash_recovery_and_restore(tmp_path):
    """A whole mission survives a crash, a backup and a restore with its chain,
    signatures, report state and key history intact, and with no network."""
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem_b, pub_b = generate_keypair()
    registry.enroll(actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    session_b = _login(ops, registry, sessions, "analyst-b", pem_b)
    out = _approve(ops, session_b, pem_b, report_id=report_id, version=version, nonce="b1",
                   apply_fn=lambda ctx: commands.approve_report(
                       ctx, report_id, expected_version=version, note="sound"))
    assert out["result"]["status"] == "APPROVED"
    assert verify_all(store)["all_genuine"]

    # Crash mid-append, recover the torn tail, then back up and restore.
    with (root / "store" / "events.jsonl").open("ab") as handle:
        handle.write(b'{"seq": 99999, "torn')
    recovery = WorkbenchStore.recover_torn_tail(root / "store")
    assert recovery["recovered"]
    recovered = WorkbenchStore(root / "store")
    backup = tmp_path / "backup"
    recovered.export_to(backup)
    restored = WorkbenchStore.import_from(backup, tmp_path / "restored")

    assert restored.verify_chain()["valid"]
    replay = verify_all(restored)  # signatures re-checked from the log alone
    assert replay["all_genuine"] and replay["count"] >= 1
    assert restored.current_reports()[report_id]["status"] in ("APPROVED", "APPROVED_WITH_DISSENT")
    assert any(r["actor_id"] == "analyst-b" for r in restored.records_of("actor_key"))
    assert claim_id in restored.current_claims()


def test_four_eyes_survives_a_crash_that_loses_the_submitted_disposition(tmp_path, monkeypatch):
    """Losing the SUBMITTED record to a crash does not let the submitter approve
    their own report: the version-transition event still names them."""
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    authz = ActorRegistry(actors_path)
    # analyst-a drafts and analyst-b submits.
    ctx_a = _cc(store, root, authz, "analyst-a", clock)
    sections = [{"kind": "key_judgments", "title": "KJ", "sentences": [
        {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
         "basis_refs": [claim_id]}]}]
    report = commands.create_report(ctx_a, title="R", question="?", sections=sections)
    rid = report["report_id"]

    # Crash between the two appends, losing the SUBMITTED record.
    import curunir_workbench.reports as reports_mod

    def _crash_disposition(*args, **kwargs):
        raise RuntimeError("killed before the SUBMITTED disposition append")
    monkeypatch.setattr(reports_mod, "_disposition", _crash_disposition)
    ctx_b = _cc(store, root, authz, "analyst-b", clock)
    with pytest.raises(RuntimeError):
        commands.submit_report(ctx_b, rid, expected_version=1)
    monkeypatch.undo()

    reopened = WorkbenchStore(root / "store")
    assert reopened.current_reports()[rid]["status"] == "IN_REVIEW"
    assert not reopened.report_dispositions(rid)
    ctx_b2 = _cc(reopened, root, authz, "analyst-b", clock)
    with pytest.raises(PermissionError, match="separation of duties"):
        commands.approve_report(ctx_b2, rid, expected_version=2)


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

    with pytest.raises(SignatureRejected):
        _approve(ops, session, pem_b, report_id=report_id, version=version, nonce="t2",
                 current=_vtoken(report_id, version + 5), apply_fn=lambda ctx: None)

    clock.advance(sessions.session_ttl + 1)
    with pytest.raises(SignatureRejected):
        _approve(ops, session, pem_b, report_id=report_id, version=version, nonce="t3",
                 apply_fn=lambda ctx: None)
    assert not store.records_of("signed_action")


def test_actor_kind_mismatch_between_key_and_registry_refused(tmp_path):
    """The enrolled key's actor kind must match the registry's, so a service key
    cannot pass a human-only gate."""
    root, store, clock, claim_id, actors_path = _mission(tmp_path)
    ops, registry, sessions, authz = _ops(store, root, clock, actors_path)
    report_id, version = _report(store, root, authz, clock, claim_id)
    pem, pub = generate_keypair()
    # analyst-b is HUMAN in the workbench registry but enrolled as SERVICE.
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


def test_pending_challenge_flood_evicts_flooders_own_not_the_victim(tmp_path):
    """A challenge flood evicts the flooder's own oldest challenge, never another
    actor's live one."""
    from curunir_identity.sessions import SessionManager, MAX_PENDING_CHALLENGES
    sm = SessionManager(now_fn=lambda: "2026-08-17T12:00:00+00:00")
    victim = sm.issue_challenge("analyst-b")["nonce"]
    for _ in range(MAX_PENDING_CHALLENGES + 50):
        sm.issue_challenge("watch-service")
    assert victim in sm._pending
    assert sm._pending[victim]["actor_id"] == "analyst-b"
    assert len(sm._pending) <= MAX_PENDING_CHALLENGES
