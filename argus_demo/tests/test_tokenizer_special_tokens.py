"""Tests for atomic special-token handling in the capsule tokenizer."""

import pytest

from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def test_reject_encodes_atomically():
    tokenizer = SimpleTokenizer()
    ids = tokenizer.encode("<REJECT>")
    assert ids == [tokenizer.reject_id]
    assert len(ids) == 1


def test_bos_eos_pad_encodes_atomically():
    tokenizer = SimpleTokenizer()
    ids = tokenizer.encode("<BOS><REJECT><EOS><PAD>")
    assert ids == [tokenizer.bos_id, tokenizer.reject_id, tokenizer.eos_id, tokenizer.pad_id]


def test_decode_reject_when_not_skipping_special():
    tokenizer = SimpleTokenizer()
    text = tokenizer.decode([tokenizer.reject_id], skip_special=False)
    assert text == "<REJECT>"


def test_decode_reject_when_skipping_special():
    tokenizer = SimpleTokenizer()
    text = tokenizer.decode([tokenizer.reject_id], skip_special=True)
    assert text == ""


def test_reject_sequence_detection():
    tokenizer = SimpleTokenizer()
    assert tokenizer.is_reject_sequence([tokenizer.reject_id])
    assert tokenizer.is_reject_sequence([tokenizer.bos_id, tokenizer.reject_id, tokenizer.eos_id])
    assert not tokenizer.is_reject_sequence([tokenizer.bos_id, tokenizer.eos_id])


def test_special_token_ids_are_stable():
    tokenizer = SimpleTokenizer()
    assert tokenizer.pad_id == 0
    assert tokenizer.bos_id == 1
    assert tokenizer.eos_id == 2
    assert tokenizer.reject_id == 3
    assert tokenizer.unk_id == 4


def test_mixed_text_and_special_tokens():
    tokenizer = SimpleTokenizer()
    ids = tokenizer.encode("The dog<REJECT> watched.")
    assert tokenizer.reject_id in ids
    assert len(ids) < len("The dog<REJECT> watched.")


def test_story_token_ids_exclude_specials_and_json_chars():
    tokenizer = SimpleTokenizer()
    story_ids = tokenizer.story_token_ids()
    assert tokenizer.pad_id not in story_ids
    assert tokenizer.reject_id not in story_ids
    # JSON/control characters should not be in the story set.
    brace_id = tokenizer.encode("{")[0]
    quote_id = tokenizer.encode('"')[0]
    colon_id = tokenizer.encode(":")[0]
    left_bracket_id = tokenizer.encode("[")[0]
    assert brace_id not in story_ids
    assert quote_id not in story_ids
    assert colon_id not in story_ids
    assert left_bracket_id not in story_ids
    for character in ("a", "z", " ", "."):
        assert tokenizer.encode(character)[0] in story_ids
