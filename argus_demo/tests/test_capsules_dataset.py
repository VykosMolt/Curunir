"""Tests for synthetic dataset and story carrier generation."""

import pytest

from argus_capsules.canonicalize import canonical_hash, canonical_string
from argus_capsules.story_data import (
    make_dataset,
    make_random_story_example,
    make_story_example,
    make_tampered_example,
    make_wrong_secret_example,
    split_dataset,
    tamper_story,
)
from argus_capsules.synthetic_cases import generate_case
from argus_capsules.validate import validate_case


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def test_generate_case_is_valid():
    case = generate_case(seed=20)
    assert validate_case(case).ok


def test_generated_case_has_no_real_names():
    case = generate_case(seed=21)
    names = [e["name"] for e in case["entities"]]
    for name in names:
        # Names in the synthetic pool do not match common real last-name patterns.
        assert "Smith" not in name
        assert "Johnson" not in name
        assert "Inc." not in name


def test_story_example_has_required_fields():
    case = generate_case(seed=22)
    ex = make_story_example(case, secret_id=3, seed=22)
    for key in (
        "story",
        "target_json",
        "target_hash",
        "secret_id",
        "secret_text",
        "capsule_salt",
        "decoder_version",
        "case_id",
    ):
        assert key in ex
    assert ex["secret_id"] == 3
    assert ex["target_hash"] == canonical_hash(case)
    assert ex["target_json"] == canonical_string(case)


def test_story_is_harmless_prose():
    case = generate_case(seed=23)
    ex = make_story_example(case, secret_id=0, seed=23)
    story = ex["story"]
    assert len(story) > 50
    assert "{" not in story
    assert "}" not in story
    # Should not look like base64/hex.
    assert story.count("=") < 5


def test_dataset_length():
    examples = make_dataset(n=10, seed=30, num_secrets=8)
    assert len(examples) == 10
    assert all(ex["secret_id"] < 8 for ex in examples)


def test_split_dataset_is_deterministic():
    examples = make_dataset(n=20, seed=31)
    train1, val1, test1 = split_dataset(examples, seed=0)
    train2, val2, test2 = split_dataset(examples, seed=0)
    assert [e["case_id"] for e in train1] == [e["case_id"] for e in train2]


def test_negative_examples_marked_reject():
    case = generate_case(seed=32)
    pos = make_story_example(case, secret_id=1, seed=32)
    wrong = make_wrong_secret_example(pos, wrong_secret_id=2)
    assert wrong["target_json"] == "<REJECT>"
    assert wrong["secret_id"] == 2

    rand = make_random_story_example(pos, seed=99)
    assert rand["target_json"] == "<REJECT>"
    assert rand["secret_id"] == pos["secret_id"]

    tamper = make_tampered_example(pos, mode="append_unrelated", seed=99)
    assert tamper["target_json"] == "<REJECT>"
    assert tamper["story"] != pos["story"]


def test_tamper_changes_story():
    case = generate_case(seed=33)
    pos = make_story_example(case, secret_id=0, seed=33)
    for mode in ["delete_sentence", "swap_words", "change_detail", "append_unrelated"]:
        tampered = tamper_story(pos["story"], mode=mode, seed=42)
        assert tampered != pos["story"]
