"""Fast contracts for the neural text-to-slot bridge."""

import inspect
import random

import pytest
import torch

from argus_capsules import (
    codec_text_to_slot,
    run_codec_text_to_slot_bridge,
)
from argus_capsules.codec_narrative import (
    CodecNarrativeConfig,
    CodecNarrativeSurface,
)
from argus_capsules.codec_text_to_slot import (
    BRIDGE_STAGES,
    SLOT_LABELS_ARE_OPEN_INPUTS,
    SLOT_SPANS_ARE_OPEN_INPUTS,
    NarrativeWordTokenizer,
    NeuralTextToSlotHead,
    RecoveredSlotFieldHeads,
    TextToSlotBridgeDecoder,
    TextToSlotCapsule,
    TextToSlotModelConfig,
    slot_reconstruction_loss,
    stage1_bridge_pass,
    stage2_bridge_pass,
    text_to_slot_bridge_verdict,
)
from argus_capsules.codec_tiny_ladder import delete_slot_spans
from argus_capsules.codec_tiny_schema import (
    TinyNarrativeEncoder,
    artifact_from_tiny_story,
)
from argus_capsules.narrative_archive import llm_narrative
from argus_capsules.run_archive_hardening import (
    ArchiveHardeningConfig,
)
from argus_capsules.run_codec_text_to_slot_bridge import (
    build_bridge_config,
    build_bridge_run_id,
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


def _small_model():
    text = (
        "The fox entered the orchard.\n\n"
        "A lantern moved softly beside the path."
    )
    tokenizer = NarrativeWordTokenizer.fit([text])
    config = TextToSlotModelConfig(
        slot_count=23,
        global_slot_count=9,
        choices_per_slot=16,
        max_words=32,
        hidden_size=32,
        encoder_layers=1,
        attention_heads=4,
        secret_vocab_size=SimpleTokenizer().vocab_size,
    )
    assignments = [
        index % len(FIELD_NAMES) for index in range(14)
    ]
    decoder = TextToSlotBridgeDecoder(
        tokenizer,
        TINY_SCHEMA_SPECS["tiny_64"],
        config,
        local_field_assignments=assignments,
    )
    return text, tokenizer, config, assignments, decoder


def test_text_to_slot_config_builds():
    config = build_bridge_config(seed=7)
    assert config.schema == "tiny_64"
    assert BRIDGE_STAGES == (
        "text_to_slot_reconstruction",
        "recovered_slot_fields",
        "valid_text_open",
    )
    assert config.run_stage1_text_to_slot
    assert config.run_stage2_recovered_slot_fields
    assert config.run_stage3_valid_open
    assert "tiny_64" in build_bridge_run_id(config)


def test_text_to_slot_head_shapes():
    text, tokenizer, config, _, _ = _small_model()
    head = NeuralTextToSlotHead(
        tokenizer.vocab_size,
        config,
        pad_id=tokenizer.pad_id,
    )
    encoded = tokenizer.encode(
        text, max_words=config.max_words
    )
    output = head(
        torch.tensor([encoded["token_ids"]]),
        torch.tensor([encoded["paragraph_ids"]]),
        torch.tensor([encoded["paragraph_positions"]]),
        torch.tensor(
            [encoded["attention_mask"]], dtype=torch.bool
        ),
    )
    assert output["slot_logits"].shape == (1, 23, 16)
    assert output["recovered_slot_embeddings"].shape == (
        1,
        23,
        32,
    )
    assert output["slot_attention"].shape == (1, 23, 32)
    assert config.global_slot_count == 9
    assert config.local_slot_count == 14


def test_slot_reconstruction_loss():
    logits = torch.randn(2, 4, 8, requires_grad=True)
    labels = torch.tensor(
        [[0, 1, -100, 3], [4, 5, 6, -100]]
    )
    loss = slot_reconstruction_loss(logits, labels)
    assert loss.ndim == 0
    assert torch.isfinite(loss)
    loss.backward()
    assert logits.grad is not None


def test_slot_labels_not_open_inputs():
    assert SLOT_LABELS_ARE_OPEN_INPUTS is False
    assert SLOT_SPANS_ARE_OPEN_INPUTS is False
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    example = generate_tiny_examples(spec, 1, seed=13)[0]
    artifact = artifact_from_tiny_story(
        example, "The fox crossed the orchard."
    )
    assert "slot_ids" not in artifact
    assert "slot_spans" not in artifact
    signature = inspect.signature(
        TextToSlotCapsule.open_artifact
    )
    assert "slot_ids" not in signature.parameters
    assert "slot_spans" not in signature.parameters


def test_stage1_verdict_logic():
    failed = {
        "stage1_heldout_per_slot_accuracy_mean": 0.40,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.50,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.50,
    }
    assert not stage1_bridge_pass(failed)
    assert (
        text_to_slot_bridge_verdict(failed)
        == "FAIL_TEXT_TO_SLOT"
    )
    passing = {
        "stage1_heldout_per_slot_accuracy_mean": 0.85,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.40,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.35,
    }
    assert stage1_bridge_pass(passing)


def test_stage2_verdict_logic():
    stage1 = {
        "stage1_heldout_per_slot_accuracy_mean": 0.90,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.50,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.50,
    }
    failed = {
        **stage1,
        "stage2_gold_slot_average_field_accuracy": 0.95,
        "stage2_predicted_slot_average_field_accuracy": 0.45,
        "stage2_text_to_slot_to_field_average_field_accuracy": 0.45,
        "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.0,
        "stage2_field_accuracy_drop_when_story_randomized": 0.0,
        "stage2_field_accuracy_drop_when_story_swapped": 0.0,
    }
    assert not stage2_bridge_pass(failed)
    assert (
        text_to_slot_bridge_verdict(failed)
        == "FAIL_SLOT_TO_FIELD_FROM_RECOVERED_TEXT"
    )
    passing = {
        **stage1,
        "stage2_text_to_slot_to_field_average_field_accuracy": 0.85,
        "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.55,
        "stage2_field_accuracy_drop_when_story_randomized": 0.40,
        "stage2_field_accuracy_drop_when_story_swapped": 0.35,
    }
    assert stage2_bridge_pass(passing)


def test_stage3_verdict_logic():
    base = {
        "stage1_heldout_per_slot_accuracy_mean": 0.90,
        "stage1_slot_accuracy_drop_when_story_randomized": 0.50,
        "stage1_slot_accuracy_drop_when_story_swapped": 0.50,
        "stage2_text_to_slot_to_field_average_field_accuracy": 0.90,
        "stage2_text_to_slot_to_field_exact_tuple_accuracy": 0.60,
        "stage2_field_accuracy_drop_when_story_randomized": 0.50,
        "stage2_field_accuracy_drop_when_story_swapped": 0.50,
        "stage3_heldout_average_field_accuracy": 0.85,
        "stage3_field_accuracy_drop_when_carrier_randomized": 0.40,
        "stage3_field_accuracy_drop_when_story_swapped": 0.40,
        "stage3_story_swap_original_hash_accept_rate": 0.0,
    }
    for key in (
        "stage3_wrong_secret_any_valid_accept_rate",
        "stage3_wrong_salt_any_valid_accept_rate",
        "stage3_wrong_version_any_valid_accept_rate",
        "stage3_random_story_any_valid_accept_rate",
        "stage3_same_distribution_impostor_any_valid_accept_rate",
        "stage3_tamper_any_valid_accept_rate",
        "stage3_story_swap_any_valid_accept_rate",
    ):
        base[key] = 0.0
    partial = {
        **base,
        "stage3_heldout_exact_tuple_accuracy": 0.30,
    }
    assert (
        text_to_slot_bridge_verdict(partial)
        == "PARTIAL_CAUSAL_FIELD_SIGNAL"
    )
    passing = {
        **base,
        "stage3_heldout_exact_tuple_accuracy": 0.55,
    }
    assert (
        text_to_slot_bridge_verdict(passing)
        == "PASS_TINY64_CAUSAL"
    )


def test_field_head_from_recovered_slots_shapes():
    _, _, config, assignments, _ = _small_model()
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    heads = RecoveredSlotFieldHeads(
        spec,
        hidden_size=config.hidden_size,
        global_slot_count=config.global_slot_count,
        local_field_assignments=assignments,
    )
    logits = heads(torch.randn(3, 23, 32))
    for field in FIELD_NAMES:
        assert logits[field].shape == (
            3,
            spec.field_sizes[field],
        )


def test_relevant_span_ablation_changes_story():
    surface = CodecNarrativeSurface(CodecNarrativeConfig())
    story, spans = surface.render_with_slot_spans(
        surface.random_slot_ids(random.Random(191))
    )
    relevant = delete_slot_spans(
        story,
        spans,
        [surface.config.global_slot_count],
    )
    irrelevant = delete_slot_spans(
        story,
        spans,
        [surface.config.global_slot_count + 1],
    )
    assert relevant != story
    assert irrelevant != story
    assert relevant != irrelevant


def test_archive_n128_config_builds():
    config = ArchiveHardeningConfig(
        n_examples=128,
        num_themes=8,
        auth_encoder="order_sensitive",
        strict_paraphrase_reject=True,
    )
    assert config.n_examples == 128
    assert config.run_id.startswith(
        "archive-strict-auth-s0-n128"
    )


def test_no_symbolic_inverse():
    source = (
        inspect.getsource(codec_text_to_slot)
        + inspect.getsource(run_codec_text_to_slot_bridge)
    ).lower()
    forbidden = (
        "story_to_" + "report",
        "decode_story_" + "to_case",
        "inverse_" + "story",
        "base64." + "b64decode",
        "bytes." + "fromhex",
        "carrier_" + "lookup",
    )
    for value in forbidden:
        assert value not in source


def test_text_authority_preserved():
    text, word_tokenizer, _, _, decoder = _small_model()
    secret_tokenizer = SimpleTokenizer()
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    capsule = TextToSlotCapsule(
        decoder,
        word_tokenizer,
        secret_tokenizer,
        spec,
        auth_threshold=0.0,
        secret_threshold=0.0,
        device=torch.device("cpu"),
    )
    example = generate_tiny_examples(spec, 1, seed=17)[0]
    artifact = artifact_from_tiny_story(example, text)
    artifact["slot_ids_debug"] = [15] * 23
    condition = SecretConditioning(
        example["secret_text"],
        example["capsule_salt"],
        example["decoder_version"],
        secret_tokenizer,
        num_secrets=32,
        num_tokens=16,
    )
    first = capsule.predict_fields(
        artifact, condition, force_authentic=True
    )
    artifact["carrier_text"] = (
        "The owl waited beside a meadow.\n\n"
        "A basket rested quietly near the gate."
    )
    second = capsule.predict_fields(
        artifact, condition, force_authentic=True
    )
    assert (
        first["predicted_slot_ids_diagnostic"]
        != second["predicted_slot_ids_diagnostic"]
        or first["authenticity_score"]
        != second["authenticity_score"]
    )


def test_open_does_not_call_encoder_renderer_or_llm(monkeypatch):
    text, word_tokenizer, _, _, decoder = _small_model()
    secret_tokenizer = SimpleTokenizer()
    spec = TINY_SCHEMA_SPECS["tiny_64"]
    capsule = TextToSlotCapsule(
        decoder,
        word_tokenizer,
        secret_tokenizer,
        spec,
        auth_threshold=0.0,
        secret_threshold=0.0,
        device=torch.device("cpu"),
    )
    example = generate_tiny_examples(spec, 1, seed=19)[0]
    artifact = artifact_from_tiny_story(example, text)
    condition = SecretConditioning(
        example["secret_text"],
        example["capsule_salt"],
        example["decoder_version"],
        secret_tokenizer,
        num_secrets=32,
        num_tokens=16,
    )

    def fail(*args, **kwargs):
        raise AssertionError("seal-side component called")

    monkeypatch.setattr(TinyNarrativeEncoder, "forward", fail)
    monkeypatch.setattr(CodecNarrativeSurface, "render", fail)
    monkeypatch.setattr(
        "argus_capsules.narrative_archive.llm_narrative",
        fail,
    )
    assert isinstance(
        capsule.open_artifact(artifact, condition), str
    )
