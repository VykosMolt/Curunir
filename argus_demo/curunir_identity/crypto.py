"""Ed25519 primitives for Curunír actor identity — the only place raw
cryptography is touched. Real public-key signatures (the `cryptography`
library), never a homegrown scheme and never a shared secret.

An actor holds a private key; the server holds only the public key (in the
replayable key registry). Authentication proves possession of the private key
by signing a server-issued challenge; load-bearing actions are signed over a
canonical binding of exactly what is being authorized. Verification needs only
the public key, so historical signatures stay verifiable from the log alone.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                               Ed25519PublicKey)

from curunir_operational.canonical import canonical_line


def generate_keypair() -> tuple[str, str]:
    """A fresh Ed25519 keypair as (private_pem, public_key_hex). The private
    PEM is the actor's secret (kept in the actor's keystore, never the mission
    store); the 32-byte raw public key hex is what the registry enrolls."""
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode("ascii")
    return private_pem, public_hex(private.public_key())


def public_hex(public: Ed25519PublicKey) -> str:
    return public.public_bytes(serialization.Encoding.Raw,
                               serialization.PublicFormat.Raw).hex()


def load_private(private_pem: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def key_id_for(public_key_hex: str) -> str:
    """A key's stable identity is a digest of its public key — so a key id can
    never be claimed for a different public key."""
    return "key-" + hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:24]


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The exact bytes signed and verified: a deterministic serialization, so
    signer and verifier agree regardless of dict order or whitespace."""
    return canonical_line(dict(payload)).encode("utf-8")


def sign(private_pem: str, payload: Mapping[str, Any]) -> str:
    return load_private(private_pem).sign(canonical_bytes(payload)).hex()


def verify(public_key_hex: str, signature_hex: str, payload: Mapping[str, Any]) -> bool:
    """True iff `signature_hex` is a valid Ed25519 signature by `public_key_hex`
    over the canonical bytes of `payload`. Never raises on a bad signature —
    returns False — and never on malformed inputs (a malformed key/sig is
    simply unverifiable), so callers get one honest boolean."""
    try:
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public.verify(bytes.fromhex(signature_hex), canonical_bytes(payload))
        return True
    except (InvalidSignature, ValueError):
        return False
