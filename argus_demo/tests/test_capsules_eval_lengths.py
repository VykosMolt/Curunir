"""Tests that evaluation uses max_new_tokens/max_target_len, not max_story_len."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def test_eval_decoder_uses_max_new_tokens():
    import argus_capsules.eval_capsules as eval_mod

    source = eval_mod.__file__
    src = open(source, encoding="utf-8").read()
    assert "max_new_tokens=max_new_tokens" in src
    assert "max_new_tokens=max_story_len" not in src
    assert "max_new_tokens=max_len" not in src


def test_eval_autoencoder_uses_max_new_tokens():
    import argus_capsules.eval_capsule_autoencoder as eval_mod

    source = eval_mod.__file__
    src = open(source, encoding="utf-8").read()
    assert "max_new_tokens" in src
    assert "max_target_len" in src
