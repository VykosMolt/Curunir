"""V5K tests: split claim detector/typer heads."""
from __future__ import annotations

import pytest

from argus_neural.universal_training_v3 import train_universal_extractor_v3

_TEXT = ("The board fined Acme Corp 250 000 EUR on 5 May 2024.\n"
         "Acme Corp is a logistics company.\n"
         "The board ordered Acme Corp to delete the records.\n")


def _rows():
    # derive claim spans from the segmenter's own candidates so gold is
    # reachable by construction
    from argus_neural.structural_segmenter import segment_document
    from argus_neural.universal_extractor_v3 import structural_candidate_spans
    cands = [s.as_tuple() for s, _, k in
             structural_candidate_spans(_TEXT, segment_document(_TEXT))
             if k == "inner_sentence"]
    types = [("incident", "EUR 250,000"), ("status", "logistics company"),
             ("policy", "deletion ordered")]
    claims = []
    for (start, end), (ctype, value) in zip(sorted(cands), types):
        claims.append({"claim_text": _TEXT[start:end], "claim_type": ctype,
                       "subject_text": "board", "predicate_text": ctype,
                       "object_text": "Acme Corp", "value": value,
                       "start_char": start, "end_char": end,
                       "evidence_start_char": start, "evidence_end_char": end})
    return [{
        "example_id": "v5k-t-001", "source_id": "v5k:t:001",
        "source_text": _TEXT, "source_hash": "e" * 64,
        "document_origin": "public_style_synthetic", "document_style": "test",
        "template_id": "v5k:test:tp_v5k_fixture:001",
        "annotation_completeness": "exhaustive",
        "entities": [], "relations": [], "dates": [],
        "claims": claims,
    }]


ROWS = _rows()


@pytest.fixture(scope="module")
def model():
    m, diag = train_universal_extractor_v3(ROWS, epochs=4, seed=11)
    return m, diag


@pytest.mark.no_db
def test_hybrid_heads_detector_typed_and_typer_exists(model):
    m, diag = model
    # hybrid: the combined typed head keeps detection (pure binary split
    # regressed detection in V5K iteration 1); the typer reassigns types
    assert set(m.claim_model.labels) == {"none", "incident", "status", "policy"}
    assert m.claim_type_model is not None
    typed = [l for l in m.claim_type_model.labels if l != "none"]
    assert set(typed) == {"incident", "status", "policy"}
    assert diag["claim_type_training_samples"] == len(ROWS[0]["claims"])


@pytest.mark.no_db
def test_typer_never_returns_none(model):
    m, _ = model
    pred = m.extract_document(ROWS[0]["source_text"], "v5k:t:001")
    assert pred.claims, "detector found no claims on its own training doc"
    assert all(c.claim_type != "none" and c.claim_type != "claim" for c in pred.claims)


@pytest.mark.no_db
def test_split_heads_roundtrip_save_load(tmp_path, model):
    m, _ = model
    m.save(tmp_path / "m")
    from argus_neural.universal_extractor_v3 import ArgusUniversalNeuralExtractorV3
    loaded = ArgusUniversalNeuralExtractorV3.load(tmp_path / "m")
    assert loaded.claim_model.labels == m.claim_model.labels
    assert loaded.claim_type_model is not None
    assert loaded.claim_type_model.weights == m.claim_type_model.weights


@pytest.mark.no_db
def test_legacy_payload_without_typer_still_loads(tmp_path, model):
    import json
    m, _ = model
    m.save(tmp_path / "m")
    p = tmp_path / "m" / "model.json"
    payload = json.loads(p.read_text())
    del payload["claim_type_model"]
    p.write_text(json.dumps(payload))
    from argus_neural.universal_extractor_v3 import ArgusUniversalNeuralExtractorV3
    loaded = ArgusUniversalNeuralExtractorV3.load(tmp_path / "m")
    assert loaded.claim_type_model is None  # legacy combined-head path