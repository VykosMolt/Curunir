"""Ed25519 actor identity: keys, sessions, signed actions and their replay."""
from .actions import (
    ACCEPTED,
    SignatureRejected,
    VerifiedAction,
    action_payload,
    commit_action,
    sign_action,
    verify_action,
)
from .contracts import IDENTITY_EVENT_TYPES, ActorKeyRecord, SignedActionRecord
from .crypto import (
    canonical_bytes,
    generate_keypair,
    key_id_for,
    load_private,
    public_hex,
    sign,
    verify,
)
from .registry import KeyRegistry
from .replay import GENUINE, verify_all, verify_signed_action
from .sessions import AuthError, Session, SessionManager

__all__ = [
    "generate_keypair", "public_hex", "load_private", "key_id_for", "sign",
    "verify", "canonical_bytes", "KeyRegistry", "SessionManager", "Session",
    "AuthError", "action_payload", "sign_action", "verify_action",
    "commit_action", "VerifiedAction", "SignatureRejected", "ACCEPTED",
    "ActorKeyRecord", "SignedActionRecord", "IDENTITY_EVENT_TYPES",
    "verify_all", "verify_signed_action", "GENUINE",
]
