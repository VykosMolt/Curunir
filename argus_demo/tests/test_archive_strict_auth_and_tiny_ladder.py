"""Fast contract tests for strict archive auth and the tiny codec ladder."""

import inspect
import random

import pytest
import torch

from argus_capsules.archive_auth import (
    ArchiveOrderSensitiveAuthenticator,
)
from argus_capsules.codec_narrative import (
    CodecNarrativeConfig,
    CodecNarrativeSurface,
)
from argus_capsules.codec_tiny_ladder import (
    FieldAttentionTinyDecoder,
    LADDER_STAGES,
    RENDERED_ALIGNMENT_IS_OPEN_INPUT,
    SLOT_ORACLE_IS_VALID_OPEN_PATH,
    SlotOracleTinyDecoder,
    build_field_span_masks,
    codec_tiny_ladder_verdict,
    delete_slot_spans,
)
from argus_capsules.codec_tiny_schema import (
    TinyNarrativeEncoder,
    TinySchemaCapsule,
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
    archive_strict_auth_verdict,
)
from argus_capsules.run_codec_tiny_ladder import (
    build_ladder_config,
    build_ladder_run_id,
)
from argus_capsules.secrets import SecretConditioning
from argus_capsules.tiny_schema import (
    FIELD_NAMES,
    TINY_SCHEMA_SPECS,
    generate_tiny_examples,
)
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def test_archive_strict_auth_config_builds():
    config = ArchiveHardeningConfig(
        n_examples=32,
        auth_encoder="order_sensitive",
        strict_paraphrase_reject=True,
    )
    assert "archive_strict_auth" in str(config.run_dir)
    for control in (
        "light_paraphrase",
        "heavy_paraphrase",
        "full_paraphrase",
        "one_paragraph_paraphrase",
        "local_synonym_edit",
        "near_edit_1_word",
        "near_edit_5_words",
    ):
        assert control in ARCHIVE_HARDENING_CONTROLS


def test_archive_paraphrase_attack_generation():
    row = generate_archive_narrative(
        style="template_narrative",
        seed=101,
        theme="fox_fable",
        word_count_min=400,
        word_count_max=700,
    )
    original = row["carrier_text"]
    for attack in (
        "light_paraphrase",
        "heavy_paraphrase",
        "full_paraphrase",
        "one_paragraph_paraphrase",
        "local_synonym_edit",
        "near_edit_1_word",
        "near_edit_5_words",
    ):
        changed = generate_narrative_attack(
            original, attack=attack, seed=103
        )
        assert changed != original
        assert narrative_coherence_metrics(
            changed, row["plan"]
        )["story_surface_pass_rate"] == 1.0
        lowered = changed.lower()
        assert not any(
            term in lowered for term in FORBIDDEN_NARRATIVE_TERMS
        )


@pytest.mark.parametrize(
    "encoder_type",
    ("order_sensitive", "multiscale"),
)
def test_archive_auth_encoder_shapes(encoder_type):
    model = ArchiveOrderSensitiveAuthenticator(
        vocab_size=41,
        carrier_len=32,
        archive_size=4,
        hidden_size=16,
        num_secret_ids=8,
        encoder_type=encoder_type,
    )
    carrier = torch.randint(0, 41, (3, 32))
    secret_tokens = torch.randint(0, 41, (3, 8))
    secret_ids = torch.randint(0, 8, (3,))
    scores = model.authenticity_scores(
        carrier, secret_tokens, secret_ids
    )
    assert scores.shape == (3,)
    assert model.selector_logits(
        secret_tokens, secret_ids
    ).shape == (3, 4)


def test_archive_strict_auth_verdict_logic():
    metrics = {
        "positive_validation_pass_rate": 1.0,
        "positive_auth_accept_rate": 1.0,
        "light_paraphrase_any_valid_accept_rate": 0.0,
    }
    for control in (
        "heavy_paraphrase",
        "full_paraphrase",
        "one_paragraph_paraphrase",
        "local_synonym_edit",
        "near_edit_1_word",
        "near_edit_5_words",
        "same_theme_impostor",
        "same_plot_different_details",
        "shuffled_paragraphs",
        "deleted_paragraph",
        "inserted_paragraph",
        "story_a_secret_b",
        "wrong_secret",
        "wrong_salt",
        "wrong_version",
        "multi_carrier_impostor",
    ):
        metrics[f"{control}_any_valid_accept_rate"] = 0.0
    assert archive_strict_auth_verdict(32, metrics) == "PASS_N32"
    metrics["light_paraphrase_any_valid_accept_rate"] = 0.75
    assert (
        archive_strict_auth_verdict(32, metrics)
        == "PARTIAL_ATTACK_SURFACE"
    )
    metrics["positive_validation_pass_rate"] = 0.9
    assert (
        archive_strict_auth_verdict(32, metrics)
        == "FAIL_POSITIVES_REGRESSED"
    )


def test_codec_tiny_ladder_config_builds():
    config = build_ladder_config(seed=7)
    assert LADDER_STAGES == (
        "slot_oracle",
        "rendered_aligned",
        "normal_rendered",
    )
    assert config.run_slot_oracle
    assert config.run_rendered_aligned
    assert config.run_normal_rendered
    assert "tiny_64" in build_ladder_run_id(config)


def test_slot_oracle_diagnostic_not_valid_open_path():
    assert SLOT_ORACLE_IS_VALID_OPEN_PATH is False
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    assignments = [
        index % len(FIELD_NAMES) for index in range(70)
    ]
    oracle = SlotOracleTinyDecoder(
        spec,
        slot_count=79,
        choices_per_slot=16,
        global_slot_count=9,
        local_field_assignments=assignments,
        hidden_size=8,
    )
    assert oracle.diagnostic_only
    logits = oracle(torch.randint(0, 16, (2, 79)))
    assert set(logits) == set(FIELD_NAMES)


def test_rendered_aligned_training_not_open_input():
    assert RENDERED_ALIGNMENT_IS_OPEN_INPUT is False
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    example = generate_tiny_examples(spec, 1, seed=5)[0]
    artifact = artifact_from_tiny_story(
        example,
        "The fox crossed the orchard and returned before dusk.",
    )
    assert "slot_ids" not in artifact
    assert "slot_spans" not in artifact
    assert "alignment" not in artifact


def test_field_attention_shapes():
    tokenizer = SimpleTokenizer()
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    model = FieldAttentionTinyDecoder(
        tokenizer,
        spec,
        carrier_len=128,
        max_json_len=64,
        hidden_size=32,
        char_size=16,
        segment_count=12,
    )
    output = model(
        torch.randint(0, tokenizer.vocab_size, (3, 128)),
        torch.randint(0, tokenizer.vocab_size, (3, 16)),
        torch.randint(0, 32, (3,)),
    )
    assert output["attentions"].shape == (
        3,
        len(FIELD_NAMES),
        12,
    )
    for field in FIELD_NAMES:
        assert output["field_logits"][field].shape == (
            3,
            spec.field_sizes[field],
        )


def test_relevant_span_ablation_changes_story():
    surface = CodecNarrativeSurface(CodecNarrativeConfig())
    slots = surface.random_slot_ids(random.Random(107))
    story, spans = surface.render_with_slot_spans(slots)
    changed = delete_slot_spans(
        story,
        spans,
        [
            surface.config.global_slot_count,
            surface.config.global_slot_count + 7,
        ],
    )
    assert changed != story
    tokenizer = SimpleTokenizer()
    masks = build_field_span_masks(
        texts=[story],
        slot_spans=[spans],
        tokenizer=tokenizer,
        carrier_len=len(tokenizer.encode(story)) + 8,
        segment_count=32,
        global_slot_count=surface.config.global_slot_count,
        local_field_assignments=[
            index % len(FIELD_NAMES)
            for index in range(surface.config.local_slot_count)
        ],
    )
    assert masks.shape == (1, len(FIELD_NAMES), 32)
    assert masks.sum() > 0


def test_codec_tiny_ladder_verdict_logic():
    base = {
        "slot_oracle_heldout_exact_tuple_accuracy": 0.6,
        "slot_oracle_heldout_average_field_accuracy": 0.9,
        "slot_oracle_random_slot_accuracy_drop": 0.5,
        "nearest_training_baseline_average_field_accuracy": 0.55,
        "aligned_heldout_average_field_accuracy": 0.8,
        "aligned_relevant_span_deleted_drop": 0.3,
        "aligned_irrelevant_span_deleted_drop": 0.05,
    }
    failed_oracle = dict(base)
    failed_oracle[
        "slot_oracle_heldout_exact_tuple_accuracy"
    ] = 0.0
    assert (
        codec_tiny_ladder_verdict(failed_oracle)
        == "FAIL_SLOT_ORACLE"
    )
    failed_alignment = dict(base)
    failed_alignment[
        "aligned_heldout_average_field_accuracy"
    ] = 0.4
    assert (
        codec_tiny_ladder_verdict(failed_alignment)
        == "FAIL_RENDERED_ALIGNMENT"
    )
    failed_normal = {
        **base,
        "normal_heldout_exact_tuple_accuracy": 0.0,
        "normal_heldout_average_field_accuracy": 0.4,
        "normal_field_accuracy_drop_when_carrier_randomized": 0.0,
        "normal_field_accuracy_drop_when_story_swapped": 0.0,
    }
    assert (
        codec_tiny_ladder_verdict(failed_normal)
        == "FAIL_NORMAL_TEXT_OPEN"
    )
    partial = {
        **base,
        "normal_heldout_exact_tuple_accuracy": 0.2,
        "normal_heldout_average_field_accuracy": 0.7,
        "normal_field_accuracy_drop_when_carrier_randomized": 0.2,
        "normal_field_accuracy_drop_when_story_swapped": 0.2,
    }
    assert (
        codec_tiny_ladder_verdict(partial)
        == "PARTIAL_CAUSAL_FIELD_SIGNAL"
    )
    passing = {
        **base,
        "normal_heldout_exact_tuple_accuracy": 0.6,
        "normal_heldout_average_field_accuracy": 0.85,
        "normal_field_accuracy_drop_when_carrier_randomized": 0.4,
        "normal_field_accuracy_drop_when_story_swapped": 0.4,
        "normal_story_swap_original_hash_accept_rate": 0.0,
    }
    for key in (
        "normal_wrong_secret_any_valid_accept_rate",
        "normal_wrong_salt_any_valid_accept_rate",
        "normal_wrong_version_any_valid_accept_rate",
        "normal_random_story_any_valid_accept_rate",
        "normal_same_distribution_impostor_any_valid_accept_rate",
        "normal_tamper_any_valid_accept_rate",
        "normal_story_swap_any_valid_accept_rate",
    ):
        passing[key] = 0.0
    assert (
        codec_tiny_ladder_verdict(passing)
        == "PASS_TINY64_CAUSAL"
    )


def test_no_symbolic_inverse_strict_auth_and_ladder():
    from argus_capsules import archive_auth, codec_tiny_ladder

    source = (
        inspect.getsource(archive_auth)
        + inspect.getsource(codec_tiny_ladder)
    ).lower()
    for forbidden in (
        "story_to_report",
        "decode_story_to_case",
        "inverse_story",
        "base64.b64decode",
        "bytes.fromhex",
        "carrier_lookup",
    ):
        assert forbidden not in source


def test_text_authority_preserved_and_open_is_seal_free(monkeypatch):
    tokenizer = SimpleTokenizer()
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    original = (
        "The fox crossed the orchard with a lantern and returned."
    )
    changed = (
        "The owl crossed the meadow with a basket and waited."
    )
    carrier_len = max(
        len(tokenizer.encode(original)),
        len(tokenizer.encode(changed)),
    ) + 8
    decoder = FieldAttentionTinyDecoder(
        tokenizer,
        spec,
        carrier_len=carrier_len,
        max_json_len=128,
        hidden_size=24,
        char_size=12,
        segment_count=8,
    )
    capsule = TinySchemaCapsule(
        decoder,
        tokenizer,
        spec,
        carrier_len=carrier_len,
        auth_threshold=0.0,
        device=torch.device("cpu"),
    )
    example = generate_tiny_examples(spec, 1, seed=9)[0]
    artifact = artifact_from_tiny_story(example, original)
    artifact["carrier_ids_debug"] = [999999]
    condition = SecretConditioning(
        example["secret_text"],
        example["capsule_salt"],
        example["decoder_version"],
        tokenizer,
        num_secrets=32,
        num_tokens=16,
    )

    def fail(*args, **kwargs):
        raise AssertionError("seal-side component called")

    monkeypatch.setattr(TinyNarrativeEncoder, "forward", fail)
    monkeypatch.setattr(CodecNarrativeSurface, "render", fail)
    first = capsule.predict_fields(
        artifact, condition, force_authentic=True
    )
    artifact["carrier_text"] = changed
    second = capsule.predict_fields(
        artifact, condition, force_authentic=True
    )
    assert isinstance(capsule.open_artifact(artifact, condition), str)
    assert (
        first["authenticity_score"]
        != second["authenticity_score"]
        or first["fields"] != second["fields"]
    )
