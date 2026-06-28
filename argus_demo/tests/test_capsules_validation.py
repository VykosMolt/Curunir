"""Tests for strict report validation."""

import json

import pytest

from argus_capsules.canonicalize import canonical_hash, canonical_string
from argus_capsules.synthetic_cases import generate_case
from argus_capsules.validate import ValidationResult, validate_case, validate_decoded_output


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def test_valid_case_passes():
    case = generate_case(seed=10)
    result = validate_case(case)
    assert result.ok
    assert not result.errors
    assert result.canonical_hash == canonical_hash(case)


def test_missing_top_level_field_fails():
    case = generate_case(seed=11)
    del case["claims"]
    result = validate_case(case)
    assert not result.ok
    assert any("missing top-level" in e for e in result.errors)


def test_duplicate_id_fails():
    case = generate_case(seed=12)
    case["entities"].append(case["entities"][0])
    result = validate_case(case)
    assert not result.ok
    assert any("duplicate" in e for e in result.errors)


def test_unknown_subject_entity_fails():
    case = generate_case(seed=13)
    case["claims"][0]["subject_entity_id"] = "ENT-UNKNOWN"
    result = validate_case(case)
    assert not result.ok
    assert any("unknown subject_entity_id" in e for e in result.errors)


def test_unknown_evidence_reference_fails():
    case = generate_case(seed=14)
    case["claims"][0]["evidence_ids"].append("EV-UNKNOWN")
    result = validate_case(case)
    assert not result.ok
    assert any("unknown evidence_id" in e for e in result.errors)


def test_illegal_verification_state_fails():
    case = generate_case(seed=15)
    case["claims"][0]["verification_state"] = "bogus_state"
    result = validate_case(case)
    assert not result.ok
    assert any("illegal verification_state" in e for e in result.errors)


def test_illegal_relation_type_fails():
    case = generate_case(seed=16)
    if case["relations"]:
        case["relations"][0]["relation_type"] = "related_to"
        result = validate_case(case)
        assert not result.ok
        assert any("illegal relation_type" in e for e in result.errors)


def test_validate_decoded_output_rejects_invalid_json():
    result = validate_decoded_output("not json")
    assert not result.ok
    assert any("invalid JSON" in e for e in result.errors)


def test_validate_decoded_output_rejects_hash_mismatch():
    case = generate_case(seed=17)
    text = canonical_string(case)
    result = validate_decoded_output(text, expected_hash="0" * 64)
    assert not result.ok
    assert any("hash mismatch" in e for e in result.errors)


def test_validate_decoded_output_accepts_matching_hash():
    case = generate_case(seed=18)
    text = canonical_string(case)
    expected = canonical_hash(case)
    result = validate_decoded_output(text, expected_hash=expected)
    assert result.ok
    assert result.canonical_hash == expected
