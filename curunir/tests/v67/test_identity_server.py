"""Cryptographic identity over HTTP: challenge, sign, authenticate, then sign a
real action and apply it. The client only proves it holds the key; the server
works out what that actor may do. Malformed input never becomes a 500."""
from __future__ import annotations

import hashlib
import json
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
    # Enrol analyst-b's key directly; enrolment is an admin step, not an action.
    pem_b, pub_b = generate_keypair()
    KeyRegistry(store, marking=MARK, now_fn=clock.now).enroll(
        actor_id="analyst-b", actor_kind="HUMAN", public_key_hex=pub_b)
    return root, actors_path, clock, report["report_id"], submitted["version"], pem_b


def test_http_challenge_authenticate_and_signed_approval(tmp_path):
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    app = create_app(root, actors_path, now_fn=clock.now)
    client = TestClient(app)

    # Ask for a challenge.
    ch = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"}, headers={"Authorization": "Bearer tok-b"}).json()
    assert ch["nonce"]
    # Sign it with the private key.
    from curunir_identity.sessions import SessionManager
    challenge_payload = SessionManager.challenge_payload("analyst-b", ch["nonce"])
    auth = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch["nonce"],
        "signature": sign(pem_b, challenge_payload)})
    assert auth.status_code == 200, auth.text
    session_id = auth.json()["session_id"]

    # Sign the approval, bound to this report version, and apply it.
    signed = sign_action(pem_b, actor_id="analyst-b", actor_kind="HUMAN",
                         action_type="approve_report", target_kind="workbench_report",
                         target_id=report_id,
                         target_version_token=f"workbench_report:{report_id}@v{version}",
                         mission_id=MISSION, nonce="http-n1", timestamp=clock.now(),
                         command={"disposition": "APPROVED", "note": "sound"})
    resp = client.post(f"/api/commands/reports/{report_id}/approve-signed", json={
        "session_id": session_id, "payload": signed["payload"],
        # Deliberately wrong and unsigned: only the signed token counts.
        "signature": signed["signature"], "expected_version": version + 99})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "APPROVED"

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
    """Re-submitting someone else's public key does not take their key over."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    reg = KeyRegistry(WorkbenchStore(root / "store"))
    pub_b = reg.active_keys_for("analyst-b")[0]["public_key"]
    key_id_b = reg.active_keys_for("analyst-b")[0]["key_id"]

    r = client.post("/api/auth/enroll", json={"public_key_hex": pub_b},
                    headers={"Authorization": "Bearer tok-a"})
    assert r.status_code == 409, r.text

    reg2 = KeyRegistry(WorkbenchStore(root / "store"))
    assert reg2.current(key_id_b)["actor_id"] == "analyst-b"
    assert reg2.current(key_id_b)["status"] == "ACTIVE"
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200


def test_two_device_keys_for_one_actor_both_authenticate(tmp_path):
    """Enrolling a second device does not lock out the first."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    pem2, pub2 = generate_keypair()
    r = client.post("/api/auth/enroll", json={"public_key_hex": pub2},
                    headers={"Authorization": "Bearer tok-b"})
    assert r.status_code == 200, r.text
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200
    assert _challenge_auth(client, "analyst-b", pem2, "tok-b").status_code == 200
    assert _challenge_auth(client, "analyst-b", pem_b, "tok-b").status_code == 200


def test_challenge_requires_a_bearer(tmp_path):
    """The challenge endpoint is not an anonymous, unbounded path."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now))
    assert client.post("/api/auth/challenge", json={"actor_id": "analyst-b"}).status_code == 401
    assert client.get("/api/auth/time").status_code == 401


def test_ascii_escaped_surrogate_body_yields_422_not_500(tmp_path):
    """A body whose escaped \\ud800 decodes to a lone surrogate is a 422.

    The escape is plain ASCII on the wire, so nothing rejects it earlier; the
    validation handler has to scrub it before it reaches the response.
    """
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    for path in ("/api/auth/challenge", "/api/auth/authenticate"):
        r = client.post(path, content=b'{"z":"\\ud800"}',  # ASCII bytes, no auth
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 422, f"{path} -> {r.status_code}"


def test_render_safe_neutralizes_a_surrogate_reaching_the_response(tmp_path):
    """render_safe replaces a lone surrogate, so encoding the response cannot
    fail however the surrogate reached the string."""
    from curunir_workbench.server import render_safe
    out = render_safe("unknown target ghost\ud800 tail")
    assert "\ud800" not in out
    assert out.encode("utf-8") == out.encode("utf-8", "strict")
    assert render_safe(ValueError("bad id \udfff")).encode("utf-8")


def test_non_ascii_bearer_token_fails_closed_not_typeerror(tmp_path):
    """A bearer token with a non-ASCII character raises AuthError.

    Headers arrive decoded as latin-1, and compare_digest raises TypeError on a
    non-ASCII string, so the check has to refuse before it gets there.
    """
    from curunir_workbench.auth import AuthError
    actors = tmp_path / "actors.json"
    write_registry(actors, [{"token": "tok-a", "actor_id": "a", "roles": ["ANALYST"],
                             "releasability": ["PUBLIC"], "organisation": "m"}])
    reg = ActorRegistry(actors)
    assert reg.context_for("tok-a").actor_id == "a"  # an ASCII token still works
    for bad in ("caf\xe9", "\xff", "tok\x80abc"):
        with pytest.raises(AuthError):
            reg.context_for(bad)


def test_non_ascii_bearer_over_http_is_401_not_500(tmp_path):
    """The same token over the wire is a clean 401.

    The test client refuses to encode a non-ASCII header, so the app is driven
    directly with the raw bytes a server would hand it.
    """
    import asyncio
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    app = create_app(root, actors_path, now_fn=clock.now)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "GET", "path": "/api/session",
             "raw_path": b"/api/session", "query_string": b"", "root_path": "",
             "scheme": "http", "headers": [(b"authorization", b"Bearer \xe9\xff")],
             "client": ("test", 1), "server": ("test", 80)}
    out = []
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    async def send(m): out.append(m)
    asyncio.run(app(scope, receive, send))
    status = next(m for m in out if m["type"] == "http.response.start")["status"]
    assert status == 401


def test_non_utf8_and_nan_bodies_yield_422_not_500(tmp_path):
    """A body that is not UTF-8, and one holding NaN, are both plain 422s."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    for path in ("/api/auth/challenge", "/api/auth/authenticate"):
        a = client.post(path, content=b"\xff",  # not UTF-8, and unauthenticated
                        headers={"Content-Type": "text/plain"})
        assert a.status_code == 422, f"{path} -> {a.status_code}"
        b = client.post(path, content=b'{"zz":NaN}',  # a non-finite float
                        headers={"Content-Type": "application/json"})
        assert b.status_code == 422, f"{path} -> {b.status_code}"


def test_non_finite_float_saved_view_is_refused_not_committed(tmp_path):
    """A NaN in a saved view is a 400 and nothing reaches the log.

    A bare NaN token is not valid JSON, so it must never enter the chain.
    """
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    head_before = WorkbenchStore(root / "store").head()["head_hash"]
    r = client.post("/api/commands/saved-views",
                    content=b'{"title":"t","view_kind":"graph",'
                            b'"definition":{"zoom":NaN},"compartments":[]}',
                    headers={"Authorization": "Bearer tok-a",
                             "Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert WorkbenchStore(root / "store").head()["head_hash"] == head_before


def test_signed_payload_with_non_finite_is_refused_not_recorded(tmp_path):
    """A signed payload holding a NaN cannot become a signed-action record.

    Canonicalizing it to check the signature raises, which reads as an
    unverifiable signature.
    """
    import json as _json
    from curunir_identity.sessions import SessionManager
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    ch = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"},
                     headers={"Authorization": "Bearer tok-b"}).json()
    auth = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch["nonce"],
        "signature": sign(pem_b, SessionManager.challenge_payload("analyst-b", ch["nonce"]))})
    session_id = auth.json()["session_id"]
    body = ('{"session_id":"%s","signature":"%s","expected_version":%d,"payload":'
            '{"actor_id":"analyst-b","actor_kind":"HUMAN","action_type":"approve_report",'
            '"target_kind":"workbench_report","target_id":"%s",'
            '"target_version_token":"workbench_report:%s@v%d","mission_id":"%s",'
            '"nonce":"nan-n1","timestamp":"%s","command":{"disposition":"APPROVED",'
            '"score":NaN}}}' % (session_id, "00" * 64, version, report_id, report_id,
                                version, MISSION, clock.now())).encode()
    r = client.post(f"/api/commands/reports/{report_id}/approve-signed",
                    content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401, r.text
    store = WorkbenchStore(root / "store")
    signed = [e for e in store.records_of("signed_action")] if "signed_action" in store.EVENT_TYPES.values() else []
    assert all("NaN" not in _json.dumps(e) for e in signed)


def test_malformed_report_section_is_400_not_500(tmp_path):
    """Report sections are free-form, so a wrong field type is a 400 rather than
    an error later on, deeper in the code."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    bad = [
        [{"kind": "key_judgments", "title": 5, "sentences": []}],  # a numeric title
        [{"kind": "key_judgments", "title": "T", "sentences": 7}],  # sentences is not a list
        [{"kind": "key_judgments", "title": "T",
          "sentences": [{"text": "x", "status": "SUPPORTED", "basis_refs": "no"}]}],  # basis_refs is not a list
        [{"kind": "key_judgments", "title": "T",
          "sentences": [{"text": 9, "status": "SUPPORTED"}]}],  # numeric sentence text
    ]
    for sections in bad:
        r = client.post("/api/commands/reports",
                        json={"title": "T", "question": "Q", "sections": sections, "compartments": []},
                        headers={"Authorization": "Bearer tok-a"})
        assert r.status_code == 400, f"{sections} -> {r.status_code}: {r.text}"


def test_report_edit_with_surrogate_is_400_not_409(tmp_path):
    """Bad content is a 400, not a version conflict the client can never win by
    retrying."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    good = [{"kind": "key_judgments", "title": "T",
             "sentences": [{"text": "ok", "status": "SUPPORTED", "basis_refs": []}]}]
    created = client.post("/api/commands/reports",
                          json={"title": "R2", "question": "Q", "sections": good, "compartments": []},
                          headers={"Authorization": "Bearer tok-a"})
    assert created.status_code == 200, created.text
    rid = created.json()["report_id"]
    bad = [{"kind": "key_judgments", "title": "T",
            "sentences": [{"text": "bad \ud800 tail", "status": "SUPPORTED", "basis_refs": []}]}]
    body = json.dumps({"expected_version": 1, "change_note": "x", "sections": bad},
                      ensure_ascii=True).encode()  # sent as an ASCII escape
    r = client.post(f"/api/commands/reports/{rid}/edit", content=body,
                    headers={"Authorization": "Bearer tok-a", "Content-Type": "application/json"})
    assert r.status_code == 400, f"{r.status_code}: {r.text}"


def test_signed_command_wrong_shape_is_400_not_500(tmp_path):
    """A signed command with a wrongly typed field is a 400."""
    from curunir_identity.sessions import SessionManager
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    ch = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"},
                     headers={"Authorization": "Bearer tok-b"}).json()
    auth = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch["nonce"],
        "signature": sign(pem_b, SessionManager.challenge_payload("analyst-b", ch["nonce"]))})
    sid = auth.json()["session_id"]
    for cmd in ({"disposition": "APPROVED", "acknowledge_dissent": 5},
                {"disposition": "APPROVED", "acknowledge_dissent": "x"}):
        signed = sign_action(pem_b, actor_id="analyst-b", actor_kind="HUMAN",
                             action_type="approve_report", target_kind="workbench_report",
                             target_id=report_id,
                             target_version_token=f"workbench_report:{report_id}@v{version}",
                             mission_id=MISSION, nonce=f"n-{cmd['acknowledge_dissent']}",
                             timestamp=clock.now(), command=cmd)
        r = client.post(f"/api/commands/reports/{report_id}/approve-signed", json={
            "session_id": sid, "payload": signed["payload"],
            "signature": signed["signature"], "expected_version": version})
        assert r.status_code == 400, f"acknowledge_dissent={cmd['acknowledge_dissent']!r} -> {r.status_code}"


def test_report_sentence_id_wrong_type_is_400_not_500(tmp_path):
    """A section id or sentence id that is not a string is a 400."""
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    for sections in (
        [{"kind": "key_judgments", "title": "T", "section_id": ["x"],
          "sentences": [{"text": "ok", "status": "SUPPORTED"}]}],
        [{"kind": "key_judgments", "title": "T",
          "sentences": [{"text": "ok", "status": "SUPPORTED", "sentence_id": {"k": "v"}}]}],
    ):
        r = client.post("/api/commands/reports",
                        json={"title": "T", "question": "Q", "sections": sections, "compartments": []},
                        headers={"Authorization": "Bearer tok-a"})
        assert r.status_code == 400, f"{sections} -> {r.status_code}"


def test_signed_timestamp_non_string_is_401_not_500(tmp_path):
    """A signed payload whose timestamp is not a string reads as unparseable, so
    the request is refused."""
    from curunir_identity.sessions import SessionManager
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    ch = client.post("/api/auth/challenge", json={"actor_id": "analyst-b"},
                     headers={"Authorization": "Bearer tok-b"}).json()
    auth = client.post("/api/auth/authenticate", json={
        "actor_id": "analyst-b", "nonce": ch["nonce"],
        "signature": sign(pem_b, SessionManager.challenge_payload("analyst-b", ch["nonce"]))})
    sid = auth.json()["session_id"]
    signed = sign_action(pem_b, actor_id="analyst-b", actor_kind="HUMAN",
                         action_type="approve_report", target_kind="workbench_report",
                         target_id=report_id,
                         target_version_token=f"workbench_report:{report_id}@v{version}",
                         mission_id=MISSION, nonce="ts-n1", timestamp=12345,  # an int, not a string
                         command={"disposition": "APPROVED"})
    r = client.post(f"/api/commands/reports/{report_id}/approve-signed", json={
        "session_id": sid, "payload": signed["payload"],
        "signature": signed["signature"], "expected_version": version})
    assert r.status_code == 401, f"{r.status_code}: {r.text}"
