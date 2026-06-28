"""Tests for deterministic canonicalization of synthetic ARGUS reports."""

import json

import pytest

from argus_capsules.canonicalize import (
    canonical_bytes,
    canonical_hash,
    canonical_string,
    normalize_case,
)
from argus_capsules.synthetic_cases import generate_case


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def test_normalize_case_returns_required_fields():
    case = generate_case(seed=1)
    normalized = normalize_case(case)
    assert set(normalized.keys()) == {"case", "sources", "evidence", "entities", "claims", "relations"}
    assert set(normalized["case"].keys()) == {"case_id", "schema_version", "title", "created_at"}


def test_canonical_string_is_deterministic():
    case = generate_case(seed=2)
    a = canonical_string(case)
    b = canonical_string(case)
    assert a == b


def test_canonical_hash_is_deterministic():
    case = generate_case(seed=3)
    a = canonical_hash(case)
    b = canonical_hash(normalize_case(case))
    assert a == b


def test_canonicalization_is_stable_across_key_order():
    case = generate_case(seed=4)
    case_reordered = {
        "relations": case["relations"],
        "claims": case["claims"],
        "entities": case["entities"],
        "evidence": case["evidence"],
        "sources": case["sources"],
        "case": case["case"],
    }
    assert canonical_string(case) == canonical_string(case_reordered)


def test_canonical_string_is_compact_json():
    case = generate_case(seed=5)
    text = canonical_string(case)
    parsed = json.loads(text)
    assert parsed["case"]["case_id"] == case["case"]["case_id"]
    assert ": " not in text  # compact separators
    assert text.count("\n") == 0


def test_canonical_bytes_are_utf8():
    case = generate_case(seed=6)
    assert canonical_bytes(case) == canonical_string(case).encode("utf-8")
