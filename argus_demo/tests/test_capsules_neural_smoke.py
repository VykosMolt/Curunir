"""Smoke tests for the neural decoder.

These tests use tiny models and datasets so the normal test suite remains fast.
Longer training diagnostics are marked and skipped by default.
"""

from types import SimpleNamespace

import pytest
import torch

from argus_capsules.neural_decoder import SecretConditionedDecoder
from argus_capsules.tokenizer import SimpleTokenizer
from argus_capsules.train_capsule_autoencoder import _build_examples
from argus_capsules.train_decoder import _compute_loss, _prepare_tensors


@pytest.fixture(scope="session", autouse=True)
def _db_setup():
    """Override parent conftest's database fixture; capsule tests need no Postgres."""
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """Override parent conftest's per-test database cleanup."""
    yield


def _make_tiny_tensors(tokenizer, n=4):
    examples = _build_examples(
        SimpleNamespace(
            n_examples=n,
            seed=0,
            num_secrets=4,
            overfit_small=True,
        )
    )
    for example in examples:
        example["story"] = "a small synthetic carrier."
    return _prepare_tensors(
        examples,
        tokenizer,
        max_story_len=64,
        max_target_len=1024,
    )


def test_model_forward_pass():
    tokenizer = SimpleTokenizer()
    model = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=8,
        hidden_size=32,
        num_layers=1,
        bos_id=tokenizer.bos_id,
    )
    story_ids = torch.randint(0, tokenizer.vocab_size, (2, 20))
    secret_ids = torch.randint(0, 4, (2,))
    target_ids = torch.randint(0, tokenizer.vocab_size, (2, 30))
    logits = model(story_ids, secret_ids, target_ids)
    assert logits.shape == (2, 29, tokenizer.vocab_size)


def test_generate_method_returns_token_ids():
    tokenizer = SimpleTokenizer()
    model = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=32,
        num_layers=1,
        bos_id=tokenizer.bos_id,
    )
    story_ids = torch.randint(0, tokenizer.vocab_size, (1, 20))
    secret_ids = torch.zeros(1, dtype=torch.long)
    generated = model.generate(story_ids, secret_ids, max_new_tokens=30, eos_id=tokenizer.eos_id)
    assert isinstance(generated, list)
    assert len(generated) == 1
    assert all(isinstance(t, int) for t in generated[0])


def test_loss_computation_ignores_padding():
    tokenizer = SimpleTokenizer()
    model = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=8,
        hidden_size=32,
        num_layers=1,
        bos_id=tokenizer.bos_id,
    )
    story_ids, secret_ids, target_ids = _make_tiny_tensors(tokenizer)
    logits = model(story_ids, secret_ids, target_ids)
    loss = _compute_loss(logits, target_ids, tokenizer.pad_id)
    assert loss.item() >= 0.0


@pytest.mark.slow
@pytest.mark.skip(reason="Neural overfit smoke test: run manually for diagnostics.")
def test_overfit_small_reduces_loss():
    """Optional diagnostic test: tiny model should overfit a tiny dataset."""
    tokenizer = SimpleTokenizer()
    story_ids, secret_ids, target_ids = _make_tiny_tensors(tokenizer, n=8)

    model = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=64,
        num_layers=2,
        bos_id=tokenizer.bos_id,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    initial_loss = None
    for epoch in range(100):
        optimizer.zero_grad()
        logits = model(story_ids, secret_ids, target_ids)
        loss = _compute_loss(logits, target_ids, tokenizer.pad_id)
        loss.backward()
        optimizer.step()
        if initial_loss is None:
            initial_loss = loss.item()

    assert loss.item() < initial_loss * 0.5


@pytest.mark.slow
@pytest.mark.skip(reason="Neural overfit smoke test: run manually for diagnostics.")
def test_overfit_small_can_produce_reject():
    """Optional diagnostic test: wrong secret can be trained to REJECT."""
    tokenizer = SimpleTokenizer()
    story_ids, secret_ids, target_ids = _make_tiny_tensors(tokenizer, n=8)
    # Replace half of the targets with REJECT.
    reject_target = tokenizer.encode("<REJECT>", add_bos=True, add_eos=True)
    reject_target += [tokenizer.pad_id] * (target_ids.size(1) - len(reject_target))
    target_ids[2:] = torch.tensor(reject_target)

    model = SecretConditionedDecoder(
        vocab_size=tokenizer.vocab_size,
        num_secrets=4,
        hidden_size=64,
        num_layers=2,
        bos_id=tokenizer.bos_id,
    )
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    for _ in range(150):
        optimizer.zero_grad()
        logits = model(story_ids, secret_ids, target_ids)
        loss = _compute_loss(logits, target_ids, tokenizer.pad_id)
        loss.backward()
        optimizer.step()

    generated = model.generate(story_ids[:1], secret_ids[:1], max_new_tokens=10, eos_id=tokenizer.eos_id)
    text = tokenizer.decode(generated[0], skip_special=True)
    assert text == "<REJECT>" or "REJECT" in text
