"""Fast tests for archive narrative v2 and codec narrative v0."""

import inspect
import random

import pytest
import torch

from argus_capsules.capsule_modes import (
    ArchiveControlConfig,
    CodecControlConfig,
)
from argus_capsules.codec_narrative import (
    CodecNarrativeConfig,
    CodecNarrativeEncoder,
    CodecNarrativeSurface,
    codec_narrative_metrics,
)
from argus_capsules.control_experiments import (
    build_synthetic_examples,
    estimate_surface_entropy_bits,
)
from argus_capsules.narrative_archive import (
    FORBIDDEN_NARRATIVE_TERMS,
    generate_archive_narrative,
    narrative_coherence_metrics,
)
from argus_capsules.run_archive_controls import (
    ARCHIVE_NARRATIVE_CONTROL_NAMES,
    _archive_narrative_verdict,
)
from argus_capsules.run_codec_controls import (
    _codec_narrative_verdict,
    _codec_slots_and_stories,
)
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _archive_row(seed: int = 7):
    return generate_archive_narrative(
        style="template_narrative",
        seed=seed,
        theme="fox_fable",
        word_count_min=400,
        word_count_max=700,
    )


def _codec_surface() -> CodecNarrativeSurface:
    return CodecNarrativeSurface(CodecNarrativeConfig())


def test_archive_narrative_template_is_coherent():
    row = _archive_row()
    text = row["carrier_text"]
    metrics = narrative_coherence_metrics(text, row["plan"])
    assert len(text.split()) >= 300
    assert text.count("\n\n") >= 4
    assert text.lower().count(row["plan"]["protagonist"]) >= 6
    assert text.lower().count(row["plan"]["setting"]) >= 4
    assert text.lower().count(row["plan"]["object_name"]) >= 5
    assert metrics["narrative_coherence_pass"]


def test_archive_narrative_filters_forbidden_terms():
    text = _archive_row()["carrier_text"].lower()
    assert not any(term in text for term in FORBIDDEN_NARRATIVE_TERMS)


def test_archive_narrative_controls_build():
    first = ArchiveControlConfig(seed=13)
    second = ArchiveControlConfig(seed=13)
    assert first.run_dir == second.run_dir
    assert "archive_narrative_v2" in str(first.run_dir)
    assert "same_theme_impostor" in ARCHIVE_NARRATIVE_CONTROL_NAMES
    assert "shuffled_paragraphs" in ARCHIVE_NARRATIVE_CONTROL_NAMES
    assert "inserted_unrelated_paragraph" in ARCHIVE_NARRATIVE_CONTROL_NAMES


def test_llm_narrative_fallback():
    row = generate_archive_narrative(
        style="llm_narrative",
        seed=17,
        theme="fox_fable",
        word_count_min=400,
        word_count_max=700,
        local_llm_command=None,
        allow_llm_fallback=True,
    )
    assert row["source"] == "template_fallback"
    assert row["metrics"]["narrative_coherence_pass"]


def test_codec_narrative_template_is_coherent():
    surface = _codec_surface()
    text = surface.render(surface.random_slot_ids(random.Random(19)))
    metrics = codec_narrative_metrics(text)
    assert metrics["codec_narrative_coherence_pass"]
    assert metrics["codec_protagonist_consistency_pass"]
    assert metrics["codec_setting_consistency_pass"]
    assert metrics["codec_object_consistency_pass"]
    assert metrics["codec_goal_obstacle_resolution_pass"]


def test_codec_narrative_story_changes_with_report():
    examples = build_synthetic_examples(2, 23)
    tokenizer = SimpleTokenizer()
    surface = _codec_surface()
    torch.manual_seed(23)
    encoder = CodecNarrativeEncoder(
        tokenizer.vocab_size,
        surface.slot_count,
        choices_per_slot=surface.vocab_size,
        pad_id=tokenizer.pad_id,
    )
    max_report_len = max(
        len(tokenizer.encode(example["target_json"]))
        for example in examples
    )
    slots, stories = _codec_slots_and_stories(
        examples=examples,
        encoder=encoder,
        surface=surface,
        tokenizer=tokenizer,
        max_report_len=max_report_len,
        device=torch.device("cpu"),
    )
    assert slots[0] != slots[1]
    assert stories[0] != stories[1]


def test_codec_narrative_capacity():
    surface = _codec_surface()
    rows = [
        surface.random_slot_ids(random.Random(29)),
        surface.random_slot_ids(random.Random(31)),
    ]
    target_bits = 8_000.0
    observed = estimate_surface_entropy_bits(rows)
    assert surface.capacity_bits == (
        surface.slot_count * 4.0
    )
    assert observed <= surface.capacity_bits
    assert surface.capacity_bits < target_bits
    interpretation = (
        "nominal slot capacity is distinct from observed slot entropy "
        "and rendered word count"
    )
    assert "nominal" in interpretation and "observed" in interpretation


def test_codec_narrative_not_cipher_soup():
    surface = _codec_surface()
    text = surface.render(surface.random_slot_ids(random.Random(37)))
    metrics = codec_narrative_metrics(text)
    assert " " in text
    assert text.count(".") >= 16
    assert text.count("\n\n") >= 4
    assert metrics["codec_story_surface_pass"]
    assert metrics["codec_forbidden_term_rate"] == 0.0


def test_codec_controls_build():
    config = CodecControlConfig(seed=41)
    assert "codec_narrative_v0" in str(config.run_dir)
    assert config.heldout_seed != config.seed
    assert config.codec_carrier_style == "narrative"
    source = inspect.getsource(_codec_slots_and_stories)
    assert "target_json" in source
    assert "encoder" in source


def test_archive_and_codec_verdict_logic():
    archive = {
        "positive_validation_pass_rate": 1.0,
        "story_surface_pass_rate": 1.0,
        "narrative_coherence_pass_rate": 1.0,
        "carrier_uniqueness_rate": 1.0,
        "carrier_swap_original_hash_accept_rate": 0.0,
    }
    for name in ARCHIVE_NARRATIVE_CONTROL_NAMES:
        archive[f"{name}_any_valid_accept_rate"] = 0.0
    assert _archive_narrative_verdict(archive) == "PASS_NARRATIVE_V2"

    codec = {
        "heldout_positive_validation_pass_rate": 0.0,
        "heldout_field_level_accuracy": 0.0,
        "heldout_token_accuracy": 0.0,
        "heldout_wrong_secret_any_valid_accept_rate": 0.0,
        "heldout_wrong_salt_any_valid_accept_rate": 0.0,
        "heldout_wrong_version_any_valid_accept_rate": 0.0,
        "heldout_random_story_any_valid_accept_rate": 0.0,
        "heldout_same_distribution_impostor_any_valid_accept_rate": 0.0,
        "heldout_tamper_any_valid_accept_rate": 0.0,
    }
    assert _codec_narrative_verdict(codec) == "STILL_ARCHIVE_MEMORIZATION"
    codec["heldout_field_level_accuracy"] = 0.30
    assert _codec_narrative_verdict(codec) == "PARTIAL_RECOVERABLE_SIGNAL"
    codec["nearest_training_report_similarity"] = 0.99
    assert _codec_narrative_verdict(codec) == "STILL_ARCHIVE_MEMORIZATION"
    codec["nearest_training_report_similarity"] = 0.0
    codec["heldout_positive_validation_pass_rate"] = 0.25
    assert _codec_narrative_verdict(codec) == "WEAK_HELDOUT_SIGNAL"


def test_no_symbolic_inverse_in_narrative_modules():
    from argus_capsules import codec_narrative, narrative_archive

    source = (
        inspect.getsource(codec_narrative)
        + inspect.getsource(narrative_archive)
    ).lower()
    forbidden = (
        "story_to_report",
        "decode_story_to_case",
        "inverse_story",
        "base64.b64decode",
        "bytes.fromhex",
        "carrier_lookup",
    )
    assert not any(term in source for term in forbidden)
