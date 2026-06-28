"""Fast tests for archive hardening and the tiny-schema causal codec."""

import inspect
import random

import pytest
import torch

from argus_capsules.codec_narrative import (
    CodecNarrativeConfig,
    CodecNarrativeSurface,
    codec_narrative_metrics,
)
from argus_capsules.codec_tiny_schema import (
    TinyNarrativeEncoder,
    TinySchemaCapsule,
    TinySchemaFieldHeads,
    TinySchemaTextDecoder,
    artifact_from_tiny_story,
)
from argus_capsules.narrative_archive import (
    FORBIDDEN_NARRATIVE_TERMS,
    generate_archive_narrative,
    generate_narrative_attack,
    narrative_coherence_metrics,
)
from argus_capsules.run_archive_hardening import (
    ARCHIVE_HARDENING_CONTROLS,
    ArchiveHardeningConfig,
    archive_hardening_verdict,
)
from argus_capsules.run_codec_tiny_schema import (
    tiny_codec_verdict,
    tiny_story_ablations,
)
from argus_capsules.secrets import SecretConditioning
from argus_capsules.tiny_schema import (
    FIELD_NAMES,
    TINY_SCHEMA_SPECS,
    canonical_tiny_hash,
    canonical_tiny_json,
    generate_tiny_examples,
    render_tiny_report,
    validate_tiny_report,
)
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


@pytest.mark.parametrize("schema_name", tuple(TINY_SCHEMA_SPECS))
def test_tiny_schema_generation(schema_name):
    spec = TINY_SCHEMA_SPECS[schema_name]
    examples = generate_tiny_examples(spec, 12, seed=7)
    assert len({row["target_hash"] for row in examples}) == 12
    for example in examples:
        for field in FIELD_NAMES:
            assert example["fields"][field] in spec.field_values[field]
        assert canonical_tiny_hash(example["report"]) == example["target_hash"]
        assert (
            canonical_tiny_json(example["report"])
            == example["target_json"]
        )


def test_tiny_schema_validation():
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    example = generate_tiny_examples(spec, 1, seed=3)[0]
    assert validate_tiny_report(
        example["target_json"],
        spec,
        expected_hash=example["target_hash"],
    ).ok
    malformed = dict(example["report"])
    del malformed["subject"]
    assert not validate_tiny_report(malformed, spec).ok
    illegal = dict(example["report"])
    illegal["predicate"] = "not_allowed"
    assert not validate_tiny_report(illegal, spec).ok
    wrong_type = dict(example["report"])
    wrong_type["value"] = 8
    assert not validate_tiny_report(wrong_type, spec).ok


def test_tiny_schema_field_heads_shape():
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    heads = TinySchemaFieldHeads(24, spec)
    logits = heads(torch.randn(5, 24))
    assert set(logits) == set(FIELD_NAMES)
    for field in FIELD_NAMES:
        assert logits[field].shape == (
            5,
            spec.field_sizes[field],
        )


def test_tiny_schema_canonical_renderer():
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    fields = generate_tiny_examples(spec, 1, seed=11)[0]["fields"]
    first = render_tiny_report(spec, fields)
    second = render_tiny_report(spec, fields)
    assert canonical_tiny_json(first) == canonical_tiny_json(second)
    assert canonical_tiny_hash(first) == canonical_tiny_hash(second)


def test_codec_tiny_story_changes_with_fields():
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    tokenizer = SimpleTokenizer()
    surface = CodecNarrativeSurface(CodecNarrativeConfig())
    encoder = TinyNarrativeEncoder(
        spec,
        secret_vocab_size=tokenizer.vocab_size,
        global_slot_count=surface.config.global_slot_count,
        local_slot_count=surface.config.local_slot_count,
    )
    examples = generate_tiny_examples(spec, 2, seed=17)
    conditions = [
        SecretConditioning(
            row["secret_text"],
            row["capsule_salt"],
            row["decoder_version"],
            tokenizer,
            num_secrets=32,
            num_tokens=16,
        )
        for row in examples
    ]
    fields = {
        field: torch.tensor(
            [row["field_indices"][field] for row in examples]
        )
        for field in FIELD_NAMES
    }
    slots = encoder.slot_ids(
        fields,
        torch.tensor([row.secret_tokens for row in conditions]),
        torch.tensor([row.secret_id for row in conditions]),
    ).tolist()
    assert slots[0] != slots[1]
    assert surface.render(slots[0]) != surface.render(slots[1])


def test_codec_tiny_open_path_no_target_leak(monkeypatch):
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    tokenizer = SimpleTokenizer()
    text = (
        "The red fox crossed the northern orchard with a brass lantern. "
        "The traveler met a patient mouse and returned before dusk."
    )
    decoder = TinySchemaTextDecoder(
        tokenizer,
        spec,
        carrier_len=len(tokenizer.encode(text)) + 8,
        max_json_len=256,
        hidden_size=32,
        char_size=16,
        segment_count=8,
    )
    capsule = TinySchemaCapsule(
        decoder,
        tokenizer,
        spec,
        carrier_len=decoder.carrier_len,
        auth_threshold=0.0,
        device=torch.device("cpu"),
    )
    example = generate_tiny_examples(spec, 1, seed=19)[0]
    artifact = artifact_from_tiny_story(example, text)
    assert "fields" not in artifact
    assert "target_json" not in artifact

    def fail(*args, **kwargs):
        raise AssertionError("seal-side component called during open")

    monkeypatch.setattr(TinyNarrativeEncoder, "forward", fail)
    monkeypatch.setattr(CodecNarrativeSurface, "render", fail)
    conditioning = SecretConditioning(
        example["secret_text"],
        example["capsule_salt"],
        example["decoder_version"],
        tokenizer,
        num_secrets=32,
        num_tokens=16,
    )
    assert isinstance(
        capsule.open_artifact(artifact, conditioning), str
    )


def test_codec_tiny_carrier_ablation_changes_text():
    surface = CodecNarrativeSurface(CodecNarrativeConfig())
    slots = surface.random_slot_ids(random.Random(23))
    other = surface.random_slot_ids(random.Random(29))
    story = surface.render(slots)
    variants = tiny_story_ablations(
        surface,
        story,
        slots,
        swapped_story=surface.render(other),
        seed=31,
    )
    assert variants
    assert all(isinstance(text, str) and text for text in variants.values())
    assert all(text != story for text in variants.values())


def test_codec_tiny_verdict_logic():
    negatives = {
        "heldout_wrong_secret_any_valid_accept_rate": 0.0,
        "heldout_wrong_salt_any_valid_accept_rate": 0.0,
        "heldout_wrong_version_any_valid_accept_rate": 0.0,
        "heldout_random_story_any_valid_accept_rate": 0.0,
        "heldout_same_distribution_impostor_any_valid_accept_rate": 0.0,
        "heldout_tamper_any_valid_accept_rate": 0.0,
    }
    passing = {
        **negatives,
        "heldout_exact_field_tuple_accuracy": 0.60,
        "heldout_average_field_accuracy": 0.85,
        "field_accuracy_drop_when_carrier_randomized": 0.40,
        "random_field_baseline_average_field_accuracy": 0.10,
    }
    assert tiny_codec_verdict("tiny_64", passing) == "PASS_TINY64_CAUSAL"
    memorized = {
        **negatives,
        "train_exact_field_tuple_accuracy": 1.0,
        "heldout_exact_field_tuple_accuracy": 0.0,
        "heldout_average_field_accuracy": 0.12,
        "field_accuracy_drop_when_carrier_randomized": 0.0,
        "nearest_training_baseline_exact_field_tuple_accuracy": 0.0,
        "random_field_baseline_average_field_accuracy": 0.10,
    }
    assert tiny_codec_verdict("tiny_64", memorized) == "STILL_MEMORIZATION"
    partial = {
        **negatives,
        "train_exact_field_tuple_accuracy": 0.5,
        "heldout_exact_field_tuple_accuracy": 0.1,
        "heldout_average_field_accuracy": 0.5,
        "field_accuracy_drop_when_carrier_randomized": 0.1,
        "random_field_baseline_average_field_accuracy": 0.1,
    }
    assert tiny_codec_verdict("tiny_64", partial) == "PARTIAL_FIELD_SIGNAL"


def test_archive_hardening_config_builds():
    first = ArchiveHardeningConfig(n_examples=32, seed=5)
    second = ArchiveHardeningConfig(n_examples=128, seed=5, num_themes=8)
    assert "archive_strict_auth" in str(first.run_dir)
    assert first.n_examples == 32
    assert second.n_examples == 128
    assert len(second.themes) == 8
    for control in (
        "same_theme_impostor",
        "heavy_paraphrase",
        "full_paraphrase",
        "multi_carrier_impostor",
    ):
        assert control in ARCHIVE_HARDENING_CONTROLS


def test_archive_narrative_attack_generation():
    row = generate_archive_narrative(
        style="template_narrative",
        seed=37,
        theme="fox_fable",
        word_count_min=400,
        word_count_max=700,
    )
    original = row["carrier_text"]
    for attack in (
        "light_paraphrase",
        "heavy_paraphrase",
        "full_paraphrase",
        "shuffle",
        "delete",
        "insert",
    ):
        changed = generate_narrative_attack(
            original, attack=attack, seed=41
        )
        assert changed != original
        assert len(changed.split()) >= 300
        assert narrative_coherence_metrics(
            changed, row["plan"]
        )["story_surface_pass_rate"] == 1.0


def test_narrative_forbidden_terms():
    row = generate_archive_narrative(
        style="template_narrative",
        seed=43,
        theme="river_bell",
        word_count_min=400,
        word_count_max=700,
    )
    lowered = row["carrier_text"].lower()
    assert not any(term in lowered for term in FORBIDDEN_NARRATIVE_TERMS)
    assert codec_narrative_metrics(
        CodecNarrativeSurface(
            CodecNarrativeConfig()
        ).random_story(random.Random(47))
        if hasattr(CodecNarrativeSurface, "random_story")
        else CodecNarrativeSurface(
            CodecNarrativeConfig()
        ).render(
            CodecNarrativeSurface(
                CodecNarrativeConfig()
            ).random_slot_ids(random.Random(47))
        )
    )["codec_forbidden_term_rate"] == 0.0


def test_archive_hardening_verdict_logic():
    metrics = {
        "positive_validation_pass_rate": 1.0,
        "story_surface_pass_rate": 1.0,
        "narrative_coherence_pass_rate": 1.0,
        "carrier_uniqueness_rate": 1.0,
        "story_a_expected_hash_b_original_hash_accept_rate": 0.0,
    }
    for control in ARCHIVE_HARDENING_CONTROLS:
        metrics[f"{control}_any_valid_accept_rate"] = 0.0
    assert archive_hardening_verdict(32, metrics) == "PASS_N32"
    assert archive_hardening_verdict(128, metrics) == "PASS_N128"
    metrics["full_paraphrase_any_valid_accept_rate"] = 1.0
    assert (
        archive_hardening_verdict(32, metrics)
        == "PARTIAL_ATTACK_SURFACE"
    )


def test_no_symbolic_inverse_tiny_codec():
    from argus_capsules import codec_tiny_schema, tiny_schema

    source = (
        inspect.getsource(codec_tiny_schema)
        + inspect.getsource(tiny_schema)
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
