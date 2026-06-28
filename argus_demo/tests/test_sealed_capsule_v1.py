"""Contract tests for sealed-capsule v1."""

from types import SimpleNamespace

import pytest
import torch

from argus_capsules.eval_capsule_autoencoder import (
    _random_carrier_artifact,
    _tampered_carrier_artifact,
    evaluate_capsules,
)
from argus_capsules.neural_capsule import NeuralCapsule
from argus_capsules.neural_decoder import SecretConditionedDecoder
from argus_capsules.neural_encoder import NeuralCarrierEncoder
from argus_capsules.secrets import SecretConditioning
from argus_capsules.story_data import tamper_carrier_text
from argus_capsules.tokenizer import SimpleTokenizer
from argus_capsules.train_capsule_autoencoder import (
    _build_examples,
    _prepare_tensors,
    measure_token_lengths,
    resolve_length_limits,
)
from argus_capsules.validate import validate_decoded_output


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _setup(device="cpu"):
    tokenizer = SimpleTokenizer()
    encoder = NeuralCarrierEncoder(
        tokenizer.vocab_size,
        hidden_size=24,
        num_layers=1,
        carrier_len=12,
        pad_id=tokenizer.pad_id,
    )
    decoder = SecretConditionedDecoder(
        tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=24,
        num_layers=1,
        bos_id=tokenizer.bos_id,
        eos_id=tokenizer.eos_id,
        reject_id=tokenizer.reject_id,
        max_output_len=1024,
        code_size=8,
    )
    capsule = NeuralCapsule(
        encoder,
        decoder,
        tokenizer,
        carrier_len=12,
        story_token_ids=tokenizer.story_token_ids(),
    ).to(device)
    example = _build_examples(
        SimpleNamespace(
            n_examples=1,
            seed=17,
            num_secrets=4,
            overfit_small=True,
        )
    )[0]
    conditioning = SecretConditioning(
        example["secret_text"],
        example["capsule_salt"],
        example["decoder_version"],
        tokenizer,
        num_secrets=4,
        num_tokens=8,
    )
    artifact = capsule.seal_to_artifact(
        example["target_json"],
        example["case_id"],
        example["target_hash"],
        conditioning,
        max_report_len=len(tokenizer.encode(example["target_json"])),
    )
    return capsule, tokenizer, example, conditioning, artifact


def test_open_artifact_does_not_call_encoder(monkeypatch):
    capsule, _, _, conditioning, artifact = _setup()

    def fail(*args, **kwargs):
        raise AssertionError("encoder called during open")

    monkeypatch.setattr(capsule.encoder, "forward", fail)
    decoded = capsule.open_artifact(artifact, conditioning, 64)
    assert isinstance(decoded, str)


def test_open_artifact_does_not_call_encoder_or_llm(monkeypatch):
    import argus_capsules.narrative_archive as narrative_archive

    capsule, _, _, conditioning, artifact = _setup()

    def fail(*args, **kwargs):
        raise AssertionError("seal-side component called during open")

    monkeypatch.setattr(capsule.encoder, "forward", fail)
    monkeypatch.setattr(narrative_archive, "llm_narrative", fail)
    decoded = capsule.open_artifact(artifact, conditioning, 64)
    assert isinstance(decoded, str)


def test_open_artifact_uses_carrier_text_not_ids(monkeypatch):
    capsule, tokenizer, _, conditioning, artifact = _setup()
    original_debug_ids = list(artifact["carrier_ids_debug"])
    modified_text = tamper_carrier_text(
        artifact["carrier_text"], mode="replace", seed=9
    )
    artifact["carrier_text"] = modified_text
    artifact["carrier_ids"] = original_debug_ids
    artifact["carrier_ids_debug"] = original_debug_ids
    captured = {}

    def capture_open(
        carrier_ids,
        secret_tokens,
        secret_ids,
        max_new_tokens,
        eos_id,
        force_authentic=False,
    ):
        del (
            secret_tokens,
            secret_ids,
            max_new_tokens,
            eos_id,
            force_authentic,
        )
        captured["carrier_ids"] = carrier_ids.detach().cpu().tolist()[0]
        return [[tokenizer.reject_id, tokenizer.eos_id]]

    monkeypatch.setattr(capsule, "open_carrier", capture_open)
    assert capsule.open_artifact(artifact, conditioning, 64) == "<REJECT>"

    expected_ids = tokenizer.encode(modified_text)
    expected_ids += [tokenizer.pad_id] * (
        capsule.carrier_len - len(expected_ids)
    )
    assert captured["carrier_ids"] == expected_ids
    assert captured["carrier_ids"] != original_debug_ids


def test_open_artifact_without_carrier_ids():
    capsule, _, _, conditioning, artifact = _setup()
    artifact.pop("carrier_ids", None)
    artifact.pop("carrier_ids_debug", None)
    decoded = capsule.open_artifact(artifact, conditioning, 64)
    assert isinstance(decoded, str)


def test_forced_auth_bypasses_only_authenticity_gate(monkeypatch):
    capsule, tokenizer, _, conditioning, artifact = _setup()
    observed = {}

    def capture_generate(
        carrier_ids,
        secret_ids,
        max_new_tokens,
        eos_id,
        secret_tokens=None,
        force_authentic=False,
    ):
        del carrier_ids, secret_ids, max_new_tokens, secret_tokens
        observed["force_authentic"] = force_authentic
        return [[tokenizer.reject_id, eos_id]]

    monkeypatch.setattr(capsule.decoder, "generate", capture_generate)
    capsule.open_artifact(
        artifact,
        conditioning,
        64,
        force_authentic=True,
    )
    assert observed["force_authentic"] is True


@pytest.mark.parametrize("carrier_text", [None, 123, ["not", "text"]])
def test_open_artifact_rejects_missing_or_non_string_text(carrier_text):
    capsule, _, _, conditioning, artifact = _setup()
    if carrier_text is None:
        artifact.pop("carrier_text", None)
    else:
        artifact["carrier_text"] = carrier_text
    assert capsule.open_artifact(artifact, conditioning, 64) == "<REJECT>"


def test_open_artifact_does_not_read_carrier_ids():
    capsule, _, _, conditioning, artifact = _setup()
    artifact_without_ids = dict(artifact)
    artifact_without_ids.pop("carrier_ids", None)
    artifact_without_ids.pop("carrier_ids_debug", None)
    expected = capsule.open_artifact(
        artifact_without_ids, conditioning, 64
    )

    artifact_with_junk = dict(artifact_without_ids)
    artifact_with_junk["carrier_ids"] = [
        -10**12,
        "not-an-id",
        object(),
    ]
    artifact_with_junk["carrier_ids_debug"] = [10**12, None]
    actual = capsule.open_artifact(artifact_with_junk, conditioning, 64)
    assert actual == expected


def test_tampered_text_rejected_even_with_original_debug_ids():
    capsule, _, example, conditioning, artifact = _setup()
    original_debug_ids = list(artifact["carrier_ids_debug"])
    artifact["carrier_text"] = tamper_carrier_text(
        artifact["carrier_text"], mode="replace", seed=11
    )
    artifact["carrier_ids_debug"] = original_debug_ids
    decoded = capsule.open_artifact(artifact, conditioning, 128)
    assert not validate_decoded_output(
        decoded, expected_hash=example["target_hash"]
    ).ok


def test_random_text_ignores_debug_ids():
    capsule, tokenizer, example, conditioning, artifact = _setup()
    original_debug_ids = list(artifact["carrier_ids_debug"])
    random_artifact = _random_carrier_artifact(
        artifact,
        tokenizer,
        capsule.carrier_len,
        __import__("random").Random(23),
    )
    random_artifact["carrier_ids_debug"] = original_debug_ids
    decoded = capsule.open_artifact(
        random_artifact, conditioning, 128
    )
    assert not validate_decoded_output(
        decoded, expected_hash=example["target_hash"]
    ).ok


def test_eval_tamper_helper_removes_debug_ids():
    _, tokenizer, _, _, artifact = _setup()
    artifact["carrier_ids"] = [1, 2, 3]
    tampered = _tampered_carrier_artifact(
        artifact, tokenizer, mode="replace", seed=7
    )
    assert tampered["carrier_text"] != artifact["carrier_text"]
    assert "carrier_ids" not in tampered
    assert "carrier_ids_debug" not in tampered


def test_eval_random_helper_removes_debug_ids():
    _, tokenizer, _, _, artifact = _setup()
    artifact["carrier_ids"] = [1, 2, 3]
    randomized = _random_carrier_artifact(
        artifact,
        tokenizer,
        len(artifact["carrier_text"]),
        __import__("random").Random(29),
    )
    assert randomized["carrier_text"] != artifact["carrier_text"]
    assert "carrier_ids" not in randomized
    assert "carrier_ids_debug" not in randomized


def test_same_carrier_wrong_secret_fails_expected_hash():
    capsule, tokenizer, example, conditioning, artifact = _setup()
    wrong = SecretConditioning(
        conditioning.secret_text + "-WRONG",
        conditioning.capsule_salt,
        conditioning.decoder_version,
        tokenizer,
        num_secrets=4,
        num_tokens=8,
    )
    decoded = capsule.open_artifact(artifact, wrong, 128)
    assert not validate_decoded_output(
        decoded, expected_hash=example["target_hash"]
    ).ok


def test_same_carrier_wrong_salt_fails_expected_hash():
    capsule, tokenizer, example, conditioning, artifact = _setup()
    wrong = SecretConditioning(
        conditioning.secret_text,
        conditioning.capsule_salt + "-WRONG",
        conditioning.decoder_version,
        tokenizer,
        num_secrets=4,
        num_tokens=8,
    )
    decoded = capsule.open_artifact(artifact, wrong, 128)
    assert not validate_decoded_output(
        decoded, expected_hash=example["target_hash"]
    ).ok


@pytest.mark.parametrize("mode", ["replace", "delete", "swap", "rotate"])
def test_carrier_tamper_modes_always_change_text(mode):
    original = "a small carrier text."
    tampered = tamper_carrier_text(original, mode=mode, seed=5)
    assert isinstance(tampered, str)
    assert tampered
    assert tampered != original


def test_random_carrier_does_not_validate_against_expected_hash():
    capsule, tokenizer, example, conditioning, artifact = _setup()
    random_artifact = _random_carrier_artifact(
        artifact,
        tokenizer,
        capsule.carrier_len,
        __import__("random").Random(3),
    )
    decoded = capsule.open_artifact(random_artifact, conditioning, 128)
    assert not validate_decoded_output(
        decoded, expected_hash=example["target_hash"]
    ).ok


def test_eval_metrics_include_both_negative_acceptance_semantics():
    capsule, tokenizer, _, _, _ = _setup()
    config = {
        "num_secrets": 4,
        "num_secret_tokens": 8,
        "overfit_small": True,
        "max_report_len": 1024,
        "max_target_len": 1024,
        "carrier_len": capsule.carrier_len,
    }
    metrics, _ = evaluate_capsules(
        capsule,
        tokenizer,
        config,
        n_examples=1,
        seed=17,
        max_new_tokens=64,
    )
    for prefix in (
        "wrong_secret",
        "wrong_salt",
        "random_carrier",
        "tamper",
    ):
        assert f"{prefix}_original_hash_accept_rate" in metrics
        assert f"{prefix}_any_valid_accept_rate" in metrics
    assert "reject_accuracy_original_hash" in metrics
    assert "reject_accuracy_any_valid" in metrics


def test_length_limits_raise_or_auto_adjust():
    tokenizer = SimpleTokenizer()
    examples = _build_examples(
        SimpleNamespace(
            n_examples=2,
            seed=3,
            num_secrets=4,
            overfit_small=True,
        )
    )
    with pytest.raises(ValueError, match="max_report_len"):
        resolve_length_limits(examples, tokenizer, 2, 2, False)
    report_limit, target_limit = resolve_length_limits(
        examples, tokenizer, 2, 2, True
    )
    lengths = measure_token_lengths(examples, tokenizer)
    assert report_limit >= max(lengths["report_lengths"])
    assert target_limit >= max(lengths["target_lengths"])


def test_prepare_tensors_checks_raw_length_before_padding():
    tokenizer = SimpleTokenizer()
    examples = _build_examples(
        SimpleNamespace(
            n_examples=1,
            seed=5,
            num_secrets=4,
            overfit_small=True,
        )
    )
    with pytest.raises(ValueError, match="report input length"):
        _prepare_tensors(
            examples,
            tokenizer,
            max_report_len=1,
            max_target_len=2048,
            num_secrets=4,
            num_secret_tokens=8,
        )


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_seal_and_open_internal_tensors_follow_cuda_model():
    capsule, _, _, conditioning, artifact = _setup(device="cuda")
    decoded = capsule.open_artifact(artifact, conditioning, 32)
    assert isinstance(decoded, str)
