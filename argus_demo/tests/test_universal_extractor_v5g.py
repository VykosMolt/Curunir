"""V5G tests: build determinism guards (reviewed-span order insensitivity,
canonical feature insertion order) and multi-seed statistics helpers."""
from __future__ import annotations

import pytest

from argus_neural.public_doc_features_v5b import shape_features
from argus_neural.span_utils import Span
from argus_neural.universal_extractor_v5b import train_universal_extractor_v5b

TEXT = ("The Supervisory Board fined Acme Corp 250 000 EUR.\n"
        "Acme Corp appealed the decision to the Board.\n"
        "The Board rejected the appeal by Acme Corp.\n")


def _row(reviewed_entity_spans):
    return {
        "example_id": "v5g-t-001",
        "source_id": "v5g:t:001",
        "source_text": TEXT,
        "source_hash": "f" * 64,
        "document_origin": "public_style_synthetic",
        "document_style": "test",
        "template_id": "v5g:test:tp_v5g_fixture:001",
        "annotation_completeness": "partial",
        "entities": [
            {"text": "Acme Corp", "normalized_text": "acme corp",
             "entity_type": "organization",
             "start_char": TEXT.find("Acme Corp"),
             "end_char": TEXT.find("Acme Corp") + len("Acme Corp"),
             "sentence_index": -1, "source_id": "v5g:t:001"},
        ],
        "claims": [], "relations": [], "dates": [],
        "reviewed_negative_spans": {"entity": reviewed_entity_spans},
    }


@pytest.mark.no_db
def test_reviewed_span_order_does_not_change_the_trained_model():
    spans = [[0, 3], [4, 15], [21, 30], [52, 61]]
    m1, _ = train_universal_extractor_v5b([_row(spans)], epochs=2, seed=7)
    m2, _ = train_universal_extractor_v5b([_row(list(reversed(spans)))], epochs=2, seed=7)
    assert m1.entity_model.weights == m2.entity_model.weights
    assert m1.boundary_weights == m2.boundary_weights


@pytest.mark.no_db
def test_shape_feature_insertion_order_is_canonical():
    span = Span(TEXT.find("Acme Corp 250 000 EUR"), TEXT.find("EUR") + 3)
    feats = shape_features(TEXT, span, prefix="e")
    tok_keys = [k for k in feats if k.startswith("e:tok=")]
    assert tok_keys == sorted(tok_keys)


@pytest.mark.no_db
def test_v5g_stats_helper():
    from argus_neural.run_v5g_variance_aware_evaluation import _stats
    s = _stats([0.42, 0.52, 0.47])
    assert s["min"] == 0.42 and s["max"] == 0.52
    assert s["spread"] == pytest.approx(0.10)
    assert s["mean"] == pytest.approx(0.47)


@pytest.mark.no_db
def test_v5g_runner_declares_fixed_seeds_and_baselines():
    from argus_neural.run_v5g_variance_aware_evaluation import (
        CLAIM_BAND,
        SEEDS,
        V5E_BASE,
    )
    assert len(SEEDS) >= 5 and len(set(SEEDS)) == len(SEEDS)
    assert V5E_BASE["claim"] == 0.55 and V5E_BASE["entity"] == 0.1515
    assert CLAIM_BAND == 0.53
