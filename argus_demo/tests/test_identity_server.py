"""V6.7 — the cryptographic-identity HTTP surface (§7, §99 over the wire).

Challenge → sign → authenticate → sign a load-bearing action → apply, all over
HTTP. The client only proves possession of its key; the server resolves the
actor's authority itself. A forged signature is refused.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from argus.source_intelligence.models import digest_id
from curunir_fabric.catalog import seed_starter_catalog
from curunir_fabric.contracts import ManifestationRecord
from curunir_identity import KeyRegistry, generate_keypair, sign, sign_action
from curunir_operational.access import Marking
from curunir_operational.canonical import parse_time
from curunir_semantic.pipeline import SemanticPipeline
from curunir_workbench import commands
from curunir_workbench.auth import ActorRegistry, write_registry
from curunir_workbench.commands import CommandContext
from curunir_workbench.server import create_app
from curunir_workbench.store import WorkbenchStore

from semantic_support import T0

pytestmark = pytest.mark.no_db
MISSION = "http-mission"
MARK = Marking(owning_authority=MISSION, releasability=("PUBLIC",))
ACME = "LEI:ACMELEI000000000001"


class Clock:
    def __init__(self, s): self.t = parse_time(s)
    def now(self): return self.t.isoformat()


def _build(tmp_path):
    root = tmp_path / "mission"
    store = WorkbenchStore.create(root / "store", MISSION, T0)
    seed_starter_catalog(store, recorded_time=T0, actor="t")
    clock = Clock(T0)
    pipeline = SemanticPipeline(store=store, custody_root=root / "custody",
                               actor="t", marking=MARK, now_fn=clock.now)
    body = (b'{"data": {"id": "ACMELEI000000000001", "attributes": {"lei": '
            b'"ACMELEI000000000001", "entity": {"legalName": {"name": "Acme AS"}, '
            b'"jurisdiction": "NO", "status": "ACTIVE", "otherNames": [], '
            b'"legalAddress": {"city": "Oslo", "country": "NO"}}, "registration": '
            b'{"status": "ISSUED", "initialRegistrationDate": "2014-03-02", '
            b'"lastUpdateDate": "2026-08-01"}}}}')
    digest = hashlib.sha256(body).hexdigest()
    p = Path(pipeline.custody_root) / "sha256" / digest[:2] / digest[2:4] / digest
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    store.append("FABRIC_MANIFESTATION_RECORDED", ManifestationRecord(
        manifestation_id=digest_id("m", digest), source_id="gleif",
        connector_id="c", connector_version="1", native_id="lei/x", request_url="lei/x",
        final_url="lei/x", content_sha256=digest, content_store_path=str(p),
        media_type="application/json", temporal_status="LIVE", source_time=None,
        archive_capture_time=None, retrieval_time=T0, http_status=200, redirects=(),
        etag="", last_modified="", truncated=False, retrieval_id=digest_id("r", digest),
        custody_ingestion_id=digest_id("i", digest),
        source_object_id=digest_id("s", digest), execution_id=digest_id("e", digest),
        prior_manifestation_id=None, marking=MARK), recorded_time=clock.now(), actor="t")
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
    ])
    authz = ActorRegistry(actors_path)
    ctx = CommandContext(store=store, root=root,
                         context=authz.context_for_actor("analyst-a"),
                         marking=MARK, now_fn=clock.now)
    sections = [{"kind": "key_judgments", "title": "KJ", "sentences": [
        {"text": "Acme holds an ISSUED registration.", "status": "SUPPORTED",
         "basis_refs": [claim["claim_id"]]}]}]
    report = commands.create_report(ctx, title="R", question="?", sections=sections)
    submitted = commands.submit_report(ctx, report["report_id"], expected_version=1)
    # enroll analyst-b's key (deployment/admin op, not an HTTP action)
    pem_b, pub_b = generate_keypair()
    KeyRegistry(store, marking=MARK, now_fn=clock.now).enroll(
        actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    return root, actors_path, clock, report["report_id"], submitted["version"], pem_b


def test_http_challenge_authenticate_and_signed_approval(tmp_path):
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    app = create_app(root, actors_path, now_fn=clock.now)
    client = TestClient(app)

    # 1. challenge
    ch = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"}, headers={"Authorization": "Bearer tok-b"}).json()
    assert ch["nonce"]
    # 2. the client signs the challenge with its private key
    from curunir_identity.sessions import SessionManager
    challenge_payload = SessionManager.challenge_payload("analyst-b", ch["nonce"])
    auth = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch["nonce"],
        "signature": sign(pem_b, challenge_payload)})
    assert auth.status_code == 200, auth.text
    session_id = auth.json()["session_id"]

    # 3. the client signs the approval, bound to the report version, and applies
    signed = sign_action(pem_b, actor_id="analyst-b", actor_kind="HUMAN",
                         action_type="approve_report", target_kind="workbench_report",
                         target_id=report_id,
                         target_version_token=f"workbench_report:{report_id}@v{version}",
                         mission_id=MISSION, nonce="http-n1", timestamp=clock.now(),
                         command={"disposition": "APPROVED", "note": "sound"})
    resp = client.post(f"/api/commands/reports/{report_id}/approve-signed", json={
        "session_id": session_id, "payload": signed["payload"],
        "signature": signed["signature"], "expected_version": version})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"

    # a forged signature is refused (401)
    ch2 = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"}, headers={"Authorization": "Bearer tok-b"}).json()
    forged = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch2["nonce"], "signature": "00" * 64})
    assert forged.status_code == 401


def _challenge_auth(client, actor_id, pem, token):
    from curunir_identity.sessions import SessionManager
    ch = client.post("/api/auth/challenge", json={"actor_id": actor_id},
                     headers={"Authorization": f"Bearer {token}"}).json()
    return client.post("/api/auth/authenticate", json={
        "actor_id": actor_id, "nonce": ch["nonce"],
        "signature": sign(pem, SessionManager.challenge_payload(actor_id, ch["nonce"]))})


def test_enroll_refuses_cross_actor_key_hijack(tmp_path):
    # review F1 (CRITICAL): a bearer holder must NOT be able to enrol / hijack a
    # key already owned by another actor by resubmitting that actor's PUBLIC key.
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    reg = KeyRegistry(WorkbenchStore(root / "store"))
    pub_b = reg.active_keys_for("analyst-b")[0]["public_key"]
    key_id_b = reg.active_keys_for("analyst-b")[0]["key_id"]

    # analyst-a submits analyst-b's public key → refused, no cross-actor effect
    r = client.post("/api/auth/enroll", json={"public_key_hex": pub_b},
                    headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 409, r.text

    reg2 = KeyRegistry(WorkbenchStore(root / "store"))
    assert reg2.current(key_id_b)["actor_id"] == "analyst-b"      # still owned by b
    assert reg2.current(key_id_b)["status"] == "ACTIVE"           # not retired
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200  # b still signs


def test_two_device_keys_for_one_actor_both_authenticate(tmp_path):
    # review F2 (MAJOR): a second enrolled device must not brick the first.
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    pem2, pub2 = generate_keypair()
    r = client.post("/api/auth/enroll", json={"public_key_hex": pub2},
                    headers={"Authorization": "Bearer tok-b"})
    assert r.status_code == 200, r.text
    # both device keys authenticate; neither locks the other out
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200
    assert _challenge_auth(client, "analyst-b", pem2, "tok-b").status_code == 200
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200  # first still live


def test_challenge_requires_a_bearer(tmp_path):
    # review F5: the challenge endpoint is no longer an anonymous, unbounded path
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    assert client.post("/api/auth/challenge", json={"actor_id": "analyst-b"}).status_code == 401
    assert client.get("/api/auth/time").status_code == 401
