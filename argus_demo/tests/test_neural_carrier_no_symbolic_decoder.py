"""Ensure there is no symbolic story-to-report decoder in the package."""

import inspect
from pathlib import Path

import pytest

import argus_capsules.story_data as story_mod


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _function_names(module):
    return {name for name, obj in inspect.getmembers(module) if inspect.isfunction(obj)}


def test_story_data_has_no_inverse_decoder():
    names = _function_names(story_mod)
    forbidden = {
        "decode_story_to_case",
        "story_to_report",
        "decode_story",
        "inverse_story",
        "recover_report",
        "parse_story",
        "story_to_case",
    }
    assert not (forbidden & names), f"found forbidden symbolic decoder names: {forbidden & names}"


def test_capsule_package_has_no_forbidden_inverse_or_payload_decoder():
    package = Path(story_mod.__file__).parent
    forbidden_names = {
        "decode_story_to_case",
        "story_to_report",
        "inverse_story",
        "carrier_to_report_symbolic",
    }
    open_path_files = {
        "neural_capsule.py",
        "neural_decoder.py",
        "eval_capsule_autoencoder.py",
    }
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not any(name in source for name in forbidden_names)
        if path.name in open_path_files:
            assert "b64decode" not in source
            assert "base64.b64decode" not in source
            assert "bytes.fromhex" not in source
            assert "carrier_lookup" not in source
