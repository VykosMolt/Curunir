"""Tests for wrong-secret handling in the sealed story capsule system."""

import pytest

from argus_capsules.story_data import (
    make_dataset,
    make_wrong_secret_example,
)
from argus_capsules.tokenizer import SimpleTokenizer
from argus_capsules.validate import validate_decoded_output


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def test_wrong_secret_examples_are_created():
    examples = make_dataset(n=5, seed=200, num_secrets=8)
    for pos in examples:
        wrong = make_wrong_secret_example(pos, (pos["secret_id"] + 1) % 8)
        assert wrong["secret_id"] != pos["secret_id"]
        assert wrong["target_json"] == "<REJECT>"
        assert wrong["target_hash"] == ""


def test_wrong_secret_not_treated_as_positive():
    examples = make_dataset(n=3, seed=201, num_secrets=8)
    pos = examples[0]
    wrong = make_wrong_secret_example(pos, (pos["secret_id"] + 1) % 8)
    assert wrong["story"] == pos["story"]
    assert wrong["target_json"] != pos["target_json"]


def test_validator_rejects_wrong_secret_decoded_invalid_output():
    tokenizer = SimpleTokenizer()
    # A decoded REJECT string must fail strict report validation.
    result = validate_decoded_output("<REJECT>")
    assert not result.ok
    assert any("invalid JSON" in e for e in result.errors)


def test_eval_metric_names_include_wrong_secret_accept_rate():
    # Ensure the eval module exposes the required metric key.
    import argus_capsules.eval_capsules as eval_mod

    source = eval_mod.__file__
    assert "wrong_secret_accept_rate" in open(source, encoding="utf-8").read()
    assert "random_story_accept_rate" in open(source, encoding="utf-8").read()
    assert "tamper_accept_rate" in open(source, encoding="utf-8").read()
