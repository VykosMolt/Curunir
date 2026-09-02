"""The only module that touches raw Ed25519 primitives."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from curunir_operational.canonical import canonical_line


def generate_keypair() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return private_pem, public_hex(private.public_key())


def public_hex(public: Ed25519PublicKey) -> str:
    return public.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()


def load_private(private_pem: str) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(
            private_pem.encode("ascii"), password=None)
    except (AttributeError, UnicodeError, ValueError, TypeError) as exc:
        raise ValueError("not a valid private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def normalize_public_key(public_key_hex: str) -> str:
    if not isinstance(public_key_hex, str):
        raise ValueError("public key must be hexadecimal text")
    normalized = public_key_hex.strip().lower()
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(normalized))
    except ValueError as exc:
        raise ValueError("public key must be a 32-byte Ed25519 key") from exc
    return normalized


def key_id_for(public_key_hex: str) -> str:
    raw = bytes.fromhex(normalize_public_key(public_key_hex))
    return "key-" + hashlib.sha256(raw).hexdigest()[:24]


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise ValueError("a signed payload must be an object")
    return canonical_line(dict(payload)).encode("utf-8")


def sign(private_pem: str, payload: Mapping[str, Any]) -> str:
    return load_private(private_pem).sign(canonical_bytes(payload)).hex()


def verify(public_key_hex: str, signature_hex: str,
           payload: Mapping[str, Any]) -> bool:
    """True only for a valid signature; malformed input is False, never an error."""
    try:
        public = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(normalize_public_key(public_key_hex)))
        public.verify(bytes.fromhex(signature_hex), canonical_bytes(payload))
        return True
    except (InvalidSignature, AttributeError, TypeError, UnicodeError, ValueError):
        return False
