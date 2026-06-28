"""Tiny overfit smoke test for the neural carrier autoencoder.

Normal pytest runs only a few steps; full overfit diagnostics are marked slow.
"""

from types import SimpleNamespace

import pytest
import torch

from argus_capsules.neural_capsule import NeuralCapsule
from argus_capsules.neural_decoder import SecretConditionedDecoder
from argus_capsules.neural_encoder import NeuralCarrierEncoder
from argus_capsules.secrets import SecretConditioning
from argus_capsules.tokenizer import SimpleTokenizer
from argus_capsules.train_capsule_autoencoder import (
    _build_examples,
    _prepare_tensors,
    measure_token_lengths,
)
from argus_capsules.train_decoder import _compute_loss


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    yield


@pytest.fixture(autouse=True)
def clean_db():
    yield


def _build_tiny_tensors(n=4):
    tokenizer = SimpleTokenizer()
    examples = _build_examples(
        SimpleNamespace(
            n_examples=n,
            seed=900,
            num_secrets=4,
            overfit_small=True,
        )
    )
    lengths = measure_token_lengths(examples, tokenizer)
    return _prepare_tensors(
        examples,
        tokenizer,
        max_report_len=max(lengths["report_lengths"]),
        max_target_len=max(lengths["target_lengths"]),
        num_secrets=4,
        num_secret_tokens=4,
    ), tokenizer, examples


def test_autoencoder_parameters_update():
    (report, secret_tokens, secret_ids, target), tokenizer, _ = _build_tiny_tensors(n=2)
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

    before = list(capsule.parameters())[0].clone().detach()
    optimizer = torch.optim.Adam(capsule.parameters(), lr=1e-2)
    for _ in range(3):
        optimizer.zero_grad()
        recon_logits, _, _, aux_loss = capsule(report, secret_tokens, secret_ids, target)
        loss = _compute_loss(recon_logits, target, tokenizer.pad_id) + aux_loss
        loss.backward()
        optimizer.step()
    after = list(capsule.parameters())[0].clone().detach()
    assert not torch.allclose(before, after)


@pytest.mark.slow
@pytest.mark.skip(reason="Full tiny overfit diagnostic; run manually.")
def test_autoencoder_can_overfit_tiny():
    (report, secret_tokens, secret_ids, target), tokenizer, examples = _build_tiny_tensors(n=4)
    encoder = NeuralCarrierEncoder(
        vocab_size=tokenizer.vocab_size,
        hidden_size=64,
        num_layers=2,
        carrier_len=32,
    )
    decoder = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=64,
        num_layers=2,
        bos_id=tokenizer.bos_id,
    )
    capsule = NeuralCapsule(
        encoder,
        decoder,
        tokenizer,
        carrier_len=32,
        story_token_ids=tokenizer.story_token_ids(),
    )

    optimizer = torch.optim.Adam(capsule.parameters(), lr=1e-3)
    initial_loss = None
    for epoch in range(100):
        optimizer.zero_grad()
        recon_logits, _, _, aux_loss = capsule(report, secret_tokens, secret_ids, target)
        loss = _compute_loss(recon_logits, target, tokenizer.pad_id) + aux_loss
        loss.backward()
        optimizer.step()
        if initial_loss is None:
            initial_loss = loss.item()

    assert loss.item() < initial_loss * 0.5

    # Try one reconstruction.
    capsule.eval()
    with torch.no_grad():
        carrier_ids = capsule.seal_report(report[:1], secret_tokens[:1])
        generated = capsule.open_carrier(
            carrier_ids,
            secret_tokens[:1],
            secret_ids[:1],
            max_new_tokens=target.size(1),
            eos_id=tokenizer.eos_id,
        )
    text = tokenizer.decode(generated[0], skip_special=False)
    assert isinstance(text, str)
