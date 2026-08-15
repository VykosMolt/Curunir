"""Test-authenticated identity, revocation and bounded authorization.

The signer deliberately uses the Python standard-library HMAC implementation
with per-subject test keys.  It is a clean replaceable interface and is
reported as TEST_SIGNER_ONLY; no production-authentication claim is made.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import replace
from hashlib import sha256 as hashlib_sha256
from pathlib import Path
from typing import Any, Mapping

from ..canonical import canonical_line, parse_time, sha256
from . import POLICY_VERSION
from .models import (AccessMarkingV3, ActorIdentity, AuthenticatedActionEnvelope,
                     AuthorizationDecision, NodeIdentity, RevocationRecord)


class IdentityError(ValueError):
    pass


class AuthenticationError(IdentityError):
    pass


class AuthorizationError(PermissionError):
    pass


class TestSigner:
    method = "HMAC_SHA256_DETERMINISTIC_TEST_SIGNER"

    @staticmethod
    def sign(key: str, record: Mapping[str, Any]) -> str:
        return hmac.new(key.encode("utf-8"), canonical_line(record).encode("utf-8"), hashlib_sha256).hexdigest()

    @classmethod
    def verify(cls, key: str, record: Mapping[str, Any], signature: str) -> bool:
        return hmac.compare_digest(cls.sign(key, record), signature)


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_line(record) + "\n")
        handle.flush()


class IdentityProvider:
    """Filesystem-backed identity provider scoped to one independent node."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.actors_path = self.root / "actor_identities.json"
        self.node_path = self.root / "node_identity.json"
        self.keyring_path = self.root / "test_keyring.json"
        self.revocations_path = self.root / "revocations.jsonl"
        self.nonces_path = self.root / "used_nonces.jsonl"
        self.decisions_path = self.root / "authorization_decisions.jsonl"
        self._actors_cache = _read_json(self.actors_path, {})
        self._keyring_cache = _read_json(self.keyring_path, {})
        self._revocations_cache = [RevocationRecord(**json.loads(line))
                                   for line in self.revocations_path.read_text(encoding="utf-8").splitlines()
                                   if line.strip()] if self.revocations_path.exists() else []
        self._nonce_keys = {json.loads(line).get("key")
                            for line in self.nonces_path.read_text(encoding="utf-8").splitlines()
                            if line.strip()} if self.nonces_path.exists() else set()

    @classmethod
    def initialize(cls, root: str | Path, node: NodeIdentity,
                   actors: tuple[ActorIdentity, ...], keys: Mapping[str, str]) -> "IdentityProvider":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        paths = (root / "node_identity.json", root / "actor_identities.json", root / "test_keyring.json")
        if any(path.exists() for path in paths):
            raise IdentityError(f"identity provider already initialized: {root}")
        actor_ids = [actor.actor_id for actor in actors]
        if len(actor_ids) != len(set(actor_ids)):
            raise IdentityError("duplicate actor id")
        if node.node_id not in keys or any(actor_id not in keys for actor_id in actor_ids):
            raise IdentityError("test keyring requires one key for the node and every actor")
        (root / "node_identity.json").write_text(canonical_line(node.to_record()) + "\n", encoding="utf-8")
        (root / "actor_identities.json").write_text(
            canonical_line({actor.actor_id: actor.to_record() for actor in actors}) + "\n", encoding="utf-8")
        (root / "test_keyring.json").write_text(canonical_line(dict(keys)) + "\n", encoding="utf-8")
        for name in ("revocations.jsonl", "used_nonces.jsonl", "authorization_decisions.jsonl"):
            (root / name).touch()
        return cls(root)

    @property
    def node(self) -> NodeIdentity:
        return NodeIdentity(**_read_json(self.node_path, {}))

    def actors(self) -> dict[str, ActorIdentity]:
        return {key: ActorIdentity(**value) for key, value in self._actors_cache.items()}

    def actor(self, actor_id: str) -> ActorIdentity:
        try:
            return self.actors()[actor_id]
        except KeyError as exc:
            raise IdentityError(f"unknown actor: {actor_id}") from exc

    def key_for(self, subject_id: str) -> str:
        try:
            return self._keyring_cache[subject_id]
        except KeyError as exc:
            raise IdentityError(f"no test signer key for {subject_id}") from exc

    def revocations(self) -> list[RevocationRecord]:
        return list(self._revocations_cache)

    def revoke(self, record: RevocationRecord) -> None:
        if record.subject_type == "ACTOR":
            self.actor(record.subject_id)
        elif record.subject_id != self.node.node_id and record.subject_id not in self.node_peer_ids():
            raise IdentityError(f"unknown node revocation subject: {record.subject_id}")
        _append_jsonl(self.revocations_path, record.to_record())
        self._revocations_cache.append(record)

    def node_peer_ids(self) -> set[str]:
        peers_path = self.root.parent / "peer_identities.json"
        return set(_read_json(peers_path, {}))

    def revoked_at(self, subject_type: str, subject_id: str, when: str) -> bool:
        point = parse_time(when)
        return any(record.subject_type == subject_type and record.subject_id == subject_id and
                   parse_time(record.effective_time) <= point for record in self.revocations())

    def actor_valid_at(self, actor_id: str, when: str) -> bool:
        actor = self.actor(actor_id)
        return actor.valid_at(when) and actor.status == "ACTIVE" and not self.revoked_at("ACTOR", actor_id, when)

    def node_valid_at(self, node_id: str, when: str) -> bool:
        if node_id == self.node.node_id:
            return self.node.status == "ACTIVE" and not self.revoked_at("NODE", node_id, when)
        peers_path = self.root.parent / "peer_identities.json"
        peers = _read_json(peers_path, {})
        if node_id not in peers:
            return False
        peer = NodeIdentity(**peers[node_id])
        return peer.status == "ACTIVE" and peer.trust_state not in ("REVOKED", "UNKNOWN") \
            and not self.revoked_at("NODE", node_id, when)

    def _nonce_used(self, actor_id: str, nonce: str) -> bool:
        return f"{actor_id}:{nonce}" in self._nonce_keys

    def make_action(self, actor_id: str, action_type: str, payload: Mapping[str, Any],
                    recorded_time: str, nonce: str, *, node_id: str | None = None,
                    policy_version: str = POLICY_VERSION) -> AuthenticatedActionEnvelope:
        node_id = node_id or self.node.node_id
        unsigned = AuthenticatedActionEnvelope(
            action_id=f"act-{sha256({'actor': actor_id, 'node': node_id, 'nonce': nonce})[:20]}",
            actor_id=actor_id, node_id=node_id, action_type=action_type,
            payload_hash=sha256(payload), policy_version=policy_version,
            recorded_time=recorded_time, nonce=nonce, authentication_method=TestSigner.method,
            signature="", verification_result="UNVERIFIED")
        return replace(unsigned, signature=TestSigner.sign(self.key_for(actor_id), unsigned.signing_record()))

    def verify_action(self, envelope: AuthenticatedActionEnvelope, payload: Mapping[str, Any], *,
                      expected_node: str | None = None, consume_nonce: bool = True,
                      historical: bool = False) -> AuthenticatedActionEnvelope:
        if envelope.authentication_method != TestSigner.method:
            raise AuthenticationError("unsupported authentication method")
        if expected_node and envelope.node_id != expected_node:
            raise AuthenticationError("authenticated action bound to wrong node")
        if envelope.policy_version != POLICY_VERSION:
            raise AuthenticationError("authenticated action policy version mismatch")
        if envelope.payload_hash != sha256(payload):
            raise AuthenticationError("authenticated action payload hash mismatch")
        if not TestSigner.verify(self.key_for(envelope.actor_id), envelope.signing_record(), envelope.signature):
            raise AuthenticationError("authenticated action signature invalid")
        if not self.actor_valid_at(envelope.actor_id, envelope.recorded_time):
            raise AuthenticationError("actor invalid or revoked at action time")
        if not self.node_valid_at(envelope.node_id, envelope.recorded_time):
            raise AuthenticationError("node invalid or revoked at action time")
        if not historical and self._nonce_used(envelope.actor_id, envelope.nonce):
            raise AuthenticationError("authenticated action nonce replayed")
        if consume_nonce and not historical:
            nonce_key = f"{envelope.actor_id}:{envelope.nonce}"
            _append_jsonl(self.nonces_path, {"key": nonce_key,
                                             "action_id": envelope.action_id,
                                             "recorded_time": envelope.recorded_time})
            self._nonce_keys.add(nonce_key)
        return replace(envelope, verification_result="VERIFIED_TEST_SIGNER")

    def record_decision(self, decision: AuthorizationDecision) -> None:
        _append_jsonl(self.decisions_path, decision.to_record())


ACTION_ROLE_RULES: dict[str, set[str]] = {
    "ANNOTATION": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "TASK_ASSIGNMENT": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "JOINT_COORDINATOR"},
    "TASK_ACCEPTANCE": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "TASK_COMPLETION": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "HANDOFF": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "REVIEW": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "RECOMMENDATION_DISPOSITION": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "JOINT_COORDINATOR"},
    "DECISION": {"JOINT_COORDINATOR"},
    "CONFLICT_RESOLUTION": {"JOINT_COORDINATOR", "CIVIL_PROTECTION_ENGINEER"},
    "ACCESS_POLICY_CHANGE": {"JOINT_COORDINATOR"},
    "SUBMIT_ENGINEERING_EVIDENCE": {"CIVIL_PROTECTION_ENGINEER", "JOINT_COORDINATOR"},
    "ATTACH_PUBLIC_EVIDENCE": {"STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
    "MARK_INFRASTRUCTURE_STATUS": {"CIVIL_PROTECTION_ENGINEER", "JOINT_COORDINATOR"},
    "PROPOSE_IMPLICATION": {"STRATEGIC_ANALYST", "PROVIDER", "JOINT_COORDINATOR"},
    "INFORMATION_REQUIREMENT": {"LOGISTICS_ANALYST", "CIVIL_PROTECTION_ENGINEER", "STRATEGIC_ANALYST", "JOINT_COORDINATOR"},
}


def authorize(actor: ActorIdentity, action: str, object_ref: str, recorded_time: str,
              *, object_owner: str = "", mission_scope: str = "",
              conflict_type: str = "", is_provider: bool = False) -> AuthorizationDecision:
    roles = set(actor.roles)
    allowed_roles = ACTION_ROLE_RULES.get(action, set())
    reasons: list[str] = []
    allow = bool(roles & allowed_roles)
    if not allow:
        reasons.append("actor role is not authorized for action")
    if mission_scope and mission_scope not in actor.mission_scopes:
        allow = False
        reasons.append("mission scope absent")
    if object_owner and actor.organization != object_owner and "CROSS_AUTHORITY" not in actor.authority_scopes:
        allow = False
        reasons.append("object ownership authority absent")
    if action == "CONFLICT_RESOLUTION" and conflict_type in (
            "CONTRADICTORY_OPERATIONAL_STATUS", "CONCURRENT_ATTRIBUTE_CHANGE"):
        if "CIVIL_PROTECTION_ENGINEER" in roles and "ENGINEERING_STATUS" not in actor.authority_scopes:
            allow = False
            reasons.append("engineering-status resolver authority absent")
        if "LOGISTICS_ANALYST" in roles:
            allow = False
            reasons.append("logistics analyst may not resolve engineering status")
    if action == "MARK_INFRASTRUCTURE_STATUS" and "STRATEGIC_ANALYST" in roles:
        allow = False
        reasons.append("strategic analyst may not mark infrastructure operational")
    if is_provider or "PROVIDER" in roles:
        if action != "PROPOSE_IMPLICATION":
            allow = False
            reasons.append("providers may propose implications only; they may not decide")
    rationale = "; ".join(reasons) if reasons else "bounded role, scope, ownership and action checks satisfied"
    return AuthorizationDecision(
        decision_id=f"authz-{sha256({'actor': actor.actor_id, 'action': action, 'object': object_ref, 'time': recorded_time})[:20]}",
        policy_version=POLICY_VERSION, actor_id=actor.actor_id, action=action,
        object_ref=object_ref, result="ALLOW" if allow else "DENY", rationale=rationale, time=recorded_time)


ROLE_RANK_V3 = {
    "OBSERVER": 0, "LOGISTICS_ANALYST": 1, "STRATEGIC_ANALYST": 1,
    "CIVIL_PROTECTION_ENGINEER": 2, "JOINT_COORDINATOR": 3, "AUDITOR": 4,
}


def can_receive(marking: AccessMarkingV3 | Mapping[str, Any], destination: NodeIdentity,
                access_context: Mapping[str, Any]) -> bool:
    record = marking.to_record() if isinstance(marking, AccessMarkingV3) else dict(marking)
    roles = tuple(access_context.get("roles", ()))
    max_rank = max((ROLE_RANK_V3.get(role, -1) for role in roles), default=-1)
    if max_rank < ROLE_RANK_V3.get(record.get("min_role", "OBSERVER"), 999):
        return False
    if not set(record.get("compartments", ())) <= set(access_context.get("compartments", ())):
        return False
    releasability = set(record.get("releasability", ()))
    if releasability and not releasability.intersection(access_context.get("releasability", ())):
        return False
    scopes = set(record.get("mission_scopes", ()))
    if scopes and not scopes.intersection(access_context.get("mission_scopes", ())):
        return False
    controls = set(record.get("originator_controls", ()))
    if controls:
        permits = {f"NODE:{destination.node_id}", f"AUTHORITY:{destination.owning_authority}",
                   *(f"SCOPE:{scope}" for scope in destination.sharing_scopes)}
        if not controls.intersection(permits):
            return False
    return destination.status == "ACTIVE" and destination.trust_state not in ("REVOKED", "UNKNOWN")
