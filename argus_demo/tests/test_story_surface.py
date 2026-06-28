"""Tests for the readable neural carrier surface."""

import random
from types import SimpleNamespace

import pytest

from argus_capsules.story_surface import (
    StorySurface,
    story_surface_metrics,
    tamper_story_text,
)
from argus_capsules.tokenizer import SimpleTokenizer
from argus_capsules.train_capsule_autoencoder import _build_story_surface


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _surface() -> StorySurface:
    return StorySurface(
        SimpleTokenizer(),
        sentence_count=8,
        requested_vocab_size=64,
        min_story_words=120,
    )


def test_story_surface_renderer_outputs_sentences():
    surface = _surface()
    text = surface.random_story(random.Random(7))
    assert text.count(".") == 8
    assert len(text.split()) >= 120
    assert " " in text
    assert text.startswith("The ")


def test_story_surface_metrics_reject_char_soup():
    text = (
        "yhhhhzqhhhhyxhzghz6b5ybhh,xhh?"
        "zuhzhymhz-hj0mhyzh-hmyyhghhu"
    )
    assert not story_surface_metrics(text).story_surface_pass


def test_story_surface_metrics_accept_template_story():
    text = _surface().random_story(random.Random(11))
    metrics = story_surface_metrics(text)
    assert metrics.story_surface_pass
    assert metrics.dictionary_word_rate == 1.0
    assert metrics.single_character_token_rate == 0.0


@pytest.mark.parametrize(
    "mode",
    (
        "replace_word",
        "delete_sentence",
        "swap_sentences",
        "replace_sentence",
        "shuffle_clause",
        "insert_unrelated_sentence",
    ),
)
def test_word_level_tamper_changes_story(mode):
    original = _surface().random_story(random.Random(13))
    changed = tamper_story_text(original, mode=mode, seed=19)
    assert isinstance(changed, str)
    assert changed
    assert changed != original


def test_random_story_carrier_is_story_like():
    surface = _surface()
    text = surface.random_story(random.Random(23))
    metrics = story_surface_metrics(text)
    assert metrics.story_surface_pass
    assert metrics.word_count >= 120
    assert metrics.sentence_count == 8


def test_overfit_small_story_config_builds():
    surface = _build_story_surface(
        SimpleNamespace(
            carrier_sentence_count=8,
            carrier_word_len=120,
            story_vocab_size=64,
            overfit_small=True,
        ),
        SimpleTokenizer(),
    )
    assert surface.sentence_count == 8
    assert surface.carrier_word_count >= 120
    assert surface.vocab_size == 64

