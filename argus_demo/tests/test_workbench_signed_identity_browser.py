"""Browser E2E for the cryptographic-identity LAST MILE (V6.7 §2): the real
Chromium workbench performs a load-bearing report approval whose attribution is
a genuine Ed25519 signature by the analyst's NON-EXTRACTABLE in-browser device
key, bound to the exact report+version — not a bearer assertion.

Unit-level signature semantics (transplant/stale/replay/rotation/revocation/
historical) are already locked in test_identity_crypto.py at the verify_action
layer. This file proves the browser-to-signature-binding end to end: that the
real WebCrypto path the operator uses is accepted, recorded for replay, and that
the HTTP surface enforces the binding against real browser-signed attacks.
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

try:
    from playwright.sync_api import expect, sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

from curunir_identity.replay import GENUINE, verify_all
from curunir_workbench.auth import write_registry
from curunir_workbench.server import create_app
from curunir_workbench.store import WorkbenchStore

from semantic_support import clock
from workbench_support import make_workbench, seed_mission

pytestmark = [pytest.mark.no_db,
              pytest.mark.skipif(not HAVE_PLAYWRIGHT, reason="playwright not installed")]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _api(base, tok, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import uvicorn
    tmp_path = tmp_path_factory.mktemp("wb-signed")
    pipeline, ctx = make_workbench(tmp_path)
    seeded = seed_mission(pipeline, ctx)
    actors = tmp_path / "actors.json"
    write_registry(actors, [
        {"token": "tok-author", "actor_id": "analyst-a", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "compartments": ["SPECIAL"],
         "releasability": ["PUBLIC"], "organisation": "wb"},
        {"token": "tok-approver", "actor_id": "analyst-b", "actor_kind": "HUMAN",
         "roles": ["ANALYST"], "releasability": ["PUBLIC"], "organisation": "wb"},
    ])
    app = create_app(tmp_path, actors, now_fn=clock(start_minute=600))
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"base": f"http://127.0.0.1:{port}", "root": tmp_path,
           "claim_id": seeded["status_claim"]["claim_id"]}
    server.should_exit = True
    thread.join(timeout=5)


def _author_report_in_review(base, claim_id, title):
    """Author a report as analyst-a, bind a SUPPORTED sentence to a real claim,
    and submit it — returns (report_id, version) in IN_REVIEW."""
    st, rep = _api(base, "tok-author", "POST", "/api/commands/reports",
                   {"title": title, "question": "Is Acme viable?", "compartments": []})
    assert st == 200, rep
    rid = rep["report_id"]
    st, r2 = _api(base, "tok-author", "POST", f"/api/commands/reports/{rid}/edit",
                  {"expected_version": rep["version"],
                   "sections": [{"kind": "current_situation", "title": "Situation",
                                 "sentences": [{"text": "Acme Industri AS holds an ISSUED GLEIF registration.",
                                                "status": "SUPPORTED",
                                                "basis_refs": [claim_id]}]}]})
    assert st == 200, r2
    st, r3 = _api(base, "tok-author", "POST", f"/api/commands/reports/{rid}/submit",
                  {"expected_version": r2["version"]})
    assert st == 200, r3
    return rid, r3["version"]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _login(page, base, tok):
    page.goto(base)
    page.fill("#token", tok)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#shell:not(.hidden)")


# ---- 1. the real operator path: signed approval, recorded for replay --------

def test_browser_signed_approval_is_genuine_and_four_eyes(browser, env):
    base, root = env["base"], env["root"]
    rid, submitted_version = _author_report_in_review(base, env["claim_id"], "Signed dossier")

    # analyst-b (the approver — NOT the author) approves through the real UI,
    # which routes through WebCrypto Ed25519 signing.
    page = browser.new_page()
    _login(page, base, "tok-approver")
    page.goto(f"{base}/#/reports/{rid}")
    expect(page.locator("main h1")).to_contain_text("IN_REVIEW")
    page.get_by_role("button", name="Approve (validated, human act)").click()
    expect(page.locator("main h1")).to_contain_text("APPROVED")

    # the private key never leaves the browser: pkcs8 export must be refused
    exportable = page.evaluate(
        """async () => {
            const req = indexedDB.open('curunir-identity', 1);
            const db = await new Promise((res, rej) => { req.onsuccess=()=>res(req.result); req.onerror=()=>rej(req.error); });
            const rec = await new Promise((res, rej) => { const t=db.transaction('keys','readonly').objectStore('keys').get('analyst-b'); t.onsuccess=()=>res(t.result); t.onerror=()=>rej(t.error); });
            try { await crypto.subtle.exportKey('pkcs8', rec.privateKey); return true; }
            catch { return false; }
        }""")
    assert exportable is False
    page.close()

    # the attribution survives to the immutable log and replays GENUINE
    store = WorkbenchStore(root / "store")
    signed = [r for r in store.records_of("signed_action")
              if r["target_id"] == rid and r["action_type"] == "approve_report"]
    assert len(signed) == 1, signed
    rec = signed[0]
    assert rec["actor_id"] == "analyst-b"          # four-eyes: approver, not author
    assert rec["actor_kind"] == "HUMAN"
    assert rec["target_version_token"] == f"workbench_report:{rid}@v{submitted_version}"
    report = report_of(store, rid)
    assert report["status"] in ("APPROVED", "APPROVED_WITH_DISSENT")
    assert report["author"] == "analyst-a"          # author distinct from signer

    result = verify_all(store)
    assert result["all_genuine"], result
    assert any(v["verdict"] == GENUINE and v["actor_id"] == "analyst-b"
               for v in result["verdicts"])


def report_of(store, rid):
    return store.current_reports()[rid]


# ---- 2. the wire enforces the binding against real browser-signed attacks ----

# a page-side signer with controllable overrides, using the REAL WebCrypto key
# and the shipped canonical serializer, exercised against the live server.
_SIGNER = r"""
async ({reportId, version, actorId, actorKind, missionId, overrides, tamper, postTo}) => {
  const { canonicalBytes } = await import('/static/js/canonical.js');
  const bearer = sessionStorage.getItem('curunir-token');
  const jpost = async (path, body, useBearer) => {
    const h = {'Content-Type':'application/json'};
    if (useBearer) h['Authorization'] = 'Bearer ' + bearer;
    const r = await fetch(path, {method:'POST', headers:h, body:JSON.stringify(body)});
    let b = null; try { b = await r.json(); } catch {}
    return {status: r.status, body: b};
  };
  // fresh non-extractable key, enrolled (bearer bootstrap), then authenticate
  const kp = await crypto.subtle.generateKey({name:'Ed25519'}, false, ['sign','verify']);
  const rawPub = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  const hex = (u8)=>Array.from(u8).map(b=>b.toString(16).padStart(2,'0')).join('');
  await jpost('/api/auth/enroll', {public_key_hex: hex(rawPub)}, true);
  const ch = (await jpost('/api/auth/challenge', {actor_id: actorId}, false)).body;
  const chSig = hex(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, kp.privateKey,
      canonicalBytes({purpose:'curunir-authenticate', actor_id: actorId, nonce: ch.nonce}))));
  const auth = (await jpost('/api/auth/authenticate', {actor_id: actorId, nonce: ch.nonce, signature: chSig}, false)).body;
  const now = (await (await fetch('/api/auth/time')).json()).now;
  let payload = {
    actor_id: actorId, actor_kind: actorKind, action_type: 'approve_report',
    target_kind: 'workbench_report', target_id: reportId,
    target_version_token: `workbench_report:${reportId}@v${version}`,
    mission_id: missionId, nonce: crypto.randomUUID(), timestamp: now,
    command: {note: '', acknowledge_dissent: []},
  };
  Object.assign(payload, overrides || {});
  let signature = hex(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, kp.privateKey, canonicalBytes(payload))));
  if (tamper) signature = (signature.slice(0,-2) + (signature.slice(-2)==='00'?'11':'00'));
  const target = postTo || reportId;
  return jpost(`/api/commands/reports/${target}/approve-signed`,
               {session_id: auth.session_id, payload, signature, expected_version: version}, false);
}
"""


@pytest.mark.parametrize("case,overrides,tamper,expect_status", [
    ("wrong_actor", {"actor_id": "analyst-a"}, False, "WRONG_ACTOR"),
    ("transplant", {"target_id": "rep-BOGUS"}, False, "WRONG_TARGET"),
    ("stale_version", {"target_version_token": "workbench_report:X@v999"}, False, "STALE_VERSION"),
    ("tampered_signature", {}, True, "INVALID_SIGNATURE"),
])
def test_wire_rejects_browser_signed_attacks(browser, env, case, overrides, tamper, expect_status):
    base, root = env["base"], env["root"]
    rid, version = _author_report_in_review(base, env["claim_id"], f"Neg {case}")
    page = browser.new_page()
    _login(page, base, "tok-approver")
    session = page.evaluate("async () => (await (await fetch('/api/session', {headers:{Authorization:'Bearer '+sessionStorage.getItem('curunir-token')}})).json())")
    out = page.evaluate(_SIGNER, {"reportId": rid, "version": version,
                                  "actorId": "analyst-b", "actorKind": "HUMAN",
                                  "missionId": session["mission_id"],
                                  "overrides": overrides, "tamper": tamper, "postTo": rid})
    assert out["status"] == 401, out
    assert expect_status in json.dumps(out["body"]), out
    page.close()
    # the report was NOT approved by any refused attack
    store = WorkbenchStore(root / "store")
    assert report_of(store, rid)["status"] == "IN_REVIEW"
    assert not [r for r in store.records_of("signed_action") if r["target_id"] == rid]


# ---- 3. enrollment binds to the bearer-authenticated actor ------------------

def test_enrollment_binds_to_bearer_actor_not_client_claim(browser, env):
    base = env["base"]
    page = browser.new_page()
    _login(page, base, "tok-approver")
    # the enroll body carries NO actor id; the server binds to the bearer actor.
    res = page.evaluate(
        """async () => {
            const kp = await crypto.subtle.generateKey({name:'Ed25519'}, false, ['sign','verify']);
            const raw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
            const hex = Array.from(raw).map(b=>b.toString(16).padStart(2,'0')).join('');
            const r = await fetch('/api/auth/enroll', {method:'POST',
                headers:{'Content-Type':'application/json','Authorization':'Bearer '+sessionStorage.getItem('curunir-token')},
                body: JSON.stringify({public_key_hex: hex, actor_id: 'analyst-a'})});
            return r.json();
        }""")
    assert res["actor_id"] == "analyst-b"   # bound to bearer, not the injected claim
    page.close()


# ---- 4. a revoked key kills the signed-action wire path ----------------------

def test_revoked_key_blocks_signed_action(browser, env):
    base, root = env["base"], env["root"]
    rid, version = _author_report_in_review(base, env["claim_id"], "Revoke case")
    page = browser.new_page()
    _login(page, base, "tok-approver")
    session = page.evaluate("async () => (await (await fetch('/api/session', {headers:{Authorization:'Bearer '+sessionStorage.getItem('curunir-token')}})).json())")

    # step 1: enroll + authenticate, PERSIST the device key in IndexedDB so a
    # later evaluate can sign with the SAME key against the SAME session.
    step1 = page.evaluate(r"""
    async () => {
      const { canonicalBytes } = await import('/static/js/canonical.js');
      const bearer = sessionStorage.getItem('curunir-token');
      const hex = (u8)=>Array.from(u8).map(b=>b.toString(16).padStart(2,'0')).join('');
      const jpost = async (path, body, useBearer) => {
        const h={'Content-Type':'application/json'}; if (useBearer) h['Authorization']='Bearer '+bearer;
        const r = await fetch(path,{method:'POST',headers:h,body:JSON.stringify(body)}); return {status:r.status, body: await r.json().catch(()=>null)};
      };
      const kp = await crypto.subtle.generateKey({name:'Ed25519'}, false, ['sign','verify']);
      const raw = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
      const enroll = (await jpost('/api/auth/enroll', {public_key_hex: hex(raw)}, true)).body;
      // stash the CryptoKey (non-extractable, structured-cloneable) for reuse
      const db = await new Promise((res,rej)=>{const q=indexedDB.open('revoke-test',1);q.onupgradeneeded=()=>q.result.createObjectStore('k');q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);});
      await new Promise((res,rej)=>{const t=db.transaction('k','readwrite').objectStore('k').put(kp.privateKey,'pk');t.onsuccess=()=>res();t.onerror=()=>rej(t.error);});
      const ch = (await jpost('/api/auth/challenge', {actor_id:'analyst-b'}, false)).body;
      const chSig = hex(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, kp.privateKey,
          canonicalBytes({purpose:'curunir-authenticate', actor_id:'analyst-b', nonce: ch.nonce}))));
      const auth = (await jpost('/api/auth/authenticate', {actor_id:'analyst-b', nonce: ch.nonce, signature: chSig}, false)).body;
      return {key_id: enroll.key_id, session_id: auth.session_id};
    }""")

    # step 2: revoke the just-enrolled key out-of-band (admin action), stamped
    # at server time so the append respects the store's monotonic-time rule.
    _, t = _api(base, "tok-approver", "GET", "/api/auth/time")
    store = WorkbenchStore(root / "store")
    from curunir_identity import KeyRegistry
    from curunir_operational.access import Marking
    reg = KeyRegistry(store, marking=Marking(owning_authority=store.meta["store_id"],
                                             releasability=("PUBLIC",)))
    reg.revoke(step1["key_id"], reason="compromised device", compromised=True, now=t["now"])
    assert reg.current(step1["key_id"])["status"] == "REVOKED"

    # step 3: sign a well-formed approval with the SAME (now revoked) key and the
    # captured session — the wire must refuse it; the report stays IN_REVIEW.
    out = page.evaluate(r"""
    async ({reportId, version, missionId, sessionId}) => {
      const { canonicalBytes } = await import('/static/js/canonical.js');
      const hex = (u8)=>Array.from(u8).map(b=>b.toString(16).padStart(2,'0')).join('');
      const db = await new Promise((res,rej)=>{const q=indexedDB.open('revoke-test',1);q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error);});
      const pk = await new Promise((res,rej)=>{const t=db.transaction('k','readonly').objectStore('k').get('pk');t.onsuccess=()=>res(t.result);t.onerror=()=>rej(t.error);});
      const now = (await (await fetch('/api/auth/time')).json()).now;
      const payload = {actor_id:'analyst-b', actor_kind:'HUMAN', action_type:'approve_report',
        target_kind:'workbench_report', target_id: reportId,
        target_version_token:`workbench_report:${reportId}@v${version}`, mission_id: missionId,
        nonce: crypto.randomUUID(), timestamp: now, command:{note:'', acknowledge_dissent:[]}};
      const sig = hex(new Uint8Array(await crypto.subtle.sign({name:'Ed25519'}, pk, canonicalBytes(payload))));
      const r = await fetch(`/api/commands/reports/${reportId}/approve-signed`, {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({session_id: sessionId, payload, signature: sig, expected_version: version})});
      return {status: r.status, body: await r.json().catch(()=>null)};
    }""", {"reportId": rid, "version": version, "missionId": session["mission_id"],
           "sessionId": step1["session_id"]})
    page.close()

    assert out["status"] == 401, out
    # session dies the moment its key is revoked (resolve re-checks live status)
    assert "EXPIRED_SESSION" in json.dumps(out["body"]), out
    store2 = WorkbenchStore(root / "store")
    assert report_of(store2, rid)["status"] == "IN_REVIEW"
    assert not [r for r in store2.records_of("signed_action") if r["target_id"] == rid]
