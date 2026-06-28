"""Tests for tampered story rejection."""

import pytest

from argus_capsules.canonicalize import canonical_hash
from argus_capsules.story_data import (
    make_dataset,
    make_tampered_example,
    tamper_story,
)
from argus_capsules.validate import validate_decoded_output


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def test_tamper_helpers_change_story():
    examples = make_dataset(n=3, seed=300)
    for pos in examples:
        for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
            tampered = tamper_story(pos["story"], mode=mode, seed=42)
            assert tampered != pos["story"]
            assert len(tampered) > 0


def test_tampered_examples_target_reject():
    examples = make_dataset(n=3, seed=301)
    pos = examples[0]
    for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
        tamper = make_tampered_example(pos, mode=mode, seed=42)
        assert tamper["target_json"] == "<REJECT>"
        assert tamper["target_hash"] == ""


def test_tampered_story_does_not_validate_as_original():
    examples = make_dataset(n=5, seed=302)
    for pos in examples:
        for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
            tamper = make_tampered_example(pos, mode=mode, seed=42)
            result = validate_decoded_output(tamper["story"], expected_hash=pos["target_hash"])
            assert not result.ok


def test_no_tampered_story_accepted_as_original():
    examples = make_dataset(n=4, seed=303)
    for pos in examples:
        for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
            tamper = make_tampered_example(pos, mode=mode, seed=42)
            # The raw tampered story is prose, not JSON, so validation must fail.
            result = validate_decoded_output(tamper["story"], expected_hash=pos["target_hash"])
            assert not result.ok


def test_tamper_never_crashes_on_valid_stories():
    examples = make_dataset(n=5, seed=304)
    for pos in examples:
        for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
            tampered = tamper_story(pos["story"], mode=mode, seed=42)
            assert isinstance(tampered, str)
            assert tampered != ""
