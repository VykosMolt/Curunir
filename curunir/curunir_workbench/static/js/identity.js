// Browser signing last mile: non-extractable Ed25519 device keys in IndexedDB.
import { canonicalBytes } from "./canonical.js";
import { get, post } from "./api.js";

const DATABASE = "curunir-identity";
const KEY_STORE = "keys";
const ALGORITHM = { name: "Ed25519" };

function toHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(KEY_STORE)) {
        request.result.createObjectStore(KEY_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function databaseGet(database, key) {
  return new Promise((resolve, reject) => {
    const request = database.transaction(KEY_STORE, "readonly")
      .objectStore(KEY_STORE).get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
}

function databasePut(database, key, value) {
  return new Promise((resolve, reject) => {
    const request = database.transaction(KEY_STORE, "readwrite")
      .objectStore(KEY_STORE).put(value, key);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export function signingSupported() {
  return Boolean(window.isSecureContext && window.crypto && crypto.subtle &&
                 window.indexedDB);
}

let ed25519Probe = null;
export async function ed25519Available() {
  if (ed25519Probe !== null) return ed25519Probe;
  if (!signingSupported()) { ed25519Probe = false; return false; }
  try {
    const pair = await crypto.subtle.generateKey(
      ALGORITHM, false, ["sign", "verify"]);
    await crypto.subtle.sign(ALGORITHM, pair.privateKey, new Uint8Array([0]));
    ed25519Probe = true;
  } catch {
    ed25519Probe = false;
  }
  return ed25519Probe;
}

async function deviceKey(actorId) {
  const database = await openDatabase();
  const retained = await databaseGet(database, actorId);
  if (retained && retained.privateKey && retained.publicKeyHex) return retained;
  const pair = await crypto.subtle.generateKey(
    ALGORITHM, false, ["sign", "verify"]);
  const record = {
    privateKey: pair.privateKey,
    publicKeyHex: toHex(await crypto.subtle.exportKey("raw", pair.publicKey)),
  };
  await databasePut(database, actorId, record);
  return record;
}

async function signPayload(privateKey, payload) {
  return toHex(await crypto.subtle.sign(
    ALGORITHM, privateKey, canonicalBytes(payload)));
}

export async function ensureEnrolled(actorId) {
  const key = await deviceKey(actorId);
  await post("/api/auth/enroll", { public_key_hex: key.publicKeyHex });
  return key;
}

let identitySession = null;
function sessionLive() {
  return identitySession &&
    new Date(identitySession.expires_time).getTime() - Date.now() > 5000;
}

export async function authenticate(actorId) {
  if (sessionLive() && identitySession.actor_id === actorId) {
    return identitySession;
  }
  const key = await ensureEnrolled(actorId);
  const challenge = await post("/api/auth/challenge", { actor_id: actorId });
  const payload = {
    purpose: "curunir-authenticate",
    actor_id: actorId,
    nonce: challenge.nonce,
  };
  identitySession = await post("/api/auth/authenticate", {
    actor_id: actorId,
    nonce: challenge.nonce,
    signature: await signPayload(key.privateKey, payload),
  });
  return identitySession;
}

export function clearSession() { identitySession = null; }

export async function approveReportSigned(report, missionId, actorId, actorKind,
                                          note, acknowledgeDissent) {
  const authenticated = await authenticate(actorId);
  const key = await deviceKey(actorId);
  const payload = {
    actor_id: actorId,
    actor_kind: actorKind,
    action_type: "approve_report",
    target_kind: "workbench_report",
    target_id: report.report_id,
    target_version_token:
      `workbench_report:${report.report_id}@v${report.version}`,
    mission_id: missionId,
    nonce: crypto.randomUUID(),
    timestamp: (await get("/api/auth/time")).now,
    command: {
      note: note || "",
      acknowledge_dissent: (acknowledgeDissent || []).slice().sort(),
    },
  };
  return post(`/api/commands/reports/${report.report_id}/approve-signed`, {
    session_id: authenticated.session_id,
    payload,
    signature: await signPayload(key.privateKey, payload),
    expected_version: report.version,
  });
}
