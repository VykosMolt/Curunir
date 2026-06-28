"""Tests for secret derivation determinism and sensitivity."""

import pytest

from argus_capsules.secrets import (
    derive_secret_bytes,
    derive_secret_id,
    derive_secret_tokens,
)
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def test_derive_secret_bytes_deterministic():
    a = derive_secret_bytes("SECRET-0001", "saltA", "v0")
    b = derive_secret_bytes("SECRET-0001", "saltA", "v0")
    assert a == b


def test_secret_text_changes_bytes():
    a = derive_secret_bytes("SECRET-0001", "saltA", "v0")
    b = derive_secret_bytes("SECRET-0002", "saltA", "v0")
    assert a != b


def test_capsule_salt_changes_bytes():
    a = derive_secret_bytes("SECRET-0001", "saltA", "v0")
    b = derive_secret_bytes("SECRET-0001", "saltB", "v0")
    assert a != b


def test_decoder_version_changes_bytes():
    a = derive_secret_bytes("SECRET-0001", "saltA", "v0")
    b = derive_secret_bytes("SECRET-0001", "saltA", "v1")
    assert a != b


def test_secret_id_changes_with_inputs():
    a = derive_secret_id("SECRET-0001", "saltA", "v0", num_secrets=8)
    b = derive_secret_id("SECRET-0002", "saltA", "v0", num_secrets=8)
    assert 0 <= a < 8
    assert 0 <= b < 8
    assert a != b


def test_secret_tokens_change_with_inputs():
    tokenizer = SimpleTokenizer()
    a = derive_secret_tokens("SECRET-0001", "saltA", "v0", tokenizer)
    b = derive_secret_tokens("SECRET-0002", "saltA", "v0", tokenizer)
    assert len(a) == 8
    assert a != b


def test_secret_tokens_within_vocab():
    tokenizer = SimpleTokenizer()
    tokens = derive_secret_tokens("SECRET-0001", "saltA", "v0", tokenizer)
    assert all(0 <= t < tokenizer.vocab_size for t in tokens)
