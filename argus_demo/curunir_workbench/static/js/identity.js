// Browser cryptographic identity — the signing "last mile".
//
// The analyst's signing key is a NON-EXTRACTABLE WebCrypto Ed25519 key,
// generated in-page and persisted in IndexedDB. The private key never leaves
// the browser key store: JS can ask it to sign, but cannot read it, so it
// cannot be exfiltrated for offline or elsewhere use (unlike the bearer token).
//
// Trust boundary (see CURUNIR_V6_7_SECURITY.md):
//  * ENROLLMENT of the device public key is bootstrapped by the bearer token
//    (a one-time act, server-bound to the bearer-authenticated actor — the
//    client cannot enroll a key for someone else);
//  * thereafter a LOAD-BEARING act (report approval) is a real Ed25519
//    signature by the device key over a canonical binding of the exact
//    operation (actor, action, target, target version, mission, nonce,
//    timestamp, command) — the browser proves key possession, it never asserts
//    its own authority, and the signature is recorded for replay.
//
// What-you-see-is-what-you-sign: the browser builds the canonical signing bytes
// itself (canonical.js, proven byte-identical to the server) from the action
// parameters it is showing the analyst, and the server independently re-derives
// and verifies the same bytes against the real operation. Neither side trusts
// the other's serialization.

import { canonicalBytes } from "./canonical.js";
import { get, post } from "./api.js";

const DB_NAME = "curunir-identity";
const STORE = "keys";
const ALG = { name: "Ed25519" };

// ---- hex helpers -----------------------------------------------------------
function toHex(buf) {
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ---- IndexedDB (minimal promise wrappers) ----------------------------------
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) {
        req.result.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function idbGet(db, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
    tx.onsuccess = () => resolve(tx.result || null);
    tx.onerror = () => reject(tx.error);
  });
}

function idbPut(db, key, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite").objectStore(STORE).put(value, key);
    tx.onsuccess = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// ---- capability ------------------------------------------------------------
export function signingSupported() {
  return !!(window.isSecureContext && window.crypto && crypto.subtle &&
            window.indexedDB);
}

// Probe whether this browser's WebCrypto actually implements Ed25519 (a secure
// context is necessary but not sufficient). Cached after first probe.
let _ed25519Ok = null;
export async function ed25519Available() {
  if (_ed25519Ok !== null) return _ed25519Ok;
  if (!signingSupported()) { _ed25519Ok = false; return false; }
  try {
    const kp = await crypto.subtle.generateKey(ALG, false, ["sign", "verify"]);
    await crypto.subtle.sign(ALG, kp.privateKey, new Uint8Array([0]));
    _ed25519Ok = true;
  } catch {
    _ed25519Ok = false;
  }
  return _ed25519Ok;
}

// ---- device key ------------------------------------------------------------
// One non-extractable signing key per actor per browser profile. Returned as
// { privateKey: CryptoKey (non-extractable), publicKeyHex }.
async function getOrCreateKey(actorId) {
  const db = await openDb();
  const existing = await idbGet(db, actorId);
  if (existing && existing.privateKey && existing.publicKeyHex) return existing;
  const kp = await crypto.subtle.generateKey(ALG, false, ["sign", "verify"]);
  const rawPub = await crypto.subtle.exportKey("raw", kp.publicKey);
  const rec = { privateKey: kp.privateKey, publicKeyHex: toHex(rawPub) };
  // a non-extractable CryptoKey is structured-cloneable into IndexedDB; the
  // key material stays non-extractable and origin-bound.
  await idbPut(db, actorId, rec);
  return rec;
}

async function signPayload(privateKey, payload) {
  const sig = await crypto.subtle.sign(ALG, privateKey, canonicalBytes(payload));
  return toHex(sig);
}

// ---- enrollment (bearer bootstrap) -----------------------------------------
// Register this device's public key for the bearer-authenticated actor. The
// server binds the key to the actor IT authenticated from the bearer token —
// the body carries no actor id, so a client cannot enroll a key for another
// actor. Idempotent for an already-enrolled key.
export async function ensureEnrolled(actorId) {
  const key = await getOrCreateKey(actorId);
  await post("/api/auth/enroll", { public_key_hex: key.publicKeyHex });
  return key;
}

// ---- authentication (challenge-response) -----------------------------------
let _session = null; // { session_id, actor_id, expires_time }

function sessionLive() {
  return _session && new Date(_session.expires_time).getTime() - Date.now() > 5000;
}

export async function authenticate(actorId) {
  if (sessionLive() && _session.actor_id === actorId) return _session;
  const key = await ensureEnrolled(actorId);
  const challenge = await post("/api/auth/challenge", { actor_id: actorId });
  // sign the EXACT challenge object the server verifies (sorted canonically)
  const payload = { purpose: "curunir-authenticate", actor_id: actorId,
                    nonce: challenge.nonce };
  const signature = await signPayload(key.privateKey, payload);
  _session = await post("/api/auth/authenticate",
                        { actor_id: actorId, nonce: challenge.nonce, signature });
  return _session;
}

export function clearSession() { _session = null; }

// ---- signed load-bearing act: report approval ------------------------------
// Build the canonical action binding from what the UI is showing, sign it with
// the device key, and submit it. `report` is the current report record (its
// version is what the signature is bound to); `missionId` scopes the act.
export async function approveReportSigned(report, missionId, actorId, actorKind,
                                          note, acknowledgeDissent) {
  const session = await authenticate(actorId);
  const key = await getOrCreateKey(actorId);
  // the whole material command (note AND which dissent is being acknowledged)
  // is bound by the signature — a four-eyes acknowledgement cannot be altered
  // between signing and commit.
  const payload = {
    actor_id: actorId,
    actor_kind: actorKind,
    action_type: "approve_report",
    target_kind: "workbench_report",
    target_id: report.report_id,
    target_version_token: `workbench_report:${report.report_id}@v${report.version}`,
    mission_id: missionId,
    nonce: crypto.randomUUID(),
    // timestamp sourced from server time (bounded to the verifier's skew
    // window), not the browser wall clock — so it is honest under clock drift.
    timestamp: (await get("/api/auth/time")).now,
    command: { note: note || "",
               acknowledge_dissent: (acknowledgeDissent || []).slice().sort() },
  };
  const signature = await signPayload(key.privateKey, payload);
  return post(`/api/commands/reports/${report.report_id}/approve-signed`, {
    session_id: session.session_id,
    payload,
    signature,
    expected_version: report.version,
  });
}
