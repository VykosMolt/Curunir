"""Forward-pass smoke tests for the neural carrier encoder/decoder/capsule."""

import pytest
import torch

from argus_capsules.neural_capsule import NeuralCapsule
from argus_capsules.neural_decoder import SecretConditionedDecoder
from argus_capsules.neural_encoder import NeuralCarrierEncoder
from argus_capsules.tokenizer import SimpleTokenizer


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


@pytest.fixture
def tiny_setup():
    tokenizer = SimpleTokenizer()
    encoder = NeuralCarrierEncoder(
        vocab_size=tokenizer.vocab_size,
        hidden_size=32,
        num_layers=1,
        carrier_len=16,
    )
    decoder = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=32,
        num_layers=1,
        bos_id=tokenizer.bos_id,
    )
    capsule = NeuralCapsule(
        encoder,
        decoder,
        tokenizer,
        carrier_len=16,
        story_token_ids=tokenizer.story_token_ids(),
    )
    return tokenizer, encoder, decoder, capsule


def test_encoder_forward_shape(tiny_setup):
    tokenizer, encoder, _, _ = tiny_setup
    report_ids = torch.randint(0, 100, (2, 20))
    secret_tokens = torch.randint(0, 100, (2, 4))
    logits = encoder(report_ids, secret_tokens)
    assert logits.shape == (2, 16, tokenizer.vocab_size)


def test_decoder_forward_shape(tiny_setup):
    tokenizer, _, decoder, _ = tiny_setup
    story_ids = torch.randint(0, 100, (2, 16))
    secret_ids = torch.randint(0, 4, (2,))
    target_ids = torch.randint(0, 100, (2, 10))
    logits = decoder(story_ids, secret_ids, target_ids=target_ids)
    assert logits.shape == (2, 9, tokenizer.vocab_size)


def test_decoder_is_autoregressive_gru(tiny_setup):
    _, _, decoder, _ = tiny_setup
    assert isinstance(decoder.report_decoder, torch.nn.GRU)
    assert hasattr(decoder, "output_projection")


def test_capsule_forward_shape(tiny_setup):
    tokenizer, _, _, capsule = tiny_setup
    report_ids = torch.randint(0, 100, (2, 20))
    secret_tokens = torch.randint(0, 100, (2, 4))
    secret_ids = torch.randint(0, 4, (2,))
    target_ids = torch.randint(0, 100, (2, 10))
    recon_logits, carrier_logits, carrier_ids, aux_loss = capsule(
        report_ids, secret_tokens, secret_ids, target_ids
    )
    assert recon_logits.shape == (2, 9, tokenizer.vocab_size)
    assert carrier_logits.shape == (2, 16, tokenizer.vocab_size)
    assert carrier_ids.shape == (2, 16)
    assert aux_loss.numel() == 1


def test_capsule_open_carrier_shape(tiny_setup):
    tokenizer, _, _, capsule = tiny_setup
    carrier_ids = torch.tensor(
        [[tokenizer.story_token_ids()[0]] * 16], dtype=torch.long
    )
    secret_tokens = torch.randint(0, 100, (1, 4))
    secret_ids = torch.randint(0, 4, (1,))
    generated = capsule.open_carrier(
        carrier_ids,
        secret_tokens,
        secret_ids,
        max_new_tokens=20,
        eos_id=tokenizer.eos_id,
    )
    assert len(generated) == 1
    assert all(isinstance(token, int) for token in generated[0])


def test_carrier_is_text_not_binary(tiny_setup):
    tokenizer, _, _, capsule = tiny_setup
    report_text = "The dog waited."
    report_ids = tokenizer.encode(report_text)[:32]
    report_ids += [tokenizer.pad_id] * (32 - len(report_ids))
    report_ids_t = torch.tensor([report_ids], dtype=torch.long)
    secret_tokens = [tokenizer.pad_id] * 4
    secret_tokens_t = torch.tensor([secret_tokens], dtype=torch.long)
    secret_ids_t = torch.zeros(1, dtype=torch.long)

    with torch.no_grad():
        carrier_logits = capsule.encode_carrier(report_ids_t, secret_tokens_t)
        carrier_ids = capsule.carrier_logits_to_ids(carrier_logits)
    text = tokenizer.decode(carrier_ids[0].tolist(), skip_special=True)
    assert text  # non-empty
    assert "{" not in text
    assert "}" not in text
