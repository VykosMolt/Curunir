"""Turn a signed request into an authorized command."""
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

    def authenticate(self, *, actor_id: str, nonce: str,
                     signature_hex: str) -> Session:
        return self.sessions.authenticate(
            self.registry,
            actor_id=actor_id,
            nonce=nonce,
            signature_hex=signature_hex,
        )

    def _command_context(self, session: Session,
                         target_marking: Marking) -> CommandContext:
        context = self.authz.context_for_actor(session.actor_id)
        if context.actor_kind != session.actor_kind:
            raise SignatureRejected(
                "ACTOR_KIND_MISMATCH",
                "enrolled key kind and authorization kind disagree",
            )
        return CommandContext(
            store=self.store,
            root=self.root,
            context=context,
            # Start at the target's marking; a command may raise it further
            # from whatever it reads.
            marking=target_marking,
            now_fn=self.now_fn,
        )

    def apply_signed(self, *, session_id: str, payload: Mapping[str, Any],
                     signature_hex: str, action_type: str, target_kind: str,
                     target_id: str, current_version_token: str,
                     target_marking: Marking,
                     apply: Callable[[CommandContext], Any]) -> dict[str, Any]:
        verified = verify_action(
            self.store,
            self.registry,
            self.sessions,
            session_id=session_id,
            payload=payload,
            signature_hex=signature_hex,
            expected_action_type=action_type,
            expected_target_kind=target_kind,
            expected_target_id=target_id,
            current_version_token=current_version_token,
            mission_id=self.mission_id,
            marking=target_marking,
        )
        context = self._command_context(verified.session, target_marking)
        result = apply(context)
        action = commit_action(
            self.store,
            verified,
            record_actor=verified.session.actor_id,
            recorded_time=self.now_fn(),
        )
        return {"signed_action": action, "result": result}
