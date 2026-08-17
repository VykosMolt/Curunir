"""The crypto-identity bridge for the workbench: authenticate an actor by key
possession, then apply a load-bearing command only behind a verified signature
bound to the exact target version.

This composes the three concerns the V6.7 identity model keeps separate:

  * AUTHENTICATION — `curunir_identity` proves *who* (challenge-response → a
    short-lived session), from the actor's key alone; the client never asserts
    its own identity;
  * AUTHORIZATION — the actor registry supplies *what they may do* (roles,
    compartments) for the authenticated actor id, as current server state;
  * the ACT — the existing command runs under a context built from those two,
    only after the signed action verifies against live state.

A load-bearing act is thus attributable to actor-held key material, bound to the
target's version (a signature cannot be replayed against another version), and
recorded immutably for replay re-verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from curunir_identity import KeyRegistry, SessionManager, verify_action
from curunir_identity.actions import SignatureRejected, commit_action
from curunir_identity.sessions import Session
from curunir_operational.access import Marking

from .auth import ActorRegistry
from .commands import CommandContext
from .store import WorkbenchStore


@dataclass
class SignedOperations:
    store: WorkbenchStore
    root: Path
    registry: KeyRegistry
    sessions: SessionManager
    authz: ActorRegistry
    now_fn: Callable[[], str]
    mission_id: str

    def authenticate(self, *, actor_id: str, nonce: str, signature_hex: str) -> Session:
        return self.sessions.authenticate(
            self.registry, actor_id=actor_id, nonce=nonce, signature_hex=signature_hex)

    def _command_context(self, session: Session) -> CommandContext:
        # authorization comes from current registry state for the AUTHENTICATED
        # actor — never from the client, and independent of the key. The two
        # sources of actor_kind (the enrolled key vs the authz registry) must
        # agree, so a SERVICE key listed HUMAN (or vice versa) cannot slip past
        # a kind-gated command with a mis-attributed record.
        context = self.authz.context_for_actor(session.actor_id)
        if context.actor_kind != session.actor_kind:
            raise SignatureRejected(
                "ACTOR_KIND_MISMATCH",
                "the actor's enrolled key kind and registry kind disagree")
        marking = Marking(owning_authority=self.mission_id, releasability=("PUBLIC",))
        return CommandContext(store=self.store, root=self.root, context=context,
                              marking=marking, now_fn=self.now_fn)

    def apply_signed(self, *, session_id: str, payload: Mapping[str, Any],
                     signature_hex: str, action_type: str, target_kind: str,
                     target_id: str, current_version_token: str,
                     target_marking: Marking,
                     apply: Callable[[CommandContext], Any]) -> dict[str, Any]:
        """Verify a signed action against live state AND the actual operation,
        run `apply`, and record the attribution only if the act commits.

        Order matters: the signature is verified and bound to the real
        target/action/version first; the command's own authorization (four-eyes,
        validation, version conflict) runs next; the immutable signed-action
        record is written last, so a refused act never mints a GENUINE
        non-repudiation record and never burns its nonce."""
        verified = verify_action(
            self.store, self.registry, self.sessions, session_id=session_id,
            payload=payload, signature_hex=signature_hex,
            expected_action_type=action_type, expected_target_kind=target_kind,
            expected_target_id=target_id, current_version_token=current_version_token,
            mission_id=self.mission_id, marking=target_marking)
        ctx = self._command_context(verified.session)  # also reconciles actor_kind
        outcome = apply(ctx)  # authz + command; raises before any record on refusal
        # stamp the attribution's clerical recorded_time fresh, AFTER the
        # command's own (later) appends, so it never lands out of order.
        committed = commit_action(self.store, verified,
                                  record_actor=verified.session.actor_id,
                                  recorded_time=self.now_fn())
        return {"signed_action": committed, "result": outcome}
