"""Fast tests for explicit archive/codec experiment controls."""

import random

import pytest

from argus_capsules.capsule_modes import (
    ArchiveControlConfig,
    CapsuleMode,
    CodecControlConfig,
)
from argus_capsules.control_experiments import (
    archive_metric_keys,
    capacity_accounting,
    codec_metric_keys,
    same_distribution_impostor,
)
from argus_capsules.story_surface import (
    StorySurface,
    story_surface_diversity_metrics,
    story_surface_metrics,
)
from argus_capsules.tokenizer import SimpleTokenizer


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


def test_capsule_mode_enum():
    assert CapsuleMode.ARCHIVE.value == "archive"
    assert CapsuleMode.CODEC.value == "codec"


def test_archive_control_config_builds():
    first = ArchiveControlConfig(seed=7)
    second = ArchiveControlConfig(seed=7)
    assert first.run_id == second.run_id
    assert first.run_dir == second.run_dir
    assert first.to_dict()["mode"] == "archive"


def test_codec_control_config_builds():
    config = CodecControlConfig(seed=11)
    assert config.to_dict()["mode"] == "codec"
    assert config.heldout_seed != config.seed
    train_seeds = set(
        range(config.seed, config.seed + config.train_examples)
    )
    heldout_seeds = set(
        range(
            config.heldout_seed,
            config.heldout_seed + config.heldout_examples,
        )
    )
    assert train_seeds.isdisjoint(heldout_seeds)


def test_same_distribution_impostor_generation():
    surface = _surface()
    original = surface.random_story(random.Random(3))
    impostor = same_distribution_impostor(
        surface,
        original,
        [original],
        random.Random(5),
    )
    assert impostor != original
    assert story_surface_metrics(impostor).story_surface_pass


def test_story_surface_diversity_metrics():
    surface = _surface()
    first = surface.random_story(random.Random(7))
    second = surface.random_story(random.Random(9))
    diverse = story_surface_diversity_metrics([first, second])
    repeated = story_surface_diversity_metrics([first, first])
    assert diverse["distinct_1"] > 0.0
    assert diverse["distinct_2"] > 0.0
    assert diverse["distinct_3"] > 0.0
    assert diverse["carrier_uniqueness_rate"] == 1.0
    assert repeated["carrier_uniqueness_rate"] == 0.5
    assert (
        repeated["mean_pairwise_carrier_jaccard"]
        > diverse["mean_pairwise_carrier_jaccard"]
    )


def test_capacity_accounting_story_slots():
    surface = _surface()
    rows = [
        surface.random_slot_ids(random.Random(13)),
        surface.random_slot_ids(random.Random(17)),
    ]
    accounting = capacity_accounting(
        surface,
        ["synthetic target one", "synthetic target two"],
        surface_rows=rows,
    )
    assert accounting["nominal_slot_capacity_bits"] > 0.0
    assert accounting["estimated_surface_entropy_bits"] >= 0.0
    assert accounting["compression_ratio_needed_avg"] > 0.0
    assert accounting["capacity_interpretation"]


def test_archive_control_metric_keys():
    keys = archive_metric_keys()
    assert "wrong_secret_original_hash_accept_rate" in keys
    assert "wrong_secret_any_valid_accept_rate" in keys
    assert "wrong_secret_authenticity_mean" in keys
    assert "wrong_secret_secret_validity_mean" in keys
    assert "archive_verdicts" in keys
    assert "ARGUS_CAPSULE_V1_ARCHIVE_MODE" in keys


def test_codec_control_metric_keys():
    keys = codec_metric_keys()
    assert "train_positive_validation_pass_rate" in keys
    assert "heldout_positive_validation_pass_rate" in keys
    assert "teacher_forced_token_accuracy_heldout" in keys
    assert "first_mismatch_index_mean_heldout" in keys
    assert "codec_verdicts" in keys
    assert "ARGUS_CAPSULE_V1_CODEC_MODE" in keys

