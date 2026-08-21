"""V6.7 historical cryptographic-identity HTTP exploit corpus.

Challenge → sign → authenticate → sign a load-bearing action → apply, all over
HTTP. The client only proves possession of its key; the server resolves the
actor's authority itself. A forged signature is refused.
"""
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


def test_ascii_escaped_surrogate_body_yields_422_not_500(tmp_path):
    # review NEW-1: the JSON-body vector is NOT shut at the transport (the earlier
    # F-W1 premise was wrong). JSON "\uD800" is PURE ASCII on the wire — a
    # browser's well-formed JSON.stringify emits exactly this — json.loads
    # restores the lone surrogate, and FastAPI's default 422 handler would echo it
    # into a response whose UTF-8 render 500s. Unauthenticated, on every POST.
    # The custom validation handler must scrub it to an honest 422, never a 500.
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    for path in ("/api/auth/challenge", "/api/auth/authenticate"):   # required-field models
        r = client.post(path, content=b'{"z":"\\ud800"}',           # 14 ASCII bytes, NO auth
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 422, f"{path} -> {r.status_code}: surrogate echo 500?"


def test_render_safe_neutralizes_a_surrogate_reaching_the_response(tmp_path):
    # review F-W1 (defense in depth): should a lone surrogate reach a render
    # string from STORED data or a non-body channel, render_safe replaces it so
    # building the HTTP response cannot 500 at Starlette's UTF-8 encode. Exercise
    # the ACTUAL server helper, and prove its output survives a real UTF-8 encode.
    from curunir_workbench.server import render_safe
    out = render_safe("unknown target ghost\ud800 tail")
    assert "\ud800" not in out
    assert out.encode("utf-8") == out.encode("utf-8", "strict")  # no longer raises
    assert render_safe(ValueError("bad id \udfff")).encode("utf-8")  # error path too


def test_non_ascii_bearer_token_fails_closed_not_typeerror(tmp_path):
    # review NEW-4 (fix site): Starlette decodes the Authorization header as
    # latin-1, so a bearer token can carry a byte >= 0x80; secrets.compare_digest
    # RAISES TypeError on a non-ASCII str. context_for must fail closed with an
    # AuthError (-> 401), never let a TypeError escape to an unauthenticated 500.
    from curunir_workbench.auth import AuthError
    actors = tmp_path / "actors.json"
    write_registry(actors, [{"token": "tok-a", "actor_id": "a", "roles": ["ANALYST"],
                             "releasability": ["PUBLIC"], "organisation": "m"}])
    reg = ActorRegistry(actors)
    assert reg.context_for("tok-a").actor_id == "a"           # ascii control still works
    for bad in ("caf\xe9", "\xff", "tok\x80abc"):             # latin-1 header bytes >= 0x80
        with pytest.raises(AuthError):
            reg.context_for(bad)


def test_non_ascii_bearer_over_http_is_401_not_500(tmp_path):
    # review NEW-4 (end to end): the same over the wire, unauthenticated, must be
    # a clean 401, never a 500 + per-request stack trace on the whole API surface.
    # httpx (the TestClient's client) refuses to ENCODE a non-ASCII header, so
    # drive the ASGI app directly with the raw latin-1 header bytes uvicorn would
    # hand Starlette in production (the reviewer's exact repro).
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
    # review NEW-A / NEW-B: the validation-error echo must not 500 the whole POST
    # surface, unauthenticated, on a non-UTF-8 body (jsonable_encoder's bytes
    # decode raised first) or a NaN/Infinity float (Starlette renders allow_nan=
    # False). Both must be honest 422s.
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    for path in ("/api/auth/challenge", "/api/auth/authenticate"):
        a = client.post(path, content=b"\xff",                       # NEW-A: non-UTF-8, no auth
                        headers={"Content-Type": "text/plain"})
        assert a.status_code == 422, f"NEW-A {path} -> {a.status_code}"
        b = client.post(path, content=b'{"zz":NaN}',                 # NEW-B: non-finite, no auth
                        headers={"Content-Type": "application/json"})
        assert b.status_code == 422, f"NEW-B {path} -> {b.status_code}"


def test_non_finite_float_saved_view_is_refused_not_committed(tmp_path):
    # review NEW-C (store sink): a non-finite float would serialize to a bare
    # non-RFC-8259 NaN token and poison the append-only log. canonical_line now
    # REFUSES it, so an authenticated ANALYST gets an honest 400 and NOTHING is
    # committed (no poisoned projection, no non-standard JSON in the chain).
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    head_before = WorkbenchStore(root / "store").head()["head_hash"]
    r = client.post("/api/commands/saved-views",
                    content=b'{"title":"t","view_kind":"graph",'
                            b'"definition":{"zoom":NaN},"compartments":[]}',
                    headers={"Authorization": "Bearer tok-a",
                             "Content-Type": "application/json"})
    assert r.status_code == 400, r.text                              # honest rejection, not 500
    assert WorkbenchStore(root / "store").head()["head_hash"] == head_before  # nothing written


def test_signed_payload_with_non_finite_is_refused_not_recorded(tmp_path):
    # review NEW-C (signing sink): a signed_payload carrying a non-finite float
    # must not become a non-repudiation record. The server canonicalizes the
    # received payload to verify the signature; canonical_line now raises on the
    # NaN, verify() catches it as a bad signature -> 401. No non-RFC-8259 bytes
    # can enter a SIGNED_ACTION_RECORDED event.
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
    assert r.status_code == 401, r.text                              # unverifiable, never a poisoned 200
    store = WorkbenchStore(root / "store")
    signed = [e for e in store.records_of("signed_action")] if "signed_action" in store.EVENT_TYPES.values() else []
    assert all("NaN" not in _json.dumps(e) for e in signed)         # no non-finite in any signed record


def test_malformed_report_section_is_400_not_500(tmp_path):
    # review MINOR-2: report `sections` is free-form list[dict[str,Any]]; a wrong
    # scalar type must be an honest 400 for an authenticated ANALYST, not a 500
    # from a later .strip()/.replace()/list() on the wrong type.
    root, actors_path, clock, report_id, version, pem_b = _build(tmp_path)
    client = TestClient(create_app(root, actors_path, now_fn=clock.now),
                        raise_server_exceptions=False)
    bad = [
        [{"kind": "key_judgments", "title": 5, "sentences": []}],                       # numeric title
        [{"kind": "key_judgments", "title": "T", "sentences": 7}],                       # sentences not a list
        [{"kind": "key_judgments", "title": "T",
          "sentences": [{"text": "x", "status": "SUPPORTED", "basis_refs": "no"}]}],     # basis_refs not a list
        [{"kind": "key_judgments", "title": "T",
          "sentences": [{"text": 9, "status": "SUPPORTED"}]}],                           # numeric text
    ]
    for sections in bad:
        r = client.post("/api/commands/reports",
                        json={"title": "T", "question": "Q", "sections": sections, "compartments": []},
                        headers={"Authorization": "Bearer tok-a"})
        assert r.status_code == 400, f"{sections} -> {r.status_code}: {r.text}"


def test_report_edit_with_surrogate_is_400_not_409(tmp_path):
    # review MINOR-1: reports.py _next_version caught bare ValueError -> 409; a
    # malformed-CONTENT error (a lone surrogate canonical_line refuses) must be a
    # 400, not mislabelled a version conflict the client can never win by retrying.
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
                      ensure_ascii=True).encode()          # \ud800 -> ASCII escape; server restores it
    r = client.post(f"/api/commands/reports/{rid}/edit", content=body,
                    headers={"Authorization": "Bearer tok-a", "Content-Type": "application/json"})
    assert r.status_code == 400, f"{r.status_code}: {r.text}"        # 400, not 409


def test_signed_command_wrong_shape_is_400_not_500(tmp_path):
    # self-review of round 18: a signed command that is not an object, or whose
    # acknowledge_dissent is not a list, must be an honest 400 — not an
    # authenticated 500 from the apply lambda's .get()/tuple() (wrong-type class
    # of MINOR-2, on the signed path).
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
    # self-review of round 18: sentence_id / section_id were the 12th/13th field
    # positions the MINOR-2 shape guard initially missed — a non-string id must be
    # a 400, not a 500.
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
    # review N-2: a non-str timestamp in a signed payload has no .replace() in
    # parse_time (AttributeError) — it must be an honest 401 (unparseable
    # timestamp), never an authenticated 500.
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
                         mission_id=MISSION, nonce="ts-n1", timestamp=12345,   # int, not str
                         command={"disposition": "APPROVED"})
    r = client.post(f"/api/commands/reports/{report_id}/approve-signed", json={
        "session_id": sid, "payload": signed["payload"],
        "signature": signed["signature"], "expected_version": version})
    assert r.status_code == 401, f"{r.status_code}: {r.text}"
