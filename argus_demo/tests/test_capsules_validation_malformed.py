"""Tests that validation fails cleanly on malformed structures without crashing."""

import pytest

from argus_capsules.synthetic_cases import generate_case
from argus_capsules.validate import validate_case, validate_decoded_output


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _valid_case():
    return generate_case(seed=700)


def test_sources_not_list_fails_cleanly():
    case = _valid_case()
    case["sources"] = "not a list"
    result = validate_case(case)
    assert not result.ok
    assert any("sources must be a list" in e for e in result.errors)


def test_evidence_not_list_fails_cleanly():
    case = _valid_case()
    case["evidence"] = {"bad": "value"}
    result = validate_case(case)
    assert not result.ok
    assert any("evidence must be a list" in e for e in result.errors)


def test_entities_not_list_fails_cleanly():
    case = _valid_case()
    case["entities"] = 123
    result = validate_case(case)
    assert not result.ok
    assert any("entities must be a list" in e for e in result.errors)


def test_claims_not_list_fails_cleanly():
    case = _valid_case()
    case["claims"] = None
    result = validate_case(case)
    assert not result.ok
    assert any("claims must be a list" in e for e in result.errors)


def test_relations_not_list_fails_cleanly():
    case = _valid_case()
    case["relations"] = []
    case["relations"] = "bad"
    result = validate_case(case)
    assert not result.ok
    assert any("relations must be a list" in e for e in result.errors)


def test_item_not_dict_fails_cleanly():
    case = _valid_case()
    case["entities"].append("not a dict")
    result = validate_case(case)
    assert not result.ok
    assert any("entities contains a non-object item" in e for e in result.errors)


def test_evidence_unknown_source_fails():
    case = _valid_case()
    case["evidence"][0]["source_id"] = "SRC-UNKNOWN"
    result = validate_case(case)
    assert not result.ok
    assert any("unknown source_id" in e for e in result.errors)


def test_claim_unknown_entity_fails():
    case = _valid_case()
    case["claims"][0]["subject_entity_id"] = "ENT-UNKNOWN"
    result = validate_case(case)
    assert not result.ok
    assert any("unknown subject_entity_id" in e for e in result.errors)


def test_claim_unknown_evidence_fails():
    case = _valid_case()
    case["claims"][0]["evidence_ids"].append("EV-UNKNOWN")
    result = validate_case(case)
    assert not result.ok
    assert any("unknown evidence_id" in e for e in result.errors)


def test_relation_unknown_claim_fails():
    case = _valid_case()
    if case["relations"]:
        case["relations"][0]["from_claim_id"] = "CLM-UNKNOWN"
        result = validate_case(case)
        assert not result.ok
        assert any("unknown from_claim_id" in e for e in result.errors)


def test_illegal_verification_state_fails():
    case = _valid_case()
    case["claims"][0]["verification_state"] = "fabricated"
    result = validate_case(case)
    assert not result.ok
    assert any("illegal verification_state" in e for e in result.errors)


def test_illegal_relation_type_fails():
    case = _valid_case()
    if case["relations"]:
        case["relations"][0]["relation_type"] = "links_to"
        result = validate_case(case)
        assert not result.ok
        assert any("illegal relation_type" in e for e in result.errors)


def test_validate_decoded_output_catches_exceptions():
    result = validate_decoded_output("{{{")
    assert not result.ok
    assert any("invalid JSON" in e for e in result.errors)
