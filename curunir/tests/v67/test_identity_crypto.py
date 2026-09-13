"""Cryptographic actor identity, on real Ed25519 keys.

The actor holds the private key and the server only enrolled public keys. Covers
login, session expiry, revocation, rotation with old signatures still verifying,
action binding, and replay telling genuine from tampered.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from curunir_identity import (KeyRegistry, SessionManager, SignatureRejected,
                              commit_action, generate_keypair, sign_action,
                              verify_action, verify_all)
from curunir_identity.actions import (CLOCK_SKEW, INVALID_SIGNATURE,
                                      REPLAYED_NONCE, STALE_VERSION, WRONG_ACTOR,
                                      WRONG_MISSION, WRONG_TARGET)
from curunir_identity.registry import COMPROMISED, RETIRED, VALID
from curunir_identity.replay import (DIGEST_MISMATCH, GENUINE,
                                     KEY_NOT_VALID_AT_TIME, SIGNATURE_INVALID)
from curunir_identity.sessions import AuthError
from curunir_operational.canonical import parse_time
from curunir_workbench.store import WorkbenchStore

from semantic_support import MARK, T0

pytestmark = pytest.mark.no_db
MISSION = "mission-alpha"


class Clock:
    def __init__(self, start: str):
        self.t = parse_time(start)

    def now(self) -> str:
        return self.t.isoformat()

    def advance(self, seconds: int) -> None:
        self.t += timedelta(seconds=seconds)


def _fixture(tmp_path):
    store = WorkbenchStore.create(tmp_path / "store", "identity-test", T0)
    clock = Clock(T0)
    registry = KeyRegistry(store, marking=MARK, now_fn=clock.now)
    sessions = SessionManager(now_fn=clock.now)
    return store, clock, registry, sessions


def _actor(registry, actor_id, kind="HUMAN"):
    private_pem, public_hex = generate_keypair()
    registry.enroll(actor_id=actor_id, actor_kind=kind, public_key_hex=public_hex)
    return private_pem


def _login(registry, sessions, actor_id, private_pem):
    from curunir_identity import sign as sign_payload
    challenge = sessions.issue_challenge(actor_id)
    sig = sign_payload(private_pem, sessions.challenge_payload(actor_id, challenge["nonce"]))
    return sessions.authenticate(registry, actor_id=actor_id,
                                 nonce=challenge["nonce"], signature_hex=sig)


def _do(store, registry, sessions, session, private_pem, *, nonce,
        version="report-1@v1", action_type="approve_report", mission=MISSION,
        target_id="report-1", target_kind="workbench_report"):
    fields = dict(actor_id=session.actor_id, actor_kind=session.actor_kind,
                  action_type=action_type, target_kind=target_kind,
                  target_id=target_id, target_version_token=version,
                  mission_id=mission, nonce=nonce, timestamp=sessions.now_fn(),
                  command={"disposition": "APPROVED", "note": "looks sound"})
    signed = sign_action(private_pem, **fields)
    verified = verify_action(
        store, registry, sessions, session_id=session.session_id,
        payload=signed["payload"], signature_hex=signed["signature"],
        expected_action_type=action_type, expected_target_kind=target_kind,
        expected_target_id=target_id, current_version_token=version,
        mission_id=mission, marking=MARK)
    return commit_action(store, verified, record_actor=session.actor_id)


# Authentication


def test_authenticate_requires_the_private_key(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    assert session.actor_id == "analyst-a" and session.actor_kind == "HUMAN"

    # An unenrolled key cannot authenticate as analyst-a.
    from curunir_identity import sign as sign_payload
    other_pem, _ = generate_keypair()
    ch = sessions.issue_challenge("analyst-a")
    bad = sign_payload(other_pem, sessions.challenge_payload("analyst-a", ch["nonce"]))
    with pytest.raises(AuthError):
        sessions.authenticate(registry, actor_id="analyst-a", nonce=ch["nonce"],
                              signature_hex=bad)


def test_challenge_is_single_use_and_expires(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    from curunir_identity import sign as sign_payload
    ch = sessions.issue_challenge("analyst-a")
    sig = sign_payload(pem_a, sessions.challenge_payload("analyst-a", ch["nonce"]))
    sessions.authenticate(registry, actor_id="analyst-a", nonce=ch["nonce"],
                          signature_hex=sig)
    with pytest.raises(AuthError):
        sessions.authenticate(registry, actor_id="analyst-a", nonce=ch["nonce"],
                              signature_hex=sig)
    ch2 = sessions.issue_challenge("analyst-a")
    clock.advance(sessions.challenge_ttl + 1)
    sig2 = sign_payload(pem_a, sessions.challenge_payload("analyst-a", ch2["nonce"]))
    with pytest.raises(AuthError):
        sessions.authenticate(registry, actor_id="analyst-a", nonce=ch2["nonce"],
                              signature_hex=sig2)


def test_session_expires_and_dies_on_revocation(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    assert sessions.resolve(registry, session.session_id).actor_id == "analyst-a"
    clock.advance(sessions.session_ttl + 1)
    with pytest.raises(AuthError):
        sessions.resolve(registry, session.session_id)
    # Revoking the key kills a live session immediately.
    session2 = _login(registry, sessions, "analyst-a", pem_a)
    key = registry.active_key_for("analyst-a")
    registry.revoke(key["key_id"], reason="lost laptop")
    with pytest.raises(AuthError):
        sessions.resolve(registry, session2.session_id)


# Signed actions


def test_signed_action_binds_actor_mission_nonce_and_version(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)

    def verify(signed, *, current, action_type="approve_report",
               target_kind="workbench_report", target_id="report-1"):
        return verify_action(
            store, registry, sessions, session_id=session.session_id,
            payload=signed["payload"], signature_hex=signed["signature"],
            expected_action_type=action_type, expected_target_kind=target_kind,
            expected_target_id=target_id, current_version_token=current,
            mission_id=MISSION, marking=MARK)

    def build(**over):
        f = dict(actor_id="analyst-a", actor_kind="HUMAN", action_type="approve_report",
                 target_kind="workbench_report", target_id="report-1",
                 target_version_token="report-1@v1", mission_id=MISSION, nonce="n",
                 timestamp=sessions.now_fn(), command={"disposition": "APPROVED"})
        f.update(over)
        return sign_action(pem_a, **f)

    out = _do(store, registry, sessions, session, pem_a, nonce="n1")
    assert out["action_id"] and len(store.records_of("signed_action")) == 1

    # The same nonce cannot be used twice.
    with pytest.raises(SignatureRejected) as e:
        _do(store, registry, sessions, session, pem_a, nonce="n1")
    assert e.value.status == REPLAYED_NONCE

    # A signature for one version does not carry to another.
    with pytest.raises(SignatureRejected) as e:
        verify(build(nonce="n2"), current="report-1@v2")
    assert e.value.status == STALE_VERSION

    # Nor onto a different report at the same version.
    with pytest.raises(SignatureRejected) as e:
        verify(build(nonce="n2b"), current="report-2@v1", target_id="report-2")
    assert e.value.status == WRONG_TARGET

    with pytest.raises(SignatureRejected) as e:
        verify(build(nonce="n3", mission_id="other-mission"), current="report-1@v1")
    assert e.value.status == WRONG_MISSION


def test_cannot_sign_as_another_actor_or_tamper(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    _actor(registry, "analyst-b")
    session = _login(registry, sessions, "analyst-a", pem_a)

    def verify(signed, current="report-1@v1"):
        return verify_action(
            store, registry, sessions, session_id=session.session_id,
            payload=signed["payload"], signature_hex=signed["signature"],
            expected_action_type="approve_report",
            expected_target_kind="workbench_report", expected_target_id="report-1",
            current_version_token=current, mission_id=MISSION, marking=MARK)

    common = dict(action_type="approve_report", target_kind="workbench_report",
                  target_id="report-1", target_version_token="report-1@v1",
                  mission_id=MISSION, command={"disposition": "APPROVED"})

    # The payload claims analyst-b while the session belongs to analyst-a.
    signed = sign_action(pem_a, actor_id="analyst-b", actor_kind="HUMAN",
                         nonce="n1", timestamp=sessions.now_fn(), **common)
    with pytest.raises(SignatureRejected) as e:
        verify(signed)
    assert e.value.status == WRONG_ACTOR

    good = sign_action(pem_a, actor_id="analyst-a", actor_kind="HUMAN",
                       nonce="n2", timestamp=sessions.now_fn(), **common)
    tampered = {**good["payload"], "command": {"disposition": "REJECTED"}}
    with pytest.raises(SignatureRejected) as e:
        verify({"payload": tampered, "signature": good["signature"]})
    assert e.value.status == INVALID_SIGNATURE


def test_action_timestamp_must_be_near_server_time(tmp_path):
    # An act cannot be back- or post-dated across a deadline or a key revocation.
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    for ts in ("2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"):
        signed = sign_action(pem_a, actor_id="analyst-a", actor_kind="HUMAN",
                            action_type="approve_report", target_kind="workbench_report",
                            target_id="report-1", target_version_token="report-1@v1",
                            mission_id=MISSION, nonce=f"ts-{ts}", timestamp=ts,
                            command={"disposition": "APPROVED"})
        with pytest.raises(SignatureRejected) as e:
            verify_action(store, registry, sessions, session_id=session.session_id,
                          payload=signed["payload"], signature_hex=signed["signature"],
                          expected_action_type="approve_report",
                          expected_target_kind="workbench_report",
                          expected_target_id="report-1",
                          current_version_token="report-1@v1", mission_id=MISSION,
                          marking=MARK)
        assert e.value.status == CLOCK_SKEW


# Key lifecycle and replay


def test_rotation_keeps_history_verifiable(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    _do(store, registry, sessions, session, pem_a, nonce="n1")  # under the old key
    old_key = registry.active_key_for("analyst-a")["key_id"]

    clock.advance(60)
    new_pem, new_pub = generate_keypair()
    registry.rotate(actor_id="analyst-a", new_public_key_hex=new_pub)
    assert registry.current(old_key)["status"] == RETIRED
    new_session = _login(registry, sessions, "analyst-a", new_pem)
    _do(store, registry, sessions, new_session, new_pem, nonce="n2")  # under the new key

    # Each action still verifies against the key that was valid when it was made.
    result = verify_all(store)
    assert result["all_genuine"], result
    assert result["count"] == 2


def test_revocation_stops_future_but_keeps_history_unless_compromised(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    _do(store, registry, sessions, session, pem_a, nonce="n1")
    key_id = registry.active_key_for("analyst-a")["key_id"]

    # An ordinary revocation leaves the past action genuine.
    clock.advance(60)
    registry.revoke(key_id, reason="rotated out of service")
    assert verify_all(store)["all_genuine"]

    # A compromise makes the same past action untrusted after the fact.
    registry.revoke(key_id, reason="key exfiltrated", compromised=True)
    result = verify_all(store)
    assert not result["all_genuine"]
    assert result["verdicts"][0]["verdict"] == KEY_NOT_VALID_AT_TIME


def test_replay_detects_tampered_record(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    pem_a = _actor(registry, "analyst-a")
    session = _login(registry, sessions, "analyst-a", pem_a)
    _do(store, registry, sessions, session, pem_a, nonce="n1")
    record = dict(store.records_of("signed_action")[0])

    # Forging the stored command breaks the digest.
    record["signed_payload"] = {**record["signed_payload"],
                                "command": {"disposition": "REJECTED"}}
    from curunir_identity.replay import verify_signed_action
    assert verify_signed_action(registry, record) == DIGEST_MISMATCH

    # Fixing the digest as well still leaves the signature wrong.
    from curunir_operational.canonical import sha256
    record["payload_digest"] = sha256(dict(record["signed_payload"]))
    assert verify_signed_action(registry, record) == SIGNATURE_INVALID


def test_service_actor_is_distinct_from_human(tmp_path):
    store, clock, registry, sessions = _fixture(tmp_path)
    svc_pem = _actor(registry, "watch-service", kind="SERVICE")
    session = _login(registry, sessions, "watch-service", svc_pem)
    out = _do(store, registry, sessions, session, svc_pem, nonce="n1",
              action_type="watch_tick")
    assert out["action_id"]
    # The record names SERVICE, which is what a human-only gate refuses on.
    assert store.records_of("signed_action")[0]["actor_kind"] == "SERVICE"


def test_active_key_cap_bounds_enrollment_and_auth_work(tmp_path):
    # An actor's active-key count is bounded, so an enrol flood cannot slow
    # authentication down.
    from curunir_identity.registry import MAX_ACTIVE_KEYS_PER_ACTOR
    from curunir_identity import generate_keypair, sign
    store, clock, registry, sessions = _fixture(tmp_path)
    pems = []
    for _ in range(MAX_ACTIVE_KEYS_PER_ACTOR):
        pem, pub = generate_keypair()
        registry.enroll(actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=pub)
        pems.append(pem)
    assert len(registry.active_keys_for("analyst-a")) == MAX_ACTIVE_KEYS_PER_ACTOR
    _, over = generate_keypair()
    with pytest.raises(ValueError, match="maximum"):
        registry.enroll(actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=over)
    # Every enrolled device still authenticates.
    for pem in (pems[0], pems[-1]):
        ch = sessions.issue_challenge("analyst-a")
        sig = sign(pem, sessions.challenge_payload("analyst-a", ch["nonce"]))
        assert sessions.authenticate(registry, actor_id="analyst-a",
                                     nonce=ch["nonce"], signature_hex=sig)


def test_session_eviction_hits_the_flooding_actor_not_victims(monkeypatch):
    # At the session cap, the authenticating actor loses its own oldest session,
    # never someone else's live one.
    from curunir_identity import sessions as sess_mod
    from curunir_identity.sessions import Session, SessionManager
    monkeypatch.setattr(sess_mod, "MAX_SESSIONS", 3)
    sm = SessionManager(now_fn=lambda: "2026-08-17T12:00:00+00:00")
    sm._sessions = {
        "vb": Session("vb", "analyst-b", "HUMAN", "kb",
                      "2026-08-17T11:00:00+00:00", "2026-08-17T13:00:00+00:00"),  # the oldest of all
        "a1": Session("a1", "analyst-a", "HUMAN", "ka",
                      "2026-08-17T11:30:00+00:00", "2026-08-17T13:00:00+00:00"),
        "a2": Session("a2", "analyst-a", "HUMAN", "ka",
                      "2026-08-17T11:45:00+00:00", "2026-08-17T13:00:00+00:00"),
    }
    sm._prune_sessions("2026-08-17T12:00:00+00:00", "analyst-a")  # analyst-a re-authenticates
    assert "vb" in sm._sessions
    assert "a1" not in sm._sessions


def test_active_key_cap_is_atomic_across_store_instances(tmp_path):
    """The cap check and append share the store mutation lock."""
    from concurrent.futures import ThreadPoolExecutor
    from curunir_identity.registry import MAX_ACTIVE_KEYS_PER_ACTOR

    store, clock, registry, sessions = _fixture(tmp_path)
    for _ in range(MAX_ACTIVE_KEYS_PER_ACTOR - 1):
        _, public = generate_keypair()
        registry.enroll(
            actor_id="analyst-a", actor_kind="HUMAN", public_key_hex=public)
    candidates = [generate_keypair()[1] for _ in range(2)]

    def enroll(public):
        separate = WorkbenchStore(store.root)
        try:
            KeyRegistry(separate, marking=MARK, now_fn=clock.now).enroll(
                actor_id="analyst-a", actor_kind="HUMAN",
                public_key_hex=public)
            return "accepted"
        except ValueError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(enroll, candidates))
    reopened = WorkbenchStore(store.root)
    assert sorted(outcomes) == ["accepted", "refused"]
    assert len(KeyRegistry(reopened).active_keys_for("analyst-a")) \
        == MAX_ACTIVE_KEYS_PER_ACTOR


def test_rotation_retry_completes_predecessor_retirement(tmp_path, monkeypatch):
    """A crash after replacement enrollment cannot strand both keys forever."""
    store, clock, registry, sessions = _fixture(tmp_path)
    _actor(registry, "analyst-a")
    old_key = registry.active_key_for("analyst-a")["key_id"]
    _, replacement = generate_keypair()
    original_transition = registry._transition

    def crash(*args, **kwargs):
        raise RuntimeError("crash before predecessor retirement")

    monkeypatch.setattr(registry, "_transition", crash)
    with pytest.raises(RuntimeError):
        registry.rotate(
            actor_id="analyst-a", new_public_key_hex=replacement)
    monkeypatch.setattr(registry, "_transition", original_transition)
    new_record = registry.rotate(
        actor_id="analyst-a", new_public_key_hex=replacement)
    assert new_record["supersedes_key_id"] == old_key
    assert registry.current(old_key)["status"] == "RETIRED"
